"""服务守护通知：把"刚被拉起"告诉主人。

关键设计：**通知发不出去时不能丢**。
2026-10-24 真实事故：守护 15:11 拉起了 AstrBot，但当时 SnowLuma/QQ 也没起来，
通知重试 180 秒后放弃，主人完全不知道——所以改成"排队 + 反复重试直到送达"。

- 待通知标记：D:\\QQrobot\\watchdog_restart.json（守护写）
- 待送达队列：D:\\QQrobot\\guard_pending.json（本插件维护，成功送达才删除）
- 每 2 分钟尝试一次；超过 24 小时未送达则丢弃并记日志
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain
from astrbot.core.message.message_event_result import MessageChain

ROOT = Path(r"D:\QQrobot")
FLAG = ROOT / "watchdog_restart.json"
PENDING = ROOT / "guard_pending.json"
HEARTBEAT = ROOT / "watchdog_heartbeat.json"
PUSH_CFG = ROOT / "bili_push.json"
MAX_PENDING = 20
MAX_AGE_HOURS = 24


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        super().__init__(context)
        self._task: asyncio.Task | None = None
        self._stop = False

    # ---------------- 队列 ----------------
    def _load_pending(self) -> list[dict]:
        try:
            data = json.loads(PENDING.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _save_pending(self, items: list[dict]) -> None:
        try:
            PENDING.write_text(
                json.dumps(items[-MAX_PENDING:], ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            logger.info(f"[guard] 队列写入失败：{exc}")

    def _drain_flag(self) -> None:
        """把守护写的标记搬进队列。"""
        if not FLAG.exists():
            return
        try:
            info = json.loads(FLAG.read_text(encoding="utf-8"))
        except Exception:
            info = {}
        FLAG.unlink(missing_ok=True)
        at = info.get("at") or datetime.now().isoformat(timespec="seconds")
        reason = info.get("reason") or "未知原因"
        items = self._load_pending()
        items.append(
            {
                "at": at,
                "reason": reason,
                "tries": 0,
                "queued": datetime.now().isoformat(timespec="seconds"),
            }
        )
        self._save_pending(items)
        logger.info(f"[guard] 收到待通知：{reason}")

    # ---------------- 发送 ----------------
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

    async def _try_send(self) -> None:
        items = self._load_pending()
        if not items:
            return
        umo = self._umo(self._target())
        if not umo:
            return
        now = datetime.now()
        kept: list[dict] = []
        for it in items:
            try:
                age = now - datetime.fromisoformat(it.get("queued") or now.isoformat())
            except Exception:
                age = timedelta(0)
            if age > timedelta(hours=MAX_AGE_HOURS):
                logger.info(f"[guard] 通知超过 24 小时未送达，丢弃：{it.get('reason')}")
                continue
            text = (
                f"刚被守护脚本拉起来了（{it.get('at')} 掉的，原因：{it.get('reason')}）。\n"
                "现在正常，刚才那段时间的消息可能没回。"
            )
            try:
                ok = await self.context.send_message(umo, MessageChain([Plain(text)]))
            except Exception as exc:  # noqa: BLE001
                logger.info(f"[guard] 发送异常（第 {int(it.get('tries', 0)) + 1} 次）：{exc}")
                ok = False
            if ok:
                logger.info(f"[guard] 已送达：{it.get('at')} 掉线通知")
                continue
            it["tries"] = int(it.get("tries", 0)) + 1
            kept.append(it)
            logger.info(f"[guard] 发送失败，留在队列（第 {it['tries']} 次）：{it.get('reason')}")
        self._save_pending(kept)

    async def _loop(self) -> None:
        await asyncio.sleep(20)
        while not self._stop:
            try:
                self._drain_flag()
                await self._try_send()
            except Exception as exc:  # noqa: BLE001
                logger.info(f"[guard] 循环异常：{exc}")
            await asyncio.sleep(120)

    async def initialize(self) -> None:
        self._drain_flag()
        self._task = asyncio.create_task(self._loop())

    async def terminate(self) -> None:
        self._stop = True
        if self._task:
            self._task.cancel()

    # ---------------- 指令 ----------------
    @filter.command("守护状态")
    async def guard_status(self, event: AstrMessageEvent):
        """看守护进程、各服务和待通知队列"""
        lines = []
        if HEARTBEAT.exists():
            try:
                hb = json.loads(HEARTBEAT.read_text(encoding="utf-8"))
            except Exception:
                hb = {}
            try:
                age = (datetime.now() - datetime.fromisoformat(hb.get("at"))).total_seconds()
            except Exception:
                age = 9999
            state = "已暂停" if hb.get("paused") else ("正常" if age < 120 else "心跳过期")
            lines.append(f"守护：{state}（最后心跳 {hb.get('at')}，{int(age)} 秒前）")
            ports = hb.get("ports") or {}
            if ports:
                names = {"6185": "AstrBot", "5099": "SnowLuma"}
                parts = [f"{names.get(k, k)} {'✅' if v else '❌'}" for k, v in ports.items()]
                lines.append("服务：" + "｜".join(parts))
            if hb.get("restarts_recent"):
                lines.append(f"近期重启：{hb['restarts_recent']} 次")
        else:
            lines.append("守护：没在跑（找不到心跳文件）")

        pending = self._load_pending()
        if pending:
            lines.append(f"待送达通知：{len(pending)} 条")
        yield event.plain_result("\n".join(lines))
