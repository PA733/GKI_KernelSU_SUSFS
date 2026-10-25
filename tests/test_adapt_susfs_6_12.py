"""Regression coverage for SUSFS 3ff9fd3 on the Pixel A9 6.12.69 layout.

The patch fixture contains the relevant upstream hunks from
https://gitlab.com/simonpunk/susfs4ksu/-/commit/3ff9fd3fac5bb01b71b2cfe96c612d469710fb74
Run with: python3 -m unittest discover -s tests -v
"""

import difflib
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.adapt_susfs_6_12 import adapt_exec, adapt_patch, adapt_rollup


PATCH = (Path(__file__).parent / "fixtures/susfs-6.12-layout.patch").read_text()
EXEC = """#include <linux/rseq.h>
#include <linux/ksm.h>
#include <linux/dma-buf.h>

#include <linux/uaccess.h>
#include <asm/mmu_context.h>
"""
# Keep the real helper prologue and representative legacy stats calls. Other
# kernel details are unnecessary for testing patch application and preservation.
MMU = """static void smap_gather_stats(struct vm_area_struct *vma,
		struct mem_size_stats *mss, unsigned long start)
{
	const struct mm_walk_ops *ops = &smaps_walk_ops;
	unsigned long end = VMA_PAD_START(vma);

	/* Invalid start */
	if (start >= end)
		return;
	walk_page_range(vma->vm_mm, start, end, ops, mss);
}

static int show_smap(struct seq_file *m, void *v)
{
	struct vm_area_struct *vma = v;
	struct mem_size_stats mss = {};

	if (!vma_data_pages(vma))
		goto show_pad;

	smap_gather_stats(vma, &mss, 0);
show_pad:
	return 0;
}

static int show_smaps_rollup(struct seq_file *m, void *v)
{
	smap_gather_stats(vma, &mss, 0);
	smap_gather_stats(vma, &mss, last_vma_end);
	last_vma_end = vma->vm_end;
}
"""


class AdaptSusfsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "fs/proc").mkdir(parents=True)
        (self.root / "fs/exec.c").write_text(EXEC)
        (self.root / "fs/proc/task_mmu.c").write_text(MMU)

    def apply(self, patch):
        result = subprocess.run(
            ["patch", "--batch", "--forward", "--fuzz=0", "-p1"],
            input=patch, cwd=self.root, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(list(self.root.rglob("*.rej")), [])

    def test_pixel_patch_applies_and_filters_common_stats_helper(self):
        adapted = adapt_patch(PATCH, self.root)
        self.apply(adapted)
        exec_source = (self.root / "fs/exec.c").read_text()
        self.assertEqual(exec_source.count("#include <linux/susfs_def.h>"), 1)
        self.assertIn("#include <linux/dma-buf.h>", exec_source)
        mmu = (self.root / "fs/proc/task_mmu.c").read_text()
        helper = mmu.split("static int show_smap(")[0]
        self.assertIn("SUSFS_IS_INODE_SUS_MAP(file_inode(vma->vm_file))", helper)
        self.assertLess(helper.index("SUSFS_IS_INODE_SUS_MAP"), helper.index("walk_page_range"))
        # Both the full and partial stats paths retain stock iteration/state.
        self.assertEqual(mmu.split("static int show_smaps_rollup")[1],
                         MMU.split("static int show_smaps_rollup")[1])

    def test_patch_preparation_is_idempotent_and_does_not_edit_source(self):
        adapted = adapt_patch(PATCH, self.root)
        self.assertEqual(adapt_patch(adapted, self.root), adapted)
        self.assertEqual((self.root / "fs/exec.c").read_text(), EXEC)
        self.assertEqual((self.root / "fs/proc/task_mmu.c").read_text(), MMU)

    def test_exec_context_supports_old_and_new_patch_and_source(self):
        for source_has_dma in (False, True):
            for patch_has_dma in (False, True):
                with self.subTest(source_has_dma=source_has_dma, patch_has_dma=patch_has_dma):
                    source = EXEC if source_has_dma else EXEC.replace("#include <linux/dma-buf.h>\n", "")
                    before = EXEC if patch_has_dma else EXEC.replace("#include <linux/dma-buf.h>\n", "")
                    anchor = "#include <linux/dma-buf.h>\n" if patch_has_dma else "#include <linux/ksm.h>\n"
                    after = before.replace(anchor, anchor + "#ifdef CONFIG_KSU_SUSFS\n#include <linux/susfs_def.h>\n#endif\n")
                    patch = "".join(difflib.unified_diff(
                        before.splitlines(keepends=True), after.splitlines(keepends=True),
                        fromfile="a/fs/exec.c", tofile="b/fs/exec.c",
                    ))
                    (self.root / "fs/exec.c").write_text(source)
                    self.apply(adapt_exec(patch, source))
                    result = (self.root / "fs/exec.c").read_text()
                    self.assertEqual(result.count("#include <linux/susfs_def.h>"), 1)
                    self.assertEqual("#include <linux/dma-buf.h>" in result, source_has_dma)

    def test_modern_layout_keeps_upstream_rollup_hunks(self):
        modern = MMU.replace("smap_gather_stats(vma, &mss, 0);", "smap_gather_stats(priv, vma, &mss);")
        self.assertEqual(adapt_rollup(PATCH, modern), PATCH)

    def test_legacy_patch_is_not_rewritten(self):
        legacy = PATCH.replace("smap_gather_stats(priv, vma, &mss);", "smap_gather_stats(vma, &mss, 0);")
        self.assertEqual(adapt_rollup(legacy, MMU), legacy)

    def test_unknown_upstream_changes_fail_instead_of_losing_logic(self):
        changed = PATCH.replace("+\t\t\tgoto bypass_orig_flow;", "+\t\t\tnew_upstream_behavior();")
        with self.assertRaisesRegex(ValueError, "refusing to drop hunks"):
            adapt_patch(changed, self.root)

    def test_unknown_legacy_source_fails(self):
        (self.root / "fs/proc/task_mmu.c").write_text(MMU.replace("VMA_PAD_START(vma)", "vma->vm_end"))
        with self.assertRaisesRegex(ValueError, "Unrecognized legacy"):
            adapt_patch(PATCH, self.root)


if __name__ == "__main__":
    unittest.main()
