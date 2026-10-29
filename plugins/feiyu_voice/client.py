"""HTTP-only client: the bot does not load TTS/RVC weights or share server paths."""

import asyncio
import re
import time
from pathlib import Path

import httpx


class VoiceClient:
    def __init__(self, base_url, api_key):
        self.http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": "Bearer " + api_key},
            timeout=60,
            follow_redirects=False,
            trust_env=False,
        )

    async def close(self):
        await self.http.aclose()

    async def request(self, method, path, **kwargs):
        response = await self.http.request(method, path, **kwargs)
        if response.is_error:
            try:
                detail = response.json().get("detail", "Request failed")
            except ValueError:
                detail = "Request failed"
            raise RuntimeError(f"Voice service HTTP {response.status_code}: {detail}")
        return response

    async def upload(self, path, filename=None):
        path = Path(path)
        with path.open("rb") as stream:
            response = await self.request(
                "POST", "/v1/assets", files={"file": (filename or path.name, stream)}
            )
        return response.json()["id"]

    async def submit(self, kind, payload):
        routes = {
            "speech": "speech",
            "conversion": "voice-conversion",
            "singing": "singing",
        }
        return (await self.request("POST", "/v1/" + routes[kind], json=payload)).json()

    async def status(self, job_id):
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise ValueError("Invalid job ID")
        return (await self.request("GET", "/v1/jobs/" + job_id)).json()

    async def wait(self, job_id, timeout=1800):
        started = time.monotonic()
        while time.monotonic() - started < timeout:
            result = await self.status(job_id)
            if result["status"] in ("success", "failed", "interrupted"):
                if result["status"] != "success":
                    raise RuntimeError(result.get("error", "Voice job failed"))
                return result
            await asyncio.sleep(2)
        raise TimeoutError("Voice job is still pending; use the job status command")

    async def download(self, job_id, folder):
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise ValueError("Invalid job ID")
        path = Path(folder) / (job_id + ".wav")
        if path.exists():
            return path
        response = await self.request("GET", "/v1/jobs/" + job_id + "/audio")
        if len(response.content) > 128 * 1024**2 or response.content[:4] != b"RIFF":
            raise ValueError("Voice service returned an invalid or oversized WAV")
        with path.open("xb") as stream:
            stream.write(response.content)
        return path
