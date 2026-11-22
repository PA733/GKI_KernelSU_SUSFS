"""Resolve a Pixel full OTA and derive the GKI build inputs from its stock images."""
import argparse
import base64
import datetime
import gzip
import hashlib
import json
import re
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
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
    # Google lists releases chronologically within each month. A later carrier
    # rollout must not replace the most recently published global release.
    global_builds = [build for build, row in builds.items() if not row[1]]
    if not global_builds:
        raise ValueError("No global release for this device; specify a regional Build ID")
    return max(reversed(global_builds), key=lambda build: builds[build][0])


def fetch(url, *options):
    return subprocess.check_output(["curl", "--fail", "--silent", "--show-error", "--location",
                                    "--retry", "3", "--max-time", "90", "--proto", "=https", *options, url])


def resolve_ota(device, requested, *, acknowledged=False):
    options = ("--cookie", "devsite_wall_acks=nexus-ota-tos") if acknowledged else ()
    for domain in ("developers.google.com", "developers.google.cn"):
        try:
            html = fetch(f"https://{domain}/android/ota?hl=en", *options).decode()
        except subprocess.CalledProcessError:
            continue
        builds = releases(html, device)
        if builds:
            build = select_ota(builds, requested)
            return build, builds[build][2], builds[build][3]
    raise ValueError(f"No official full OTA table available for {device}")


def extract_partitions(url, device, build, work):
    work.mkdir(parents=True, exist_ok=True)
    def dumper_json(command, filename, *args):
        # v0.1.6 prints a timing line even in metadata --json mode.
        subprocess.run(["payload-dumper", command, url, "--json", "--save", "-o", str(work), *args],
                       stdout=subprocess.DEVNULL, check=True)
        return json.loads((work / filename).read_text())
    metadata = dumper_json("metadata", "metadata_info.json")["properties"]
    if (device not in metadata.get("pre-device", "").split("|") or
            not any(f"/{build}/" in item for item in metadata.get("post-build", "").split("|"))):
        raise ValueError("OTA metadata disagrees with the selected device/build")
    names = "boot,system_dlkm"
    partitions = dumper_json("list", "partitions_info.json", "-p", names)
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


def stock_modules(work, output):
    image = work / "system_dlkm.img"
    modules = work / "modules"
    modules.mkdir()
    kind = subprocess.check_output(["blkid", "-p", "-s", "TYPE", "-o", "value", image],
                                   text=True).strip()
    if kind == "erofs":
        subprocess.run(["fsck.erofs", f"--extract={modules}", image], check=True)
    elif kind == "ext4":
        subprocess.run(["debugfs", "-R", f"rdump / {modules}", image], check=True)
    else:
        raise ValueError(f"Unsupported stock system_dlkm filesystem: {kind}")
    subprocess.run([sys.executable, Path(__file__).with_name("pixel_modules.py"),
                    modules, work / "Image", output], check=True)


def snapshot_manifest(template, revisions, common):
    root = ET.fromstring(template)
    paths = set()
    for project in root.findall("project"):
        path = project.get("path", project.get("name"))
        project.set("revision", revisions[path])
        paths.add(path)
    if paths != set(revisions):
        raise ValueError("The superproject and its manifest template list different projects")
    # The moving superproject branch in a template must not override explicit pins.
    for entry in root.findall("superproject"):
        root.remove(entry)
    xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    validate_manifest(xml, common)
    return xml


def resolve_sources(identity, cert, work, output):
    build, common = identity["kernel_build"], identity["common"]
    artifact = f"https://ci.android.com/builds/submitted/{build}/kernel_aarch64/latest/raw"
    try:
        xml = fetch(f"{artifact}/manifest_{build}.xml")
    except subprocess.CalledProcessError:
        xml = b""
    if xml.lstrip().startswith((b"<?xml", b"<manifest")):
        pins = validate_manifest(xml, common)
        (output / "manifest.xml").write_bytes(xml)
        return dict(source="Android CI manifest", revisions=pins)

    try:
        raw_info = fetch(f"{artifact}/BUILD_INFO")
        info = json.loads(raw_info)["parsed_manifest"]
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError):
        info = None
    repo = work / "superproject.git"
    branch = "common-android16-6.12-sp"
    subprocess.run(["git", "clone", "--bare", "--single-branch", "--branch", branch,
                    "https://android.googlesource.com/kernel/superproject", repo], check=True)

    def git(*args):
        return subprocess.check_output(["git", "-C", repo, *args], text=True).strip()

    if info is not None:
        rev = info["superproject"]["revision"]
        if not re.fullmatch("[0-9a-f]{40}", rev):
            raise ValueError("Invalid Android CI superproject revision")
        source = "Android CI BUILD_INFO"
    else:
        start = subprocess.check_output(["openssl", "x509", "-in", cert, "-noout",
                                        "-startdate"], text=True).strip().split("=", 1)[1]
        when = parsedate_to_datetime(start).astimezone(datetime.timezone.utc).isoformat()
        rev = git("rev-list", "--first-parent", "--max-count=1", "--before=" + when, "HEAD")
        if not rev:
            raise ValueError(f"No public superproject snapshot at stock signing time {when}")
        source = f"Public superproject at stock certificate start {when} (inferred)"
    tree = git("ls-tree", "-r", rev)
    pins = {line.split("\t")[1]: line.split()[2] for line in tree.splitlines()
            if line.startswith("160000 ")}
    if not pins["common"].startswith(common):
        raise ValueError(f"Snapshot {rev} contains {pins['common']}; stock kernel requires {common}")
    if info is not None:
        ci_pins = {p.get("path", p["name"]): p["revision"] for p in info["projects"]}
        if ci_pins != pins:
            raise ValueError("Android CI project revisions disagree with its superproject")
    name, _, template_rev = git("show", rev + ":.supermanifest").split()
    if name != "kernel/manifest" or not re.fullmatch("[a-f0-9]{40}", template_rev):
        raise ValueError(f"Invalid manifest template at superproject {rev}")
    template = base64.b64decode(fetch(f"https://android.googlesource.com/kernel/manifest/+/"
                                      f"{template_rev}/default.xml?format=TEXT").strip(), validate=True)
    xml = snapshot_manifest(template, pins, common)
    (output / "manifest.xml").write_bytes(xml)
    print(f"{source}: {rev} ({len(pins)} projects)", flush=True)
    return dict(source=source, superproject=rev, manifest_revision=template_rev, revisions=pins)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("device", choices=["kodiak", "grizzly", "cubs", "yogi"])
    parser.add_argument("--build", default="")
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    work, output = args.work, args.output
    output.mkdir(parents=True, exist_ok=True)
    build, url, ota_hash = resolve_ota(args.device, args.build.strip(), acknowledged=True)
    print(f"{args.device} {build} {url}", flush=True)
    hashes = extract_partitions(url, args.device, build, work)
    kernel = unpack_kernel((work / "boot.img").read_bytes())
    identity = kernel_identity(kernel)
    (work / "Image").write_bytes(kernel)
    stock_modules(work, output)
    sources = resolve_sources(identity, output / "stock-gki-cert.pem", work, output)
    metadata = json.loads((work / "metadata_info.json").read_text())["properties"]
    patch = datetime.date.fromisoformat(metadata["post-security-patch-level"])
    profile = dict(device=args.device, build=build, ota_url=url, ota_sha256=ota_hash,
                   security_patch=patch.isoformat(), partitions=hashes, **sources, **identity)
    (output / "profile.json").write_text(json.dumps(profile, indent=2) + "\n")
    if args.github_output:
        with args.github_output.open("a") as out:
            print(f"target={args.device}-{build}", file=out)
            print(f"sub_level={identity['sub_level']}", file=out)
            print(f"patch_month={patch:%Y-%m}", file=out)
    print(f"Stock kernel: {identity['release']} ({identity['timestamp']})")


if __name__ == "__main__":
    main()
