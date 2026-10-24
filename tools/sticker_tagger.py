"""给表情包打五维标签（调 DeepSeek 视觉），供导入脚本和补索引脚本共用。

用法（命令行）：
    python tools/sticker_tagger.py --dry            # 只打印，不写
    python tools/sticker_tagger.py                  # 补齐索引 + 归类目录
"""

from __future__ import annotations

import argparse
import base64
import json
import pathlib
import sys
import urllib.request

LIB = pathlib.Path(r"C:\QQBot\stickers")
INDEX = LIB / "index.json"
CONFIG = pathlib.Path(r"C:\QQBot\astrbot\data\cmd_config.json")
EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# 主情绪 -> 目录（只用于文件归档；选图靠 index.json）
FOLDER = {
    "生气": "angry", "暴怒": "angry", "抓狂": "angry", "嘲讽": "angry",
    "吐槽": "angry", "摆烂": "angry", "搞笑": "angry",
    "难过": "sad", "委屈": "sad", "破防": "sad", "卖惨": "sad",
    "含泪": "sad", "忧郁": "sad", "尴尬": "sad",
    "害羞": "shy", "媚": "shy",
    "困": "sleepy", "累": "sleepy", "无语": "sleepy",
    "开心": "happy", "大笑": "happy", "满足": "happy", "得意": "happy",
    "卖萌": "happy", "沙雕": "happy", "平静": "happy",
}

PROMPT = (
    "这是一张 QQ 表情包。按这个 JSON 格式输出，不要解释：\n"
    '{"mood":"主情绪，中文两字，如 开心/生气/难过/害羞/困/嘲讽/无语/得意/委屈",'
    '"tags":["2到4个中文短标签"],"intensity":1到5的整数,'
    '"scenes":["1到3个使用场景，如 被夸/被骂/晚安/群聊玩梗/撒娇"],'
    '"safety":"safe 或 mild 或 risky（含脏话、擦边、攻击性写 risky）"}'
)


def _key() -> tuple[str, str]:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    src = cfg["provider_sources"][0]
    key = src["key"]
    if isinstance(key, list):
        key = key[0]
    return str(key).strip().strip("'").strip('"'), src["api_base"].rstrip("/")


def tag_image(path: pathlib.Path) -> dict:
    key, base = _key()
    b64 = base64.b64encode(path.read_bytes()).decode()
    ext = path.suffix.lower().lstrip(".") or "png"
    body = json.dumps(
        {
            "model": "deepseek-flash",
            "messages": [
                {"role": "system", "content": "你在给表情包打标签，只输出 JSON。"},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": PROMPT},
                        {"type": "image_url", "image_url": {"url": f"data:image/{ext};base64,{b64}"}},
                    ],
                },
            ],
            "max_tokens": 1200,
        }
    ).encode()
    req = urllib.request.Request(
        base + "/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    d = json.loads(urllib.request.urlopen(req, timeout=120).read())
    raw = (d["choices"][0]["message"].get("content") or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < 0:
        raise ValueError(f"模型没吐 JSON：{raw[:80]}")
    data = json.loads(raw[start:end + 1])
    safety = str(data.get("safety") or "safe")
    return {
        "mood": str(data.get("mood") or "开心")[:6],
        "tags": [str(t)[:8] for t in (data.get("tags") or [])][:4] or ["未分类"],
        "intensity": max(1, min(5, int(data.get("intensity") or 3))),
        "scenes": [str(s)[:10] for s in (data.get("scenes") or [])][:3] or ["闲聊"],
        "safety": safety if safety in ("safe", "mild", "risky") else "safe",
    }


def load_index() -> dict:
    try:
        return json.loads(INDEX.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "items": []}


def save_index(data: dict) -> None:
    INDEX.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def list_files() -> list[pathlib.Path]:
    out = []
    for d in sorted(LIB.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        for p in sorted(d.iterdir()):
            if p.is_file() and p.suffix.lower() in EXT and not p.name.startswith("_"):
                out.append(p)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    data = load_index()
    indexed = {e["file"] for e in data.get("items", [])}
    files = list_files()
    missing = [p for p in files if f"{p.parent.name}/{p.name}" not in indexed]
    if args.limit:
        missing = missing[: args.limit]
    print(f"库内 {len(files)} 张，索引 {len(indexed)} 条，待补 {len(missing)} 张")

    ok = fail = 0
    for i, p in enumerate(missing, 1):
        try:
            meta = tag_image(p)
        except Exception as exc:  # noqa: BLE001
            print(f"  [{i}/{len(missing)}] 失败 {p.name}: {exc}")
            fail += 1
            continue

        folder = FOLDER.get(meta["mood"], "misc")
        target = p
        if not args.dry and folder != p.parent.name:
            target = LIB / folder / p.name
            target.parent.mkdir(parents=True, exist_ok=True)
            p.rename(target)

        rel = f"{target.parent.name}/{target.name}"
        if not args.dry:
            data.setdefault("items", []).append({"file": rel, **meta})
            save_index(data)
        ok += 1
        print(
            f"  [{i}/{len(missing)}] {rel} -> {meta['mood']} "
            f"强度{meta['intensity']} {meta['safety']} {meta['tags']}"
        )

    if not args.dry and ok:
        save_index(data)
        print(f"\n已补 {ok} 条，失败 {fail} 条，索引现在 {len(data['items'])} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
