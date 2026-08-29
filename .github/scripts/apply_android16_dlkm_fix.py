from pathlib import Path

p = Path('.github/workflows/build.yml')
s = p.read_text()

def one(old, new):
    global s
    n=s.count(old)
    if n != 1:
        raise SystemExit(f'expected 1 occurrence, found {n}: {old[:120]!r}')
    s=s.replace(old,new,1)

def alln(old,new,n):
    global s
    c=s.count(old)
    if c != n:
        raise SystemExit(f'expected {n} occurrences, found {c}: {old[:120]!r}')
    s=s.replace(old,new)

# workflow_call inputs
one('''      build_bypass:\n        required: false\n        type: boolean\n        default: false\n\njobs:''', '''      build_bypass:\n        required: false\n        type: boolean\n        default: false\n      image_only_compat:\n        description: "Use Kleaf --notrim so an Image-only build can load the device stock system_dlkm modules"\n        required: false\n        type: boolean\n        default: false\n      build_matching_system_dlkm:\n        description: "Also export a matching GKI distribution including system_dlkm artifacts"\n        required: false\n        type: boolean\n        default: false\n\njobs:''')

# summary
one('''          echo "Bypass 构建   : ${{ inputs.build_bypass }}"\n          echo "Stock Config  : ${{ inputs.clean_build && '禁用' || '自动检测 config/stock_defconfig' }}"''', '''          echo "Bypass 构建   : ${{ inputs.build_bypass }}"\n          echo "Image-only兼容: ${{ inputs.image_only_compat }}"\n          echo "匹配 system_dlkm: ${{ inputs.build_matching_system_dlkm }}"\n          echo "Stock Config  : ${{ inputs.clean_build && '禁用' || '自动检测 config/stock_defconfig' }}"''')

# Backup the pristine GKI build config before build-time sed mutations.
one('''      # ==================== 备份原始 defconfig ====================\n      # 用于后续生成 defconfig fragment（bazel trim 兼容）\n      - name: 备份原始 defconfig\n        run: cp "$DEFCONFIG" "$DEFCONFIG.orig"''', '''      # ==================== 备份原始构建配置 ====================\n      # defconfig 用于 fragment；build.config.gki.aarch64 用于后续构建匹配 system_dlkm。\n      - name: 备份原始构建配置\n        run: |\n          cp "$DEFCONFIG" "$DEFCONFIG.orig"\n          cp "$KERNEL_ROOT/common/build.config.gki.aarch64" "$KERNEL_ROOT/common/build.config.gki.aarch64.orig"''')

# Inject NOTRIM_FLAG into normal and bypass build commands.
needle='''              LTO_FLAG="--lto=thin"\n              if [ "${{ inputs.kernel_version }}" = "6.12" ]; then\n                LTO_FLAG="--lto=none"\n              fi\n              tools/bazel build --disk_cache=/home/runner/.cache/bazel --config=fast $LTO_FLAG $FRAG_FLAG //common:kernel_aarch64_dist || exit 1'''
replacement='''              LTO_FLAG="--lto=thin"\n              if [ "${{ inputs.kernel_version }}" = "6.12" ]; then\n                LTO_FLAG="--lto=none"\n              fi\n              NOTRIM_FLAG=""\n              if [ "${{ inputs.image_only_compat }}" = "true" ]; then\n                echo "启用 Image-only system_dlkm 兼容模式: --notrim"\n                NOTRIM_FLAG="--notrim"\n              fi\n              tools/bazel build --disk_cache=/home/runner/.cache/bazel --config=fast $LTO_FLAG $NOTRIM_FLAG $FRAG_FLAG //common:kernel_aarch64_dist || exit 1'''
alln(needle,replacement,2)

# Add post-build Image config validation after KMI validation, before preparing Image.
one('''      # ==================== 准备内核 Image ====================\n      # 复制编译产物到工作目录，供 AnyKernel3 打包使用''', '''      # ==================== 验证 Image-only 模块兼容配置 ====================\n      - name: 验证 Image-only 模块兼容配置\n        if: inputs.android_version == 'android16' && inputs.kernel_version == '6.12'\n        shell: bash\n        run: |\n          set -euo pipefail\n          IMAGE="$KERNEL_ROOT/bazel-bin/common/kernel_aarch64/Image"\n          FINAL_CONFIG="$RUNNER_TEMP/${CONFIG}-final.config"\n          "$KERNEL_ROOT/common/scripts/extract-ikconfig" "$IMAGE" > "$FINAL_CONFIG"\n\n          echo "=== Module compatibility configuration ==="\n          grep -E 'CONFIG_(MODULE_SIG_PROTECT|MODULE_SIG_PROTECT_LIST|TRIM_UNUSED_KSYMS|MODVERSIONS|GENDWARFKSYMS)' "$FINAL_CONFIG" || true\n\n          grep -q '^CONFIG_MODVERSIONS=y$' "$FINAL_CONFIG" || {\n            echo "::error::CONFIG_MODVERSIONS must remain enabled"; exit 1;\n          }\n\n          if [ "${{ inputs.image_only_compat }}" = "true" ]; then\n            if grep -q '^CONFIG_MODULE_SIG_PROTECT=y$' "$FINAL_CONFIG"; then\n              echo "::error::--notrim did not disable CONFIG_MODULE_SIG_PROTECT"\n              exit 1\n            fi\n            if grep -q '^CONFIG_TRIM_UNUSED_KSYMS=y$' "$FINAL_CONFIG"; then\n              echo "::error::--notrim did not disable CONFIG_TRIM_UNUSED_KSYMS"\n              exit 1\n            fi\n          fi\n\n      # ==================== 构建匹配的 GKI system_dlkm 分发 ====================\n      - name: 构建匹配的 GKI system_dlkm 分发\n        if: inputs.build_matching_system_dlkm && inputs.kernel_version != '5.10' && inputs.kernel_version != '5.15'\n        shell: bash\n        run: |\n          set -euo pipefail\n          cd "$KERNEL_ROOT"\n\n          # Restore the untouched AOSP distribution configuration so GKI modules\n          # and system_dlkm are generated instead of shipping Image alone.\n          cp ./common/build.config.gki.aarch64.orig ./common/build.config.gki.aarch64\n\n          FRAG="common/arch/arm64/configs/ksu.fragment"\n          FRAG_FLAG=""\n          [ -s "$FRAG" ] && FRAG_FLAG="--defconfig_fragment=//common:arch/arm64/configs/ksu.fragment"\n          LTO_FLAG="--lto=thin"\n          [ "${{ inputs.kernel_version }}" = "6.12" ] && LTO_FLAG="--lto=none"\n\n          DIST_DIR="$GITHUB_WORKSPACE/gki-dist/$CONFIG"\n          mkdir -p "$DIST_DIR"\n          tools/bazel run --disk_cache=/home/runner/.cache/bazel --config=fast \\\n            $LTO_FLAG $FRAG_FLAG //common:kernel_aarch64_dist -- --destdir="$DIST_DIR"\n\n          echo "=== matching GKI dist ==="\n          find "$DIST_DIR" -maxdepth 1 -type f -printf '%f\\n' | sort\n\n      # ==================== 准备内核 Image ====================\n      # 复制编译产物到工作目录，供 AnyKernel3 打包使用''')

# Fail the build if any patch reject exists. Keep collection for debugging first.
one('''          if [ "$REJ_COUNT" -gt 0 ]; then\n            for REJ in "${REJS[@]}"; do\n              REL="${REJ#"$KERNEL_ROOT"/}"\n              DEST="$REJECTS_DIR/$REL"\n              mkdir -p "$(dirname "$DEST")"\n              cp "$REJ" "$DEST"\n\n              ORIG="${REJ%.rej}"\n              if [ -f "$ORIG" ]; then\n                cp "$ORIG" "${DEST%.rej}"\n              fi\n              echo "$REL" >> "$REJECTS_DIR/index.txt"\n            done\n          fi''', '''          if [ "$REJ_COUNT" -gt 0 ]; then\n            for REJ in "${REJS[@]}"; do\n              REL="${REJ#"$KERNEL_ROOT"/}"\n              DEST="$REJECTS_DIR/$REL"\n              mkdir -p "$(dirname "$DEST")"\n              cp "$REJ" "$DEST"\n\n              ORIG="${REJ%.rej}"\n              if [ -f "$ORIG" ]; then\n                cp "$ORIG" "${DEST%.rej}"\n              fi\n              echo "$REL" >> "$REJECTS_DIR/index.txt"\n            done\n            echo "::error::检测到 $REJ_COUNT 个未解决的补丁冲突，拒绝发布可能部分应用的内核"\n            printf '  %s\\n' "${REJS[@]}"\n          fi''')

# Add a gate after reject collection, before upload; always() lets collection finish first.
one('''      # ==================== 上传构建产物 ====================\n      # AnyKernel3 包本身已是 zip，upload-artifact 会再套一层 zip 容器''', '''      - name: 拒绝包含补丁冲突的构建\n        if: always() && env.REJ_COUNT > 0\n        run: |\n          echo "::error::存在未解决的 .rej 补丁冲突；构建产物不会作为有效内核发布。"\n          exit 1\n\n      # ==================== 上传匹配 GKI 分发文件 ====================\n      - name: 上传匹配 GKI 分发文件\n        if: inputs.build_matching_system_dlkm\n        uses: actions/upload-artifact@v6\n        with:\n          name: ${{ env.BUILD_VARIANT }}_kernel-${{ env.CONFIG }}-gki-dist\n          path: gki-dist/${{ env.CONFIG }}/\n          if-no-files-found: error\n          compression-level: 0\n\n      # ==================== 上传构建产物 ====================\n      # AnyKernel3 包本身已是 zip，upload-artifact 会再套一层 zip 容器''')

p.write_text(s)
print('patched build.yml')
