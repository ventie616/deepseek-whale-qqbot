"""Shared conversational TTS for automatic replies and the speech command."""

from __future__ import annotations

import asyncio
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from astrbot.api import logger

from .qwen_tts import synthesize
from .rvc_client import convert

ROOT = Path(__file__).resolve().parents[4]
VOICE_CONFIG = ROOT / "astrbot" / "data" / "config" / "feiyu_voice_config.json"
EMOTION_CONFIG = ROOT / "voice_emotion.json"
MOOD_FILE = ROOT / "mood.json"
PITCH = {
    "开心": 1.04, "撒娇": 1.02, "期待": 1.03, "惊讶": 1.04,
    "困了": 0.96, "低落": 0.97, "委屈": 0.98, "烦躁": 0.98,
}
SENTENCE_END = "，、。！？；：,.!?;:…—"
TECHNICAL = re.compile(r"(?m)^\s*(?:#{1,6}\s|[-*+]\s|\d+[.)、]\s|\|.+\|\s*$)")


@dataclass(frozen=True)
class SpeechPlan:
    text: str
    voice: str
    speed: float
    pitch: float


def read_config(path: Path = VOICE_CONFIG) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _number(value, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) and result > 0 else default


def speech_plan(text: str, config: dict, *, mood: str | None = None) -> SpeechPlan:
    """Let punctuation carry pauses, and vary delivery within one base voice."""
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    spoken = "".join(
        line + (" " if line[-1] in SENTENCE_END else "，") if i < len(lines) - 1 else line
        for i, line in enumerate(lines)
    )
    base_speed = _number(config.get("qwen_speed"), 1.0)
    speed = base_speed
    pitch = 1.0
    emotion = read_config(EMOTION_CONFIG)
    if emotion.get("enabled", True):
        label = mood if mood is not None else str(read_config(MOOD_FILE).get("mood") or "")
        rule = (emotion.get("map") or {}).get(label, {})
        default_speed = _number((emotion.get("fallback") or {}).get("speed"), 1.0)
        speed *= _number(rule.get("speed"), default_speed) / default_speed
        pitch = PITCH.get(label, 1.0)
    if spoken.rstrip('”」』').endswith(("?", "？")):
        pitch += 0.02
    return SpeechPlan(
        spoken,
        str(config.get("qwen_voice") or "longhua_v2"),
        round(base_speed * max(0.87, min(1.12, speed / base_speed)), 2),
        round(pitch, 2),
    )


async def synthesize_speech(text: str, config: dict) -> Path:
    plan = speech_plan(text, config)
    if not plan.text:
        raise ValueError("语音文本为空")
    folder = ROOT / "voice_out"
    stamp = uuid4().hex
    source = folder / f"tts_{stamp}.wav"
    await synthesize(plan.text, source, voice=plan.voice, speed=plan.speed, pitch=plan.pitch)
    if str(config.get("provider") or "").lower() != "qwen_rvc":
        return source

    selected = str(config.get("rvc_speech_voice") or "feiyu")
    target = folder / f"her_{stamp}.wav"
    for voice in dict.fromkeys((selected, "feiyu")):
        try:
            return await asyncio.to_thread(convert, source, target, voice=voice)
        except Exception as exc:
            logger.info(f"[feiyu_voice] 换声失败（{voice}）：{exc}")
    return source
