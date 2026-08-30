from pathlib import Path

build = Path('.github/workflows/build.yml')
text = build.read_text()

old = '''      pixel_a9_compat:\n        description: "Reproduce the Pixel 11 A9 production GKI snapshot and trust its stock protected system_dlkm modules"\n        required: false\n        type: boolean\n        default: false\n'''
new = old + '''      pixel_a9_pin_production_snapshot:\n        description: "Pin the exact Pixel A9 production superproject snapshot"\n        required: false\n        type: boolean\n        default: true\n      pixel_a9_public_tag:\n        description: "Public kernel/common tag used when the production snapshot pin is disabled"\n        required: false\n        type: string\n        default: ""\n'''
if text.count(old) != 1:
    raise SystemExit('pixel_a9_compat input anchor mismatch')
text = text.replace(old, new, 1)

old = '''          if [ "${{ inputs.pixel_a9_compat }}" = "true" ]; then\n            if [ "${{ inputs.android_version }}" != "android16" ] || [ "${{ inputs.kernel_version }}" != "6.12" ] || [ "${{ inputs.sub_level }}" != "69" ]; then\n              echo "::error::Pixel A9 compatibility mode requires android16 / 6.12.69"\n              exit 1\n            fi\n            # The production snapshot was published through the Android 16 / 6.12 superproject line.\n            FORMATTED_BRANCH="android16-6.12-sp"\n          fi\n'''
new = '''          if [ "${{ inputs.pixel_a9_compat }}" = "true" ]; then\n            if [ "${{ inputs.android_version }}" != "android16" ] || [ "${{ inputs.kernel_version }}" != "6.12" ] || [ "${{ inputs.sub_level }}" != "69" ]; then\n              echo "::error::Pixel A9 compatibility mode requires android16 / 6.12.69"\n              exit 1\n            fi\n            if [ "${{ inputs.pixel_a9_pin_production_snapshot }}" = "true" ]; then\n              # The production snapshot was published through the Android 16 / 6.12 superproject line.\n              FORMATTED_BRANCH="android16-6.12-sp"\n            else\n              echo "Pixel A9 diagnostic mode: production snapshot pin disabled; using public ${FORMATTED_BRANCH} manifest"\n            fi\n          fi\n'''
if text.count(old) != 1:
    raise SystemExit('source init anchor mismatch')
text = text.replace(old, new, 1)

old = '''      - name: Pin Pixel A9 production snapshot\n        if: inputs.pixel_a9_compat\n'''
new = '''      - name: Pin Pixel A9 production snapshot\n        if: inputs.pixel_a9_compat && inputs.pixel_a9_pin_production_snapshot\n'''
if text.count(old) != 1:
    raise SystemExit('production pin condition anchor mismatch')
text = text.replace(old, new, 1)

anchor = '''      # ==================== 检测 AOSP KMI Generation ====================\n'''
insert = '''      # ==================== Pixel A9 public KMI diagnostic tag ====================\n      - name: Checkout Pixel A9 public KMI diagnostic tag\n        if: inputs.pixel_a9_compat && !inputs.pixel_a9_pin_production_snapshot\n        working-directory: ${{ env.KERNEL_ROOT }}\n        shell: bash\n        run: |\n          set -euo pipefail\n          TAG="${{ inputs.pixel_a9_public_tag }}"\n          if [ -z "$TAG" ]; then\n            echo "::error::pixel_a9_public_tag is required when the production snapshot pin is disabled"\n            exit 1\n          fi\n\n          echo "Checking out public kernel/common tag: $TAG"\n          git -C common fetch -q origin "refs/tags/$TAG:refs/tags/$TAG"\n          git -C common checkout -q --detach "refs/tags/$TAG"\n          ACTUAL_COMMON="$(git -C common rev-parse HEAD)"\n          echo "Public tag common SHA: $ACTUAL_COMMON"\n\n          if [ "$TAG" = "android16-6.12-2026-03_r52" ]; then\n            EXPECTED_R52="2180dbfe30b8629e6fb80a5957b32630b9a51c7b"\n            [ "$ACTUAL_COMMON" = "$EXPECTED_R52" ] || {\n              echo "::error::r52 SHA mismatch; expected $EXPECTED_R52"\n              exit 1\n            }\n          fi\n\n          grep -q '^SUBLEVEL = 69$' common/Makefile || { echo "::error::Public diagnostic tag is not Linux 6.12.69"; exit 1; }\n          grep -q '^KMI_GENERATION=6$' common/build.config.constants || { echo "::error::Public diagnostic tag is not KMI generation 6"; exit 1; }\n          grep -q '^CLANG_VERSION=r536225$' common/build.config.constants || { echo "::error::Public diagnostic tag does not use r536225"; exit 1; }\n\n          {\n            echo "PIXEL_A9_PUBLIC_TAG=$TAG"\n            echo "PIXEL_A9_PUBLIC_COMMON=$ACTUAL_COMMON"\n          } >> "$GITHUB_ENV"\n\n'''
if text.count(anchor) != 1:
    raise SystemExit('KMI detection anchor mismatch')
text = text.replace(anchor, insert + anchor, 1)

old = '''          grep -qF "Linux version $EXPECTED_RELEASE" <<< "$VERSION" || {\n            echo "::error::Pixel A9 kernel identity mismatch; expected $EXPECTED_RELEASE"\n            exit 1\n          }\n'''
new = '''          if [ "${{ inputs.pixel_a9_pin_production_snapshot }}" = "true" ]; then\n            grep -qF "Linux version $EXPECTED_RELEASE" <<< "$VERSION" || {\n              echo "::error::Pixel A9 kernel identity mismatch; expected $EXPECTED_RELEASE"\n              exit 1\n            }\n          else\n            echo "Pixel A9 public-tag diagnostic mode: exact stock release identity check intentionally skipped"\n            echo "Diagnostic tag: ${PIXEL_A9_PUBLIC_TAG:-unknown}"\n            echo "Diagnostic common SHA: ${PIXEL_A9_PUBLIC_COMMON:-unknown}"\n          fi\n'''
if text.count(old) != 1:
    raise SystemExit('identity gate anchor mismatch')
text = text.replace(old, new, 1)

build.write_text(text)

wf = Path('.github/workflows/kernel-a16-6-12-pixel-a9.yml')
w = wf.read_text()
old = '''      use_rekernel:\n        description: "启用 Re-Kernel 驱动"\n        required: false\n        type: boolean\n        default: false\n'''
new = old + '''      pin_production_snapshot:\n        description: "Pin exact A9 production snapshot; disable for public KMI-generation experiment"\n        required: false\n        type: boolean\n        default: true\n      public_kmi_tag:\n        description: "Public kernel/common tag when production pin is disabled"\n        required: false\n        type: string\n        default: "android16-6.12-2026-03_r52"\n'''
if w.count(old) != 1:
    raise SystemExit('pixel workflow input anchor mismatch')
w = w.replace(old, new, 1)
old = '''      pixel_a9_compat: true'''
new = '''      pixel_a9_compat: true\n      pixel_a9_pin_production_snapshot: ${{ inputs.pin_production_snapshot }}\n      pixel_a9_public_tag: ${{ inputs.public_kmi_tag }}'''
if w.count(old) != 1:
    raise SystemExit('pixel workflow call anchor mismatch')
w = w.replace(old, new, 1)
wf.write_text(w)
