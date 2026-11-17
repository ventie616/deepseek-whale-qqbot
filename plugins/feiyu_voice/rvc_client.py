"""Qwen 出声 → 本地 RVC 换成她的音色。"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

BASE = "http://127.0.0.1:9880"
KEY_FILE = Path(r"D:\QQrobot\secrets\feiyu_voice_key.txt")


def _key() -> str:
    return KEY_FILE.read_text(encoding="utf-8").strip()


def _auth() -> dict:
    return {"Authorization": f"Bearer {_key()}"}


def _upload(path: Path) -> str:
    boundary = "----feiyu" + uuid4().hex
    body = bytearray()
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode()
    body += b"Content-Type: audio/wav\r\n\r\n"
    body += path.read_bytes()
    body += f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        BASE + "/v1/assets", data=bytes(body), method="POST",
        headers={**_auth(), "Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        return _read_upload(req)
    except urllib.error.URLError as exc:
        if os.name != "nt" or not isinstance(exc.reason, ConnectionRefusedError):
            raise
        _wake_local_service()
        return _read_upload(req)


def _read_upload(req: urllib.request.Request) -> str:
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.loads(response.read())["id"]


def _wake_local_service() -> None:
    """Wake the existing WSL service if Windows lost its localhost relay."""
    subprocess.run(
        ["wsl.exe", "-d", "Ubuntu-22.04", "-u", "root", "--", "systemctl", "start", "feiyu-voice"],
        check=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(12):
        try:
            with urllib.request.urlopen(BASE + "/health", timeout=1):
                return
        except urllib.error.URLError:
            time.sleep(0.25)
    raise ConnectionError("本地声音服务未能恢复")


def _submit(audio_id: str, voice: str = "feiyu") -> str:
    payload = json.dumps({"voice": voice, "audio_id": audio_id}).encode()
    req = urllib.request.Request(
        BASE + "/v1/voice-conversion", data=payload, method="POST",
        headers={**_auth(), "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())["id"]


def _wait(job_id: str, timeout: float = 180.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        req = urllib.request.Request(BASE + f"/v1/jobs/{job_id}", headers=_auth())
        with urllib.request.urlopen(req, timeout=20) as r:
            st = json.loads(r.read())
        if st.get("status") == "success":
            return
        if st.get("status") in ("failed", "interrupted"):
            raise RuntimeError(f"RVC 失败：{st.get('error')}")
        time.sleep(1.5)
    raise RuntimeError("RVC 超时")


def convert(path: Path, out: Path, voice: str = "feiyu") -> Path:
    """把 path 的语音换成她的音色，写到 out。"""
    asset = _upload(path)
    job = _submit(asset, voice)
    _wait(job)
    req = urllib.request.Request(BASE + f"/v1/jobs/{job}/audio", headers=_auth())
    with urllib.request.urlopen(req, timeout=120) as r:
        out.write_bytes(r.read())
    return out
