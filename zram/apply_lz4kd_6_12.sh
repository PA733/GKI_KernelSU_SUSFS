#!/bin/bash
# Run from kernel common/ with the SukiSU_patch checkout as the argument.
set -euo pipefail

SUPPORT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/6.12"
ALGORITHM_DIR="${1:?Usage: apply_lz4kd_6_12.sh /path/to/SukiSU_patch}/other/zram/lz4k"
PATCH_FILE="$SUPPORT_DIR/lz4kd.patch"

grep -q '^VERSION = 6$' Makefile
grep -q '^PATCHLEVEL = 12$' Makefile
grep -q 'struct zcomp_ops' drivers/block/zram/zcomp.h

for file in include/linux/lz4kd.h lib/lz4kd/Makefile \
  lib/lz4kd/lz4kd_encode.c lib/lz4kd/lz4kd_decode.c \
  lib/lz4kd/lz4kd_encode_delta.c lib/lz4kd/lz4kd_decode_delta.c \
  lib/lz4kd/lz4kd_private.h lib/lz4kd/lz4kd_encode_private.h; do
  if [ ! -f "$ALGORITHM_DIR/$file" ]; then
    echo "::error::Missing LZ4KD source: $ALGORITHM_DIR/$file"
    exit 1
  fi
done

# Validate the entire integration before changing the kernel tree.
if patch --batch --forward --dry-run -p1 < "$PATCH_FILE"; then
  patch --batch --forward -p1 < "$PATCH_FILE"
elif patch --batch --reverse --dry-run -p1 < "$PATCH_FILE"; then
  echo "LZ4KD 6.12 integration already applied"
else
  echo "::error::LZ4KD patch does not match this 6.12 ZRAM layout"
  exit 1
fi

cp "$ALGORITHM_DIR/include/linux/lz4kd.h" include/linux/
mkdir -p lib/lz4kd
cp "$ALGORITHM_DIR/lib/lz4kd/"* lib/lz4kd/
cp "$SUPPORT_DIR/backend_lz4kd.c" "$SUPPORT_DIR/backend_lz4kd.h" drivers/block/zram/
echo "Installed native ZRAM LZ4KD backend for 6.12 (4 KiB pages)"
