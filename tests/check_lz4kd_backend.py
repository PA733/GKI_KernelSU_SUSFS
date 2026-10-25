#!/usr/bin/env python3
"""Compile/run the real LZ4KD codec and 6.12 backend under ASan/UBSan.

Usage: python3 tests/check_lz4kd_backend.py /path/to/SukiSU_patch/other/zram/lz4k
The algorithm source is the same dependency copied by the build workflow.
"""

import argparse
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("algorithm", type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    algorithm = args.algorithm.resolve()
    with tempfile.TemporaryDirectory(prefix="lz4kd-test-") as temp:
        root = Path(temp)
        (root / "linux").mkdir()
        (root / "compat.h").write_text("""
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#define likely(x) __builtin_expect(!!(x), 1)
#define unlikely(x) __builtin_expect(!!(x), 0)
#define EXPORT_SYMBOL(x)
#define MODULE_LICENSE(x)
#define MODULE_DESCRIPTION(x)
""")
        (root / "linux/kernel.h").write_text("#include <errno.h>\n#include <stddef.h>\n")
        (root / "linux/sizes.h").write_text("#define SZ_4K 4096\n")
        (root / "linux/vmalloc.h").write_text("""
#include <stdlib.h>
extern int fail_allocation;
static inline void *vmalloc(size_t size) { return fail_allocation ? NULL : malloc(size); }
static inline void vfree(void *ptr) { free(ptr); }
""")
        # The public zcomp backend contract from Android 16 / 6.12. No ZRAM
        # algorithm or backend implementation is replaced by these host shims.
        (root / "zcomp.h").write_text("""
#include <errno.h>
#include <limits.h>
#include <stddef.h>
#define ZCOMP_PARAM_NO_LEVEL INT_MIN
struct zcomp_params { void *dict; size_t dict_sz; int level; void *drv_data; };
struct zcomp_ctx { void *context; };
struct zcomp_req {
    const unsigned char *src;
    const size_t src_len;
    unsigned char *dst;
    size_t dst_len;
};
struct zcomp_ops {
    int (*compress)(struct zcomp_params *, struct zcomp_ctx *, struct zcomp_req *);
    int (*decompress)(struct zcomp_params *, struct zcomp_ctx *, struct zcomp_req *);
    int (*create_ctx)(struct zcomp_params *, struct zcomp_ctx *);
    void (*destroy_ctx)(struct zcomp_ctx *);
    int (*setup_params)(struct zcomp_params *);
    void (*release_params)(struct zcomp_params *);
    const char *name;
};
""")
        sources = [algorithm / "lib/lz4kd" / name for name in (
            "lz4kd_encode.c", "lz4kd_encode_delta.c",
            "lz4kd_decode.c", "lz4kd_decode_delta.c",
        )]
        binary = root / "test"
        command = [os.environ.get("CC", "clang"), "-std=gnu11", "-O2", "-g",
                   "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                   "-DLZ4K_DELTA", "-include", str(root / "compat.h"),
                   "-I", str(root), "-I", str(algorithm / "include"),
                   "-I", str(algorithm / "include/linux"),
                   "-I", str(repo / "zram/6.12"),
                   str(repo / "tests/lz4kd_backend_test.c"),
                   str(repo / "zram/6.12/backend_lz4kd.c"),
                   *map(str, sources), "-o", str(binary)]
        subprocess.run(command, check=True)
        subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    main()
