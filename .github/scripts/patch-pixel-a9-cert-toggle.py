from pathlib import Path

build = Path('.github/workflows/build.yml')
pixel = Path('.github/workflows/kernel-a16-6-12-pixel-a9.yml')

b = build.read_text()
p = pixel.read_text()

# 1. Shared workflow input
anchor = '''      pixel_a9_public_tag:\n        description: "Public kernel/common tag used when the production snapshot pin is disabled"\n        required: false\n        type: string\n        default: ""\n'''
insert = anchor + '''      pixel_a9_inject_stock_cert:\n        description: "Trust the Pixel A9 stock GKI certificate for protected stock modules"\n        required: false\n        type: boolean\n        default: true\n'''
assert b.count(anchor) == 1
b = b.replace(anchor, insert, 1)

# 2. Make the certificate itself optional, but retain every other compatibility config.
old = '''          CERT_SRC="$GITHUB_WORKSPACE/.github/pixel-a9/stock-gki-cert.pem"\n          CERT_DST="$KERNEL_ROOT/common/certs/pixel_a9_stock_gki_cert.pem"\n          cp "$CERT_SRC" "$CERT_DST"\n\n          # The stock A9 Image keeps protected-module enforcement and trimming on.\n          # Only add the public stock GKI certificate to the trusted keyring.\n          "$KERNEL_ROOT/common/scripts/config" --file "$DEFCONFIG" \\\n            --set-str SYSTEM_TRUSTED_KEYS "certs/pixel_a9_stock_gki_cert.pem" \\\n            --set-str LOCALVERSION "-4k" \\\n'''
new = '''          CERT_SRC="$GITHUB_WORKSPACE/.github/pixel-a9/stock-gki-cert.pem"\n          CERT_DST="$KERNEL_ROOT/common/certs/pixel_a9_stock_gki_cert.pem"\n          TRUSTED_KEYS=""\n          if [ "${{ inputs.pixel_a9_inject_stock_cert }}" = "true" ]; then\n            cp "$CERT_SRC" "$CERT_DST"\n            TRUSTED_KEYS="certs/pixel_a9_stock_gki_cert.pem"\n          else\n            echo "Pixel A9 diagnostic mode: stock GKI certificate injection disabled"\n          fi\n\n          # Keep protected-module enforcement, trimming and all other stock-compatible\n          # settings identical. Toggle only the certificate injected into trusted keys.\n          "$KERNEL_ROOT/common/scripts/config" --file "$DEFCONFIG" \\\n            --set-str SYSTEM_TRUSTED_KEYS "$TRUSTED_KEYS" \\\n            --set-str LOCALVERSION "-4k" \\\n'''
assert b.count(old) == 1
b = b.replace(old, new, 1)

# 3. Fingerprint validation only makes sense when injection is enabled.
old = '''          fingerprint="$(openssl x509 -in "$CERT_SRC" -noout -fingerprint -sha256 | cut -d= -f2)"\n          echo "Pixel A9 stock GKI cert SHA256: $fingerprint"\n          [ "$fingerprint" = "8E:4A:1C:45:9B:AB:D8:41:B6:EE:1B:D6:1A:B3:1E:EA:02:67:F2:DB:7B:10:B2:EE:CE:C4:C9:48:E4:2A:55:D1" ] || {\n            echo "::error::Unexpected Pixel A9 stock GKI certificate"; exit 1;\n          }\n'''
new = '''          if [ "${{ inputs.pixel_a9_inject_stock_cert }}" = "true" ]; then\n            fingerprint="$(openssl x509 -in "$CERT_SRC" -noout -fingerprint -sha256 | cut -d= -f2)"\n            echo "Pixel A9 stock GKI cert SHA256: $fingerprint"\n            [ "$fingerprint" = "8E:4A:1C:45:9B:AB:D8:41:B6:EE:1B:D6:1A:B3:1E:EA:02:67:F2:DB:7B:10:B2:EE:CE:C4:C9:48:E4:2A:55:D1" ] || {\n              echo "::error::Unexpected Pixel A9 stock GKI certificate"; exit 1;\n            }\n          fi\n'''
assert b.count(old) == 1
b = b.replace(old, new, 1)

# 4. Validation expects the selected trusted-key state rather than always requiring cert.
old = '''          for required in \\\n            'CONFIG_MODULE_SIG_PROTECT=y' \\\n            'CONFIG_TRIM_UNUSED_KSYMS=y' \\\n            'CONFIG_MODVERSIONS=y' \\\n            'CONFIG_GENDWARFKSYMS=y' \\\n            'CONFIG_SYSTEM_TRUSTED_KEYS="certs/pixel_a9_stock_gki_cert.pem"' \\\n            'CONFIG_LOCALVERSION="-4k"'; do\n            grep -qFx "$required" "$FINAL_CONFIG" || { echo "::error::Missing stock-compatible config: $required"; exit 1; }\n          done\n'''
new = '''          for required in \\\n            'CONFIG_MODULE_SIG_PROTECT=y' \\\n            'CONFIG_TRIM_UNUSED_KSYMS=y' \\\n            'CONFIG_MODVERSIONS=y' \\\n            'CONFIG_GENDWARFKSYMS=y' \\\n            'CONFIG_LOCALVERSION="-4k"'; do\n            grep -qFx "$required" "$FINAL_CONFIG" || { echo "::error::Missing stock-compatible config: $required"; exit 1; }\n          done\n\n          if [ "${{ inputs.pixel_a9_inject_stock_cert }}" = "true" ]; then\n            EXPECTED_TRUST='CONFIG_SYSTEM_TRUSTED_KEYS="certs/pixel_a9_stock_gki_cert.pem"'\n          else\n            EXPECTED_TRUST='CONFIG_SYSTEM_TRUSTED_KEYS=""'\n          fi\n          grep -qFx "$EXPECTED_TRUST" "$FINAL_CONFIG" || {\n            echo "::error::Unexpected Pixel A9 trusted-key config; expected: $EXPECTED_TRUST"\n            exit 1\n          }\n          echo "Pixel A9 stock certificate injection: ${{ inputs.pixel_a9_inject_stock_cert }}"\n'''
assert b.count(old) == 1
b = b.replace(old, new, 1)

# 5. Pixel workflow dispatch input + passthrough
anchor = '''      public_kmi_tag:\n        description: "Public kernel/common tag when production pin is disabled"\n        required: false\n        type: string\n        default: "android16-6.12-2026-03_r52"\n'''
insert = anchor + '''      inject_stock_gki_cert:\n        description: "Inject Pixel A9 stock GKI certificate into trusted keys"\n        required: false\n        type: boolean\n        default: true\n'''
assert p.count(anchor) == 1
p = p.replace(anchor, insert, 1)

anchor = '''      pixel_a9_public_tag: ${{ inputs.public_kmi_tag }}\n'''
insert = anchor + '''      pixel_a9_inject_stock_cert: ${{ inputs.inject_stock_gki_cert }}\n'''
assert p.count(anchor) == 1
p = p.replace(anchor, insert, 1)

build.write_text(b)
pixel.write_text(p)
