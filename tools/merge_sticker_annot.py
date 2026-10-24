"""把标注结果合并进 stickers/index.json（统一两套表情的字段）。

meme 包：用 emotion/tags/use/desc/meme/text/en 推导 mood/intensity/scenes/safety
nekowhale 包：保持原有字段，补齐 text/en（若缺）
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(r"C:\QQBot")
STICKERS = ROOT / "stickers"
INDEX = STICKERS / "index.json"
ANNOT = STICKERS / "_annot" / "annot.jsonl"

EMO_MAP = {
    "开心": ("开心", 3), "大笑": ("大笑", 4), "喜欢": ("喜欢", 3), "害羞": ("害羞", 2),
    "得意": ("得意", 3), "兴奋": ("兴奋", 4), "惊讶": ("惊讶", 3), "疑惑": ("疑惑", 2),
    "思考": ("思考", 2), "无语": ("吐槽", 2), "难过": ("委屈", 3), "哭": ("含泪", 4),
    "生气": ("生气", 4), "害怕": ("慌张", 3), "疲惫": ("困", 2), "赞同": ("乖巧", 2),
    "拒绝": ("拒绝", 3), "打招呼": ("打招呼", 2), "感谢": ("感谢", 2), "道歉": ("道歉", 2),
    "卖萌": ("卖萌", 3), "搞怪": ("憨", 3), "嘲讽": ("嘲讽", 3), "安慰": ("安慰", 2),
}
DEFAULT = ("平静", 2)

SCENE_HINTS = {
    "被夸": ("求夸", "夸我", "表扬", "称赞", "得意"),
    "被骂": ("挨骂", "被骂", "骂我", "道歉", "认错"),
    "被调侃": ("调侃", "阴阳", "嘲讽", "吐槽", "损"),
    "求安慰": ("安慰", "抱抱", "难过", "委屈", "哭"),
    "摸鱼": ("摸鱼", "下班", "偷懒", "摆烂", "躺"),
    "任务": ("任务", "工作", "加班", "干活"),
    "关心": ("关心", "担心", "放心"),
    "互动": ("互动", "一起", "陪", "贴贴"),
}


def derive_scenes(tags: list[str], use: str, text: str) -> list[str]:
    blob = " ".join([*tags, use, text])
    scenes = ["群聊", "私聊"]
    for scene, kws in SCENE_HINTS.items():
        if any(k in blob for k in kws):
            scenes.append(scene)
    return scenes[:5]


def main() -> int:
    data = json.loads(INDEX.read_text(encoding="utf-8"))
    items = data["items"]

    annot: dict[str, dict] = {}
    if ANNOT.exists():
        for line in ANNOT.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                annot[row["id"]] = row
    print(f"标注 {len(annot)} 条")

    merged, updated = [], 0
    order = {}
    n = 0
    for it in items:
        if it.get("pack") == "meme":
            n += 1
            order[it["file"]] = f"m{n:03d}"
    for it in items:
        sid = order.get(it["file"], "")
        a = annot.get(sid)
        if it.get("pack") == "meme" and a:
            mood, intensity = EMO_MAP.get(str(a.get("emotion") or ""), DEFAULT)
            tags = [str(t) for t in (a.get("tags") or [])][:8] or [mood]
            scenes = derive_scenes(tags, str(a.get("use") or ""), str(a.get("text") or ""))
            safety = "mild" if mood in ("嘲讽",) or "阴阳" in " ".join(tags) else it.get("safety", "safe")
            it = {
                **it,
                "mood": mood,
                "intensity": intensity,
                "tags": tags,
                "scenes": scenes,
                "safety": safety,
                "emotion": a.get("emotion") or "",
                "use": a.get("use") or "",
                "desc": a.get("desc") or "",
                "meme": a.get("meme") or "",
                "text": a.get("text") or "",
                "en": a.get("en") or "",
            }
            updated += 1
        else:
            it.setdefault("text", "")
            it.setdefault("en", "")
        merged.append(it)

    INDEX.write_text(json.dumps({"version": 3, "items": merged}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"合并完成：{len(merged)} 条（重标 {updated} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
