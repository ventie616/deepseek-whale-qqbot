"""阿里云百炼 cosyvoice-v2 的 WebSocket TTS 客户端（插件内共用）。"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

KEY_FILE = Path(r"D:\QQrobot\secrets\qwen_tts_key.txt")
URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
MODEL = "cosyvoice-v2"


def _key() -> str:
    return KEY_FILE.read_text(encoding="utf-8").strip()


async def synthesize(
    text: str,
    out: Path,
    voice: str = "longhua_v2",
    speed: float = 1.0,
    pitch: float = 1.0,
    volume: int = 55,
    timeout: float = 60.0,
) -> Path:
    """把 text 合成成 wav，返回路径；失败抛异常。"""
    import websockets

    out.parent.mkdir(parents=True, exist_ok=True)
    task_id = uuid.uuid4().hex
    headers = {"Authorization": f"bearer {_key()}", "X-DashScope-DataInspection": "enable"}
    audio = bytearray()

    async with websockets.connect(URL, additional_headers=headers, max_size=None) as ws:
        await ws.send(json.dumps({
            "header": {"action": "run-task", "task_id": task_id, "streaming": "duplex"},
            "payload": {
                "task_group": "audio", "task": "tts", "function": "SpeechSynthesizer",
                "model": MODEL,
                "parameters": {
                    "text_type": "PlainText", "voice": voice, "format": "wav",
                    "sample_rate": 24000, "volume": volume,
                    "rate": round(max(0.5, min(2.0, speed)), 2),
                    "pitch": round(max(0.5, min(2.0, pitch)), 2),
                },
                "input": {},
            },
        }))
        while True:
            msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
            if isinstance(msg, bytes):
                audio.extend(msg)
                continue
            ev = json.loads(msg)
            event = ev.get("header", {}).get("event", "")
            if event == "task-started":
                await ws.send(json.dumps({
                    "header": {"action": "continue-task", "task_id": task_id, "streaming": "duplex"},
                    "payload": {"input": {"text": text}},
                }))
                await ws.send(json.dumps({
                    "header": {"action": "finish-task", "task_id": task_id, "streaming": "duplex"},
                    "payload": {"input": {}},
                }))
            elif event == "task-finished":
                break
            elif event == "task-failed":
                err = ev.get("header", {}).get("error_message", "unknown")
                raise RuntimeError(f"qwen tts 失败：{err}")

    if not audio:
        raise RuntimeError("qwen tts 没返回音频")
    try:
        from .wavfix import fix_wav
    except ImportError:  # 被控制台当普通模块导入时
        from wavfix import fix_wav
    out.write_bytes(fix_wav(bytes(audio)))
    return out
