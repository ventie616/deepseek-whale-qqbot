"""导入 kiriyuki.com 的「大肥鱼」表情包（322 张）到本地表情库。

来源：https://kiriyuki.com/showcase?p=大肥鱼 ｜ 作者：可以叫我云大人（B站 103739）
许可：CC BY-NC 4.0（非商用，必须保留作者与站点署名）
元数据：https://kiriyuki.com/meme/kit/nekowhale.json

用法：
    python tools/import_nekowhale.py            # 下载 + 优化 + 合并索引
    python tools/import_nekowhale.py --dry      # 只看统计
"""

from __future__ import annotations

import argparse
import json
import pathlib
import time
import urllib.parse
import urllib.request

from PIL import Image, ImageSequence  # noqa: F401

ROOT = pathlib.Path(r"C:\QQBot")
STICKERS = ROOT / "stickers"
DEST = STICKERS / "nekowhale"
INDEX = STICKERS / "index.json"
META_URL = "https://kiriyuki.com/meme/kit/nekowhale.json"
MANIFEST_URL = "https://kiriyuki.com/api/manifest"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
PACKS = ("大肥鱼", "大肥鱼和GPT娘")
MAX_SIDE = 200
COLORS = 96

# 情绪 -> (我们的 mood, 强度)
EMO_MAP = {
    "开心": ("开心", 3), "大笑": ("大笑", 4), "喜欢": ("喜欢", 3), "害羞": ("害羞", 2),
    "得意": ("得意", 3), "兴奋": ("兴奋", 4), "惊讶": ("惊讶", 3), "疑惑": ("疑惑", 2),
    "思考": ("思考", 2), "无语": ("吐槽", 2), "难过": ("委屈", 3), "哭": ("含泪", 4),
    "生气": ("生气", 4), "害怕": ("慌张", 3), "疲惫": ("困", 2), "赞同": ("乖巧", 2),
    "拒绝": ("拒绝", 3), "打招呼": ("打招呼", 2), "感谢": ("感谢", 2), "道歉": ("道歉", 2),
    "卖萌": ("卖萌", 3), "搞怪": ("憨", 3), "嘲讽": ("嘲讽", 3), "安慰": ("安慰", 2),
}
DEFAULT_MOOD = ("平静", 2)


def fetch(url: str, timeout: int = 40) -> bytes:
    last = None
    for i in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://kiriyuki.com/"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(str(last))


def optimize(src: pathlib.Path, dst: pathlib.Path) -> int:
    im = Image.open(src)
    n = getattr(im, "n_frames", 1)
    durs, frames = [], []
    for i in range(n):
        im.seek(i)
        durs.append(im.info.get("duration") or 60)
        fr = im.convert("RGBA")
        w, h = fr.size
        scale = MAX_SIDE / max(w, h)
        if scale < 1:
            fr = fr.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        frames.append(fr.convert("P", palette=Image.ADAPTIVE, colors=COLORS))
    dst.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        dst, save_all=True, append_images=frames[1:],
        duration=max(30, sum(durs) // max(1, len(durs))), loop=0, optimize=True, disposal=2,
    )
    return dst.stat().st_size


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    manifest = json.loads(fetch(MANIFEST_URL))
    gif_base = "https://kiriyuki.com" + manifest["gif_base"]
    items = [i for i in manifest["items"] if i["pack"] in PACKS]
    meta = json.loads(fetch(META_URL))
    by_id = {m["id"]: m for m in meta.get("stickers") or []}
    print(f"清单 {len(items)} 张，元数据 {len(by_id)} 条，总大小 {sum(i['bytes'] for i in items)/1024/1024:.0f} MB")
    if args.dry:
        return 0

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def work(n: int, it: dict) -> tuple[str, str]:
        stem = pathlib.Path(it["file"]).stem
        rel = f"nekowhale/{it['pack']}/{stem}.gif"
        dst = STICKERS / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            return it["id"], ""
        url = f"{gif_base}/{urllib.parse.quote(it['pack'])}/{urllib.parse.quote(it['file'])}"
        raw = STICKERS / "_neko_tmp" / f"{n}.gif"
        try:
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_bytes(fetch(url, timeout=90))
            optimize(raw, dst)
            raw.unlink(missing_ok=True)
            return it["id"], ""
        except Exception as exc:  # noqa: BLE001
            return it["id"], str(exc)[:70]

    failed = []
    done = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(work, n, it) for n, it in enumerate(items, 1)]
        for fu in as_completed(futs):
            done += 1
            iid, err = fu.result()
            if err:
                failed.append((iid, err))
                print(f"  [{done}/{len(items)}] 失败 {iid}: {err}")
            elif done % 25 == 0 or done == len(items):
                print(f"  [{done}/{len(items)}]")
    tmpdir = STICKERS / "_neko_tmp"
    if tmpdir.exists():
        for f in tmpdir.glob("*"):
            f.unlink(missing_ok=True)
        tmpdir.rmdir()

    entries = []
    for it in items:
        stem = pathlib.Path(it["file"]).stem
        rel = f"nekowhale/{it['pack']}/{stem}.gif"
        if not (STICKERS / rel).exists():
            continue
        m = by_id.get(it["id"], {})
        mood, intensity = EMO_MAP.get(str(m.get("emotion") or ""), DEFAULT_MOOD)
        tags = [str(t) for t in (m.get("tags") or [])][:6]
        if m.get("text"):
            tags.append(str(m["text"])[:8])
        scenes = ["群聊", "私聊"]
        if "双人" in it["pack"] or m.get("dir"):
            scenes.append("互动")
        entries.append({
            "file": rel,
            "mood": mood,
            "tags": tags or [mood],
            "intensity": intensity,
            "scenes": scenes,
            "safety": "mild" if mood == "嘲讽" else "safe",
            "pack": "nekowhale",
            "emotion": m.get("emotion") or "",
            "use": m.get("use") or "",
            "desc": m.get("desc") or "",
            "meme": m.get("meme") or "",
            "dir": m.get("dir") or "",
            "source": "kiriyuki.com",
            "author": "可以叫我云大人（B站 103739）",
        })
    data = json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else {"version": 1, "items": []}
    old = [i for i in data.get("items") or [] if i.get("pack") != "nekowhale"]
    for i in old:
        i.setdefault("pack", "meme")
    merged = old + entries
    INDEX.write_text(json.dumps({"version": 2, "items": merged}, ensure_ascii=False, indent=2), encoding="utf-8")
    (DEST / "CREDIT.txt").write_text(
        "表情包作者：可以叫我云大人（B站 https://space.bilibili.com/103739）\n"
        "制作/托管：kiriyuki.com\n"
        "许可：CC BY-NC 4.0 —— 仅限非商业使用，保留此署名，禁止付费分发\n"
        "套组：https://kiriyuki.com/showcase?p=%E5%A4%A7%E8%82%A5%E9%B1%BC\n",
        encoding="utf-8",
    )
    print(f"\n导入 {len(entries)} 张（失败 {len(failed)}）｜索引共 {len(merged)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
