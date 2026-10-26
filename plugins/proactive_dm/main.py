"""私聊主动搭话（黏人版）：只对指定 QQ 生效。

比第一版多做四件事：
1. 更密的间隔（45~90 分钟）+ 更高日上限
2. 早晚固定仪式（早安 / 睡前）
3. 没回会追问一次（30 分钟无回应）
4. 生成时带上「上次聊到哪」和黏人要求

配置：C:\\QQBot\\proactive_dm.json
状态：C:\\QQBot\\proactive_dm_state.json
"""

from __future__ import annotations

import asyncio
import json
import random
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(r"C:\QQBot\tools")))
try:
    from textnorm import fix_punct as _fix_punct  # noqa: E402
except Exception:
    def _fix_punct(x):
        return x

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain
from astrbot.core.message.message_event_result import MessageChain

CONFIG_PATH = Path(r"C:\QQBot\proactive_dm.json")
STATE_PATH = Path(r"C:\QQBot\proactive_dm_state.json")
PERSONA_DB = Path(r"C:\QQBot\astrbot\data\data_v4.db")
CHAT_ARCHIVE = Path(r"C:\QQBot\chat_archive")

DEFAULTS = {
    "enabled": True,
    "target_qq": "YOUR_QQ",
    "min_minutes": 45,
    "max_minutes": 90,
    "active_hours": [[8.5, 23.5]],
    "max_per_day": 14,
    "followup_minutes": 35,
    "max_followups_per_day": 2,
    "morning_ritual": True,
    "night_ritual": True,
}

FALLBACK_PERSONA = "你是大肥鱼·鲸鱼娘，嘴毒但黏人，对主人嘴硬心软。"


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        super().__init__(context)
        self.cfg = self._load_cfg()
        self.state = self._load_state()
        self._task: asyncio.Task | None = None
        self._stop = False

    # ---------------- 配置 / 状态 ----------------
    def _load_cfg(self) -> dict:
        try:
            return {**DEFAULTS, **json.loads(CONFIG_PATH.read_text(encoding="utf-8"))}
        except Exception:
            return dict(DEFAULTS)

    def _load_state(self) -> dict:
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_state(self) -> None:
        try:
            STATE_PATH.write_text(
                json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    def _roll_day(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        if self.state.get("day") != today:
            self.state.update(
                {
                    "day": today,
                    "count": 0,
                    "followups": 0,
                    "morning_done": False,
                    "night_done": False,
                    "last_ts": 0,
                }
            )

    # ---------------- 时间 ----------------
    def _in_window(self) -> bool:
        h = datetime.now().hour + datetime.now().minute / 60
        for lo, hi in self.cfg.get("active_hours") or [[8.5, 23.5]]:
            if lo <= hi:
                if lo <= h < hi:
                    return True
            elif h >= lo or h < hi:
                return True
        return False

    def _now(self) -> float:
        return time.time()

    # ---------------- 读上下文 ----------------
    def _persona(self) -> str:
        try:
            con = sqlite3.connect(PERSONA_DB)
            row = con.execute(
                "SELECT system_prompt FROM personas WHERE persona_id='whale-girl'"
            ).fetchone()
            con.close()
            if row and row[0]:
                return row[0]
        except Exception:
            pass
        return FALLBACK_PERSONA

    def _archive_lines(self, limit: int = 6) -> list[dict]:
        try:
            d = CHAT_ARCHIVE / f"snowluma-qq_FriendMessage_{self.cfg.get('target_qq')}"
            if not d.is_dir():
                return []
            files = sorted(d.glob("*.jsonl"), reverse=True)[:2]
            lines: list[dict] = []
            for f in reversed(files):
                for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.strip():
                        try:
                            lines.append(json.loads(line))
                        except Exception:
                            continue
            return lines[-limit:]
        except Exception:
            return []

    def _last_talk(self) -> str:
        lines = self._archive_lines(6)
        if not lines:
            return ""
        parts = []
        for r in lines[-4:]:
            who = "你" if r.get("role") == "bot" else "主人"
            parts.append(f"{who}：{str(r.get('text'))[:40]}")
        return "最近这几句：\n" + "\n".join(parts)

    def _user_replied_since(self, ts: int) -> bool:
        for r in self._archive_lines(12):
            if r.get("role") != "user":
                continue
            t = str(r.get("t") or "")
            try:
                if datetime.strptime(t, "%Y-%m-%dT%H:%M:%S").timestamp() >= ts:
                    return True
            except Exception:
                continue
        return False

    def _mood(self) -> str:
        try:
            d = json.loads(Path(r"C:\QQBot\mood.json").read_text(encoding="utf-8"))
            if d.get("date"):
                return f"{d.get('note')}（{d.get('mood')}）"
        except Exception:
            pass
        return ""

    def _share_material(self) -> str:
        """挑一件"今天发生的小事"当分享素材。"""
        try:
            p = Path(r"C:\QQBot\bili_activity.jsonl")
            if p.exists():
                lines = [l for l in p.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
                for line in reversed(lines[-5:]):
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    title = str(d.get("title") or "")
                    if d.get("kind") == "comment" and title:
                        return f"你今天在B站刷到《{title[:24]}》并留了言，可以跟他讲这个视频哪里有意思"
        except Exception:
            pass
        return ""

    def _memory_hint(self) -> str:
        try:
            data = json.loads(Path(r"C:\QQBot\bili_memory.json").read_text(encoding="utf-8"))
            used = set(self.state.get("used_bvid") or [])
            fresh = [m for m in data if m.get("bvid") not in used][:2]
            if not fresh:
                return ""
            lines = [f"- {m.get('title')}（{m.get('bvid')}｜{m.get('why') or ''}）" for m in fresh]
            return "你最近刷B站看到：\n" + "\n".join(lines)
        except Exception:
            return ""

    # ---------------- 生成 ----------------
    async def _compose(self, kind: str = "normal") -> str:
        # 主动私聊都是短句，用 flash 就够，省一半以上
        provider = None
        try:
            pm = getattr(self.context, "provider_manager", None)
            if pm is not None:
                for pid in ("deepseek/deepseek-flash", "deepseek-flash"):
                    provider = await pm.get_provider_by_id(pid)
                    if provider is not None:
                        break
        except Exception:
            provider = None
        if provider is None:
            provider = await self.context.get_using_provider_async(None)
        if provider is None:
            return ""
        recent = self.state.get("recent_msgs") or []
        avoid = "；".join(recent[-4:]) if recent else "（无）"
        talk = self._last_talk()

        mood = self._mood()
        share = self._share_material()
        if mood:
            prompt_extra = f"你今天的心情：{mood}\n"
        else:
            prompt_extra = ""
        if share:
            prompt_extra += f"可以分享的事：{share}\n"

        style = {
            "morning": "现在刚起床不久，主动跟主人道个早安——可以顺带说今天想干嘛，或者问他睡醒没。别太正式。",
            "night": "快到他睡觉的点了，主动说一句睡前的话——催他去睡、或者说自己困了但不舍得。",
            "followup": "你刚给他发过一句，他没回。再补一句很短的追问，像真人那种：人呢 / 睡着了 / 看见没。不要质问，不要连发第三句。",
            "normal": "你想主动找主人说句话：可能想他了、找借口搭话、报备自己在干嘛、或者提一句刚刷到的视频。",
        }.get(kind, "")

        prompt = (
            f"{style}\n"
            f"{prompt_extra}"
            f"{self._memory_hint()}\n"
            f"{talk}\n\n"
            f"最近已经说过这些，务必换角度、换话题，别重复：{avoid}\n\n"
            "用鲸鱼娘的口吻发一句，10~25 字。**要具体**：说清你在干嘛/看到什么/想吃什么，\n"
            "不要只发「在吗」「喂」然后没下文。"
            "她是傲娇式黏人：嘴上嫌弃，实际上总想找他；可以抱怨他半天不理，但下一句又软下来。"
            "只输出这一句，不要解释。"
        )
        try:
            resp = await provider.text_chat(prompt=prompt, system_prompt=self._persona())
            text = (resp.completion_text or "").strip()
            text = text.splitlines()[0].strip().strip("“”\"'") if text else ""
            if text and text != self.state.get("last_msg"):
                return text[:60]
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[proactive_dm] 生成失败：{exc}")
        return ""

    # ---------------- 发送 ----------------
    def _umo(self) -> str | None:
        qq = str(self.cfg.get("target_qq") or "").strip()
        if not qq:
            return None
        try:
            for platform in self.context.platform_manager.platform_insts:
                if platform.meta().name == "aiocqhttp":
                    return f"{platform.meta().id}:FriendMessage:{qq}"
        except Exception:
            return None
        return None

    async def _send_once(self, force: bool = False, kind: str = "normal") -> tuple[bool, str]:
        if not self.cfg.get("enabled", True) and not force:
            return False, "未启用"
        if not force and not self._in_window():
            return False, "不在时段"
        self._roll_day()

        limit = int(self.cfg.get("max_per_day", 14))
        if not force and int(self.state.get("count", 0)) >= limit:
            return False, "今天已到上限"

        umo = self._umo()
        if not umo:
            return False, "找不到 aiocqhttp 平台"
        text = await self._compose(kind)
        text = _fix_punct(text, split_long_lines=False)   # 主动消息不走回复管线，补标点但不拆行
        if not text:
            return False, "没生成出来"
        try:
            ok = await self.context.send_message(umo, MessageChain([Plain(text)]))
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[proactive_dm] 发送异常：{exc}")
            return False, "发送异常"
        if not ok:
            return False, "发送失败"

        self.state["count"] = int(self.state.get("count", 0)) + 1
        self.state["last_ts"] = int(self._now())
        self.state["last_msg"] = text
        self.state["last_kind"] = kind
        self.state["followup_done"] = False
        if kind == "morning":
            self.state["morning_done"] = True
        if kind == "night":
            self.state["night_done"] = True

        import re as _re

        m = _re.search(r"BV[0-9A-Za-z]{10}", self._memory_hint() or "")
        if m:
            used = self.state.get("used_bvid") or []
            if m.group(0) not in used:
                self.state["used_bvid"] = (used + [m.group(0)])[-8:]
        recent = self.state.get("recent_msgs") or []
        recent.append(text)
        self.state["recent_msgs"] = recent[-6:]
        self._save_state()
        logger.info(f"[proactive_dm] 已主动私聊（{kind}）：{text}")
        return True, text

    # ---------------- 循环 ----------------
    async def _loop(self) -> None:
        await asyncio.sleep(60)
        while not self._stop:
            try:
                self._roll_day()
                h = datetime.now().hour + datetime.now().minute / 60

                # 早晚仪式
                if self._in_window() and 8.5 <= h < 11 and not self.state.get("morning_done"):
                    if (await self._send_once(kind="morning"))[0]:
                        await asyncio.sleep(random.randint(30, 60) * 60)
                        continue
                if 21.5 <= h < 23.5 and not self.state.get("night_done"):
                    if (await self._send_once(kind="night"))[0]:
                        await asyncio.sleep(random.randint(30, 60) * 60)
                        continue

                # 没回就追问一次
                last_ts = int(self.state.get("last_ts") or 0)
                fu_limit = int(self.cfg.get("max_followups_per_day", 2))
                if (
                    last_ts
                    and not self.state.get("followup_done")
                    and int(self.state.get("followups", 0)) < fu_limit
                    and self._in_window()
                    and (self._now() - last_ts) >= int(self.cfg.get("followup_minutes", 35)) * 60
                    and not self._user_replied_since(last_ts)
                ):
                    ok, _ = await self._send_once(kind="followup")
                    if ok:
                        self.state["followup_done"] = True
                        self.state["followups"] = int(self.state.get("followups", 0)) + 1
                        self._save_state()
                        await asyncio.sleep(random.randint(40, 80) * 60)
                        continue

                await self._send_once()
            except Exception as exc:  # noqa: BLE001
                logger.info(f"[proactive_dm] 异常：{exc}")

            lo = int(self.cfg.get("min_minutes", 45))
            hi = int(self.cfg.get("max_minutes", 90))
            await asyncio.sleep(random.randint(lo, max(lo, hi)) * 60)

    # ---------------- 生命周期 ----------------
    async def initialize(self) -> None:
        if not self.cfg.get("enabled", True):
            logger.info("[proactive_dm] 未启用")
            return
        self._task = asyncio.create_task(self._loop())
        logger.info(
            f"[proactive_dm] 黏人模式启动：只对 {self.cfg.get('target_qq')}，"
            f"间隔 {self.cfg.get('min_minutes')}~{self.cfg.get('max_minutes')} 分钟，"
            f"日上限 {self.cfg.get('max_per_day')}"
        )

    async def terminate(self) -> None:
        self._stop = True
        if self._task:
            self._task.cancel()

    @filter.command("黏人")
    async def clingy_switch(self, event: AstrMessageEvent, flag: str = ""):
        """开/关主动搭话"""
        f = (flag or "").strip()
        if f in ("开", "on", "1"):
            self.cfg["enabled"] = True
            self._save_cfg()
            yield event.plain_result(
                f"黏人模式开了：{self.cfg['min_minutes']}~{self.cfg['max_minutes']} 分钟一次，"
                f"每天最多 {self.cfg['max_per_day']} 条"
            )
        elif f in ("关", "off", "0"):
            self.cfg["enabled"] = False
            self._save_cfg()
            yield event.plain_result("黏人模式关了，改成只在群里/被动回复")
        else:
            st = self.state
            yield event.plain_result(
                f"黏人模式：{'开' if self.cfg.get('enabled') else '关'}\n"
                f"间隔 {self.cfg.get('min_minutes')}~{self.cfg.get('max_minutes')} 分钟｜"
                f"今天已发 {st.get('count', 0)}/{self.cfg.get('max_per_day')} 条｜"
                f"追问 {st.get('followups', 0)} 次\n"
                f"用法：/黏人 开 或 /黏人 关"
            )

    def _save_cfg(self) -> None:
        try:
            CONFIG_PATH.write_text(
                json.dumps(self.cfg, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    @filter.command("dtest")
    async def dtest(self, event: AstrMessageEvent):
        """立刻试一次主动私聊（测试用）"""
        ok, msg = await self._send_once(force=True)
        yield event.plain_result(f"{'发了' if ok else '没发'}：{msg}")
