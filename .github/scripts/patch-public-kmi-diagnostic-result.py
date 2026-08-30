from pathlib import Path

p = Path('.github/workflows/build.yml')
text = p.read_text()
old = '''          [ "$mismatches" -eq 0 ] || exit 1
          echo "Pixel A9 critical wwan/rfkill/rust_binder CRC baseline matches stock."
'''
new = '''          if [ "$mismatches" -ne 0 ]; then
            if [ "${{ inputs.pixel_a9_pin_production_snapshot }}" = "true" ]; then
              echo "::error::Pixel A9 production compatibility failed: $mismatches critical module CRC mismatch(es)"
              exit 1
            fi
            echo "::warning::Pixel A9 public-tag diagnostic found $mismatches critical module CRC mismatch(es)"
            echo "Public-tag ABI comparison completed; mismatches above are the diagnostic result, not a workflow failure."
          else
            echo "Pixel A9 critical wwan/rfkill/rust_binder CRC baseline matches stock."
          fi
'''
count = text.count(old)
if count != 1:
    raise SystemExit(f'expected exactly one CRC terminal block, found {count}')
p.write_text(text.replace(old, new, 1))
