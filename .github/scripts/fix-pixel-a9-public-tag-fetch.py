from pathlib import Path

p = Path('.github/workflows/build.yml')
text = p.read_text()
old = '''          echo "Checking out public kernel/common tag: $TAG"\n          git -C common fetch -q origin "refs/tags/$TAG:refs/tags/$TAG"\n          git -C common checkout -q --detach "refs/tags/$TAG"\n'''
new = '''          echo "Checking out public kernel/common tag: $TAG"\n          # repo-managed projects are not guaranteed to name their remote "origin".\n          # Fetch from the canonical AOSP kernel/common repository instead.\n          git -C common fetch -q https://android.googlesource.com/kernel/common \\\n            "refs/tags/$TAG:refs/tags/$TAG"\n          git -C common checkout -q --detach "refs/tags/$TAG"\n'''
count = text.count(old)
if count != 1:
    raise SystemExit(f'expected exactly one public-tag fetch block, found {count}')
p.write_text(text.replace(old, new, 1))
