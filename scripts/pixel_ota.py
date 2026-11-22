"""Resolve a Pixel full OTA and derive the GKI build inputs from its stock images."""
import argparse
import datetime
import gzip
import hashlib
import json
import re
import struct
import subprocess
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit


class ReleaseTable(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.cells = []
        self.links = []
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.cells, self.links = [], []
        elif tag == "td":
            self.cell = []
        elif tag == "a" and self.cell is not None:
            self.links.append(dict(attrs).get("href", ""))

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag == "td" and self.cell is not None:
            self.cells.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.cells:
            self.rows.append((self.cells, self.links))


def releases(html, device):
    table = ReleaseTable()
    table.feed(html)
    builds = {}
    for cells, links in table.rows:
        for url in links:
            parts = urlsplit(url)
            if parts.scheme != "https" or parts.netloc != "dl.google.com":
                continue
            match = re.fullmatch(rf"/(?:dl/)?android/aosp/{re.escape(device)}-ota-([a-z0-9.]+)-([0-9a-f]{{8}})\.zip", parts.path)
            if not match:
                continue
            build, prefix = match.groups()
            label = re.search(r"\(([A-Z0-9.]+),\s*([A-Za-z]+ \d{4})(?:,\s*([^()]+))?\)", cells[0])
            checksum = cells[-1].lower()
            if (not label or label[1].lower() != build or not re.fullmatch("[a-f0-9]{64}", checksum)
                    or not checksum.startswith(prefix)):
                raise ValueError(f"Malformed Google OTA listing: {cells[0]}")
            month = datetime.datetime.strptime(label[2][:3] + label[2][-5:], "%b %Y")
            builds[label[1]] = (month, label[3], url, checksum)
    return builds


def select_ota(builds, requested):
    if requested:
        if requested.upper() not in builds:
            raise ValueError(f"Build {requested} is absent from the official device table")
        return requested.upper()
    if not builds:
        raise ValueError("No matching device in the full OTA table")
    month = max(row[0] for row in builds.values())
    candidates = [build for build, row in builds.items() if row[0] == month and not row[1]]
    if len(candidates) != 1:
        raise ValueError("Select an exact build ID for this release month: " +
                         ", ".join(build for build, row in builds.items() if row[0] == month))
    return candidates[0]


def fetch(url):
    return subprocess.check_output(["curl", "--fail", "--silent", "--show-error", "--location",
                                    "--retry", "3", "--max-time", "90", "--proto", "=https", url])


def resolve_ota(device, requested):
    for domain in ("developers.google.com", "developers.google.cn"):
        try:
            html = fetch(f"https://{domain}/android/ota?hl=en").decode()
        except subprocess.CalledProcessError:
            continue
        builds = releases(html, device)
        if builds:
            build = select_ota(builds, requested)
            return build, builds[build][2], builds[build][3]
    raise ValueError(f"No official full OTA table available for {device}")


def extract_partitions(url, device, build, work):
    def dumper_json(*args):
        return json.loads(subprocess.check_output(["payload-dumper", *args, url, "--json"]))
    metadata = dumper_json("metadata")["properties"]
    if (device not in metadata.get("pre-device", "").split("|") or
            not any(f"/{build}/" in item for item in metadata.get("post-build", "").split("|"))):
        raise ValueError("OTA metadata disagrees with the selected device/build")
    names = "boot,system_dlkm"
    partitions = dumper_json("list", "-p", names)
    subprocess.run(["payload-dumper", "extract", url, "-p", names, "-o", str(work)], check=True)
    hashes = {}
    for name in names.split(","):
        info = partitions[name]
        with (work / (name + ".img")).open("rb") as image:
            digest = hashlib.file_digest(image, "sha256").hexdigest()
        if digest != info["hash"]:
            raise ValueError(f"{name} SHA-256 differs from the OTA payload manifest")
        hashes[name] = digest
    return hashes


def unpack_kernel(boot):
    if boot[:8] != b"ANDROID!" or struct.unpack_from("<I", boot, 40)[0] not in (3, 4):
        raise ValueError("Expected Android boot header v3/v4")
    size = struct.unpack_from("<I", boot, 8)[0]
    if size == 0 or size > len(boot) - 4096:
        raise ValueError("Invalid boot kernel size")
    kernel = boot[4096:4096 + size]
    if kernel.startswith(b"\x1f\x8b"):
        kernel = gzip.decompress(kernel)
    elif kernel[:4] in (b"\x02\x21\x4c\x18", b"\x04\x22\x4d\x18"):
        kernel = subprocess.run(["lz4", "-dc"], input=kernel, capture_output=True, check=True).stdout
    if kernel[56:60] != b"ARM\x64":
        raise ValueError("Expected an arm64 Linux Image after decompression")
    return kernel


def kernel_identity(kernel):
    pattern = (rb"Linux version (?P<release>6\.12\.(?P<sub_level>\d+)-android16-(?P<kmi>\d+)"
               rb"-g(?P<common>[a-f0-9]{7,40})-ab(?P<kernel_build>\d+)-4k) "
               rb"[^\x00\n]{1,2000}? #\d+[^\x00\n]*? "
               rb"(?P<timestamp>(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) [A-Z][a-z]{2} +\d{1,2} "
               rb"\d\d:\d\d:\d\d UTC \d{4})")
    matches = {tuple(m.groupdict().items()) for m in re.finditer(pattern, kernel)}
    if len(matches) != 1:
        raise ValueError("Expected one stock 4 KiB android16-6.12 GKI banner with Git SHA and kernel build ID")
    return {name: value.decode() for name, value in matches.pop()}


def validate_manifest(xml, common):
    root = ET.fromstring(xml)
    if root.tag != "manifest" or root.find("include") is not None:
        raise ValueError("Expected a standalone pinned repo manifest")
    projects = {}
    for p in root.iter("project"):
        path = p.get("path", p.get("name"))
        rev = p.get("revision", "")
        if not re.fullmatch("[a-f0-9]{40}", rev):
            raise ValueError(f"Unpinned manifest project {path}: {rev}")
        projects[path] = rev
    for path in ("common", "build/kernel", "prebuilts/clang/host/linux-x86", "prebuilts/rust"):
        if path not in projects:
            raise ValueError(f"Missing GKI manifest project {path}")
    if not projects["common"].startswith(common):
        raise ValueError(f"Manifest common {projects['common']} differs from stock g{common}")
    return projects


def download_manifest(build, common, output):
    url = (f"https://ci.android.com/builds/submitted/{build}/kernel_aarch64/latest/raw/"
           f"manifest_{build}.xml")
    try:
        xml = fetch(url)
        pins = validate_manifest(xml, common)
    except (subprocess.CalledProcessError, ET.ParseError, ValueError) as error:
        raise ValueError(f"No matching public manifest for kernel ab{build} (stock g{common}) "
                         f"at {url}: {error}") from error
    output.write_bytes(xml)
    return pins


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("device", choices=["kodiak", "grizzly", "cubs", "yogi"])
    parser.add_argument("--build", default="")
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--diagnostic-tag", default="")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    work, output = args.work, args.output
    output.mkdir(parents=True, exist_ok=True)
    build, url, ota_hash = resolve_ota(args.device, args.build.strip())
    print(f"{args.device} {build} {url}", flush=True)
    hashes = extract_partitions(url, args.device, build, work)
    kernel = unpack_kernel((work / "boot.img").read_bytes())
    identity = kernel_identity(kernel)
    (work / "Image").write_bytes(kernel)
    if args.diagnostic_tag:
        tag = re.fullmatch(r"android16-6\.12-(\d{4}-\d{2})_r\d+", args.diagnostic_tag)
        if not tag:
            raise ValueError("Expected an android16-6.12-YYYY-MM_rN diagnostic tag")
        manifest_line = tag[1]
        pins = {}
    else:
        pins = download_manifest(identity["kernel_build"], identity["common"], output / "manifest.xml")
        manifest_line = "sp"
    profile = dict(device=args.device, build=build, ota_url=url, ota_sha256=ota_hash,
                   partitions=hashes, revisions=pins, **identity)
    (output / "profile.json").write_text(json.dumps(profile, indent=2) + "\n")
    if args.github_output:
        with args.github_output.open("a") as out:
            for name, value in dict(build=build, sub_level=identity["sub_level"],
                                    manifest_line=manifest_line).items():
                print(f"{name}={value}", file=out)
    print(f"Stock kernel: {identity['release']} ({identity['timestamp']})")


if __name__ == "__main__":
    main()
