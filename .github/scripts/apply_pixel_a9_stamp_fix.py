#!/usr/bin/env python3
from pathlib import Path

p = Path('.github/workflows/build.yml')
s = p.read_text()

# Pixel A9 stock identity requires Kleaf stamping. Without it, Kleaf deliberately
# emits only 6.12.69-android16-6-4k even when BUILD_NUMBER is set.
needle = '''              NOTRIM_FLAG=""\n              if [ "${{ inputs.pixel_a9_compat }}" = "true" ] && [ "${{ inputs.image_only_compat }}" = "true" ]; then\n                echo "::error::Pixel A9 production compatibility cannot be combined with --notrim"\n                exit 1\n              elif [ "${{ inputs.image_only_compat }}" = "true" ]; then\n                echo "启用 Image-only system_dlkm 兼容模式: --notrim"\n                NOTRIM_FLAG="--notrim"\n              fi\n              tools/bazel build --disk_cache=/home/runner/.cache/bazel --config=fast $LTO_FLAG $NOTRIM_FLAG $FRAG_FLAG //common:kernel_aarch64_dist || exit 1\n'''
replacement = '''              NOTRIM_FLAG=""\n              if [ "${{ inputs.pixel_a9_compat }}" = "true" ] && [ "${{ inputs.image_only_compat }}" = "true" ]; then\n                echo "::error::Pixel A9 production compatibility cannot be combined with --notrim"\n                exit 1\n              elif [ "${{ inputs.image_only_compat }}" = "true" ]; then\n                echo "启用 Image-only system_dlkm 兼容模式: --notrim"\n                NOTRIM_FLAG="--notrim"\n              fi\n              STAMP_FLAG=""\n              if [ "${{ inputs.pixel_a9_compat }}" = "true" ]; then\n                echo "Pixel A9 mode: enabling Kleaf SCM stamping for g5c5f2fea42dd-ab15835541"\n                STAMP_FLAG="--config=stamp"\n              fi\n              tools/bazel build --disk_cache=/home/runner/.cache/bazel --config=fast $STAMP_FLAG $LTO_FLAG $NOTRIM_FLAG $FRAG_FLAG //common:kernel_aarch64_dist || exit 1\n'''
count = s.count(needle)
if count != 2:
    raise SystemExit(f'expected normal+bypass build blocks, found {count}')
s = s.replace(needle, replacement)

# The optional matching dist must use the same stamping mode too, otherwise its
# modules would carry a different vermagic than the packaged Pixel Image.
needle2 = '''          DIST_DIR="$GITHUB_WORKSPACE/gki-dist/$CONFIG"\n          mkdir -p "$DIST_DIR"\n          tools/bazel run --disk_cache=/home/runner/.cache/bazel --config=fast \\\n            $LTO_FLAG $FRAG_FLAG //common:kernel_aarch64_dist -- --destdir="$DIST_DIR"\n'''
replacement2 = '''          STAMP_FLAG=""\n          [ "${{ inputs.pixel_a9_compat }}" = "true" ] && STAMP_FLAG="--config=stamp"\n\n          DIST_DIR="$GITHUB_WORKSPACE/gki-dist/$CONFIG"\n          mkdir -p "$DIST_DIR"\n          tools/bazel run --disk_cache=/home/runner/.cache/bazel --config=fast \\\n            $STAMP_FLAG $LTO_FLAG $FRAG_FLAG //common:kernel_aarch64_dist -- --destdir="$DIST_DIR"\n'''
count2 = s.count(needle2)
if count2 != 1:
    raise SystemExit(f'expected matching dist block once, found {count2}')
s = s.replace(needle2, replacement2)

p.write_text(s)
print('Pixel A9 Kleaf stamp fix applied')
