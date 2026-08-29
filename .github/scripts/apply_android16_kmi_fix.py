from pathlib import Path

path = Path('.github/workflows/build.yml')
text = path.read_text()


def replace_exact(old: str, new: str, count: int = 1) -> None:
    global text
    actual = text.count(old)
    if actual != count:
        raise SystemExit(
            f'expected {count} occurrence(s), found {actual}: {old[:120]!r}'
        )
    text = text.replace(old, new, count)


# 1. Detect the KMI generation from the AOSP source that repo sync actually
# checked out. The generation is part of the KMI identity and should not be
# hard-coded in CI.
sync_tail = '''          $REPO --trace sync -c -j$(nproc --all) --no-tags --fail-fast
          echo "REMOTE_BRANCH=$REMOTE_BRANCH" >> $GITHUB_ENV

      # ==================== 伪装 /proc/config.gz（自动检测） ===================='''

detect_kmi = '''          $REPO --trace sync -c -j$(nproc --all) --no-tags --fail-fast
          echo "REMOTE_BRANCH=$REMOTE_BRANCH" >> $GITHUB_ENV

      # ==================== 检测 AOSP KMI Generation ====================
      # KMI generation 是 ABI 身份的一部分，应由同步后的 AOSP 源码决定，不能硬编码。
      - name: 检测 AOSP KMI Generation
        if: inputs.android_version == 'android16' && inputs.kernel_version == '6.12'
        working-directory: ${{ env.KERNEL_ROOT }}
        shell: bash
        run: |
          set -euo pipefail

          CONSTANTS="common/build.config.constants"
          if [ ! -f "$CONSTANTS" ]; then
            echo "::error::找不到 $CONSTANTS，无法确定 KMI generation"
            exit 1
          fi

          AOSP_BRANCH="$(sed -n 's/^BRANCH=//p' "$CONSTANTS" | head -n1)"
          KMI_GENERATION="$(sed -n 's/^KMI_GENERATION=//p' "$CONSTANTS" | head -n1)"
          CLANG_VERSION="$(sed -n 's/^CLANG_VERSION=//p' "$CONSTANTS" | head -n1)"

          if [ -z "$AOSP_BRANCH" ] || [ -z "$KMI_GENERATION" ]; then
            echo "::error::无法从 $CONSTANTS 读取 BRANCH/KMI_GENERATION"
            exit 1
          fi

          ANDROID_RELEASE="${AOSP_BRANCH%%-*}"
          KMI_TAG="${ANDROID_RELEASE}-${KMI_GENERATION}"

          echo "AOSP branch    : $AOSP_BRANCH"
          echo "KMI generation : $KMI_GENERATION"
          echo "KMI tag        : $KMI_TAG"
          echo "Clang version  : ${CLANG_VERSION:-unknown}"

          {
            echo "AOSP_BRANCH=$AOSP_BRANCH"
            echo "KMI_GENERATION=$KMI_GENERATION"
            echo "KMI_TAG=$KMI_TAG"
          } >> "$GITHUB_ENV"

      # ==================== 伪装 /proc/config.gz（自动检测） ===================='''
replace_exact(sync_tail, detect_kmi)


# 2. Both version-name paths previously hard-coded android16-5. Consume the
# source-derived value instead.
replace_exact(
    '              "android16-6.12") KMI_TAG="android16-5" ;;',
    '              "android16-6.12") KMI_TAG="${KMI_TAG:?KMI_TAG was not detected from AOSP source}" ;;',
    count=2,
)


# 3. A clean build is our ABI compatibility reference. Do not remove
# protected-export/KMI strictness for that mode.
old_abi_block = '''          else
            # 配置 6.1+ 内核
            sed -i '/^[[:space:]]*"protected_exports_list"[[:space:]]*:[[:space:]]*"android\\/abi_gki_protected_exports_aarch64",$/d' ./common/BUILD.bazel
            sed -i '/kmi_symbol_list_strict_mode/d' ./common/BUILD.bazel
            rm -rf ./common/android/abi_gki_protected_exports_*
            sed -i "/stable_scmversion_cmd/s/-maybe-dirty//g" ./build/kernel/kleaf/impl/stamp.bzl
          fi'''

new_abi_block = '''          else
            # 配置 6.1+ 内核。清洁构建保留 AOSP KMI/ABI 约束，作为兼容性基线。
            if [ "${{ inputs.clean_build }}" != "true" ]; then
              sed -i '/^[[:space:]]*"protected_exports_list"[[:space:]]*:[[:space:]]*"android\\/abi_gki_protected_exports_aarch64",$/d' ./common/BUILD.bazel
              sed -i '/kmi_symbol_list_strict_mode/d' ./common/BUILD.bazel
              rm -rf ./common/android/abi_gki_protected_exports_*
            fi
            sed -i "/stable_scmversion_cmd/s/-maybe-dirty//g" ./build/kernel/kleaf/impl/stamp.bzl
          fi'''
replace_exact(old_abi_block, new_abi_block)


# 4. The build step also disabled strict mode unconditionally. Keep it for
# clean builds in both normal and bypass build paths.
replace_exact(
    "            sed -i '/KMI_SYMBOL_LIST_STRICT_MODE/d' ./common/build.config.gki.aarch64",
    '''            if [ "${{ inputs.clean_build }}" != "true" ]; then
              sed -i '/KMI_SYMBOL_LIST_STRICT_MODE/d' ./common/build.config.gki.aarch64
            fi''',
    count=2,
)


# 5. Refuse to publish an Android 16 / 6.12 Image whose release name
# contradicts the KMI generation declared by the checked-out source.
build_tail = '''            echo "构建完成: $BUILD_VARIANT"

      # ==================== 准备内核 Image ===================='''

validate_kmi = '''            echo "构建完成: $BUILD_VARIANT"

      # ==================== 验证 Android 16 KMI ====================
      - name: 验证 Android 16 KMI
        if: inputs.android_version == 'android16' && inputs.kernel_version == '6.12'
        shell: bash
        run: |
          set -euo pipefail

          IMAGE="$KERNEL_ROOT/bazel-bin/common/kernel_aarch64/Image"
          if [ ! -f "$IMAGE" ]; then
            echo "::error::找不到构建产物: $IMAGE"
            exit 1
          fi

          VERSION="$(strings "$IMAGE" | grep -m1 'Linux version')"
          echo "$VERSION"

          if [ -z "${KMI_GENERATION:-}" ]; then
            echo "::error::KMI_GENERATION 未设置"
            exit 1
          fi

          if ! grep -q -- "-android16-${KMI_GENERATION}" <<< "$VERSION"; then
            echo "::error::kernel release 与 AOSP KMI generation 不一致，期望 android16-${KMI_GENERATION}"
            exit 1
          fi

      # ==================== 准备内核 Image ===================='''
replace_exact(build_tail, validate_kmi)


path.write_text(text)
print('Patched .github/workflows/build.yml successfully')
