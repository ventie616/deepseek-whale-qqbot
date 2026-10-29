"""聊天存档：把群聊/私聊原文按天追加到 JSONL，零 token 成本。

目录结构：
  D:\\QQrobot\\chat_archive\\<平台_会话类型_会话号>\\<YYYY-MM-DD>.jsonl

每行：{"t": "2026-10-22T23:40:01", "role": "user|bot", "uid": "...", "name": "...", "text": "..."}

另外提供 search_chat 工具，让她能翻回之前的对话（按关键词 + 天数）。
保留天数默认 30 天，启动时自动清理。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain

import json

ROOT = Path(r"D:\QQrobot\chat_archive")
KEEP_DAYS = 30
MAX_TEXT = 600


def _safe(name: str) -> str:
    return re.sub(r"[^\w\-]+", "_", name).strip("_")[:80] or "unknown"


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        super().__init__(context)
        self._cleanup()

    def _cleanup(self) -> None:
        try:
            if not ROOT.is_dir():
                return
            cutoff = datetime.now() - timedelta(days=KEEP_DAYS)
            removed = 0
            for f in ROOT.rglob("*.jsonl"):
                try:
                    day = datetime.strptime(f.stem, "%Y-%m-%d")
                except ValueError:
                    continue
                if day < cutoff:
                    f.unlink(missing_ok=True)
                    removed += 1
            if removed:
                logger.info(f"[chat_archive] 清理 {removed} 个过期存档")
        except OSError:
            pass

    def _path(self, umo: str) -> Path:
        d = ROOT / _safe(umo)
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{datetime.now():%Y-%m-%d}.jsonl"

    def _append(self, umo: str, role: str, uid: str, name: str, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        rec = {
            "t": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "role": role,
            "uid": uid,
            "name": name,
            "text": text[:MAX_TEXT],
        }
        try:
            with self._path(umo).open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.info(f"[chat_archive] 写入失败：{exc}")

    # ---------------- 记录 ----------------
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def archive_incoming(self, event: AstrMessageEvent) -> None:
        try:
            text = event.get_message_str() or ""
        except Exception:
            return
        if not text.strip():
            return
        self._append(
            event.unified_msg_origin,
            "user",
            str(event.get_sender_id() or ""),
            event.get_sender_name() or "",
            text,
        )

    @filter.on_decorating_result()
    async def archive_outgoing(self, event: AstrMessageEvent) -> None:
        try:
            result = event.get_result()
            if result is None or not result.chain:
                return
            text = "".join(c.text for c in result.chain if isinstance(c, Plain)).strip()
            if text:
                self._append(event.unified_msg_origin, "bot", "self", "本鲸", text)
        except Exception:
            pass

    # ---------------- 检索 ----------------
    @filter.llm_tool(name="search_chat")
    async def search_chat(self, event: AstrMessageEvent, keyword: str = "", days: int = 7):
        """翻聊天存档，查之前谁说过什么。想知道"上次聊到的事"时用。

        Args:
            keyword(string): 搜索关键词，留空则返回最近的记录
            days(number): 往前查几天，默认 7
        """
        umo = event.unified_msg_origin
        days = max(1, min(30, int(days or 7)))
        d = ROOT / _safe(umo)
        if not d.is_dir():
            return "这个会话还没有存档"
        cutoff = datetime.now() - timedelta(days=days)
        hits: list[dict] = []
        for f in sorted(d.glob("*.jsonl"), reverse=True):
            try:
                day = datetime.strptime(f.stem, "%Y-%m-%d")
            except ValueError:
                continue
            if day < cutoff:
                continue
            try:
                for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                    if not line.strip():
                        continue
                    rec = json.loads(line)
                    if not keyword or keyword in rec.get("text", ""):
                        hits.append(rec)
            except Exception:
                continue
            if len(hits) >= 200:
                break
        if not hits:
            return f"最近 {days} 天没有找到「{keyword or '任何内容'}」"
        hits = hits[-12:]
        lines = [f"{h['t'][5:16].replace('T', ' ')} {h['name']}：{h['text'][:80]}" for h in hits]
        return "\n".join(lines)
