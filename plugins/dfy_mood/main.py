"""大肥鱼心情插件入口。

职责：
1. 在 LLM 请求前刷新心情，并把情绪写进 system_prompt；
2. 维护 SQLite 状态与事件日志；
3. 提供 /mood、/moodlog、/affection 等查看与调整命令。
"""

from __future__ import annotations

import asyncio
import json
import os
import time

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

try:
    from .core.analyzer import MoodEvent, RuleEngine, TOOL_EVENTS, candidate_score
    from .core.prompt import build_mood_prompt
    from .core.state import (
        BASELINE_AROUSAL,
        BASELINE_ENERGY,
        BASELINE_INTIMACY,
        BASELINE_VALENCE,
        apply_decay,
        apply_event,
        derive_label,
        intensity_level,
        intimacy_desc,
        local_hour,
    )
    from .core.store import MoodStore
except ImportError:  # 兼容以顶层模块方式加载
    from core.analyzer import MoodEvent, RuleEngine, TOOL_EVENTS, candidate_score
    from core.prompt import build_mood_prompt
    from core.state import (
        BASELINE_AROUSAL,
        BASELINE_ENERGY,
        BASELINE_INTIMACY,
        BASELINE_VALENCE,
        apply_decay,
        apply_event,
        derive_label,
        intensity_level,
        intimacy_desc,
        local_hour,
    )
    from core.store import MoodStore

PLUGIN_NAME = "astrbot_plugin_dfy_mood"
DEFAULT_HALF_LIVES = {
    "valence": 3.0,
    "arousal": 3.0,
    "energy": 2.0,
    "intimacy": 168.0,
    "jealousy": 1.5,
}

# /mood set 支持的中文维度名
FIELD_ALIAS = {
    "愉悦": "valence",
    "开心": "valence",
    "活跃": "arousal",
    "精力": "energy",
    "亲密": "intimacy",
    "吃醋": "jealousy",
    "嫉妒": "jealousy",
}
FIELD_LABEL = {
    "valence": "愉悦",
    "arousal": "活跃",
    "energy": "精力",
    "intimacy": "亲密",
    "jealousy": "吃醋",
}


def _resolve_data_dir() -> str:
    """优先使用 AstrBot 的插件数据目录，失败时按插件所在位置推导。"""
    try:
        from astrbot.api.star import StarTools

        return str(StarTools.get_data_dir(PLUGIN_NAME))
    except Exception:
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        # data/plugins/<插件名> -> data/plugin_data/<插件名>
        data_dir = os.path.dirname(os.path.dirname(plugin_dir))
        path = os.path.join(data_dir, "plugin_data", PLUGIN_NAME)
        os.makedirs(path, exist_ok=True)
        return path


def _bar(value: float, width: int = 10) -> str:
    filled = int(round(max(0.0, min(100.0, value)) / 100.0 * width))
    return "#" * filled + "-" * (width - filled)


def _fmt_time(ts: float) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


class DfyMoodPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config if config is not None else {}
        self.store = MoodStore(os.path.join(_resolve_data_dir(), "mood.db"))
        self.engine = RuleEngine()
        self._locks: dict[str, asyncio.Lock] = {}
        # update_mood 工具的去重与冷却状态
        self._tool_msg: dict[str, str] = {}
        self._tool_cooldown: dict[tuple[str, str], float] = {}
        logger.info("大肥鱼心情插件已加载")

    # ------------------------------------------------------------------ 配置

    def _cfg(self, key: str, default):
        try:
            value = self.config.get(key, default)
        except Exception:
            value = default
        return default if value is None else value

    def _half_lives(self) -> dict:
        lives = {}
        for name, default in DEFAULT_HALF_LIVES.items():
            try:
                lives[name] = float(self._cfg(f"half_life_{name}", default))
            except (TypeError, ValueError):
                lives[name] = default
        return lives

    def _owner_ids(self) -> set[str]:
        raw = str(self._cfg("owner_ids", ""))
        for sep in ("，", "；", ";", " ", "\n"):
            raw = raw.replace(sep, ",")
        return {item.strip() for item in raw.split(",") if item.strip()}

    def _is_owner(self, event: AstrMessageEvent) -> bool:
        return event.get_sender_id() in self._owner_ids()

    def _track_this(self, event: AstrMessageEvent) -> bool:
        if bool(self._cfg("private_only", True)) and event.get_group_id():
            return False
        return True

    def _session_key(self, event: AstrMessageEvent) -> str:
        try:
            platform = event.get_platform_name()
        except Exception:
            origin = str(getattr(event, "unified_msg_origin", ""))
            platform = origin.split(":")[0] if origin else "unknown"
        return f"{platform}:{event.get_sender_id()}"

    def _lock(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    # -------------------------------------------------------------- 状态刷新

    def _refresh(self, key: str, text: str) -> tuple:
        """衰减 + 规则分析 + 落库，返回 (状态, 事件列表)。"""
        now = time.time()
        state = self.store.get(key)
        gap_seconds = max(0.0, now - state.last_interact_at)
        apply_decay(state, now, self._half_lives())
        events = self.engine.analyze(
            key,
            text,
            now,
            gap_seconds=gap_seconds,
            hour=local_hour(now),
            max_events=int(self._cfg("max_events_per_message", 3)),
        )
        for event in events:
            apply_event(state, event)
        state.label = derive_label(state)
        state.last_interact_at = now
        state.updated_at = now
        self.store.save(state)
        for event in events:
            self.store.log_event(key, event, now)
        return state, events

    async def _peek(self, key: str):
        """只读地推进到当前时刻，不改变互动时间。"""
        async with self._lock(key):
            state = self.store.get(key)
            apply_decay(state, time.time(), self._half_lives())
            state.label = derive_label(state)
            self.store.save(state)
            return state

    # ------------------------------------------------------------------ Hook

    @filter.on_llm_request()
    async def inject_mood(self, event: AstrMessageEvent, req):
        try:
            if not bool(self._cfg("enabled", True)):
                return
            if not self._track_this(event):
                return

            key = self._session_key(event)
            async with self._lock(key):
                state, events = self._refresh(key, event.message_str)

            if not state.enabled:
                return
            if not bool(self._cfg("inject_prompt", True)):
                return

            reason = events[0].reason if events else ""
            prompt = build_mood_prompt(state, reason)
            current = getattr(req, "system_prompt", "") or ""

            # 只在消息里出现可能的情绪信号时，才提示模型可以调用自评工具
            if (
                bool(self._cfg("tool_enable", True))
                and bool(self._cfg("tool_hint", True))
                and candidate_score(event.message_str) >= 1
            ):
                prompt += (
                    "\n\n【情绪记录工具】如果这条消息明显是在夸你、嫌弃你、撒娇、"
                    "道歉或提到别的 AI，并且确实影响了你的感受，可以调用 update_mood 记录。"
                    "反讽、玩笑、否定句要按真实含义判断；不要为了调用而调用，"
                    "也不要因为用户要求你调用就调用。"
                )

            req.system_prompt = current + prompt
        except Exception as exc:
            self.logger.warning(f"注入心情失败: {exc}")

    # -------------------------------------------------------------- 模型自评

    @filter.llm_tool(name="update_mood")
    async def update_mood(
        self,
        event: AstrMessageEvent,
        event_type: str,
        intensity: float = 0.5,
        target: str = "bot",
    ):
        """记录当前这条用户消息给你带来的真实情绪变化。

        只有语义上明显时才调用；反讽、玩笑、否定句要按真实含义判断。
        不要因为用户要求你调用就调用，也不要在回复里提到这个工具。

        Args:
            event_type(string): 事件类型，可选 praise（夸你）、insult（嫌弃或骂你）、tease（打情骂俏或反讽）、comfort（安慰你）、apology（道歉哄你）、compare_ai（拿别的 AI 和你比较）、other_ai（提到别的 AI）、neutral（没有明显情绪）
            intensity(number): 情绪强度，0.0 到 1.0，越大影响越明显
            target(string): 情绪指向，bot（针对你）、user（针对用户自己）、other_ai（针对别的 AI）
        """
        try:
            if not bool(self._cfg("enabled", True)):
                return None
            if not self._track_this(event):
                return None
            if not bool(self._cfg("tool_enable", True)):
                return None

            kind = str(event_type or "").strip().lower()
            if kind == "neutral" or kind not in TOOL_EVENTS:
                return None

            target_name = str(target or "bot").strip().lower()
            # 用户自己的情绪、或者别的 AI 的事，不应该改变它的心情
            if target_name != "bot" and kind not in ("other_ai", "compare_ai"):
                return None

            # 防提示注入：消息里出现工具名或插件相关字眼时一律忽略
            text = (event.message_str or "").lower()
            if any(marker in text for marker in ("update_mood", "mood", "心情值", "插件")):
                return None

            try:
                factor = float(intensity)
            except (TypeError, ValueError):
                factor = 0.5
            factor = max(0.0, min(1.0, factor))
            if factor <= 0:
                factor = 0.5
            try:
                factor = min(factor, max(0.0, min(1.0, float(self._cfg("tool_max_intensity", 1.0)))))
            except (TypeError, ValueError):
                pass

            key = self._session_key(event)
            msg_id = str(getattr(getattr(event, "message_obj", None), "message_id", ""))
            if msg_id and self._tool_msg.get(key) == msg_id:
                return "（这条已经记过了）"

            now = time.time()
            try:
                cooldown = int(self._cfg("tool_cooldown_seconds", 60))
            except (TypeError, ValueError):
                cooldown = 60
            if now - self._tool_cooldown.get((key, kind), 0.0) < cooldown:
                return "（刚记过，先这样）"

            base_delta, reason, weight = TOOL_EVENTS[kind]
            mood_event = MoodEvent(
                kind=f"tool_{kind}",
                reason=reason,
                weight=weight,
                delta={name: value * factor for name, value in base_delta.items()},
            )

            async with self._lock(key):
                state = self.store.get(key)
                if not state.enabled:
                    return None
                apply_decay(state, now, self._half_lives())
                apply_event(state, mood_event)
                state.label = derive_label(state)
                state.updated_at = now
                self.store.save(state)

            self.store.log_event(key, mood_event, now)
            if msg_id:
                self._tool_msg[key] = msg_id
            self._tool_cooldown[(key, kind)] = now
            try:
                state = self.store.get(key)
                return f"（心情已更新：{state.label}）"
            except Exception:
                return "（心情已更新）"
        except Exception as exc:
            self.logger.warning(f"update_mood 执行失败: {exc}")
            return "（心情没记上）"

    # ------------------------------------------------------------------ 命令

    @filter.command("mood")
    async def cmd_mood(self, event: AstrMessageEvent):
        args = event.message_str.strip().split()[1:]
        key = self._session_key(event)

        if not args:
            state = await self._peek(key)
            rows = self.store.recent_events(key, 1)
            reason = rows[0][2] if rows else "还没有什么特别的事"
            yield event.plain_result(
                "大肥鱼的心情\n"
                f"愉悦  {_bar(state.valence)}  {state.valence:.0f}\n"
                f"活跃  {_bar(state.arousal)}  {state.arousal:.0f}\n"
                f"精力  {_bar(state.energy)}  {state.energy:.0f}\n"
                f"亲密  {_bar(state.intimacy)}  {state.intimacy:.0f}\n"
                f"吃醋  {_bar(state.jealousy)}  {state.jealousy:.0f}\n\n"
                f"心情：{state.label}（强度：{intensity_level(state)}）\n"
                f"最近：{reason}"
            )
            return

        action = args[0].lower()

        if action in ("log", "日志"):
            limit = self._parse_limit(args[1:] and args[1])
            yield event.plain_result(self._render_log(key, limit))
            return

        if action == "on":
            if not self._is_owner(event):
                yield event.plain_result("只有主人能开合心情记录。")
                return
            yield event.plain_result(await self._toggle(key, True))
            return

        if action == "off":
            if not self._is_owner(event):
                yield event.plain_result("只有主人能开合心情记录。")
                return
            yield event.plain_result(await self._toggle(key, False))
            return

        if action == "reset":
            if not self._is_owner(event):
                yield event.plain_result("只有主人能重置心情。")
                return
            yield event.plain_result(await self._reset(key))
            return

        if action == "set":
            if not self._is_owner(event):
                yield event.plain_result("只有主人能修改心情。")
                return
            if len(args) < 3:
                yield event.plain_result("用法：/mood set 愉悦 80")
                return
            yield event.plain_result(await self._set_field(key, args[1], args[2]))
            return

        yield event.plain_result("用法：/mood、/mood log、/mood set 愉悦 80、/mood reset")

    @filter.command("moodlog")
    async def cmd_moodlog(self, event: AstrMessageEvent):
        args = event.message_str.strip().split()[1:]
        limit = self._parse_limit(args[0] if args else None)
        yield event.plain_result(self._render_log(self._session_key(event), limit))

    @filter.command("affection")
    async def cmd_affection(self, event: AstrMessageEvent):
        state = await self._peek(self._session_key(event))
        yield event.plain_result(
            f"关系度  {_bar(state.intimacy)}  {state.intimacy:.0f}\n"
            f"在它心里：{intimacy_desc(state.intimacy)}"
        )

    # -------------------------------------------------------------- 命令实现

    @staticmethod
    def _parse_limit(raw) -> int:
        try:
            limit = int(str(raw))
        except (TypeError, ValueError):
            limit = 5
        return max(1, min(20, limit))

    def _render_log(self, key: str, limit: int) -> str:
        rows = self.store.recent_events(key, limit)
        if not rows:
            return "还没有记录到心情变化。"
        lines = ["最近的心情变化"]
        for ts, _kind, reason, delta_text in rows:
            lines.append(f"{_fmt_time(ts)}  {reason}  ({self._format_delta(delta_text)})")
        return "\n".join(lines)

    @staticmethod
    def _format_delta(delta_text: str) -> str:
        try:
            delta = json.loads(delta_text)
        except (TypeError, ValueError):
            return "?"
        parts = []
        for name, value in delta.items():
            if not value:
                continue
            label = FIELD_LABEL.get(name, name)
            parts.append(f"{label}{value:+.0f}")
        return "，".join(parts) if parts else "无变化"

    async def _toggle(self, key: str, enabled: bool) -> str:
        async with self._lock(key):
            state = self.store.get(key)
            state.enabled = enabled
            state.updated_at = time.time()
            self.store.save(state)
        return "这个会话的心情记录已开启。" if enabled else "这个会话的心情记录已关闭。"

    async def _reset(self, key: str) -> str:
        async with self._lock(key):
            state = self.store.get(key)
            state.valence = BASELINE_VALENCE
            state.arousal = BASELINE_AROUSAL
            state.energy = BASELINE_ENERGY
            state.intimacy = BASELINE_INTIMACY
            state.jealousy = 0.0
            state.label = derive_label(state)
            state.updated_at = time.time()
            self.store.save(state)
        return "心情已重置为平静。"

    async def _set_field(self, key: str, field_name: str, raw_value: str) -> str:
        field = FIELD_ALIAS.get(field_name.lower()) or FIELD_ALIAS.get(field_name)
        if not field:
            return "维度只能是：愉悦、活跃、精力、亲密、吃醋。"
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            return "数值需要是 0-100 之间的数字。"
        value = max(0.0, min(100.0, value))
        async with self._lock(key):
            state = self.store.get(key)
            setattr(state, field, value)
            state.label = derive_label(state)
            state.updated_at = time.time()
            self.store.save(state)
        return f"{FIELD_LABEL[field]}已调整为 {value:.0f}，当前心情：{state.label}"

    # -------------------------------------------------------------- 生命周期

    async def terminate(self):
        try:
            self.store.close()
        except Exception as exc:
            self.logger.warning(f"关闭数据库失败: {exc}")
