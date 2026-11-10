#!/usr/bin/env python3
"""Check actual patched Kconfig linkage before spending time on a kernel build.

Requires kconfiglib==14.1.0. Usage: check_lz4kd_linkage.py /path/to/common
"""

import argparse
from pathlib import Path
import re
import tempfile

import kconfiglib


def check(kernel):
    lib = (kernel / "lib/Kconfig").read_text()
    codecs = []
    for name in ("LZ4KD_COMPRESS", "LZ4KD_DECOMPRESS"):
        block = re.search(r"^config " + name + r"\n.*?(?=^config |\Z)", lib, re.M | re.S)
        if block is None:
            raise ValueError(f"Missing {name} Kconfig definition")
        codecs.append(block[0])

    # Supply only the parent capabilities; retain the real driver Kconfig and
    # codec symbol definitions, including their tristate/select semantics.
    model = 'config MODULES\n\tbool "modules"\n\toption modules\n\tdefault y\n'
    for name in ("BLOCK", "SYSFS", "MMU", "ARM64_4K_PAGES"):
        model += f"config {name}\n\tbool\n\tdefault y\n"
    model += "config ZSMALLOC\n\ttristate\n"
    model += "\n".join(codecs) + "\n" + (kernel / "drivers/block/zram/Kconfig").read_text()

    with tempfile.TemporaryDirectory(prefix="lz4kd-kconfig-") as temp:
        path = Path(temp) / "Kconfig"
        path.write_text(model)
        for mode in ("m", "y"):
            config = kconfiglib.Kconfig(str(path), warn=False)
            config.syms["ZRAM"].set_value(mode)
            config.syms["ZRAM_BACKEND_LZ4KD"].set_value("y")
            config.syms["ZRAM_DEF_COMP_LZ4KD"].set_value("y")
            expected = {"ZRAM": mode, "ZSMALLOC": mode,
                        "ZRAM_BACKEND_LZ4KD": "y", "LZ4KD_COMPRESS": "y",
                        "LZ4KD_DECOMPRESS": "y", "ZRAM_DEF_COMP": "lz4kd"}
            for name, value in expected.items():
                actual = config.syms[name].str_value
                if actual != value:
                    raise ValueError(f"ZRAM={mode}: expected {name}={value}, got {actual}")
            print(f"PASS: ZRAM/ZSMALLOC={mode}, LZ4KD codecs=y, default=lz4kd")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kernel", type=Path)
    check(parser.parse_args().kernel)
