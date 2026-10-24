"""修复索引：把库里存在但索引缺失的图补进来（视觉打标）。"""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(r"C:\QQBot\tools")))
from sticker_tagger import FOLDER, tag_image  # noqa: E402

LIB = pathlib.Path(r"C:\QQBot\stickers")
IDX = LIB / "index.json"
EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

data = json.loads(IDX.read_text(encoding="utf-8"))
items = data.get("items", [])
indexed = {e["file"] for e in items}

files = []
for d in sorted(LIB.iterdir()):
    if not d.is_dir() or d.name.startswith("_"):
        continue
    for p in sorted(d.iterdir()):
        if p.is_file() and p.suffix.lower() in EXT:
            files.append(p)

missing = [p for p in files if f"{p.parent.name}/{p.name}" not in indexed]
print(f"索引 {len(items)} 条 | 库内 {len(files)} 张 | 待补 {len(missing)} 张")

added = 0
for p in missing:
    try:
        meta = tag_image(p)
    except Exception as exc:  # noqa: BLE001
        print(f"  打标失败 {p.name}: {exc}")
        meta = {"mood": "开心", "tags": ["未分类"], "intensity": 3,
                "scenes": ["闲聊"], "safety": "safe"}
    # 文件已在某目录里就不搬，只记录
    items.append({"file": f"{p.parent.name}/{p.name}", **meta})
    added += 1
    print(f"  + {p.parent.name}/{p.name} → {meta['mood']} 强度{meta['intensity']} {meta['safety']} {meta['tags']}")

if added:
    data["items"] = items
    IDX.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n补入 {added} 条，索引现在 {len(items)} 条")
