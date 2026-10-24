"""把 astrbot-deepseek-meme-pack（大肥鱼表情包）导入本站表情库。

- 按包的 27 个分类目录映射成五维索引（不调视觉模型，零 token）
- 静态图缩到最长边 480，GIF 原样保留（>3MB 的跳过，发不出去）
- 写 stickers/index.json，按 SHA-256 去重
"""

import hashlib
import json
import shutil
import zipfile
from pathlib import Path

from PIL import Image

ZIP = Path(r"C:\QQBot\downloads\meme-pack.zip")
ROOT = "astrbot-deepseek-meme-pack-main/"
LIB = Path(r"C:\QQBot\stickers")
INDEX = LIB / "index.json"
MAX_BYTES = 3 * 1024 * 1024
MAX_SIDE = 480

# 分类 -> (目录, 主情绪, 标签, 强度, 场景, 安全)
MAP = {
    "angry":     ("angry",  "生气", ["生气", "不满"],      4, ["被招惹", "被批评"], "safe"),
    "baka":      ("angry",  "吐槽", ["吐槽", "笨蛋"],      2, ["低级错误", "被吐槽"], "safe"),
    "color":     ("shy",    "害羞", ["暧昧", "调情"],      3, ["夜聊", "暧昧"],     "mild"),
    "confused":  ("misc",   "疑惑", ["疑惑", "不解"],      2, ["没听懂", "求解释"], "safe"),
    "cpu":       ("sleepy", "无语", ["卡壳", "加载"],      2, ["技术讨论", "卡住"], "safe"),
    "fool":      ("happy",  "沙雕", ["沙雕", "自嘲"],      3, ["小失误", "缓和气氛"], "safe"),
    "givemoney": ("misc",   "卖惨", ["要钱", "吃白饭"],    3, ["氪金", "报酬", "吃白饭"], "safe"),
    "happy":     ("happy",  "开心", ["开心", "庆祝"],      3, ["成功", "被夸"],     "safe"),
    "like":      ("happy",  "满足", ["喜欢", "赞同"],      3, ["推荐", "被夸"],     "safe"),
    "meow":      ("happy",  "卖萌", ["卖萌", "撒娇"],      3, ["撒娇", "安抚"],     "safe"),
    "morning":   ("happy",  "平静", ["早安", "打招呼"],    1, ["早安", "打招呼"],   "safe"),
    "reply":     ("misc",   "期待", ["等待", "确认"],      2, ["提问", "等回复"],   "safe"),
    "sad":       ("sad",    "难过", ["难过", "抱歉"],      3, ["坏消息", "挫折"],   "safe"),
    "see":       ("sleepy", "偷瞄", ["偷看", "关注"],      2, ["观察", "关注"],     "safe"),
    "shy":       ("shy",    "害羞", ["害羞", "被夸"],      3, ["被夸", "告白"],     "safe"),
    "sigh":      ("sleepy", "无语", ["无奈", "叹气"],      2, ["重复问题", "无奈"], "safe"),
    "sleep":     ("sleepy", "困",   ["困", "休息"],        2, ["晚安", "熬夜"],     "safe"),
    "surprised": ("happy",  "惊讶", ["惊讶", "意外"],      4, ["意外", "重大发现"], "safe"),
    "work":      ("misc",   "累",   ["工作", "任务"],      3, ["任务分配", "进度"], "safe"),
    "_laugh":    ("happy",  "大笑", ["狂笑", "搞笑"],      4, ["群聊玩梗", "好笑"], "safe"),
    "_red":      ("happy",  "开心", ["股票涨", "发财"],    4, ["股票", "基金"],     "safe"),
    "_green":    ("sad",    "难过", ["股票跌", "亏钱"],    3, ["股票", "基金"],     "safe"),
    "_stock":    ("misc",   "平静", ["股票", "理财"],      2, ["股票", "基金"],     "safe"),
    "_genshin":  ("happy",  "得意", ["原神", "玩梗"],      3, ["原神", "游戏"],     "safe"),
    "_kg":       ("happy",  "卖萌", ["玩梗"],              2, ["群聊玩梗"],         "safe"),
    "_lkll":     ("happy",  "卖萌", ["玩梗"],              2, ["群聊玩梗"],         "safe"),
    "_lksl":     ("happy",  "卖萌", ["玩梗"],              2, ["群聊玩梗"],         "safe"),
}


def shrink_static(path: Path) -> None:
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((MAX_SIDE, MAX_SIDE))
            im.save(path, quality=88, optimize=True)
    except Exception:
        pass


def main() -> int:
    data = json.loads(INDEX.read_text(encoding="utf-8"))
    items = data.get("items", [])
    known = {e["file"] for e in items}
    known_hash = set()
    for e in items:
        p = LIB / e["file"]
        if p.exists():
            known_hash.add(hashlib.sha256(p.read_bytes()).hexdigest())

    imported = skipped_big = dup = 0
    with zipfile.ZipFile(ZIP) as zf:
        for info in zf.infolist():
            if info.is_dir() or "/memes/" not in info.filename:
                continue
            parts = info.filename[len(ROOT):].split("/")
            if len(parts) < 3:
                continue
            cat, name = parts[1], parts[2]
            if cat not in MAP:
                print(f"  [跳过] 未知分类 {cat}/{name}")
                continue
            folder, mood, tags, inten, scenes, safety = MAP[cat]
            ext = Path(name).suffix.lower()
            if ext not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
                continue
            raw = zf.read(info)
            if ext == ".gif" and len(raw) > MAX_BYTES:
                skipped_big += 1
                continue
            digest = hashlib.sha256(raw).hexdigest()
            if digest in known_hash:
                dup += 1
                continue

            target = LIB / folder / f"dspack_{cat}_{name}"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            if ext != ".gif":
                shrink_static(target)
            rel = f"{folder}/{target.name}"
            if rel in known:
                continue
            items.append(
                {
                    "file": rel,
                    "mood": mood,
                    "tags": tags,
                    "intensity": inten,
                    "scenes": scenes,
                    "safety": safety,
                }
            )
            known.add(rel)
            known_hash.add(digest)
            imported += 1

    if imported:
        data["items"] = items
        INDEX.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n导入 {imported} 张｜重复跳过 {dup}｜GIF 过大跳过 {skipped_big}")
    print(f"索引现在 {len(items)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
