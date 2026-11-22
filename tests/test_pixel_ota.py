import datetime
import sys
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from pixel_ota import select_ota, snapshot_manifest


class FirmwareSelection(unittest.TestCase):
    def setUp(self):
        march = datetime.date(2026, 3, 1)
        april = datetime.date(2026, 4, 1)
        self.rows = {"GLOBAL.1": (march, None), "GLOBAL.2": (march, None),
                     "CANADA.3": (april, "Canada")}

    def test_latest_global_in_catalogue_order(self):
        self.assertEqual(select_ota(self.rows, ""), "GLOBAL.2")

    def test_select_specific_region_and_reject_unknown(self):
        self.assertEqual(select_ota(self.rows, "canada.3"), "CANADA.3")
        with self.assertRaises(ValueError):
            select_ota(self.rows, "UNKNOWN")


class SnapshotManifest(unittest.TestCase):
    def setUp(self):
        self.pins = {name: f"{i:040x}" for i, name in enumerate((
            "common", "build/kernel", "prebuilts/clang/host/linux-x86", "prebuilts/rust"), 1)}
        self.root = ET.Element("manifest")
        ET.SubElement(self.root, "superproject", name="kernel/superproject", revision="moving")
        for path in self.pins:
            project = ET.SubElement(self.root, "project", name=path)
            if path == "build/kernel":
                ET.SubElement(project, "linkfile", src="kleaf/bazel.sh", dest="tools/bazel")

    def test_pins_every_project_and_keeps_bazel_link(self):
        rendered = ET.fromstring(snapshot_manifest(ET.tostring(self.root), self.pins, self.pins["common"]))
        self.assertIsNone(rendered.find("superproject"))
        self.assertEqual(rendered.find("project/linkfile").get("dest"), "tools/bazel")
        self.assertEqual({p.get("name"): p.get("revision") for p in rendered}, self.pins)

    def test_rejects_wrong_kernel_or_incomplete_template(self):
        with self.assertRaises(ValueError):
            snapshot_manifest(ET.tostring(self.root), self.pins, "deadbeef")
        self.root.remove(self.root.find("project"))
        with self.assertRaises(ValueError):
            snapshot_manifest(ET.tostring(self.root), self.pins, self.pins["common"])
