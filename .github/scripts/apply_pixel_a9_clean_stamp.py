#!/usr/bin/env python3
from pathlib import Path

p = Path('.github/workflows/build.yml')
s = p.read_text()
needle = '''              STAMP_FLAG=""\n              if [ "${{ inputs.pixel_a9_compat }}" = "true" ]; then\n                echo "Pixel A9 mode: enabling Kleaf SCM stamping for g5c5f2fea42dd-ab15835541"\n                STAMP_FLAG="--config=stamp"\n              fi\n              tools/bazel build --disk_cache=/home/runner/.cache/bazel --config=fast $STAMP_FLAG $LTO_FLAG $NOTRIM_FLAG $FRAG_FLAG //common:kernel_aarch64_dist || exit 1\n'''
replacement = '''              STAMP_FLAG=""\n              if [ "${{ inputs.pixel_a9_compat }}" = "true" ]; then\n                echo "Pixel A9 mode: enabling Kleaf SCM stamping for g5c5f2fea42dd-ab15835541"\n                STAMP_FLAG="--config=stamp"\n\n                # Kleaf's workspace-status logic marks the SCM version dirty when\n                # tracked files in common are intentionally modified by this build.\n                # Hide only those tracked modifications from Git status while\n                # preserving both the pinned HEAD and the file contents Bazel builds.\n                mapfile -d '' PIXEL_DIRTY_TRACKED < <(git -C common diff --name-only -z)\n                if [ "${#PIXEL_DIRTY_TRACKED[@]}" -gt 0 ]; then\n                  printf 'Pixel A9: suppressing intentional dirty status for %d tracked common files\\n' "${#PIXEL_DIRTY_TRACKED[@]}"\n                  printf '%s\\0' "${PIXEL_DIRTY_TRACKED[@]}" | git -C common update-index --assume-unchanged -z --stdin\n                fi\n                if [ -n "$(git -C common status -uno --porcelain)" ]; then\n                  echo "::error::Pixel A9 common tree is still dirty after suppressing intentional tracked edits"\n                  git -C common status -uno --porcelain\n                  exit 1\n                fi\n              fi\n              tools/bazel build --disk_cache=/home/runner/.cache/bazel --config=fast $STAMP_FLAG $LTO_FLAG $NOTRIM_FLAG $FRAG_FLAG //common:kernel_aarch64_dist || exit 1\n'''
count = s.count(needle)
if count != 1:
    raise SystemExit(f'expected one normal stamped build block, found {count}')
s = s.replace(needle, replacement, 1)
p.write_text(s)
print('Pixel A9 clean SCM stamp fix applied')
