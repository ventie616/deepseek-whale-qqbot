"""按 nekowhale 的方法重标已有表情：联系表 -> 多模态逐格标注 -> JSONL。

用法：
    python tools/annotate_stickers.py            # 续跑（已标注的跳过）
    python tools/annotate_stickers.py --limit 8  # 只跑前几张表
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import pathlib
import random
import re

import urllib.request

ROOT = pathlib.Path(r"C:\QQBot")
STICKERS = ROOT / "stickers"
ANNOT = STICKERS / "_annot"
OUT = ANNOT / "annot.jsonl"
BATCH = ANNOT / "batch.json"
CFG = ROOT / "astrbot" / "data" / "cmd_config.json"

EMOTIONS = (
    "开心 大笑 喜欢 害羞 得意 兴奋 惊讶 疑惑 思考 无语 难过 哭 生气 害怕 疲惫 "
    "赞同 拒绝 打招呼 感谢 道歉 卖萌 搞怪 嘲讽 安慰 其他"
).split()

SYSTEM = """你在给聊天机器人标注表情包，目的是让她在对话里挑对图。

一张图里有 4 格，每格左上角红字是 id。对每格输出一行 JSON，键顺序固定：
{"id":"<红字id>", "text":"画面里画出来的文字，原样，没有就空字符串", "emotion":"<从词表里选一个>",
 "tags":["4~8个短标签，写什么时候用它：场景/情绪/意图，比如 下班 摸鱼 求夸 阴阳怪气"],
 "use":"一句话，不超过30字：最适合用在哪种聊天场景", "desc":"40~100字：画面发生了什么，具体动作和表情",
 "meme":"它玩的梗/名言/番剧/游戏/网络文化出处；不确定就空字符串，绝不编造", "en":"不超过8个词的英文概括"}

emotion 只能取这些之一：""" + " ".join(EMOTIONS) + """

角色：大肥鱼是 DeepSeek 的鲸鱼拟人（深蓝长发、呆毛、女仆头饰、蓝眼睛）。
tags 要帮检索，优先写使用场景和情绪，不要写"蓝色头发"这种外观细节。
只输出 4 行 JSON，不要代码块，不要解释。"""


def provider_cfg() -> dict:
    cfg = json.loads(CFG.read_text(encoding="utf-8-sig"))
    src = cfg["provider_sources"][0]
    key = src["key"]
    if isinstance(key, list):
        key = key[0]
    return {"base": src["api_base"].rstrip("/"), "key": str(key).strip().strip("'").strip('"')}


def call(cfg: dict, image_b64: str, ids: list[str]) -> str:
    body = json.dumps({
        "model": "deepseek-flash",
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": [
                {"type": "text", "text": "这张联系表里的 4 格 id 是：" + "、".join(ids) + "。请逐格标注。"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
            ]},
        ],
        "max_tokens": 1800,
    }).encode()
    req = urllib.request.Request(
        cfg["base"] + "/chat/completions", data=body,
        headers={"Authorization": f"Bearer {cfg['key']}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        d = json.loads(r.read())
    return (d["choices"][0]["message"].get("content") or "").strip()


def parse_rows(raw: str, want: list[str]) -> list[dict]:
    rows = []
    for line in raw.splitlines():
        line = line.strip().strip(",")
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if obj.get("id") not in want:
            continue
        emo = str(obj.get("emotion") or "").strip()
        if emo not in EMOTIONS:
            obj["emotion"] = "其他"
        for k in ("text", "use", "desc", "meme", "en", "dir"):
            obj.setdefault(k, "")
        if not isinstance(obj.get("tags"), list):
            obj["tags"] = []
        obj["tags"] = [str(t)[:12] for t in obj["tags"]][:8]
        rows.append({k: obj[k] for k in ("id", "text", "emotion", "tags", "use", "desc", "meme", "en")})
    return rows


def known_ids() -> set[str]:
    if not OUT.exists():
        return set()
    ids = set()
    for line in OUT.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            ids.add(json.loads(line)["id"])
        except Exception:
            continue
    return ids


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    cfg = provider_cfg()
    batches = json.loads(BATCH.read_text(encoding="utf-8"))
    done = known_ids()
    todo = []
    for b in batches:
        ids = [i["id"] for i in b["items"]]
        if all(i in done for i in ids):
            continue
        todo.append((b, ids))
    if args.limit:
        todo = todo[: args.limit]
    print(f"待标注联系表 {len(todo)} 张（已有 {len(done)} 条）")

    lock = asyncio.Lock()
    sem = asyncio.Semaphore(3)
    written = 0

    async def work(b: dict, ids: list[str]) -> None:
        nonlocal written
        async with sem:
            img = base64.b64encode(pathlib.Path(b["sheet"]).read_bytes()).decode()
            for _ in range(3):
                try:
                    raw = await asyncio.to_thread(call, cfg, img, ids)
                    rows = parse_rows(raw, ids)
                    missing = [i for i in ids if i not in {r["id"] for r in rows}]
                    if not missing:
                        async with lock:
                            with OUT.open("a", encoding="utf-8") as f:
                                for r in rows:
                                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                            written += len(rows)
                        print(f"  {pathlib.Path(b['sheet']).name}: {len(rows)} 条")
                        return
                except Exception as exc:  # noqa: BLE001
                    print(f"  {pathlib.Path(b['sheet']).name} 重试：{type(exc).__name__} {str(exc)[:60]}")
                await asyncio.sleep(1.5)
            print(f"  {pathlib.Path(b['sheet']).name}: 失败，跳过")

    await asyncio.gather(*(work(b, ids) for b, ids in todo))
    print(f"\n本次写入 {written} 条 -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
