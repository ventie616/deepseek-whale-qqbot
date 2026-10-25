"""Token 计量：把每次 LLM 调用的 token 用量记下来，供控制台展示。

做法：在 initialize() 时把当前所有 provider 实例的 text_chat 包一层，
调用完从 resp.raw_completion.usage 里取 prompt/completion/cached。
插件直接调 provider（B站那套）也能抓到，因为它们用的还是同一个实例。

数据写 C:\\QQBot\\token_usage.jsonl（一行一次调用），超过 5 MB 自动轮转。
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
import time
from datetime import datetime
from pathlib import Path

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter

ROOT = Path(r"C:\QQBot")
USAGE = ROOT / "token_usage.jsonl"
MAX_MB = 5


def _module_of_frame(frame) -> str:
    name = str(frame.f_globals.get("__name__") or "")
    if "bili_pusher" in name:
        return "B站"
    if "agent" in name or "pipeline" in name or "respond" in name:
        return "聊天"
    if name.startswith("astrbot"):
        return "框架"
    return name.rsplit(".", 1)[-1] or "其他"


def _usage_of(resp) -> dict:
    """从 LLMResponse.raw_completion.usage 里取 token 数。"""
    raw = getattr(resp, "raw_completion", None)
    usage = getattr(raw, "usage", None)
    if usage is None and isinstance(raw, dict):
        usage = raw.get("usage")
    if usage is None:
        return {}
    get = (lambda k: usage.get(k)) if isinstance(usage, dict) else (lambda k: getattr(usage, k, None))
    prompt = get("prompt_tokens") or 0
    completion = get("completion_tokens") or 0
    total = get("total_tokens") or (prompt + completion)
    cached = 0
    details = get("prompt_tokens_details")
    if isinstance(details, dict):
        cached = details.get("cached_tokens") or 0
    elif details is not None:
        cached = getattr(details, "cached_tokens", 0) or 0
    if not cached:
        hit = get("prompt_cache_hit_tokens")
        cached = int(hit) if isinstance(hit, int) else 0
    return {"prompt": int(prompt), "completion": int(completion),
            "total": int(total), "cached": int(cached)}


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        super().__init__(context)
        self._wrapped: set[int] = set()
        self._task: asyncio.Task | None = None
        self._stop = False

    # ---------- 记录 ----------
    def _append(self, rec: dict) -> None:
        try:
            if USAGE.exists() and USAGE.stat().st_size > MAX_MB * 1024 * 1024:
                USAGE.replace(USAGE.with_suffix(".jsonl.old"))
            with USAGE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _wrap(self, provider) -> None:
        if id(provider) in self._wrapped:
            return
        original = getattr(provider, "text_chat", None)
        if original is None or not inspect.iscoroutinefunction(original):
            return
        plugin = self

        @functools.wraps(original)
        async def patched(*args, **kwargs):
            caller = None
            try:
                caller = _module_of_frame(inspect.stack()[1].frame)
            except Exception:
                caller = "其他"
            resp = await original(*args, **kwargs)
            try:
                u = _usage_of(resp)
                if u:
                    model = ""
                    try:
                        model = provider.get_model()
                    except Exception:
                        model = getattr(provider, "model_name", "") or ""
                    plugin._append({
                        "t": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "model": model,
                        "source": caller,
                        **u,
                    })
            except Exception:
                pass
            return resp

        try:
            provider.text_chat = patched
            self._wrapped.add(id(provider))
        except Exception:
            pass

    def _wrap_all(self) -> int:
        pm = getattr(self.context, "provider_manager", None)
        if pm is None:
            return 0
        insts = list(getattr(pm, "provider_insts", None) or [])
        inst_map = getattr(pm, "inst_map", None) or {}
        if isinstance(inst_map, dict):
            insts += list(inst_map.values())
        n = 0
        for p in insts:
            before = len(self._wrapped)
            self._wrap(p)
            if len(self._wrapped) > before:
                n += 1
        return n

    async def _loop(self) -> None:
        while not self._stop:
            try:
                n = await asyncio.to_thread(self._wrap_all)
                if n:
                    logger.info(f"[token_meter] 补挂了 {n} 个 provider")
            except Exception:
                pass
            await asyncio.sleep(30)

    async def initialize(self) -> None:
        n = self._wrap_all()
        logger.info(f"[token_meter] 已挂上 {n} 个 provider")
        if not USAGE.exists():
            USAGE.write_text("", encoding="utf-8")
        self._task = asyncio.create_task(self._loop())

    async def terminate(self) -> None:
        self._stop = True
        if self._task:
            self._task.cancel()

    @filter.on_llm_request()
    async def ensure_wrapped(self, event: AstrMessageEvent, req):
        """provider 可能是懒加载的，每次请求前补挂一次。"""
        self._wrap_all()

    # ---------- 指令 ----------
    @filter.command("token")
    async def token_cmd(self, event: AstrMessageEvent):
        """看今天的 token 消耗"""
        today = datetime.now().strftime("%Y-%m-%d")
        agg = {"prompt": 0, "completion": 0, "cached": 0, "total": 0, "calls": 0}
        if USAGE.exists():
            for line in USAGE.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if not str(r.get("t", "")).startswith(today):
                    continue
                for k in ("prompt", "completion", "cached", "total"):
                    agg[k] += int(r.get(k) or 0)
                agg["calls"] += 1
        if not agg["calls"]:
            yield event.plain_result("今天还没调过模型")
            return
        yield event.plain_result(
            f"今天 {agg['calls']} 次调用\n"
            f"输入 {agg['prompt']:,}（缓存 {agg['cached']:,}）\n"
            f"输出 {agg['completion']:,}\n"
            f"合计 {agg['total']:,} tokens"
        )
