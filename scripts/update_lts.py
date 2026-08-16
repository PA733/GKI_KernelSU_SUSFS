import json
import os

from gki_fetch import TARGETS, fetch_lts, parse_version, json_path


def update_lts():
    for (android_ver, kernel_ver) in TARGETS:
        path = json_path(android_ver, kernel_ver)
        print(f"\n=== {android_ver} / {kernel_ver} ===")

        if not os.path.exists(path):
            print(f"  JSON not found: {path}, skip")
            continue

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        lts_label = f"{android_ver}-{kernel_ver}-lts"
        print(f"  [{lts_label}] ", end="", flush=True)
        lts_text = fetch_lts(android_ver, kernel_ver)
        if lts_text is None:
            print("not found, skip")
            continue

        ver = parse_version(lts_text)
        if ver is None:
            print("parse failed, skip")
            continue

        version, patchlevel, sublevel = ver
        lts_value = f"{version}.{patchlevel}.{sublevel}"
        changed = False

        if kernel_ver == "5.10":
            lts_entry = next((e for e in data.get("entries", []) if e.get("date") == "lts"), None)
            if lts_entry:
                if lts_entry.get("kernel") != lts_value:
                    lts_entry["kernel"] = lts_value
                    changed = True
                    print(f"-> {lts_value} (updated entry)")
                else:
                    print(f"-> {lts_value} (unchanged)")
            else:
                data.setdefault("entries", []).append({"date": "lts", "kernel": lts_value, "revision": "r1"})
                changed = True
                print(f"-> {lts_value} (added entry)")
        else:
            old_lts = data.get("lts")
            if old_lts == lts_value:
                print(f"-> {lts_value} (unchanged)")
                continue
            data["lts"] = lts_value
            changed = True
            print(f"-> {lts_value} (was {old_lts})")

        if changed:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

    print("\nDone.")


if __name__ == "__main__":
    update_lts()