"""在 QQ 私聊中，把合适的短回复用语音说出来。"""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from datetime import date

import astrbot.api.message_components as Comp
from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.core.star.filter.command import GreedyStr

from ..feiyu_voice.client import VoiceClient
from ..feiyu_voice.speech import ROOT, TECHNICAL, read_config, speech_plan, synthesize_speech

STATE = ROOT / "voice_reply_state.json"
MAX_CHARS = 42
EXPLICIT_MAX_CHARS = 120
COOLDOWN_MIN = 15
DAILY_CAP = 10
PROBABILITY = 0.05
EXPLICIT_PAT = re.compile(
    r"说句话|说一句|说个话|说下话|说给我听|用语音|发[个条]?语音|来条语音|语音[说回]|"
    r"让我听听|听听你|你的声音|念一下|读一下|说点什么|讲句话|开个口|说两句"
)
NO_VOICE_PAT = re.compile(r"(?:别|不要|不用|不想听).{0,5}(?:语音|出声|说话|声音)|只(?:发|用)文字")
SKIP_PAT = re.compile(r"https?://|www\.|BV[0-9A-Za-z]{10}|[<>`]|\[B站|【B站")
HAS_WORDS = re.compile(r"[\u3400-\u9fffA-Za-z]")


def _save_state(state: dict) -> None:
    tmp = STATE.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(STATE)
    except OSError as exc:
        logger.warning(f"[voice_reply] 保存语音计数失败：{exc}")


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        super().__init__(context)
        self._pending: asyncio.Task | None = None

    @staticmethod
    def _should_speak(text: str, user_text: str) -> bool:
        if NO_VOICE_PAT.search(user_text):
            return False
        explicit = bool(EXPLICIT_PAT.search(user_text))
        limit = EXPLICIT_MAX_CHARS if explicit else MAX_CHARS
        if not 0 < len(text) <= limit or not HAS_WORDS.search(text):
            return False
        if SKIP_PAT.search(text) or TECHNICAL.search(text):
            return False
        state = read_config(STATE)
        if state.get("disabled"):
            return False
        if state.get("date") == date.today().isoformat():
            if int(state.get("count", 0)) >= DAILY_CAP:
                return False
            if not explicit and time.time() - float(state.get("last", 0)) < COOLDOWN_MIN * 60:
                return False
        return explicit or random.random() < PROBABILITY

    async def _speak(self, umo: str, text: str) -> bool:
        config = read_config()
        provider = str(config.get("provider") or "local").lower()
        path = None
        try:
            if provider in ("qwen", "qwen_rvc"):
                try:
                    path = await synthesize_speech(text, config)
                except Exception as exc:
                    logger.info(f"[voice_reply] 云端合成失败，尝试本地：{exc}")
            if path is None:
                client = VoiceClient(config.get("base_url") or "http://127.0.0.1:9880",
                                     config.get("api_key") or "")
                try:
                    job = await client.submit("speech", {
                        "voice": config.get("voice") or "feiyu",
                        "text": speech_plan(text, config).text,
                        "style": config.get("style") or "cute",
                        "emotion": config.get("emotion") or "奶音",
                        "speed": config.get("speed", 0.9),
                        "liveliness": config.get("liveliness", 0.35),
                        "chunk": bool(config.get("chunk", False)),
                    })
                    await client.wait(job["id"], timeout=90)
                    folder = ROOT / "voice_out"
                    folder.mkdir(parents=True, exist_ok=True)
                    path = await client.download(job["id"], folder)
                finally:
                    await client.close()
            chain = MessageChain([Comp.Record.fromFileSystem(str(path))])
            return await self.context.send_message(umo, chain)
        except Exception as exc:
            logger.info(f"[voice_reply] 语音未送达：{exc}")
            return False

    async def _deliver(self, umo: str, text: str, original: MessageChain) -> None:
        if not await self._speak(umo, text):
            try:
                await self.context.send_message(umo, original)
            except Exception as exc:
                logger.warning(f"[voice_reply] 补发原回复失败：{exc}")
            return
        today = date.today().isoformat()
        state = read_config(STATE)
        count = int(state.get("count", 0)) if state.get("date") == today else 0
        state.update(count=count + 1, date=today, last=time.time())
        _save_state(state)

    @filter.on_llm_request()
    async def prepare_explicit_reply(self, event: AstrMessageEvent, req) -> None:
        umo = event.unified_msg_origin
        user_text = str(getattr(event, "message_str", "") or "")
        if "FriendMessage" not in umo or "webchat" in umo:
            return
        if not EXPLICIT_PAT.search(user_text) or NO_VOICE_PAT.search(user_text):
            return
        if read_config(STATE).get("disabled"):
            return
        req.system_prompt += (
            "\n\n对方这轮想听你说话。日常对话用一两句直接接住上一句话，"
            "用标点留自然停顿，别念语气说明、动作旁白或重复口头禅。"
            "如果对方指定了朗读内容，保留原文。"
        )

    @filter.on_decorating_result()
    async def capture_text(self, event: AstrMessageEvent) -> None:
        """只接管完整的纯文本私聊回复，失败时补发原消息。"""
        if event.get_extra("feiyu_audio_scheduled") or (self._pending and not self._pending.done()):
            return
        umo = event.unified_msg_origin
        if "FriendMessage" not in umo or "webchat" in umo:
            return
        result = event.get_result()
        if result is None or not result.is_llm_result() or not result.chain:
            return
        if any(not isinstance(part, Comp.Plain) for part in result.chain):
            return
        text = "\n".join(part.text for part in result.chain if part.text.strip()).strip()
        user_text = str(getattr(event, "message_str", "") or "")
        if not self._should_speak(text, user_text):
            return
        original = MessageChain(list(result.chain))
        self._pending = asyncio.create_task(self._deliver(umo, text, original))
        result.chain = []
        event.set_extra("voice_consumed", True)

    @filter.command("语音测试")
    async def test_cmd(self, event: AstrMessageEvent, text: GreedyStr = ""):
        say = str(text or "").strip() or "嗯？听见了吗？我就在这儿。"
        if not await self._speak(event.unified_msg_origin, say[:EXPLICIT_MAX_CHARS]):
            yield event.plain_result("这次语音没发出去。")

    @filter.command("语音")
    async def toggle(self, event: AstrMessageEvent, arg: str = ""):
        """开关自动语音：/语音 on|off|状态"""
        state = read_config(STATE)
        action = (arg or "").strip().lower()
        if action in ("off", "关", "关闭", "on", "开", "开启"):
            disabled = action in ("off", "关", "关闭")
            state["disabled"] = disabled
            _save_state(state)
            yield event.plain_result("自动语音关了" if disabled else "自动语音开了")
            return
        count = int(state.get("count", 0)) if state.get("date") == date.today().isoformat() else 0
        flag = "关" if state.get("disabled") else "开"
        yield event.plain_result(
            f"自动语音：{flag}\n今天已发 {count}/{DAILY_CAP} 条\n"
            f"平时冷却 {COOLDOWN_MIN} 分钟 · QQ 私聊"
        )

    async def terminate(self):
        if self._pending:
            await asyncio.gather(self._pending, return_exceptions=True)
