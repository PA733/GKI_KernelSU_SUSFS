#!/usr/bin/env python3
"""Prepare Pixel A9 LZ4KD images from a matching CI bundle and stock backups.

Requires the lz4 command. This tool writes local files only; it never flashes a
device. Outputs are unsigned images for an unlocked bootloader. Keep both stock
boot and vendor_kernel_boot backups: the replacement module and kernel are a pair.
"""

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import zlib


LEGACY_LZ4 = b"\x02\x21\x4c\x18"
REMOVED = {"zram_gs", "zcomp_gs_cpu"}


def align(size, boundary):
    return (size + boundary - 1) // boundary * boundary


def padded(data, boundary):
    return data + bytes(align(len(data), boundary) - len(data))


def lz4(data, decompress=False):
    args = ["lz4", "-d", "-c"] if decompress else ["lz4", "-l", "-9", "-c"]
    return subprocess.run(args, input=data, capture_output=True, check=True).stdout


@dataclass
class Entry:
    name: str
    fields: list
    data: bytes


def read_cpio(data):
    entries = []
    offset = 0
    while offset + 110 <= len(data):
        header = data[offset:offset + 110]
        if header[:6] != b"070701":
            raise ValueError("Expected an uncompressed newc ramdisk")
        fields = [int(header[i:i + 8], 16) for i in range(6, 110, 8)]
        size, namesize = fields[6], fields[11]
        if not namesize or offset + 110 + namesize > len(data):
            raise ValueError("Truncated cpio name")
        name = data[offset + 110:offset + 110 + namesize]
        if name[-1:] != b"\0":
            raise ValueError("Unterminated cpio name")
        start = align(offset + 110 + namesize, 4)
        if start + size > len(data):
            raise ValueError("Truncated cpio file")
        entry = Entry(name[:-1].decode(), fields, data[start:start + size])
        entries.append(entry)
        offset = align(start + size, 4)
        if entry.name == "TRAILER!!!":
            if any(data[offset:]):
                raise ValueError("Unexpected data after cpio trailer")
            return entries
    raise ValueError("Missing cpio trailer")


def write_cpio(entries):
    output = bytearray()
    for entry in entries:
        name = entry.name.encode() + b"\0"
        fields = entry.fields.copy()
        fields[6], fields[11] = len(entry.data), len(name)
        output += b"070701" + "".join(f"{v:08x}" for v in fields).encode()
        output += name
        output += bytes(align(len(output), 4) - len(output))
        output += padded(entry.data, 4)
    return padded(bytes(output), 512)


def module_name(path):
    return Path(path).name.removesuffix(".ko").replace("-", "_")


def patch_ramdisk(entries, module):
    paths = {e.name.removeprefix("./"): e for e in entries}
    if len(paths) != len(entries):
        raise ValueError("Duplicate ramdisk paths")
    for name in ["modules.load", "modules.dep", "zsmalloc.ko", "zram_gs.ko", "zcomp_gs_cpu.ko"]:
        if "lib/modules/" + name not in paths:
            raise ValueError(f"Missing stock Pixel ramdisk entry: {name}")
    if "lib/modules/zram.ko" in paths:
        raise ValueError("Ramdisk already contains zram.ko; use the stock backup")

    deps = paths["lib/modules/modules.dep"]
    lines = []
    for line in deps.data.decode().splitlines():
        owner, sep, requirements = line.partition(":")
        if not sep:
            raise ValueError("Malformed modules.dep")
        if module_name(owner) in REMOVED:
            continue
        if any(module_name(x) in REMOVED for x in requirements.split()):
            raise ValueError(f"Another module depends on vendor ZRAM: {owner}")
        lines.append(line)
    lines.append("/lib/modules/zram.ko: /lib/modules/zsmalloc.ko")
    deps.data = ("\n".join(lines) + "\n").encode()

    load = paths["lib/modules/modules.load"]
    lines = []
    replacements = 0
    for line in load.data.decode().splitlines():
        name = module_name(line.strip())
        if name == "zcomp_gs_cpu":
            continue
        if name == "zram_gs":
            line = line.replace("zram_gs.ko", "zram.ko")
            replacements += 1
        lines.append(line)
    if replacements != 1:
        raise ValueError("Expected one zram_gs.ko entry in modules.load")
    load.data = ("\n".join(lines) + "\n").encode()

    # Alias and softdep entries owned by the removed modules are obsolete.
    # A softdep from any retained module must not be silently discarded.
    for filename in ("modules.alias", "modules.softdep"):
        entry = paths.get("lib/modules/" + filename)
        if entry is None:
            continue
        lines = []
        for line in entry.data.decode().splitlines():
            tokens = line.split()
            if not tokens or tokens[0].startswith("#"):
                lines.append(line)
                continue
            owner = tokens[-1] if filename == "modules.alias" else tokens[1]
            if module_name(owner) in REMOVED:
                continue
            if any(module_name(x) in REMOVED for x in tokens):
                raise ValueError(f"Unexpected vendor ZRAM reference in {filename}")
            lines.append(line)
        entry.data = ("\n".join(lines) + "\n").encode()

    old = paths["lib/modules/zram_gs.ko"]
    old.name = old.name.replace("zram_gs.ko", "zram.ko")
    old.data = module
    return [e for e in entries if e is not paths["lib/modules/zcomp_gs_cpu.ko"]]


class Elf:
    def __init__(self, data):
        if data[:6] != b"\x7fELF\x02\x01" or struct.unpack_from("<H", data, 18)[0] != 183:
            raise ValueError("Expected an AArch64 ELF64 module")
        offset = struct.unpack_from("<Q", data, 40)[0]
        stride, count, names_index = struct.unpack_from("<3H", data, 58)
        if stride != 64 or not 0 < names_index < count or offset + stride * count > len(data):
            raise ValueError("Invalid module section table")
        self.headers = [struct.unpack_from("<IIQQQQIIQQ", data, offset + i * stride) for i in range(count)]
        self.contents = [data[h[4]:h[4] + h[5]] if h[1] != 8 else b"" for h in self.headers]
        strings = self.contents[names_index]
        self.sections = {self.string(strings, h[0]): self.contents[i] for i, h in enumerate(self.headers)}

    @staticmethod
    def string(data, offset):
        return data[offset:data.index(b"\0", offset)].decode()

    def info(self):
        return dict(x.decode().split("=", 1) for x in self.sections[".modinfo"].split(b"\0") if b"=" in x)

    def imports(self):
        names = self.sections["__version_ext_names"].rstrip(b"\0").split(b"\0")
        crcs = self.sections["__version_ext_crcs"]
        if len(crcs) != 4 * len(names):
            raise ValueError("Invalid extended module versions")
        return {name.decode(): struct.unpack_from("<I", crcs, 4 * i)[0] for i, name in enumerate(names)}

    def exports(self):
        result = {}
        for i, h in enumerate(self.headers):
            if h[1] != 2:
                continue
            strings = self.contents[h[6]]
            for offset in range(0, len(self.contents[i]), 24):
                name, _, _, section, value, _ = struct.unpack_from("<IBBHQQ", self.contents[i], offset)
                name = self.string(strings, name)
                if name.startswith("__crc_"):
                    result[name[6:]] = value if section == 0xfff1 else struct.unpack_from("<I", self.contents[section], value)[0]
        return result


def validate_module(module, stock_zsmalloc, image, symvers):
    new, stock = Elf(module), Elf(stock_zsmalloc)
    info, stock_info = new.info(), stock.info()
    release = re.search(rb"Linux version (\S+)", image)
    if not release or info.get("vermagic") != stock_info.get("vermagic"):
        raise ValueError("New ZRAM and stock ZSMALLOC vermagic do not match")
    if info["vermagic"].split()[0] != release[1].decode():
        raise ValueError("Kernel and module release do not match")
    if info.get("name") != "zram" or info.get("depends") != "zsmalloc":
        raise ValueError("Expected zram.ko with only zsmalloc as its module dependency")
    if not module.endswith(b"~Module signature appended~\n"):
        raise ValueError("Expected the CI-signed GKI zram.ko")
    kernel_exports = {parts[1]: int(parts[0], 16) for line in symvers.splitlines() if len(parts := line.split()) >= 2}
    exports = kernel_exports | stock.exports()
    for symbol, expected in new.imports().items():
        if exports.get(symbol) != expected:
            raise ValueError(f"ZRAM import CRC mismatch or missing symbol: {symbol}")
    start = image.index(b"IKCFG_ST") + 8
    config = zlib.decompress(image[start:], 31).decode().splitlines()
    for required in ("CONFIG_ZRAM=m", "CONFIG_ZSMALLOC=m", "CONFIG_ZRAM_BACKEND_LZ4KD=y",
                     "CONFIG_LZ4KD_COMPRESS=y", "CONFIG_LZ4KD_DECOMPRESS=y",
                     "CONFIG_MODULE_SIG_PROTECT=y", "CONFIG_ARM64_4K_PAGES=y"):
        if required not in config:
            raise ValueError(f"Image configuration missing: {required}")
    return release[1].decode()


def vendor_parts(data):
    if data[:8] != b"VNDRBOOT" or len(data) < 2128:
        raise ValueError("Expected a Pixel vendor_kernel_boot image")
    version, page = struct.unpack_from("<2I", data, 8)
    ramdisk_size = struct.unpack_from("<I", data, 24)[0]
    header_size, dtb_size, _, table_size, count, entry_size, bootconfig_size = struct.unpack_from("<IIQ4I", data, 2096)
    if version != 4 or page not in (2048, 4096) or header_size != 2128:
        raise ValueError("Only Pixel vendor_boot v4 is supported")
    if count != 1 or entry_size != 108 or table_size != 108:
        raise ValueError("Expected one Pixel vendor kernel ramdisk fragment")
    offset = align(header_size, page)
    parts = []
    for size in (ramdisk_size, dtb_size, table_size, bootconfig_size):
        if offset + size > len(data):
            raise ValueError("Truncated vendor_kernel_boot image")
        parts.append(data[offset:offset + size])
        offset += align(size, page)
    if struct.unpack_from("<2I", parts[2]) != (ramdisk_size, 0):
        raise ValueError("Unexpected vendor ramdisk fragment range")
    if not parts[0].startswith(LEGACY_LZ4):
        raise ValueError("Expected a legacy LZ4 compressed Pixel ramdisk")
    return bytearray(data[:header_size]), page, parts


def make_vendor_boot(original, ramdisk):
    header, page, (_, dtb, table, bootconfig) = vendor_parts(original)
    compressed = lz4(ramdisk)
    struct.pack_into("<I", header, 24, len(compressed))
    table = bytearray(table)
    struct.pack_into("<I", table, 0, len(compressed))
    result = b"".join(padded(bytes(part), page) for part in (header, compressed, dtb, table, bootconfig))
    if len(result) > len(original):
        raise ValueError("Patched vendor_kernel_boot exceeds the original partition size")
    # The old AVB footer/signature covers different content and must not survive.
    return result + bytes(len(original) - len(result))


def make_boot(original, image):
    if original[:8] != b"ANDROID!" or len(original) < 1584:
        raise ValueError("Expected an Android boot v4 image")
    kernel_size, ramdisk_size = struct.unpack_from("<2I", original, 8)
    header_size = struct.unpack_from("<I", original, 20)[0]
    version = struct.unpack_from("<I", original, 40)[0]
    if version != 4 or header_size != 1584:
        raise ValueError("Only Android boot v4 is supported")
    page = 4096
    ramdisk_start = page + align(kernel_size, page)
    if ramdisk_start + ramdisk_size > len(original):
        raise ValueError("Truncated boot image")
    old_kernel = original[page:page + kernel_size]
    if old_kernel.startswith(LEGACY_LZ4):
        image = lz4(image)
    elif old_kernel[56:60] != b"ARM\x64":
        raise ValueError("Unsupported boot kernel compression")
    header = bytearray(original[:header_size])
    struct.pack_into("<I", header, 8, len(image))
    struct.pack_into("<I", header, 1580, 0)  # Drop the obsolete GKI boot signature.
    ramdisk = original[ramdisk_start:ramdisk_start + ramdisk_size]
    result = padded(header, page) + padded(image, page) + padded(ramdisk, page)
    if len(result) > len(original):
        raise ValueError("Patched boot exceeds the original partition size")
    return result + bytes(len(original) - len(result))


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--vendor-kernel-boot", type=Path, required=True)
    parser.add_argument("--boot", type=Path, help="Optional stock/working boot partition backup")
    parser.add_argument("--output", type=Path, required=True, help="New, nonexistent output directory")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output directory must not exist; preserve earlier backups")
    try:
        original = args.vendor_kernel_boot.read_bytes()
        _, _, parts = vendor_parts(original)
        entries = read_cpio(lz4(parts[0], decompress=True))
        stock_zsmalloc = next(e.data for e in entries if e.name.removeprefix("./") == "lib/modules/zsmalloc.ko")
        image = (args.bundle / "Image").read_bytes()
        module = (args.bundle / "zram.ko").read_bytes()
        release = validate_module(module, stock_zsmalloc, image, (args.bundle / "vmlinux.symvers").read_text())
        changed = write_cpio(patch_ramdisk(entries, module))
        vendor = make_vendor_boot(original, changed)
        # Round-trip the actual compressed image before writing any outputs.
        assert lz4(vendor_parts(vendor)[2][0], decompress=True) == changed
        outputs = {"vendor_kernel_boot-stock.img": original, "vendor_kernel_boot-lz4kd.img": vendor}
        if args.boot:
            stock_boot = args.boot.read_bytes()
            outputs.update({"boot-stock.img": stock_boot, "boot-lz4kd.img": make_boot(stock_boot, image)})
        report = {"kernel_release": release, "image_sha256": sha256(image),
                  "zram_module_sha256": sha256(module),
                  "files": {name: sha256(data) for name, data in outputs.items()}}
        args.output.mkdir(parents=True)
        for name, data in outputs.items():
            (args.output / name).write_bytes(data)
        (args.output / "manifest.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
    except (OSError, ValueError, KeyError, StopIteration, struct.error,
            zlib.error, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Pixel ZRAM image preparation failed: {error}\n")


if __name__ == "__main__":
    main()
