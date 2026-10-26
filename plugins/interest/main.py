"""她最近感兴趣什么：从看过的视频里攒话题，攒够了就给判据/聊天用。"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(r"C:\QQBot\tools")))
try:
    from interest import PROFILE, build_profile, topic_hint  # noqa: E402
except Exception:  # pragma: no cover
    PROFILE = Path(r"C:\QQBot\interest_profile.json")
    build_profile = None
    topic_hint = lambda top=6: ""  # noqa: E731

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter

REBUILD_EVERY = 4 * 3600
MIN_SAMPLES = 40


def _load() -> dict:
    try:
        return json.loads(PROFILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        super().__init__(context)
        self._stop = False
        self._task: asyncio.Task | None = None
        self._profile: dict = {}

    # ---------------- 画像 ----------------
    def _rebuild(self, force: bool = False) -> dict:
        if build_profile is None:
            return {}
        old = _load()
        if not force and old.get("updated"):
            try:
                age = time.time() - time.mktime(
                    time.strptime(old["updated"], "%Y-%m-%d %H:%M")
                )
                if 0 <= age < REBUILD_EVERY:
                    self._profile = old
                    return old
            except Exception:
                pass
        profile = build_profile(days=14, min_samples=MIN_SAMPLES)
        self._profile = profile
        if profile.get("enough"):
            names = "、".join(t["topic"] for t in profile["topics"][:6])
            logger.info(f"[interest] 画像更新：{profile['samples']} 条样本 → {names}")
        else:
            logger.info(
                f"[interest] 样本不足（{profile.get('samples', 0)}/{MIN_SAMPLES}），暂不启用"
            )
        return profile

    async def _loop(self) -> None:
        await asyncio.sleep(180)
        while not self._stop:
            try:
                await asyncio.to_thread(self._rebuild)
            except Exception as exc:  # noqa: BLE001
                logger.info(f"[interest] 更新失败：{exc}")
            await asyncio.sleep(1800)

    async def initialize(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def terminate(self) -> None:
        self._stop = True
        if self._task:
            self._task.cancel()

    # ---------------- 注入 ----------------
    @filter.on_llm_request()
    async def inject_interest(self, event: AstrMessageEvent, req) -> None:
        hint = topic_hint(6)
        if not hint:
            return
        req.system_prompt += (
            f"\n\n【你最近最感兴趣的话题】{hint}\n"
            "（这是从你自己刷过、评论过的视频里总结的，聊到相关的东西可以自然提起，"
            "别当成清单念出来。）"
        )

    # ---------------- 指令 ----------------
    @filter.command("兴趣")
    async def interest_cmd(self, event: AstrMessageEvent):
        """看她最近对什么感兴趣"""
        p = await asyncio.to_thread(self._rebuild)
        if not p:
            yield event.plain_result("兴趣画像没算出来")
            return
        if not p.get("enough"):
            yield event.plain_result(
                f"样本还不够：{p.get('samples', 0)}/{p.get('need', MIN_SAMPLES)} 条\n"
                "多刷几天 B站 就有了"
            )
            return
        lines = [f"最近 {p['days']} 天看了 {p['samples']} 条，最感兴趣的是："]
        for i, t in enumerate(p["topics"][:8], 1):
            lines.append(f"{i}. {t['topic']}（权重 {t['weight']}）")
        yield event.plain_result("\n".join(lines))

    # ---------------- 工具 ----------------
    @filter.llm_tool(name="my_interests")
    async def my_interests_tool(self, event: AstrMessageEvent):
        """查自己最近对什么感兴趣（从刷过的视频里总结）。"""
        p = await asyncio.to_thread(self._rebuild)
        if not p.get("enough"):
            return "样本还不够，我还没总结出自己最近喜欢什么"
        names = "、".join(t["topic"] for t in p["topics"][:8])
        return f"最近 {p['days']} 天看了 {p['samples']} 条，我最近感兴趣的是：{names}"
