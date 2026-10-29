"""唱歌彩蛋：节选走语音、整首走文件。

素材库（voice_clips/index.json v2）：
  clips/  12 秒节选（默认发这个）
  full/   整首（只有明确说"整首/完整"时才发）

发送策略：
- 节选 → Record（SnowLuma 自动转 silk）
- 整首 → File（语音消息有时长上限，整首几 MB 发语音会失败）
- 顺序：等文字发完（after_message_sent）再发音频，避免"语音抢在文字前面"
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from datetime import datetime
from pathlib import Path

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import File, Record
from astrbot.core.message.message_event_result import MessageChain

LIB = Path(r"D:\QQrobot\voice_clips")
INDEX = LIB / "index.json"
ARCHIVE = Path(r"D:\QQrobot\chat_archive")
PENDING_KEY = "pending_clip"
FULL_HINTS = ("整首", "完整", "全曲", "全长")


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        super().__init__(context)

    # ---------------- 数据 ----------------
    def _index(self) -> dict:
        try:
            data = json.loads(INDEX.read_text(encoding="utf-8"))
            if "clips" not in data and isinstance(data.get("items"), list):
                data = {"clips": data["items"], "fulls": []}
            clips = [{**c, "path": str(LIB / c["file"])} for c in data.get("clips") or []
                     if (LIB / c.get("file", "")).is_file()]
            fulls = [{**c, "path": str(LIB / c["file"])} for c in data.get("fulls") or []
                     if (LIB / c.get("file", "")).is_file()]
            return {"clips": clips, "fulls": fulls}
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[clips] 索引读取失败：{exc}")
            return {"clips": [], "fulls": []}

    def _pick(self, want: str, full: bool) -> tuple[dict | None, str]:
        data = self._index()
        pool = data["fulls"] if full else data["clips"]
        if not pool:
            return None, "素材库是空的"
        want = (want or "").strip()
        for h in FULL_HINTS:
            if h in want:
                want = want.replace(h, "").strip()
        if want:
            pool = [c for c in pool if want in str(c.get("song"))]
            if not pool:
                names = "、".join(sorted({str(c.get("song")) for c in data["clips"]}))
                return None, f"没有「{want}」，有的歌是：{names}"
        return random.choice(pool), ""

    # ---------------- 发送 ----------------
    async def _send_audio(self, umo: str, info: dict) -> bool:
        path = Path(info.get("path", ""))
        if not path.is_file():
            return False
        is_full = info.get("type") == "full"
        try:
            if is_full:
                ok = await self.context.send_message(
                    umo, MessageChain([File(name=path.name, file=str(path))])
                )
            else:
                ok = await self.context.send_message(
                    umo, MessageChain([Record.fromFileSystem(str(path))])
                )
            if ok:
                self._archive(umo, info)
                return True
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[clips] 发送失败，改发文件：{exc}")
        try:
            ok = await self.context.send_message(
                umo, MessageChain([File(name=path.name, file=str(path))])
            )
            if ok:
                self._archive(umo, info)
            return bool(ok)
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[clips] 文件发送也失败：{exc}")
            return False

    def _archive(self, umo: str, info: dict) -> None:
        try:
            d = ARCHIVE / re.sub(r"[^\w\-]+", "_", umo).strip("_")[:80]
            d.mkdir(parents=True, exist_ok=True)
            kind = "整首" if info.get("type") == "full" else "一段"
            rec = {
                "t": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "role": "bot",
                "uid": "self",
                "name": "本鲸",
                "text": f"（唱了{kind}）《{info.get('song')}》",
            }
            with (d / f"{datetime.now():%Y-%m-%d}.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass

    # ---------------- 钩子 ----------------
    @filter.after_message_sent()
    async def send_pending(self, event: AstrMessageEvent) -> None:
        info = event.get_extra(PENDING_KEY)
        if not info:
            return
        event.set_extra(PENDING_KEY, None)
        await asyncio.sleep(0.6)
        await self._send_audio(event.unified_msg_origin, info)

    async def _fallback(self, event: AstrMessageEvent, delay: int = 15) -> None:
        await asyncio.sleep(delay)
        info = event.get_extra(PENDING_KEY)
        if not info:
            return
        event.set_extra(PENDING_KEY, None)
        logger.info("[clips] after 钩子没触发，直接补发")
        await self._send_audio(event.unified_msg_origin, info)

    def _queue(self, event: AstrMessageEvent, info: dict) -> None:
        event.set_extra(PENDING_KEY, info)
        asyncio.create_task(self._fallback(event))

    # ---------------- 指令 / 工具 ----------------
    @filter.command("唱一段")
    async def sing(self, event: AstrMessageEvent, song: str = ""):
        """唱一段（默认节选；说「整首」才发完整）"""
        full = any(h in (song or "") for h in FULL_HINTS)
        info, err = self._pick(song, full=full)
        if info is None:
            yield event.plain_result(err)
            return
        self._queue(event, info)
        if full:
            yield event.plain_result(
                f"整首《{info.get('song')}》给你，{info.get('size_kb', 0)//1024} MB，慢慢听"
            )
        else:
            yield event.plain_result(f"喏 唱一段…《{info.get('song')}》")

    @filter.command("歌单")
    async def song_list(self, event: AstrMessageEvent):
        data = self._index()
        if not data["clips"]:
            yield event.plain_result("素材库是空的")
            return
        songs: dict[str, dict] = {}
        for c in data["clips"]:
            k = str(c.get("song"))
            songs.setdefault(k, {"clips": 0, "full": False})
            songs[k]["clips"] += 1
        for f in data["fulls"]:
            songs.setdefault(str(f.get("song")), {"clips": 0, "full": False})["full"] = True
        lines = [
            f"· {k}（{v['clips']} 段节选{'｜有整首' if v['full'] else ''}）"
            for k, v in songs.items()
        ]
        yield event.plain_result(
            "彩蛋曲库：\n" + "\n".join(lines) + "\n\n/唱一段 或 /唱一段 歌名；加「整首」发完整版"
        )

    @filter.llm_tool(name="sing_a_clip")
    async def sing_tool(self, event: AstrMessageEvent, song: str = "", full: bool = False):
        """给主人唱歌。本机已有素材，优先用这个；默认发 12 秒节选，主人说"整首/完整版"时才发全长。

        Args:
            song(string): 歌名关键词，留空随机
            full(boolean): 是否发整首，默认 False
        """
        info, err = self._pick(song, full=bool(full))
        if info is None:
            return err
        self._queue(event, info)
        kind = "整首" if info.get("type") == "full" else "一段"
        return f"要唱{kind}《{info.get('song')}》，跟主人说一句再放"
