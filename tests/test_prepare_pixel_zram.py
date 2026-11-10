import copy
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

from scripts.prepare_pixel_zram import (
    Entry, Elf, LEGACY_LZ4, align, lz4, make_boot, make_vendor_boot,
    padded, patch_ramdisk, read_cpio, vendor_parts, write_cpio, verify_module_signature,
    avb_metadata, refresh_avb_footer,
)


def entry(name, data=b"", mode=0o100644):
    return Entry(name, [17, mode, 1000, 1000, 1, 123456, len(data), 0, 0, 0, 0, len(name) + 1, 0], data)


def stock_entries():
    return [
        entry("lib/modules/zsmalloc.ko", b"stock zsmalloc"),
        entry("lib/modules/zram_gs.ko", b"stock zram"),
        entry("lib/modules/zcomp_gs_cpu.ko", b"stock compressor"),
        entry("lib/modules/other.ko", b"unrelated vendor driver"),
        entry("lib/modules/modules.load", b"zsmalloc.ko\nother.ko\nzcomp_gs_cpu.ko\nzram_gs.ko\n"),
        entry("lib/modules/modules.dep", b"/lib/modules/zsmalloc.ko:\n/lib/modules/other.ko:\n"
              b"/lib/modules/zram_gs.ko: /lib/modules/zsmalloc.ko\n"
              b"/lib/modules/zcomp_gs_cpu.ko: /lib/modules/zram_gs.ko /lib/modules/zsmalloc.ko\n"),
        entry("lib/modules/modules.alias", b"alias old_zram zram_gs\nalias other_device other\n"),
        entry("lib/modules/modules.softdep", b"softdep zram_gs pre: zsmalloc\nsoftdep other pre: unrelated\n"),
        entry("lib/modules/current", b"other.ko", 0o120777),
        entry("TRAILER!!!", mode=0),
    ]


class RamdiskTests(unittest.TestCase):
    def test_round_trip_preserves_file_metadata_and_symlink(self):
        entries = stock_entries()
        self.assertEqual(read_cpio(write_cpio(entries)), entries)

    def test_patch_keeps_allocator_and_unrelated_drivers(self):
        before = stock_entries()
        after = patch_ramdisk(copy.deepcopy(before), b"replacement zram")
        result = {e.name: e for e in read_cpio(write_cpio(after))}
        for old in before:
            if old.name in ("lib/modules/zsmalloc.ko", "lib/modules/other.ko", "lib/modules/current"):
                self.assertEqual(result[old.name], old)
        self.assertNotIn("lib/modules/zram_gs.ko", result)
        self.assertNotIn("lib/modules/zcomp_gs_cpu.ko", result)
        self.assertEqual(result["lib/modules/zram.ko"].data, b"replacement zram")
        self.assertEqual(result["lib/modules/modules.load"].data, b"zsmalloc.ko\nother.ko\nzram.ko\n")
        self.assertIn(b"/lib/modules/zram.ko: /lib/modules/zsmalloc.ko\n", result["lib/modules/modules.dep"].data)
        self.assertEqual(result["lib/modules/modules.alias"].data, b"alias other_device other\n")
        self.assertEqual(result["lib/modules/modules.softdep"].data, b"softdep other pre: unrelated\n")

    def test_unknown_vendor_consumer_is_rejected(self):
        entries = stock_entries()
        next(e for e in entries if e.name.endswith("modules.dep")).data += b"/lib/modules/new.ko: /lib/modules/zram_gs.ko\n"
        with self.assertRaisesRegex(ValueError, "Another module depends"):
            patch_ramdisk(entries, b"replacement")

    def test_retained_soft_dependency_is_not_silently_removed(self):
        entries = stock_entries()
        next(e for e in entries if e.name.endswith("modules.softdep")).data += b"softdep consumer pre: zcomp_gs_cpu\n"
        with self.assertRaisesRegex(ValueError, "Unexpected vendor ZRAM reference"):
            patch_ramdisk(entries, b"replacement")

    def test_already_modified_ramdisk_is_rejected(self):
        entries = stock_entries()
        entries.insert(0, entry("lib/modules/zram.ko", b"another module"))
        with self.assertRaisesRegex(ValueError, "already contains"):
            patch_ramdisk(entries, b"replacement")

    def test_truncated_archive_and_extra_archive_are_rejected(self):
        data = write_cpio(stock_entries())
        with self.assertRaises(ValueError):
            read_cpio(data[:250])
        with self.assertRaisesRegex(ValueError, "after cpio trailer"):
            read_cpio(data + data)

    def test_non_arm64_module_is_rejected(self):
        with self.assertRaises(ValueError):
            Elf(b"not an ELF module" * 8)


class SelectionModuleTests(unittest.TestCase):
    def run_module(self, algorithms, initialized):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        sysfs = root / "sysfs"
        sysfs.mkdir()
        (sysfs / "comp_algorithm").write_text(algorithms)
        (sysfs / "initstate").write_text(str(initialized))
        resetprop = root / "resetprop"
        resetprop.write_text('#!/bin/sh\nprintf "%s\\n" "$*" > "' + str(root / "property-call") + '"\n')
        resetprop.chmod(0o755)
        source = (Path(__file__).resolve().parents[1] / "zram/pixel-module/post-fs-data.sh").read_text()
        source = source.replace("/sys/block/zram0", str(sysfs))
        script = root / "post-fs-data.sh"
        script.write_text(source)
        result = subprocess.run(["sh", str(script)], env=dict(os.environ, PATH=str(root) + ":" + os.environ["PATH"]), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return root, sysfs

    def test_lz4kd_is_selected_before_swap_setup(self):
        root, sysfs = self.run_module("lzo [lz4] lz4kd\n", 0)
        self.assertEqual((root / "property-call").read_text(), "-n mmd.zram.comp_algorithm lz4kd\n")
        self.assertEqual((sysfs / "comp_algorithm").read_text(), "lz4kd\n")

    def test_stock_kernel_properties_are_untouched(self):
        root, sysfs = self.run_module("lzo [lz4]\n", 0)
        self.assertFalse((root / "property-call").exists())
        self.assertEqual((sysfs / "comp_algorithm").read_text(), "lzo [lz4]\n")

    def test_active_swap_is_not_reconfigured(self):
        root, sysfs = self.run_module("lzo [lz4] lz4kd\n", 1)
        self.assertFalse((root / "property-call").exists())
        self.assertEqual((sysfs / "comp_algorithm").read_text(), "lzo [lz4] lz4kd\n")


@unittest.skipUnless(shutil.which("openssl"), "openssl command required")
class ModuleSignatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name)
        cls.certificates = []
        for name in ("matching", "other"):
            subprocess.run(["openssl", "req", "-new", "-newkey", "rsa:2048", "-nodes", "-x509",
                            "-days", "1", "-subj", f"/CN={name}", "-keyout", str(root / f"{name}.key"),
                            "-out", str(root / f"{name}.pem")], check=True, capture_output=True)
            cls.certificates.append(subprocess.check_output([
                "openssl", "x509", "-in", str(root / f"{name}.pem"), "-outform", "DER"]))
        payload = b"module content for signature regression"
        (root / "payload").write_bytes(payload)
        subprocess.run(["openssl", "cms", "-sign", "-binary", "-nocerts", "-noattr",
                        "-in", str(root / "payload"), "-signer", str(root / "matching.pem"),
                        "-inkey", str(root / "matching.key"), "-outform", "DER",
                        "-out", str(root / "signature")], check=True, capture_output=True)
        signature = (root / "signature").read_bytes()
        header = bytes([0, 0, 2, 0, 0, 0, 0, 0]) + len(signature).to_bytes(4, "big")
        cls.module = payload + signature + header + b"~Module signature appended~\n"

    def test_matching_embedded_certificate_verifies(self):
        verify_module_signature(self.module, b"Image prefix" + self.certificates[0])

    def test_different_build_key_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "does not verify"):
            verify_module_signature(self.module, b"Image prefix" + self.certificates[1])

    def test_modified_module_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "does not verify"):
            verify_module_signature(b"X" + self.module[1:], self.certificates[0])


@unittest.skipUnless(shutil.which("lz4"), "lz4 command required")
class BootImageTests(unittest.TestCase):
    @staticmethod
    def vendor_image():
        header = bytearray(2128)
        header[:8] = b"VNDRBOOT"
        ramdisk = lz4(write_cpio(stock_entries()))
        dtb, bootconfig = b"original DTB\0" * 137, b"original bootconfig\n"
        struct.pack_into("<5I", header, 8, 4, 2048, 0x10008000, 0x11000000, len(ramdisk))
        struct.pack_into("<IIQ4I", header, 2096, 2128, len(dtb), 0x11f00000, 108, 1, 108, len(bootconfig))
        table = bytearray(108)
        struct.pack_into("<3I", table, 0, len(ramdisk), 0, 1)
        table[12:16] = b"test"
        image = b"".join(padded(bytes(x), 2048) for x in (header, ramdisk, dtb, table, bootconfig))
        image += bytes(65536 - len(image) - 64) + b"AVBf" + bytes(60)
        return image

    def test_vendor_image_preserves_dtb_config_and_fragment_metadata(self):
        original = self.vendor_image()
        old_header, page, old = vendor_parts(original)
        changed = write_cpio(patch_ramdisk(read_cpio(lz4(old[0], True)), b"replacement"))
        result = make_vendor_boot(original, changed)
        header, new_page, parts = vendor_parts(result)
        self.assertEqual(len(result), len(original))
        self.assertEqual(page, new_page)
        self.assertEqual(parts[1], old[1])
        self.assertEqual(parts[2][4:], old[2][4:])
        self.assertEqual(parts[3], old[3])
        self.assertEqual(lz4(parts[0], True), changed)
        self.assertEqual(header[:24], old_header[:24])
        self.assertEqual(header[28:], old_header[28:])
        self.assertEqual(result[-64:], bytes(64))

    def test_multiple_fragments_are_rejected_before_editing(self):
        original = bytearray(self.vendor_image())
        struct.pack_into("<I", original, 2116, 2)
        with self.assertRaisesRegex(ValueError, "one Pixel"):
            vendor_parts(original)

    def test_boot_replaces_kernel_preserves_ramdisk_and_drops_signatures(self):
        for compressed in (False, True):
            with self.subTest(compressed=compressed):
                old_kernel = b"MZ" + bytes(54) + b"ARM\x64" + b"old" * 2300
                kernel = lz4(old_kernel) if compressed else old_kernel
                ramdisk = b"preserve this init ramdisk"
                header = bytearray(1584)
                header[:8] = b"ANDROID!"
                struct.pack_into("<II", header, 8, len(kernel), len(ramdisk))
                struct.pack_into("<I", header, 20, 1584)
                struct.pack_into("<I", header, 40, 4)
                struct.pack_into("<I", header, 1580, 4096)
                original = padded(header, 4096) + padded(kernel, 4096) + padded(ramdisk, 4096)
                original += b"obsolete signature" + bytes(65536 - len(original) - 18)
                new_kernel = b"MZ" + bytes(54) + b"ARM\x64" + b"new" * 2500
                result = make_boot(original, new_kernel)
                size = struct.unpack_from("<I", result, 8)[0]
                packed = result[4096:4096 + size]
                self.assertEqual(lz4(packed, True) if compressed else packed, new_kernel)
                start = 4096 + align(size, 4096)
                self.assertEqual(result[start:start + len(ramdisk)], ramdisk)
                self.assertEqual(struct.unpack_from("<I", result, 1580)[0], 0)
                self.assertNotIn(b"obsolete signature", result)
                self.assertEqual(len(result), len(original))


AVBTOOL = os.environ.get("AVBTOOL") or shutil.which("avbtool")


@unittest.skipUnless(AVBTOOL and Path(AVBTOOL).is_file() and shutil.which("lz4"), "avbtool and lz4 required")
class AvbMetadataTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        original = BootImageTests.vendor_image()
        header, page, parts = vendor_parts(original)
        size = align(len(header), page) + sum(align(len(x), page) for x in parts)
        path = self.root / "vendor_kernel_boot.img"
        path.write_bytes(original[:size])
        self.tool = Path(AVBTOOL)
        subprocess.run([str(self.tool), "add_hash_footer", "--image", str(path),
                        "--partition_name", "vendor_kernel_boot", "--partition_size", str(1024 * 1024),
                        "--algorithm", "NONE", "--rollback_index", "1700000000",
                        "--prop", "com.android.build.vendor_kernel_boot.fingerprint:test/device:17/build:user",
                        "--salt", "aa" * 32], check=True, capture_output=True)
        self.original = path.read_bytes()

    def test_footer_refresh_preserves_metadata_and_verifies_new_hash(self):
        entries = read_cpio(lz4(vendor_parts(self.original)[2][0], True))
        changed = write_cpio(patch_ramdisk(entries, b"replacement module"))
        patched = make_vendor_boot(self.original, changed)
        result = refresh_avb_footer(patched, self.original, "vendor_kernel_boot", self.tool)
        self.assertEqual(len(result), len(self.original))
        self.assertNotEqual(result, self.original)
        metadata = avb_metadata(result, "vendor_kernel_boot")
        self.assertEqual(metadata, avb_metadata(self.original, "vendor_kernel_boot"))
        self.assertEqual(metadata["rollback"], 1700000000)
        self.assertEqual(metadata["props"], ["com.android.build.vendor_kernel_boot.fingerprint:test/device:17/build:user"])

    def test_wrong_partition_backup_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "does not match"):
            avb_metadata(self.original, "boot")


if __name__ == "__main__":
    unittest.main()
