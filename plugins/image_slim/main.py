"""历史瘦身：把会话历史里存成 base64 的图片换成一行文字。

问题：图片按 `image_url` + base64 存进会话历史，之后每一轮都重新发一遍
（视觉 token 按图算），历史还会膨胀到几百 KB。

做法：每 60 秒直接扫一次 sqlite 的 conversations 表，
把 `image_url` 部件替换成占位文字。当次请求该看图还是看了，只是不再留在历史里反复付费。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

from astrbot.api import logger, star

DB = Path(r"C:\QQBot\astrbot\data\data_v4.db")
PLACEHOLDER = "[之前看过的一张图，内容已不在上下文里]"


def _strip(content: str) -> tuple[str, int]:
    try:
        msgs = json.loads(content)
    except Exception:
        return content, 0
    if not isinstance(msgs, list):
        return content, 0
    n = 0
    for m in msgs:
        if not isinstance(m, dict):
            continue
        parts = m.get("content")
        if not isinstance(parts, list):
            continue
        new = []
        for part in parts:
            if isinstance(part, dict) and part.get("type") in ("image_url", "image"):
                new.append({"type": "text", "text": PLACEHOLDER})
                n += 1
            else:
                new.append(part)
        m["content"] = new
    if not n:
        return content, 0
    return json.dumps(msgs, ensure_ascii=False), n


def _sweep_sync() -> int:
    if not DB.exists():
        return 0
    total = 0
    try:
        con = sqlite3.connect(str(DB), timeout=5)
        con.execute("PRAGMA busy_timeout = 4000")
        rows = list(con.execute("SELECT conversation_id, content FROM conversations"))
        with con:
            for cid, content in rows:
                if not content:
                    continue
                s = content if isinstance(content, str) else content.decode("utf-8", "replace")
                if "image_url" not in s:
                    continue
                new, n = _strip(s)
                if n:
                    con.execute(
                        "UPDATE conversations SET content = ? WHERE conversation_id = ?",
                        (new, cid),
                    )
                    total += n
        con.close()
    except sqlite3.Error as exc:
        logger.info(f"[image_slim] sqlite 异常：{exc}")
        return 0
    return total


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        super().__init__(context)
        self._stop = False
        self._task: asyncio.Task | None = None

    async def _loop(self) -> None:
        await asyncio.sleep(15)
        while not self._stop:
            try:
                n = await asyncio.to_thread(_sweep_sync)
                if n:
                    logger.info(f"[image_slim] 清掉历史里的 {n} 张图（换成占位文字）")
            except Exception as exc:  # noqa: BLE001
                logger.info(f"[image_slim] 异常：{exc}")
            await asyncio.sleep(60)

    async def initialize(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def terminate(self) -> None:
        self._stop = True
        if self._task:
            self._task.cancel()
