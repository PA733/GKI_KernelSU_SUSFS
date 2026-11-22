"""Extract the stock module CRCs and their signing certificate for the Pixel GKI build."""
import argparse
import io
import re
import ssl
import struct
import subprocess
import tempfile
from pathlib import Path

from elftools.elf.elffile import ELFFile


MODULES = ("wwan", "rfkill", "rust_binder")
SIGNATURE_MAGIC = b"~Module signature appended~\n"


def read_module(path):
    tools = {".xz": "xz", ".gz": "gzip", ".zst": "zstd", ".lz4": "lz4"}
    if path.suffix in tools:
        return subprocess.check_output([tools[path.suffix], "-dc", path])
    return path.read_bytes()


def module_versions(data):
    elf = ELFFile(io.BytesIO(data))
    if elf.elfclass != 64 or not elf.little_endian or elf["e_machine"] != "EM_AARCH64":
        raise ValueError("Expected an arm64 stock module")
    crcs = elf.get_section_by_name("__version_ext_crcs")
    names = elf.get_section_by_name("__version_ext_names")
    if crcs is not None and names is not None:
        symbols = names.data().rstrip(b"\0").split(b"\0")
        rows = ((value[0], name) for value, name in
                zip(struct.iter_unpack("<I", crcs.data()), symbols, strict=True))
    else:
        section = elf.get_section_by_name("__versions")
        if section is None:
            raise ValueError("Missing module symbol versions")
        rows = struct.iter_unpack("<Q56s", section.data())
    versions = {}
    for crc, raw in rows:
        symbol = raw.split(b"\0", 1)[0].decode("ascii")
        if not re.fullmatch(r"[A-Za-z0-9_.$]+", symbol):
            raise ValueError(f"Invalid module symbol {symbol!r}")
        versions[symbol] = crc
    if not versions:
        raise ValueError("Empty stock module CRC baseline")
    return versions


def split_signature(data):
    if not data.endswith(SIGNATURE_MAGIC):
        raise ValueError("Stock GKI module has no PKCS#7 signature")
    end = len(data) - len(SIGNATURE_MAGIC) - 12
    size = int.from_bytes(data[end + 8:end + 12], "big")
    if not 0 < size < end or data[end + 2] != 2:
        raise ValueError("Invalid module signature trailer")
    return data[:end-size], data[end-size:end]


def kernel_certs(image):
    # DER Certificate -> TBSCertificate -> explicit X.509 version.
    for match in re.finditer(rb"\x30\x82..\x30\x82..\xa0\x03\x02\x01", image, re.DOTALL):
        size = int.from_bytes(image[match.start()+2:match.start()+4], "big") + 4
        yield image[match.start():match.start()+size]


def verified_certificate(image, modules):
    # Linux sign-file normally omits the certificate from the module's CMS message.
    # Match boot's public key to the actual detached signatures in system_dlkm.
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        signed = []
        for name, data in modules.items():
            content, signature = split_signature(data)
            body, sig = tmp / name, tmp / (name + ".p7")
            body.write_bytes(content)
            sig.write_bytes(signature)
            signed.append((body, sig))
        cert = tmp / "candidate.pem"
        for der in kernel_certs(image):
            pem = ssl.DER_cert_to_PEM_cert(der)
            cert.write_text(pem)
            if all(subprocess.run([
                    "openssl", "cms", "-verify", "-binary", "-nointern", "-noverify",
                    "-inform", "DER", "-in", sig, "-content", body, "-certfile", cert,
                    "-out", "/dev/null"], stderr=subprocess.DEVNULL).returncode == 0
                   for body, sig in signed):
                return pem
    raise ValueError("No certificate embedded in boot verifies the selected stock modules")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("modules", type=Path)
    parser.add_argument("kernel", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    modules = {}
    lines = []
    for name in MODULES:
        paths = [p for p in args.modules.rglob(name + ".ko*")
                 if p.name in [name + ".ko" + s for s in ("", ".gz", ".xz", ".zst", ".lz4")]]
        if len(paths) != 1:
            raise ValueError(f"Expected one stock {name} module, found {paths}")
        modules[name] = data = read_module(paths[0])
        for symbol, crc in sorted(module_versions(data).items()):
            lines.append(f"{name}\t0x{crc:08x}\t{symbol}\n")
    cert = verified_certificate(args.kernel.read_bytes(), modules)
    (args.output / "stock-gki-cert.pem").write_text(cert)
    (args.output / "stock-module-crcs-critical.tsv").write_text("".join(lines))


if __name__ == "__main__":
    main()
