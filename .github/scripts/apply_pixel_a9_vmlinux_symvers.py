#!/usr/bin/env python3
from pathlib import Path

p = Path('.github/workflows/build.yml')
s = p.read_text()
old = '''          SYMVERS="$(find "$KERNEL_ROOT/bazel-bin" -type f -name Module.symvers -print -quit)"\n          if [ -z "$SYMVERS" ]; then\n            echo "::error::Could not locate Module.symvers for Pixel A9 ABI validation"\n            exit 1\n          fi\n'''
new = '''          SYMVERS="$KERNEL_ROOT/bazel-bin/common/kernel_aarch64/vmlinux.symvers"\n          if [ ! -f "$SYMVERS" ]; then\n            SYMVERS="$(find -L "$KERNEL_ROOT/bazel-bin" -type f \\\n              \( -name vmlinux.symvers -o -name Module.symvers \) -print -quit 2>/dev/null || true)"\n          fi\n          if [ -z "$SYMVERS" ] || [ ! -f "$SYMVERS" ]; then\n            echo "::error::Could not locate vmlinux.symvers/Module.symvers for Pixel A9 ABI validation"\n            echo "=== available symvers-like outputs ==="\n            find -L "$KERNEL_ROOT/bazel-bin/common" -maxdepth 4 -type f -iname '*symvers*' -print 2>/dev/null || true\n            exit 1\n          fi\n'''
count=s.count(old)
if count != 1:
    raise SystemExit(f'expected one Module.symvers validator block, found {count}')
s=s.replace(old,new,1)
p.write_text(s)
print('Pixel A9 validator now uses vmlinux.symvers')
