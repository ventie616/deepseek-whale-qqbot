"""把表情包做成「联系表」(contact sheet)，供多模态模型批量标注。

参考 dsh-nekowhale-stickers 的标注方法：抽帧拼图 + 每张一行 JSON。
这里一次拼 4 张（2×2），每格左上角标 id，GIF 抽最多 6 帧。

用法：
    python tools/make_sticker_sheets.py          # 生成 sheets + batch.json
"""

from __future__ import annotations

import json
import pathlib

from PIL import Image, ImageDraw, ImageFont

STICKERS = pathlib.Path(r"C:\QQBot\stickers")
OUT = STICKERS / "_annot"
SHEETS = OUT / "sheets"
CELL = 320
PAD = 8
HEADER = 26
MAX_FRAMES = 6


def font(size: int = 18):
    for name in ("msyh.ttc", "msyhbd.ttc", "simhei.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def frames_of(path: pathlib.Path) -> list[Image.Image]:
    im = Image.open(path)
    if path.suffix.lower() != ".gif" or getattr(im, "n_frames", 1) <= 1:
        return [im.convert("RGBA")]
    n = im.n_frames
    idxs = [round(i * (n - 1) / (MAX_FRAMES - 1)) for i in range(MAX_FRAMES)]
    out = []
    for k in idxs:
        im.seek(k)
        out.append(im.convert("RGBA"))
    return out


def fit(im: Image.Image, box: int) -> Image.Image:
    w, h = im.size
    scale = min(box / w, box / h)
    return im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)


def build_sheet(batch: list[dict], dst: pathlib.Path) -> None:
    cols, rows = 2, 2
    w = cols * (CELL + PAD) + PAD
    h = rows * (CELL + HEADER + PAD) + PAD
    sheet = Image.new("RGB", (w, h), (232, 232, 232))
    d = ImageDraw.Draw(sheet)
    f = font(15)
    for k, item in enumerate(batch):
        r, c = divmod(k, cols)
        x0 = PAD + c * (CELL + PAD)
        y0 = PAD + r * (CELL + HEADER + PAD)
        d.rectangle([x0, y0, x0 + CELL, y0 + HEADER], fill=(255, 255, 255))
        d.text((x0 + 4, y0 + 4), item["id"], fill=(200, 0, 0), font=f)
        frames = frames_of(STICKERS / item["file"])
        # 多帧横向排布，单帧居中
        if len(frames) == 1:
            fw = CELL // max(1, 1)
            img = fit(frames[0], CELL - 6)
            sheet.paste(img, (x0 + (CELL - img.width) // 2, y0 + HEADER + (CELL - img.height) // 2), img)
        else:
            sub = CELL // len(frames)
            for i, fr in enumerate(frames):
                img = fit(fr, sub - 2)
                sheet.paste(img, (x0 + i * sub + 1, y0 + HEADER + (CELL - img.height) // 2), img)
    sheet.save(dst, quality=88, optimize=True)


def main() -> int:
    SHEETS.mkdir(parents=True, exist_ok=True)
    for f in SHEETS.glob("*.jpg"):
        f.unlink()
    items = [
        i for i in json.loads((STICKERS / "index.json").read_text(encoding="utf-8"))["items"]
        if i.get("pack") == "meme" and (STICKERS / i["file"]).exists()
    ]
    # id 用短编号：哈希名太长且前缀会撞，改用 m001~m192
    for n, it in enumerate(items, 1):
        it["id"] = f"m{n:03d}"
    batches = [items[i:i + 4] for i in range(0, len(items), 4)]
    manifest = []
    for n, batch in enumerate(batches, 1):
        dst = SHEETS / f"sheet_{n:03d}.jpg"
        build_sheet(batch, dst)
        manifest.append({"sheet": str(dst), "items": [{"id": b["id"], "file": b["file"]} for b in batch]})
        if n % 10 == 0 or n == len(batches):
            print(f"  生成 {n}/{len(batches)} 张联系表")
    (OUT / "batch.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{len(items)} 张表情 -> {len(batches)} 张联系表（每张 4 格）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
