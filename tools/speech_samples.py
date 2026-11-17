"""从真实聊天记录里采样「人说话的样子」。

不是假想人类怎么说话，而是把你们实际聊过的句子抽出来，供人格与上下文参考。
输出：D:\QQrobot\her_room\speech_samples.md（只保留句子，不留账号）
"""

from __future__ import annotations

import json
import pathlib
import random
import re

ROOT = pathlib.Path(r"D:\QQrobot")
ARCHIVE = ROOT / "chat_archive"
OUT = ROOT / "her_room" / "speech_samples.md"

# 明显是测试/指令的句子不要
SKIP_PAT = re.compile(r"^(/|@|#sl)|^(在吗|test|测试)$")
BAD = ("http://", "https://", "[图片]", "[表情]", "[语音]", "[视频]", "[文件]", "[引用消息]")
MIN_LEN, MAX_LEN = 2, 80


def _clean(text: str) -> str:
    t = text.replace("\n", " ").strip()
    t = re.sub(r"\s+", " ", t)
    return t


def collect(limit: int = 400) -> list[str]:
    rows: list[tuple[str, str]] = []
    for f in ARCHIVE.rglob("*.jsonl"):
        if "webchat" in f.as_posix():      # 控制台测试对话不算真实语料
            continue
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("role") != "user":
                continue
            uid = str(d.get("uid") or "")
            name = str(d.get("name") or "")
            if uid in ("self", "astrbot") or name in ("本鲸", "astrbot"):
                continue
            text = _clean(str(d.get("text") or ""))
            if not text or not (MIN_LEN <= len(text) <= MAX_LEN):
                continue
            if any(b in text for b in BAD) or SKIP_PAT.search(text):
                continue
            rows.append((str(d.get("t") or ""), text))

    rows.sort(key=lambda r: r[0])
    seen, out = set(), []
    for _, text in rows:
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out[-limit:]


def build(limit: int = 400) -> list[str]:
    samples = collect(limit)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    body = ["# 他平时怎么说话（真实采样，非臆想）", ""]
    body += [f"- {s}" for s in samples]
    OUT.write_text("\n".join(body) + "\n", encoding="utf-8")
    return samples


def pick(n: int = 6) -> list[str]:
    """随机取几句，给上下文做说话风格参考。"""
    try:
        samples = collect(200)
    except Exception:
        samples = []
    if not samples:
        return []
    return random.sample(samples, min(n, len(samples)))


if __name__ == "__main__":
    got = build()
    print(f"采样 {len(got)} 句 → {OUT}")
    for s in got[:8]:
        print("  ", s)
