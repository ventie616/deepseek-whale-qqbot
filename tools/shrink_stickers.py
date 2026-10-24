"""压缩过大的表情包（目标是发得更快）。

- GIF：最多 20 帧、边长 160、64 色
- PNG/JPG：长边 320
- 只处理 >180KB 的；`nekowhale` 可重新导入不备份，`meme` 包备份到 _orig/

用法：
    python tools/shrink_stickers.py --dry
    python tools/shrink_stickers.py
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil

from PIL import Image

ROOT = pathlib.Path(r"C:\QQBot")
STICKERS = ROOT / "stickers"
INDEX = STICKERS / "index.json"
ORIG = STICKERS / "_orig"
LIMIT = 150 * 1024
MAX_SIDE = 112
MAX_FRAMES = 12
COLORS = 40


def load_gif_frames(path: pathlib.Path) -> tuple[list[Image.Image], int]:
    im = Image.open(path)
    n = getattr(im, "n_frames", 1)
    keep = min(n, MAX_FRAMES)
    idxs = [round(i * (n - 1) / max(1, keep - 1)) for i in range(keep)] if keep > 1 else [0]
    frames, durs = [], []
    for k in idxs:
        im.seek(k)
        durs.append(im.info.get("duration") or 60)
        fr = im.convert("RGBA")
        w, h = fr.size
        scale = MAX_SIDE / max(w, h)
        if scale < 1:
            fr = fr.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        frames.append(fr.convert("P", palette=Image.ADAPTIVE, colors=COLORS))
    im.close()
    return frames, max(30, sum(durs) // max(1, len(durs)))


def shrink_gif(path: pathlib.Path) -> bool:
    frames, dur = load_gif_frames(path)
    tmp = path.with_name(path.stem + ".__tmp.gif")
    frames[0].save(
        tmp, format="GIF", save_all=True, append_images=frames[1:],
        duration=dur, loop=0, optimize=True, disposal=2,
    )
    if tmp.stat().st_size < path.stat().st_size:
        os.replace(tmp, path)
        return True
    tmp.unlink(missing_ok=True)
    return False


def shrink_image(path: pathlib.Path) -> bool:
    im = Image.open(path)
    im.load()
    w, h = im.size
    scale = (MAX_SIDE * 2) / max(w, h)
    out = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS) if scale < 1 else im
    ext = path.suffix.lower()
    fmt = "JPEG" if ext in (".jpg", ".jpeg") else "PNG" if ext == ".png" else "WEBP"
    tmp = path.with_name(path.stem + ".__tmp" + (".jpg" if fmt == "JPEG" else ".png"))
    if fmt == "JPEG":
        out.convert("RGB").save(tmp, format="JPEG", quality=78, optimize=True)
    elif fmt == "PNG":
        out.save(tmp, format="PNG", optimize=True)
    else:
        out.convert("RGB").save(tmp, format="WEBP", quality=80)
    im.close()
    if tmp.stat().st_size < path.stat().st_size:
        os.replace(tmp, path)
        return True
    tmp.unlink(missing_ok=True)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    items = json.loads(INDEX.read_text(encoding="utf-8"))["items"]
    targets = [
        i for i in items
        if (STICKERS / i["file"]).is_file() and (STICKERS / i["file"]).stat().st_size > LIMIT
    ]
    before = sum((STICKERS / i["file"]).stat().st_size for i in targets)
    print(f"待压缩 {len(targets)} 张 / {before/1024/1024:.1f} MB")
    if args.dry:
        return 0

    done = failed = 0
    fails: list[str] = []
    for n, it in enumerate(targets, 1):
        path = STICKERS / it["file"]
        try:
            if it.get("pack") == "meme":
                bak = ORIG / it["file"]
                if not bak.exists():
                    bak.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, bak)
            ok = shrink_gif(path) if path.suffix.lower() == ".gif" else shrink_image(path)
            if ok:
                done += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            fails.append(f"{it['file']}: {type(exc).__name__} {str(exc)[:40]}")
        if n % 50 == 0 or n == len(targets):
            print(f"  [{n}/{len(targets)}] 已压 {done} 失败 {failed}", flush=True)

    after = sum((STICKERS / i["file"]).stat().st_size for i in targets)
    print(f"\n完成：{done} 张 / 失败 {failed} | {before/1024/1024:.1f} MB -> {after/1024/1024:.1f} MB")
    for f in fails[:5]:
        print("  ", f)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
