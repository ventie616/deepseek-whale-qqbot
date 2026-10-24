"""服务守护通知：AstrBot 被守护进程拉起后，主动私聊告诉你一声。

守护进程在拉起前会写 C:\\QQBot\\watchdog_restart.json；
本插件在 initialize 时读它、发消息、再删掉标记。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain
from astrbot.core.message.message_event_result import MessageChain

ROOT = Path(r"C:\QQBot")
FLAG = ROOT / "watchdog_restart.json"
HEARTBEAT = ROOT / "watchdog_heartbeat.json"
PUSH_CFG = ROOT / "bili_push.json"


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        super().__init__(context)

    def _target(self) -> str:
        try:
            return str(json.loads(PUSH_CFG.read_text(encoding="utf-8")).get("target_qq") or "")
        except Exception:
            return ""

    def _umo(self, qq: str) -> str | None:
        if not qq:
            return None
        try:
            for platform in self.context.platform_manager.platform_insts:
                if platform.meta().name == "aiocqhttp":
                    return f"{platform.meta().id}:FriendMessage:{qq}"
        except Exception:
            return None
        return None

    async def initialize(self) -> None:
        if not FLAG.exists():
            return
        try:
            info = json.loads(FLAG.read_text(encoding="utf-8"))
        except Exception:
            info = {}
        asyncio.create_task(self._notify(info))

    async def _notify(self, info: dict) -> None:
        """等平台连上再发（重启后 OneBot 连接要几秒到几十秒）。"""
        at = info.get("at") or "未知时间"
        reason = info.get("reason") or "未知原因"
        text = (
            f"刚被守护脚本拉起来了（{at} 掉的，原因：{reason}）。\n"
            "现在正常，刚才那段时间的消息可能没回。"
        )
        for _ in range(60):
            umo = self._umo(self._target())
            if umo:
                try:
                    ok = await self.context.send_message(umo, MessageChain([Plain(text)]))
                    if ok:
                        logger.info(f"[guard] 已通知主人：{at} 掉线，已恢复")
                        FLAG.unlink(missing_ok=True)
                        return
                except Exception as exc:  # noqa: BLE001
                    logger.info(f"[guard] 发送失败，重试：{exc}")
            await asyncio.sleep(3)
        logger.info("[guard] 等了 180 秒仍发不出去，放弃通知")
        FLAG.unlink(missing_ok=True)

    @filter.command("守护状态")
    async def guard_status(self, event: AstrMessageEvent):
        """看守护进程是否还活着"""
        if not HEARTBEAT.exists():
            yield event.plain_result("守护脚本没在跑（找不到心跳文件）")
            return
        try:
            hb = json.loads(HEARTBEAT.read_text(encoding="utf-8"))
        except Exception:
            yield event.plain_result("心跳文件读不出来")
            return
        try:
            age = (datetime.now() - datetime.fromisoformat(hb.get("at"))).total_seconds()
        except Exception:
            age = 9999
        state = "已暂停" if hb.get("paused") else ("AstrBot 在线" if hb.get("port_up") else "AstrBot 离线")
        yield event.plain_result(
            f"守护：{'正常' if age < 120 else '心跳过期'}\n"
            f"最后心跳：{hb.get('at')}（{int(age)} 秒前）\n"
            f"状态：{state}｜近期重启 {hb.get('restarts_recent', 0)} 次"
        )
