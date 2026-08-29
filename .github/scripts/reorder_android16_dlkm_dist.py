from pathlib import Path
p=Path('.github/workflows/build.yml')
s=p.read_text()
block='''      # ==================== 构建匹配的 GKI system_dlkm 分发 ====================
      - name: 构建匹配的 GKI system_dlkm 分发
        if: inputs.build_matching_system_dlkm && inputs.kernel_version != '5.10' && inputs.kernel_version != '5.15'
        shell: bash
        run: |
          set -euo pipefail
          cd "$KERNEL_ROOT"

          # Restore the untouched AOSP distribution configuration so GKI modules
          # and system_dlkm are generated instead of shipping Image alone.
          cp ./common/build.config.gki.aarch64.orig ./common/build.config.gki.aarch64

          FRAG="common/arch/arm64/configs/ksu.fragment"
          FRAG_FLAG=""
          [ -s "$FRAG" ] && FRAG_FLAG="--defconfig_fragment=//common:arch/arm64/configs/ksu.fragment"
          LTO_FLAG="--lto=thin"
          [ "${{ inputs.kernel_version }}" = "6.12" ] && LTO_FLAG="--lto=none"

          DIST_DIR="$GITHUB_WORKSPACE/gki-dist/$CONFIG"
          mkdir -p "$DIST_DIR"
          tools/bazel run --disk_cache=/home/runner/.cache/bazel --config=fast \\
            $LTO_FLAG $FRAG_FLAG //common:kernel_aarch64_dist -- --destdir="$DIST_DIR"

          echo "=== matching GKI dist ==="
          find "$DIST_DIR" -maxdepth 1 -type f -printf '%f\\n' | sort

'''
if s.count(block)!=1: raise SystemExit(f'expected one dist block, found {s.count(block)}')
s=s.replace(block,'',1)
anchor='''          cp "$SRC_DIR/Image" ./

      # ==================== 激活 Bypass 构建 ===================='''
if s.count(anchor)!=1: raise SystemExit(f'prepare anchor count {s.count(anchor)}')
s=s.replace(anchor,'''          cp "$SRC_DIR/Image" ./

'''+block+'''      # ==================== 激活 Bypass 构建 ====================''',1)
p.write_text(s)
print('reordered matching system_dlkm build after Image staging')
