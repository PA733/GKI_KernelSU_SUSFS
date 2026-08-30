from pathlib import Path

p = Path('.github/workflows/build.yml')
text = p.read_text()
anchor = '''          patch -p1 < 50_add_susfs_in_gki-${{ inputs.android_version }}-${{ inputs.kernel_version }}.patch || true
'''
replacement = '''          # Pixel A9 production snapshot uses vma_data_pages() in show_smap(),
          # while the upstream android16-6.12 SUSFS patch still uses vma_pages()
          # as context for the SUS_MAP hunk. Adapt only the local patch copy so
          # the SUSFS logic applies without weakening the global .rej safety gate.
          if [[ "${{ inputs.pixel_a9_compat }}" == "true" && "${{ inputs.android_version }}" == "android16" && "${{ inputs.kernel_version }}" == "6.12" ]]; then
            SUSFS_MAIN_PATCH="50_add_susfs_in_gki-${{ inputs.android_version }}-${{ inputs.kernel_version }}.patch"
            if grep -qF 'if (!vma_data_pages(vma))' fs/proc/task_mmu.c \
              && grep -qF 'if (!vma_pages(vma))' "$SUSFS_MAIN_PATCH"; then
              sed -i 's/if (!vma_pages(vma))/if (!vma_data_pages(vma))/' "$SUSFS_MAIN_PATCH"
              echo "Pixel A9: adapted SUSFS task_mmu.c show_smap context to vma_data_pages()"
            fi
          fi

          patch -p1 < 50_add_susfs_in_gki-${{ inputs.android_version }}-${{ inputs.kernel_version }}.patch || true
'''
count = text.count(anchor)
if count != 1:
    raise SystemExit(f'expected exactly one SUSFS main patch anchor, found {count}')

p.write_text(text.replace(anchor, replacement, 1))
