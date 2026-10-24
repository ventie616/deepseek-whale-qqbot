"""B站 登录态每日自检：失效就主动私聊提醒补 cookie。

每天 09:00 探一次 nav 接口；启动后 2 分钟也探一次（方便刚改完 cookie 马上验证）。
只在"状态变化"时提醒，避免每天重复烦人（除非一直没修，每天提醒一次）。
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain
from astrbot.core.message.message_event_result import MessageChain

import urllib.request

ACCOUNT = Path(r"C:\QQBot\bili_account.json")
PUSH_CFG = Path(r"C:\QQBot\bili_push.json")
STATE = Path(r"C:\QQBot\bili_login_state.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
CHECK_HOUR = 9


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        super().__init__(context)
        self._task: asyncio.Task | None = None
        self._stop = False
        self.state = self._load_state()

    def _load_state(self) -> dict:
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_state(self) -> None:
        try:
            STATE.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _target_qq(self) -> str:
        try:
            return str(json.loads(PUSH_CFG.read_text(encoding="utf-8")).get("target_qq") or "")
        except Exception:
            return ""

    def _umo(self) -> str | None:
        qq = self._target_qq()
        if not qq:
            return None
        try:
            for platform in self.context.platform_manager.platform_insts:
                if platform.meta().name == "aiocqhttp":
                    return f"{platform.meta().id}:FriendMessage:{qq}"
        except Exception:
            return None
        return None

    # ---------------- 检查 ----------------
    def _check(self) -> dict:
        try:
            acc = json.loads(ACCOUNT.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": f"读不到 bili_account.json：{exc}"}
        cookie = (
            f"SESSDATA={acc.get('sessdata','')}; bili_jct={acc.get('bili_jct','')}; "
            f"DedeUserID={acc.get('dedeuserid','')}"
        )
        req = urllib.request.Request(
            "https://api.bilibili.com/x/web-interface/nav",
            headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/", "Cookie": cookie},
        )
        try:
            d = json.loads(urllib.request.urlopen(req, timeout=20).read())
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": f"请求失败：{exc}"}
        data = d.get("data") or {}
        if d.get("code") == 0 and data.get("isLogin"):
            return {
                "ok": True,
                "name": data.get("uname"),
                "mid": data.get("mid"),
                "level": (data.get("level_info") or {}).get("current_level"),
            }
        return {"ok": False, "reason": f"code={d.get('code')} {d.get('message')}"}

    async def _notify(self, text: str) -> None:
        umo = self._umo()
        if not umo:
            logger.info("[bili_watch] 找不到目标会话，跳过通知")
            return
        try:
            await self.context.send_message(umo, MessageChain([Plain(text)]))
            logger.info(f"[bili_watch] 已通知：{text}")
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[bili_watch] 通知失败：{exc}")

    async def _run_check(self, notify: bool = True) -> tuple[bool, str]:
        res = self._check()
        today = datetime.now().strftime("%Y-%m-%d")
        self.state["last_check"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.state["last_ok"] = bool(res.get("ok"))

        if res.get("ok"):
            prev = self.state.get("last_status")
            self.state["last_status"] = "ok"
            self.state["notified_on"] = ""
            self._save_state()
            logger.info(f"[bili_watch] 登录正常：{res.get('name')} Lv{res.get('level')}")
            if notify and prev == "expired":
                await self._notify("B站 登录恢复了，能正常发评论了")
            return True, f"正常：{res.get('name')}"

        self.state["last_status"] = "expired"
        self._save_state()
        logger.info(f"[bili_watch] 登录失效：{res.get('reason')}")
        if notify and self.state.get("notified_on") != today:
            self.state["notified_on"] = today
            self._save_state()
            await self._notify(
                f"B站 登录失效了（{res.get('reason')}）。\n"
                "有空补一下 SESSDATA + bili_jct，不然评论区 @ 你 会全部失败。"
            )
        return False, f"失效：{res.get('reason')}"

    # ---------------- 定时 ----------------
    async def _loop(self) -> None:
        await asyncio.sleep(120)
        while not self._stop:
            try:
                await self._run_check()
            except Exception as exc:  # noqa: BLE001
                logger.info(f"[bili_watch] 自检异常：{exc}")
            now = datetime.now()
            nxt = now.replace(hour=CHECK_HOUR, minute=0, second=0, microsecond=0)
            if nxt <= now:
                nxt += timedelta(days=1)
            wait = (nxt - now).total_seconds()
            logger.info(f"[bili_watch] 下次自检 {nxt:%m-%d %H:%M}（{wait/3600:.1f} 小时后）")
            await asyncio.sleep(wait)

    async def initialize(self) -> None:
        self._task = asyncio.create_task(self._loop())
        logger.info(f"[bili_watch] 已启动：每天 {CHECK_HOUR}:00 自检登录态")

    async def terminate(self) -> None:
        self._stop = True
        if self._task:
            self._task.cancel()

    @filter.command("bilicheck")
    async def bilicheck(self, event: AstrMessageEvent):
        """立刻检查 B站 登录态"""
        ok, msg = await self._run_check(notify=False)
        yield event.plain_result(("✅ " if ok else "❌ ") + msg)
