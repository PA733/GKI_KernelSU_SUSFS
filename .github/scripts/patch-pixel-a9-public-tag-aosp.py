from pathlib import Path

p = Path('.github/workflows/build.yml')
text = p.read_text()
old = '''          echo "Checking out public kernel/common tag: $TAG"\n          git -C common fetch -q origin "refs/tags/$TAG:refs/tags/$TAG"\n          git -C common checkout -q --detach "refs/tags/$TAG"\n'''
new = '''          echo "Checking out public kernel/common tag: $TAG"\n          # repo sync configures kernel/common with the AOSP remote named "aosp".\n          # Reuse that existing repo-managed remote rather than assuming "origin".\n          git -C common fetch -q aosp "refs/tags/$TAG:refs/tags/$TAG"\n          git -C common checkout -q --detach "refs/tags/$TAG"\n'''
count = text.count(old)
if count != 1:
    raise SystemExit(f'expected exactly one public-tag fetch block, found {count}')
p.write_text(text.replace(old, new, 1))
