"""群聊记忆：人物卡 + 滚动摘要 + 她上一句，替代"每轮灌 50 条原文"。

设计目标：花更少的 token，换来"记得住人 / 记得住聊过什么 / 接得上话"。

数据：C:\\QQBot\\group_memory.json
- members[umo][uid] = {name, aliases, notes[], count, last}
- summary[umo] = {text, topic, at}
- buffer[umo] = ["昵称: 内容", ...] 最近若干条，攒够就压缩成摘要
- last_reply[umo] = 她上一句说了什么
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain


try:  # 易变注入挂到用户消息后，让 system_prompt 保持稳定（缓存友好）
    from astrbot.core.agent.message import TextPart as _TextPart
except Exception:  # pragma: no cover
    _TextPart = None


def _extra(req, text: str) -> None:
    if _TextPart is not None and hasattr(req, "extra_user_content_parts"):
        req.extra_user_content_parts.append(_TextPart(text=text))
    else:
        req.system_prompt += text

STATE = Path(r"C:\QQBot\group_memory.json")
SUMMARY_EVERY = 24          # 攒够多少条压一次摘要
BUFFER_MAX = 48
MAX_NOTES = 5
INJECT_MEMBERS = 3          # 最多注入几个人物卡
SUMMARY_MAX_CHARS = 160


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        super().__init__(context)
        self.state = self._load()
        self._busy: set[str] = set()

    # ---------------- 存储 ----------------
    def _load(self) -> dict:
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            return {"members": {}, "summary": {}, "buffer": {}, "last_reply": {}}

    def _save(self) -> None:
        try:
            STATE.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.info(f"[group_memory] 保存失败：{exc}")

    # ---------------- 收集 ----------------
    @filter.event_message_type(filter.EventMessageType.ALL)
    async def collect(self, event: AstrMessageEvent) -> None:
        try:
            mtype = event.get_message_type().name
        except Exception:
            return
        is_group = mtype == "GROUP_MESSAGE"
        is_private = mtype == "FRIEND_MESSAGE"
        if not (is_group or is_private):
            return
        umo = event.unified_msg_origin
        text = (event.get_message_str() or "").strip()
        if not text:
            return
        uid = str(event.get_sender_id() or "")
        name = (event.get_sender_name() or uid) if is_group else "主人"

        members = self.state.setdefault("members", {}).setdefault(umo, {})
        if uid and is_group:
            card = members.setdefault(uid, {"name": name, "aliases": [], "notes": [], "count": 0, "last": 0})
            card["name"] = name
            card["count"] = int(card.get("count", 0)) + 1
            card["last"] = int(time.time())

        buf = self.state.setdefault("buffer", {}).setdefault(umo, [])
        buf.append(f"{name}: {text[:120]}")
        if len(buf) > BUFFER_MAX:
            del buf[: len(buf) - BUFFER_MAX]
        self._save()

        if len(buf) >= SUMMARY_EVERY and umo not in self._busy:
            self._busy.add(umo)
            asyncio.create_task(self._summarize(umo))

    @filter.on_decorating_result()
    async def note_reply(self, event: AstrMessageEvent) -> None:
        try:
            if event.get_message_type().name not in ("GROUP_MESSAGE", "FRIEND_MESSAGE"):
                return
            result = event.get_result()
            if result is None or not result.chain:
                return
            text = "".join(c.text for c in result.chain if isinstance(c, Plain)).strip()
            if text:
                self.state.setdefault("last_reply", {})[event.unified_msg_origin] = text[:120]
                self._save()
        except Exception:
            pass

    # ---------------- 压缩 ----------------
    async def _summarize(self, umo: str) -> None:
        try:
            buf = list(self.state.get("buffer", {}).get(umo) or [])
            if len(buf) < 8:
                return
            provider = await self.context.get_using_provider_async(umo)
            if provider is None:
                return
            old = (self.state.get("summary", {}).get(umo) or {}).get("text", "")
            prompt = (
                (f"上一段摘要：{old}\n\n" if old else "")
                + "聊天记录：\n"
                + "\n".join(buf[-SUMMARY_EVERY:])
                + f"\n\n输出两段，逐行，不要编号、不要解释：\n"
                f"摘要：<不超过 {SUMMARY_MAX_CHARS} 字，讲清在聊什么、谁对谁说了什么关键的、有没有冲突或梗，第三人称>\n"
                "人物：<昵称>|<这个人的一句特征，15 字内，比如 爱玩梗/中二自称残影/爱发红包>\n"
                "人物：<昵称>|<特征>\n"
                "（只写这轮里真出现、且有明显特征的人；最多 5 行；没有就不写人物行）"
            )
            resp = await provider.text_chat(
                prompt=prompt,
                system_prompt="你在做聊天摘要，只输出一段话，不解释。",
            )
            raw = (resp.completion_text or "").strip()
            summary_text = ""
            people: list[tuple[str, str]] = []
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                if line.startswith("摘要"):
                    summary_text = line.split("：", 1)[-1].split(":", 1)[-1].strip()
                elif line.startswith("人物"):
                    body = line.split("：", 1)[-1].split(":", 1)[-1].strip()
                    if "|" in body:
                        name, trait = body.split("|", 1)
                        if name.strip() and trait.strip():
                            people.append((name.strip(), trait.strip()[:20]))
            if not summary_text:
                summary_text = raw.replace("\n", " ")[: SUMMARY_MAX_CHARS * 2]

            self.state.setdefault("summary", {})[umo] = {
                "text": summary_text[: SUMMARY_MAX_CHARS * 2],
                "at": int(time.time()),
            }
            self.state["buffer"][umo] = []

            # 人物特征写进人物卡
            members = (self.state.get("members") or {}).get(umo) or {}
            wrote = 0
            for name, trait in people:
                target = None
                for uid, card in members.items():
                    if name == card.get("name") or name in (card.get("aliases") or []):
                        target = card
                        break
                if target is None:
                    continue
                notes = target.setdefault("notes", [])
                if trait not in notes:
                    notes.append(trait)
                    target["notes"] = notes[-MAX_NOTES:]
                    wrote += 1

            self._save()
            logger.info(
                f"[group_memory] 摘要已更新（{len(summary_text)} 字）｜人物特征写入 {wrote} 条"
            )
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[group_memory] 摘要失败：{exc}")
        finally:
            self._busy.discard(umo)

    # ---------------- 注入 ----------------
    @filter.on_llm_request()
    async def _inject_private(self, event: AstrMessageEvent, req) -> None:
        """私聊记忆：最近聊过什么 + 她上一句 → 跨会话也接得上。"""
        umo = event.unified_msg_origin
        summary = (self.state.get("summary") or {}).get(umo) or {}
        last = (self.state.get("last_reply") or {}).get(umo)
        buf = (self.state.get("buffer") or {}).get(umo) or []
        lines = []
        if summary.get("text"):
            lines.append(f"【你们最近聊过】{summary['text']}")
        if last:
            lines.append(f"【你上一句说的】{last}")
        if buf:
            recent = " / ".join(
                b.split("：", 1)[-1].split(":", 1)[-1].strip()[:30] for b in buf[-3:]
            )
            lines.append(f"【主人最近说的】{recent}")
        if not lines:
            return
        _extra(req, (
            "\n\n" + "\n".join(lines)
            + "\n（这是你和主人的私聊记忆：接得上就别说「你怎么了」，也别把记忆原样背出来。）"
        ))
        logger.info(
            f"[group_memory] 私聊注入：摘要{bool(summary.get('text'))} 上句{bool(last)}"
        )

    async def inject_memory(self, event: AstrMessageEvent, req) -> None:
        try:
            mtype = event.get_message_type().name
        except Exception:
            return
        if mtype == "FRIEND_MESSAGE":
            await self._inject_private(event, req)
            return
        if mtype != "GROUP_MESSAGE":
            return
        umo = event.unified_msg_origin
        text = event.get_message_str() or ""
        uid = str(event.get_sender_id() or "")
        members = (self.state.get("members") or {}).get(umo) or {}

        lines: list[str] = []
        # 1) 说话人 + 被提到的人（最多 3 个）
        picked: list[str] = []
        if uid and uid in members:
            picked.append(uid)
        for muid, card in members.items():
            if muid in picked:
                continue
            for nm in [card.get("name", ""), *card.get("aliases", [])]:
                if nm and len(nm) > 1 and nm in text:
                    picked.append(muid)
                    break
            if len(picked) >= INJECT_MEMBERS:
                break
        buf = (self.state.get("buffer") or {}).get(umo) or []
        for muid in picked[:INJECT_MEMBERS]:
            card = members[muid]
            notes = "；".join(card.get("notes") or []) or "（还不了解）"
            line = f"- {card.get('name')}（uid {muid}）：{notes}"
            # 被 @ 或正在说话的人，附上他最近说的两句
            nm = card.get("name") or ""
            recent = [b.split("：", 1)[-1].strip() for b in buf if b.startswith(f"{nm}：")]
            if recent:
                line += "｜最近说过：" + " / ".join(x[:24] for x in recent[-2:])
            lines.append(line)

        block = ""
        if lines:
            block += "【群里的人】\n" + "\n".join(lines) + "\n"
        summary = (self.state.get("summary") or {}).get(umo) or {}
        if summary.get("text"):
            block += f"【最近群里在聊】{summary['text']}\n"
        last = (self.state.get("last_reply") or {}).get(umo)
        if last:
            block += f"【你上一句说的】{last}\n"
        if block:
            _extra(req, (
                "\n\n" + block
                + "（这些是你的记忆，自然地用：认得出人就叫名字，接得上就别重复问；"
                "不确定的就别硬编。）"
            ))
            logger.info(f"[group_memory] 注入 {len(picked)} 人物卡 + 摘要{bool(summary.get('text'))}")

    # ---------------- 工具 ----------------
    @filter.llm_tool(name="remember_member")
    async def remember_member(self, event: AstrMessageEvent, fact: str, who: str = ""):
        """记下某个群友的一条信息（喜好、称呼、关系、约定等），以后能想起来。

        Args:
            fact(string): 要记住的一句话，比如"喜欢喝奶茶，讨厌被叫小胖"
            who(string): 群友昵称或 uid，留空表示记当前说话的人
        """
        try:
            if event.get_message_type().name != "GROUP_MESSAGE":
                return "只在群里记人"
        except Exception:
            return "只在群里记人"
        umo = event.unified_msg_origin
        members = self.state.setdefault("members", {}).setdefault(umo, {})
        target = ""
        who = (who or "").strip()
        if not who:
            target = str(event.get_sender_id() or "")
        else:
            for muid, card in members.items():
                if who == muid or who == card.get("name") or who in (card.get("aliases") or []):
                    target = muid
                    break
        if not target:
            return f"群里没有登记过「{who}」"
        card = members.setdefault(
            target, {"name": who or target, "aliases": [], "notes": [], "count": 0, "last": 0}
        )
        notes = card.setdefault("notes", [])
        fact = fact.strip()[:60]
        if fact and fact not in notes:
            notes.append(fact)
            card["notes"] = notes[-MAX_NOTES:]
            self._save()
        return f"记住了：{card.get('name')} —— {fact}"

    @filter.llm_tool(name="who_is")
    async def who_is(self, event: AstrMessageEvent, name: str = ""):
        """查某个群友是谁、之前聊过什么。不确定对方身份时用。

        Args:
            name(string): 昵称或 uid，留空表示当前说话的人
        """
        umo = event.unified_msg_origin
        members = (self.state.get("members") or {}).get(umo) or {}
        name = (name or "").strip()
        if not name:
            name = str(event.get_sender_id() or "")
        for muid, card in members.items():
            if name in (muid, card.get("name"), *(card.get("aliases") or [])):
                notes = "；".join(card.get("notes") or []) or "没记下什么"
                return f"{card.get('name')}（uid {muid}），发过 {card.get('count', 0)} 条消息。已知：{notes}"
        return f"群里没有「{name}」的记录"

    @filter.llm_tool(name="group_recap")
    async def group_recap(self, event: AstrMessageEvent):
        """回顾群里最近在聊什么。"""
        umo = event.unified_msg_origin
        s = (self.state.get("summary") or {}).get(umo) or {}
        if s.get("text"):
            return s["text"]
        buf = (self.state.get("buffer") or {}).get(umo) or []
        if buf:
            return "还没压成摘要，最近几条：" + " / ".join(buf[-8:])
        return "这个群我还没攒到记录"
