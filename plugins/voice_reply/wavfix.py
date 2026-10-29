"""合并 cosyvoice 流式返回的多个 WAV 分片，写出一个合法 WAV。"""

from __future__ import annotations

import io
import struct
import wave


def _chunks(raw: bytes) -> list[bytes]:
    """把拼接在一起的多个 RIFF 文件拆开，返回每段的 PCM 数据。"""
    out: list[bytes] = []
    pos = 0
    while True:
        start = raw.find(b"RIFF", pos)
        if start < 0:
            break
        if start + 12 > len(raw):
            break
        # RIFF<size>WAVE
        size = struct.unpack("<I", raw[start + 4:start + 8])[0]
        end = min(len(raw), start + 8 + size)
        body = raw[start:end]
        dpos = body.find(b"data")
        if dpos >= 0 and dpos + 8 <= len(body):
            dsize = struct.unpack("<I", body[dpos + 4:dpos + 8])[0]
            data = body[dpos + 8:dpos + 8 + dsize]
            if data:
                out.append(data)
        pos = end if end > start else start + 4
    return out


def fix_wav(raw: bytes, sample_rate: int = 24000, channels: int = 1, width: int = 2) -> bytes:
    """把可能带多个头的音频流整理成单个标准 WAV。"""
    if not raw.startswith(b"RIFF"):
        # 纯 PCM
        pcm = raw
    else:
        parts = _chunks(raw)
        if parts:
            pcm = b"".join(parts)
        else:
            pcm = raw
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def duration(raw: bytes, sample_rate: int = 24000, width: int = 2, channels: int = 1) -> float:
    fixed = fix_wav(raw, sample_rate, channels, width)
    with wave.open(io.BytesIO(fixed), "rb") as w:
        return w.getnframes() / w.getframerate()
