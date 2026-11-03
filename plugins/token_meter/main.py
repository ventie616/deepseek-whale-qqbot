"""Token 计量：把每次 LLM 调用的 token 用量记下来，供控制台展示。

做法：在 initialize() 时把当前所有 provider 实例的 text_chat 包一层，
调用完从 resp.raw_completion.usage 里取 prompt/completion/cached。
插件直接调 provider（B站那套）也能抓到，因为它们用的还是同一个实例。

数据写 D:\\QQrobot\\token_usage.jsonl（一行一次调用），超过 5 MB 自动轮转。
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

ROOT = Path(r"D:\QQrobot")
USAGE = ROOT / "token_usage.jsonl"
MAX_MB = 5


SOURCE_MAP = (
    # 后台自己上网/读书/查资料 —— 这类也烧 token，单独算
    ("web_explore", "网页浏览"),
    ("her_reading", "网页浏览"),
    ("web_access", "网页浏览"),
    ("slang_learn", "网页浏览"),
    ("her_room", "自省"),
    ("qzone_post", "说说"),
    ("cover_sing", "翻唱"),
    ("bili_pusher", "B站"),
)


def estimate_tokens(text: str) -> int:
    """没拿到官方 usage 时按字符粗估（中文约 1 字 ≈ 1.6 token）。"""
    s = str(text or "")
    if not s:
        return 0
    cn = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
    other = len(s) - cn
    return int(cn * 1.6 + other * 0.3)


def _module_of_frame(frame) -> str:
    name = str(frame.f_globals.get("__name__") or "")
    # 插件是以 __name__ == "main" 加载的，光看模块名分不出来 → 看文件路径
    path = str(frame.f_globals.get("__file__") or "").replace("\\", "/").lower()
    for key, label in SOURCE_MAP:
        if key in path or key in name:
            return label
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


def _scan_callers(depth: int = 14) -> str:
    """从调用栈里找最近的一层插件代码，比只看上一层可靠（异步包装会插好几层）。"""
    import inspect as _inspect

    try:
        stack = _inspect.stack()
    except Exception:
        return ""
    for fr in stack[1:depth]:
        path = str(fr.frame.f_globals.get("__file__") or "").replace("\\", "/").lower()
        if "/token_meter/" in path:
            continue          # 跳过计量插件自己
        if "/plugins/" in path:
            label = _module_of_frame(fr.frame)
            if label not in ("其他", "main"):
                return label
            # 插件目录名兜底
            seg = path.split("/plugins/")[-1].split("/")[0]
            return seg or label
    return ""


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
            caller = _scan_callers() or "其他"
            resp = await original(*args, **kwargs)
            try:
                u = _usage_of(resp)
                if not u:
                    prompt_text = " ".join(str(a) for a in args) + " " + " ".join(
                        f"{k}={v}" for k, v in kwargs.items() if k != "model")
                    est_p = estimate_tokens(prompt_text)
                    est_c = estimate_tokens(getattr(resp, "completion_text", "") or "")
                    if est_p or est_c:
                        u = {"prompt": est_p, "completion": est_c,
                             "total": est_p + est_c, "cached": 0, "est": True}
                if u:
                    model = ""
                    try:
                        # 优先用本次请求实际指定的模型（cost_router 会改 kwargs/model）
                        model = str(kwargs.get("model") or "") or ""
                    except Exception:
                        model = ""
                    if not model:
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

        # 包流式接口：webchat 走 text_chat_stream，否则控制台看不到这部分用量
        original_stream = getattr(provider, "text_chat_stream", None)
        if original_stream is not None and inspect.isasyncgenfunction(original_stream):
            @functools.wraps(original_stream)
            async def patched_stream(*args, **kwargs):
                caller = _scan_callers() or "其他"
                agen = original_stream(*args, **kwargs)
                last = None
                collected: list[str] = []
                try:
                    async for chunk in agen:
                        last = chunk
                        try:
                            txt = getattr(chunk, "completion_text", None)
                            if txt:
                                collected.append(str(txt))
                        except Exception:
                            pass
                        yield chunk
                finally:
                    # 注意：调用方可能提前 break/close，所以记录放在 finally 里
                    try:
                        u = _usage_of(last) if last is not None else None
                        if not u:
                            prompt_text = " ".join(str(a) for a in args) + " " + " ".join(
                                f"{k}={v}" for k, v in kwargs.items() if k != "model")
                            est_p = estimate_tokens(prompt_text)
                            est_c = estimate_tokens("".join(collected))
                            if est_p or est_c:
                                u = {"prompt": est_p, "completion": est_c,
                                     "total": est_p + est_c, "cached": 0, "est": True}
                        if u:
                            model = str(kwargs.get("model") or "") or ""
                            if not model:
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

            try:
                provider.text_chat_stream = patched_stream
            except Exception:
                pass

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
