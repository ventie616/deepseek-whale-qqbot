"""把表情包从 sticker_inbox 归入表情库，按文件名关键词自动分情绪类。

用法：
    C:\\QQBot\\venv\\Scripts\\python.exe C:\\QQBot\\tools\\import_stickers.py
    （或双击 C:\\QQBot\\import-stickers.bat）

支持 png / jpg / jpeg / gif / webp / bmp。
- 自动去重（按内容 SHA-256），重复的会跳过
- 文件名含关键词就归到对应情绪目录，否则进 misc
- 大于 320px 的静态图会等比缩小到 320px；gif 原样保留（保住动图）
- 归库后会从 inbox 移到 stickers/_imported/，不会重复导入
"""

from __future__ import annotations

import argparse

try:  # 同目录的视觉打标模块
    from sticker_tagger import FOLDER as MOOD_FOLDER
    from sticker_tagger import load_index, save_index, tag_image
except Exception:  # pragma: no cover
    MOOD_FOLDER, load_index, save_index, tag_image = {}, None, None, None


import hashlib
import shutil
import sys
from pathlib import Path

INBOX = Path(r"C:\QQBot\sticker_inbox")
LIB = Path(r"C:\QQBot\stickers")
DONE = LIB / "_imported"

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# 文件名关键词 -> 情绪目录（按顺序匹配，先命中先用）
MOOD_RULES: list[tuple[str, list[str]]] = [
    ("angry", ["生气", "怒", "气", "哼", "讨厌", "凶", "打", "揍", "angry"]),
    ("sad", ["哭", "难过", "伤心", "委屈", "泪", "sad", "呜"]),
    ("sleepy", ["困", "睡", "晚安", "累", "躺", "sleepy"]),
    ("shy", ["害羞", "脸红", "羞", "shy", "比心", "爱你", "心动"]),
    ("happy", ["笑", "开心", "好耶", "嘿嘿", "哈哈", "赞", "棒", "happy", "耶"]),
]

MAX_SIDE = 320
no_tag = False


def mood_of(name: str) -> str:
    low = name.lower()
    for mood, words in MOOD_RULES:
        if any(w in low for w in words):
            return mood
    return "misc"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def known_hashes() -> dict[str, Path]:
    result: dict[str, Path] = {}
    for p in LIB.rglob("*"):
        if p.is_file() and p.suffix.lower() in IMAGE_EXT:
            try:
                result[sha256_of(p)] = p
            except OSError:
                pass
    return result


def shrink_static(path: Path) -> None:
    """静态图太大就等比缩到 MAX_SIDE，动图不动。"""
    if path.suffix.lower() == ".gif":
        return
    try:
        from PIL import Image
    except ImportError:
        return
    try:
        with Image.open(path) as im:
            w, h = im.size
            if max(w, h) <= MAX_SIDE:
                return
            scale = MAX_SIDE / max(w, h)
            im = im.convert("RGBA") if im.mode not in ("RGB", "RGBA") else im
            im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))))
            out = path.with_suffix(".png")
            im.save(out)
            if out != path:
                path.unlink()
            print(f"    缩放 {w}x{h} -> {out.stat().st_size // 1024}KB  {out.name}")
    except Exception as exc:  # noqa: BLE001
        print(f"    跳过缩放（{exc}）")


def main() -> int:
    global no_tag
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-tag", action="store_true", help="不调视觉模型，只按文件名分类")
    args = ap.parse_args()
    no_tag = args.no_tag

    INBOX.mkdir(parents=True, exist_ok=True)
    DONE.mkdir(parents=True, exist_ok=True)

    files = [
        p for p in INBOX.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXT
    ]
    if not files:
        print(f"收件箱是空的：{INBOX}")
        print("把你的表情包图片放进去，再运行一次。")
        return 0

    print(f"发现 {len(files)} 张待导入图片")
    seen = known_hashes()
    print(f"库里已有 {len(seen)} 张去重样本\n")

    imported, dup, failed = 0, 0, 0
    for src in sorted(files):
        try:
            digest = sha256_of(src)
        except OSError as exc:
            print(f"[跳过] {src.name}: 读取失败 {exc}")
            failed += 1
            continue

        if digest in seen:
            print(f"[重复] {src.name} -> 已在 {seen[digest].relative_to(LIB)}")
            dup += 1
            continue

        # 先让视觉模型判断归属，失败再退回文件名关键词
        meta = None
        if tag_image is not None and not no_tag:
            try:
                meta = tag_image(src)
            except Exception as exc:  # noqa: BLE001
                print(f"  [打标失败] {src.name}: {exc}")
        mood = MOOD_FOLDER.get(meta["mood"], "misc") if meta else mood_of(src.stem)

        target_dir = LIB / mood
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / src.name
        i = 2
        while target.exists():
            target = target_dir / f"{src.stem}_{i}{src.suffix}"
            i += 1

        shutil.copy2(src, target)
        shrink_static(target)
        # 放回 _imported 存档，保留原图
        shutil.move(str(src), DONE / src.name)
        seen[digest] = target
        imported += 1
        if meta:
            data = load_index()
            data.setdefault("items", []).append(
                {"file": f"{mood}/{target.name}", **meta}
            )
            save_index(data)
            print(
                f"[导入] {src.name} -> {mood}/  "
                f"（{meta['mood']} 强度{meta['intensity']} {meta['safety']} {meta['tags']}）"
            )
        else:
            print(f"[导入] {src.name} -> {mood}/（未打标）")

    print(f"\n完成：导入 {imported} 张，重复 {dup} 张，失败 {failed} 张")
    counts = {
        d.name: len(list(d.glob("*")))
        for d in sorted(LIB.iterdir())
        if d.is_dir() and d.name != "_imported"
    }
    print("库容量：", ", ".join(f"{k}={v}" for k, v in counts.items()))
    print("\n插件每次回复都会重新扫目录，无需重启 AstrBot。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
