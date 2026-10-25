#!/usr/bin/env python3
"""Adapt the local SUSFS patch to older Android 16 / 6.12 source layouts."""

import argparse
import difflib
from pathlib import Path
import re


HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)\n")
ROLLUP_ADDITIONS = [
    "#ifdef CONFIG_KSU_SUSFS_SUS_MAP\n"
    "\t\tif (vma->vm_file && SUSFS_IS_INODE_SUS_MAP(file_inode(vma->vm_file)))\n"
    "\t\t\tgoto bypass_orig_flow;\n"
    "#endif // #ifdef CONFIG_KSU_SUSFS_SUS_MAP\n\n",
    "#ifdef CONFIG_KSU_SUSFS_SUS_MAP\n"
    "bypass_orig_flow:\n"
    "#endif // #ifdef CONFIG_KSU_SUSFS_SUS_MAP\n",
]


def recount_hunk(hunk: str) -> str:
    header = HUNK_HEADER.match(hunk)
    if header is None:
        raise ValueError("Invalid SUSFS hunk header")
    lines = hunk[header.end():].splitlines(keepends=True)
    old_count = sum(line.startswith((" ", "-")) for line in lines)
    new_count = sum(line.startswith((" ", "+")) for line in lines)
    return (f"@@ -{header[1]},{old_count} +{header[2]},{new_count} @@{header[3]}\n"
            + "".join(lines))


def adapt_exec(section: str, source: str) -> str:
    """Match dma-buf.h context without temporarily deleting a kernel include."""
    hunks = re.split(r"(?=^@@ )", section, flags=re.MULTILINE)
    for i, hunk in enumerate(hunks[1:], 1):
        if "+#include <linux/susfs_def.h>\n" not in hunk:
            continue
        context = " #include <linux/dma-buf.h>\n"
        source_has_header = "#include <linux/dma-buf.h>\n" in source
        if source_has_header == (context in hunk):
            continue
        if source_has_header:
            anchor = " #include <linux/ksm.h>\n"
            if hunk.count(anchor) != 1:
                raise ValueError("Unrecognized exec.c SUSFS include context")
            hunk = hunk.replace(anchor, anchor + context, 1)
        else:
            hunk = hunk.replace(context, "", 1)
        hunks[i] = recount_hunk(hunk)
    return "".join(hunks)


def adapt_rollup(section: str, source: str) -> str:
    # Newer SUSFS targets the per-VMA-lock implementation. Older kernels have
    # three stats calls, including two paths after dropping/reacquiring mmap_lock.
    if "smap_gather_stats(vma, &mss, 0);" not in source:
        return section
    if "smap_gather_stats(priv, vma, &mss);" not in section:
        return section

    hunks = re.split(r"(?=^@@ )", section, flags=re.MULTILINE)
    rollup = [hunk for hunk in hunks[1:]
              if "static int show_smaps_rollup(" in hunk.splitlines()[0]]
    additions = ["".join(line[1:] for line in hunk.splitlines(keepends=True)[1:]
                         if line.startswith("+")) for hunk in rollup]
    if additions != ROLLUP_ADDITIONS or any(
        line.startswith("-") for hunk in rollup for line in hunk.splitlines()[1:]
    ):
        raise ValueError("Unrecognized SUSFS smaps_rollup changes; refusing to drop hunks")

    anchor = (
        "static void smap_gather_stats(struct vm_area_struct *vma,\n"
        "\t\tstruct mem_size_stats *mss, unsigned long start)\n"
        "{\n"
        "\tconst struct mm_walk_ops *ops = &smaps_walk_ops;\n"
        "\tunsigned long end = VMA_PAD_START(vma);\n\n"
    )
    if source.count(anchor) != 1:
        raise ValueError("Unrecognized legacy smap_gather_stats implementation")
    # Filter in the common helper so ordinary, next-VMA and partial-VMA walks
    # all skip hidden mappings, while retaining last_vma_end and lock handling.
    guard = (
        "#ifdef CONFIG_KSU_SUSFS_SUS_MAP\n"
        "\tif (vma->vm_file && SUSFS_IS_INODE_SUS_MAP(file_inode(vma->vm_file)))\n"
        "\t\treturn;\n"
        "#endif // #ifdef CONFIG_KSU_SUSFS_SUS_MAP\n\n"
    )
    updated = source.replace(anchor, anchor + guard, 1)
    helper_hunk = "".join(list(difflib.unified_diff(
        source.splitlines(keepends=True), updated.splitlines(keepends=True)
    ))[2:])
    remaining = [hunk for hunk in hunks if hunk not in rollup]
    insertion = [i for i, hunk in enumerate(remaining[1:], 1)
                 if "static int show_smap(" in hunk.splitlines()[0]]
    if len(insertion) != 1:
        raise ValueError("Cannot locate SUSFS show_smap hunk")
    remaining.insert(insertion[0], helper_hunk)
    return "".join(remaining)


def adapt_patch(patch: str, kernel: Path) -> str:
    sections = re.split(r"(?=^diff --git )", patch, flags=re.MULTILINE)
    for i, section in enumerate(sections):
        if section.startswith("diff --git a/fs/exec.c b/fs/exec.c\n"):
            sections[i] = adapt_exec(section, (kernel / "fs/exec.c").read_text())
        elif section.startswith("diff --git a/fs/proc/task_mmu.c b/fs/proc/task_mmu.c\n"):
            sections[i] = adapt_rollup(section, (kernel / "fs/proc/task_mmu.c").read_text())
    return "".join(sections)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kernel", type=Path, help="kernel common directory")
    parser.add_argument("patch", type=Path, help="local SUSFS main patch copy")
    args = parser.parse_args()
    original = args.patch.read_text()
    try:
        adapted = adapt_patch(original, args.kernel)
    except ValueError as error:
        parser.exit(1, f"SUSFS 6.12 adaptation failed: {error}\n")
    if adapted != original:
        args.patch.write_text(adapted)
        print("Adapted SUSFS 6.12 patch to the target exec/smaps layout")


if __name__ == "__main__":
    main()
