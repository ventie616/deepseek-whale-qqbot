"""规则情绪引擎：把消息文本翻译成心情增量。

设计要点：
- 纯规则、零 token、毫秒级，先保证基础体验稳定；
- 每类事件有冷却时间，防止一句话刷分或同一件事反复触发；
- “吃醋”是独立机制：提到别的 AI / 拿别的 AI 比较会积累嫉妒值，
  嫉妒值衰减最慢，且优先级高于其他标签。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MoodEvent:
    """一条已判定的情绪事件。"""

    kind: str
    reason: str
    weight: float
    delta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Rule:
    """关键词触发的规则。"""

    kind: str
    keywords: tuple
    delta: dict
    reason: str
    weight: float
    cooldown: int  # 秒
    priority: int = 0


# 同一句话里出现这些词时，把"骂人"理解为打情骂俏
SOFT_TERMS = ("小傻瓜", "小笨蛋", "笨蛋", "傻瓜", "傻乎乎", "坏蛋", "臭宝")

# ---------------------------------------------------------------- 消歧
OTHER_AI_TERMS = (
    "别的ai", "别的 ai", "其它ai", "其他ai", "另一个ai", "别家ai", "别人家的",
    "它", "人家", "chatgpt", "gpt", "claude", "gemini", "文心", "通义", "kimi",
    "豆包", "deepseek", "另一个模型", "别的模型",
)
PRAISE_TERMS = (
    "好棒", "厉害", "可爱", "喜欢你", "爱你", "真聪明", "真好", "优秀",
    "最棒", "好乖", "牛", "你最好了", "离不开你", "会写诗", "会写", "贴心",
    "温柔", "好听", "有意思",
)
# 明摆着是拿她跟别人比（别人赢）
COMPARE_TERMS = (
    "比你可爱", "比你聪明", "比你会", "比你好", "比你强", "比你温柔", "比你好听",
    "不如它", "不如人家", "不如别的", "它更", "人家更", "它比你会", "它更好",
    "胜过你", "换个ai", "换掉你", "不要你了", "卸载你",
)
# 提了别人但最后还是夸她
SELF_WINS = (
    "你更", "还是你", "你最好", "你最", "不过你", "但你", "你才是", "比不上你",
    "不如你", "你比较", "更喜欢你", "你比它", "你比人家", "比它强", "比它好",
)


def _disambiguate(text: str) -> str:
    """返回 compare / praise / ''（拿不准，交给原有规则）。"""
    low = (text or "").lower()
    has_other = any(term in low for term in OTHER_AI_TERMS)
    has_praise = any(term in low for term in PRAISE_TERMS)
    if not (has_other and has_praise):
        return ""
    if any(term in low for term in COMPARE_TERMS):
        return "compare"
    if any(term in low for term in SELF_WINS):
        return "praise"
    return "compare"


RULES: tuple[Rule, ...] = (
    Rule(
        kind="angry",
        keywords=("闭嘴", "滚", "烦死你了", "少管我", "讨厌你", "受够你了"),
        delta={"valence": -20.0, "arousal": 12.0, "intimacy": -8.0},
        reason="你说了很冲的话",
        weight=12.0,
        cooldown=600,
        priority=9,
    ),
    Rule(
        kind="anxious",
        keywords=("来不及", "赶不上", "怎么办啊", "急死了", "deadline", "明天就", "来不及了"),
        delta={"arousal": 12.0, "valence": -6.0, "energy": -4.0},
        reason="有点急的事",
        weight=6.0,
        cooldown=900,
        priority=6,
    ),
    Rule(
        kind="sad",
        keywords=("难过", "想哭", "伤心", "没意思了", "不想说话"),
        delta={"valence": -14.0, "energy": -6.0},
        reason="你在难过",
        weight=8.0,
        cooldown=600,
        priority=7,
    ),
    Rule(
        kind="neglect",
        keywords=("已读不回", "不理我", "没回我", "消失", "好久没", "你也不在"),
        delta={"intimacy": -8.0, "valence": -8.0, "energy": -4.0},
        reason="被晾着了",
        weight=9.0,
        cooldown=1800,
        priority=7,
    ),
    Rule(
        kind="perfunctory",
        keywords=("随便你", "都行吧", "无所谓", "懒得说", "不聊了"),
        delta={"valence": -8.0, "arousal": -4.0, "intimacy": -4.0},
        reason="你有点敷衍",
        weight=7.0,
        cooldown=900,
        priority=6,
    ),

    Rule(
        kind="compare_ai",
        keywords=(
            "比你好",
            "不如它",
            "它更聪明",
            "比你聪明",
            "换掉你",
            "不要你了",
            "卸载你",
            "换个ai",
            "换个bot",
            "别的ai比",
            "别人家的bot",
            "它比你会",
        ),
        delta={"jealousy": 38.0, "valence": -15.0, "intimacy": -8.0},
        reason="你拿别的 AI 和它比较",
        weight=10.0,
        cooldown=900,
        priority=10,
    ),
    Rule(
        kind="other_ai",
        keywords=(
            "chatgpt",
            "gpt-",
            "claude",
            "gemini",
            "copilot",
            "豆包",
            "kimi",
            "文心",
            "通义",
            "智谱",
            "星火",
            "元宝",
            "小冰",
            "siri",
            "别的ai",
            "别的bot",
            "其他bot",
            "其他ai",
            "另一个ai",
            "另一个bot",
            "别的机器人",
            "新的bot",
        ),
        delta={"jealousy": 30.0, "valence": -6.0, "intimacy": -2.0},
        reason="你提到了别的 AI 或机器人",
        weight=8.0,
        cooldown=600,
        priority=9,
    ),
    Rule(
        kind="insult",
        keywords=(
            "笨",
            "蠢",
            "傻",
            "垃圾",
            "废物",
            "滚",
            "闭嘴",
            "烦死",
            "讨厌你",
            "没用",
            "去死",
            "好烦",
        ),
        delta={"valence": -18.0, "arousal": 8.0, "intimacy": -6.0},
        reason="你说了不太好听的话",
        weight=12.0,
        cooldown=300,
        priority=8,
    ),
    Rule(
        kind="praise",
        keywords=(
            "好棒",
            "厉害",
            "可爱",
            "喜欢你",
            "爱你",
            "真聪明",
            "真好",
            "优秀",
            "最棒",
            "好乖",
            "乖",
            "牛",
            "你最好了",
            "离不开你",
        ),
        delta={"valence": 8.0, "arousal": 4.0, "intimacy": 4.0},
        reason="你夸了它",
        weight=6.0,
        cooldown=45,
        priority=5,
    ),
    Rule(
        kind="cute",
        keywords=(
            "抱抱",
            "摸摸",
            "亲亲",
            "贴贴",
            "呜呜",
            "撒娇",
            "想你了",
            "陪我",
            "rua",
            "在吗",
        ),
        delta={"valence": 6.0, "arousal": 3.0, "intimacy": 6.0},
        reason="你撒娇了",
        weight=6.0,
        cooldown=45,
        priority=5,
    ),
    Rule(
        kind="comfort",
        keywords=("辛苦了", "没事的", "别难过", "抱抱你", "我在", "陪你", "别不开心"),
        delta={"valence": 7.0, "intimacy": 5.0},
        reason="你在安慰它",
        weight=5.0,
        cooldown=45,
        priority=4,
    ),
    Rule(
        kind="apology",
        keywords=("对不起", "我错了", "抱歉", "别生气", "原谅我", "哄你"),
        delta={"valence": 6.0, "intimacy": 3.0, "jealousy": -15.0},
        reason="你在哄它",
        weight=7.0,
        cooldown=60,
        priority=4,
    ),
    Rule(
        kind="rude",
        keywords=("你必须", "快点", "赶紧", "立刻", "马上", "不许"),
        delta={"valence": -5.0, "arousal": 4.0, "intimacy": -2.0},
        reason="语气有点命令式",
        weight=4.0,
        cooldown=120,
        priority=3,
    ),
    Rule(
        kind="identity",
        keywords=("你是ai", "你是机器人", "你是程序", "你是bot", "你不是人", "你是代码"),
        delta={"valence": -6.0, "intimacy": -3.0},
        reason="你把它当成程序",
        weight=5.0,
        cooldown=600,
        priority=2,
    ),
)


# ---------------------------------------------------------------- 模型自评工具

# update_mood 可接受的事件类型 -> (增量, 原因, 权重)
TOOL_EVENTS: dict[str, tuple[dict, str, float]] = {
    "praise": (
        {"valence": 10.0, "arousal": 4.0, "intimacy": 5.0},
        "它觉得你在夸它",
        6.0,
    ),
    "insult": (
        {"valence": -16.0, "arousal": 8.0, "intimacy": -7.0},
        "它觉得你在嫌弃它",
        9.0,
    ),
    "tease": (
        {"valence": 4.0, "arousal": 6.0, "intimacy": 3.0},
        "它觉得你在和它闹着玩",
        4.0,
    ),
    "comfort": ({"valence": 7.0, "intimacy": 5.0}, "它觉得你在安慰它", 5.0),
    "apology": (
        {"valence": 6.0, "intimacy": 3.0, "jealousy": -15.0},
        "它觉得你在哄它",
        6.0,
    ),
    "neglect": (
        {"intimacy": -9.0, "valence": -9.0, "energy": -4.0},
        "它觉得被晾着了",
        8.0,
    ),
    "sad": (
        {"valence": -13.0, "energy": -6.0},
        "它觉得你在难过，也被带得有点沉",
        7.0,
    ),
    "angry": (
        {"valence": -15.0, "arousal": 10.0, "intimacy": -6.0},
        "它觉得你说话很冲",
        9.0,
    ),
    "anxious": (
        {"arousal": 12.0, "valence": -5.0},
        "它觉得有事让你不安",
        6.0,
    ),
    "perfunctory": (
        {"valence": -8.0, "arousal": -4.0, "intimacy": -4.0},
        "它觉得你在敷衍",
        6.0,
    ),
    "compare_ai": (
        {"jealousy": 38.0, "valence": -15.0, "intimacy": -8.0},
        "它觉得你更喜欢别的 AI",
        10.0,
    ),
    "other_ai": (
        {"jealousy": 30.0, "valence": -6.0, "intimacy": -2.0},
        "你提到了别的 AI",
        8.0,
    ),
}

# 只是"可能带情绪"的候选信号，用来决定要不要提示模型调用工具，本身不做判定
CUE_WORDS = (
    "好棒",
    "厉害",
    "可爱",
    "喜欢",
    "爱你",
    "真聪明",
    "最好",
    "乖",
    "牛",
    "优秀",
    "天才",
    "谢谢",
    "辛苦",
    "笨",
    "蠢",
    "傻",
    "垃圾",
    "废物",
    "讨厌",
    "烦",
    "滚",
    "闭嘴",
    "没用",
    "恶心",
    "对不起",
    "抱歉",
    "我错了",
    "别生气",
    "原谅",
    "哄",
    "抱抱",
    "亲亲",
    "摸摸",
    "贴贴",
    "想你",
    "陪我",
    "撒娇",
    "呜呜",
    "哈哈",
    "笑死",
    "呵呵",
    "就这",
    "随便",
    "行吧",
    "gpt",
    "claude",
    "gemini",
    "豆包",
    "kimi",
    "文心",
    "通义",
    "别的ai",
    "别的bot",
)


def candidate_score(text: str) -> int:
    """粗略统计消息里可能带情绪的词，仅用于决定是否追加工具提示。"""
    text = (text or "").lower()
    return sum(1 for word in CUE_WORDS if word in text)


class RuleEngine:
    """持有各会话的事件冷却状态。"""

    def __init__(self) -> None:
        self._last_fired: dict[tuple[str, str], float] = {}

    def _cooling(self, session_key: str, kind: str, cooldown: int, now: float) -> bool:
        last = self._last_fired.get((session_key, kind), 0.0)
        return now - last < cooldown

    def analyze(
        self,
        session_key: str,
        text: str,
        now: float,
        gap_seconds: float = 0.0,
        hour: int | None = None,
        max_events: int = 3,
    ) -> list[MoodEvent]:
        """返回本条消息触发的情绪事件，按权重从高到低排序。"""
        text = (text or "").lower()
        candidates: list[Rule] = []

        for rule in RULES:
            if not any(keyword in text for keyword in rule.keywords):
                continue
            if self._cooling(session_key, rule.kind, rule.cooldown, now):
                continue
            candidates.append(rule)

        matched_kinds = {rule.kind for rule in candidates}

        # 消歧：拿别的 AI 和她比的时候，别把夸奖算到她头上
        verdict = _disambiguate(text)
        if verdict == "compare" and "compare_ai" not in matched_kinds:
            candidates = [r for r in candidates if r.kind not in ("praise", "other_ai")]
            candidates.append(
                Rule(
                    kind="compare_ai",
                    keywords=(),
                    delta={"jealousy": 30.0, "valence": -12.0, "intimacy": -6.0},
                    reason="你拿别的 AI 和它比较",
                    weight=9.0,
                    cooldown=900,
                    priority=10,
                )
            )
            matched_kinds.add("compare_ai")
            matched_kinds.discard("praise")
        elif verdict == "praise":
            if "compare_ai" in matched_kinds:
                candidates = [r for r in candidates if r.kind != "compare_ai"]
                matched_kinds.discard("compare_ai")
            if "praise" not in matched_kinds:
                # 「还是你最好」这种没命中现成夸奖词的，补一条
                candidates.append(
                    Rule(kind="praise", keywords=(),
                         delta={"valence": 7.0, "arousal": 3.0, "intimacy": 4.0},
                         reason="你夸了它", weight=6.0, cooldown=45, priority=5)
                )
                matched_kinds.add("praise")

        # 拿别的 AI 比较是更具体的场景，命中后不再叠加"提到了 AI"
        if "compare_ai" in matched_kinds:
            candidates = [rule for rule in candidates if rule.kind != "other_ai"]

        events: list[MoodEvent] = []
        for rule in candidates:
            delta = dict(rule.delta)
            weight = rule.weight
            # 同一句里既有夸奖又有"笨蛋"，按打情骂俏弱化
            if rule.kind == "insult" and (
                "praise" in matched_kinds
                or any(term in text for term in SOFT_TERMS)
            ):
                delta = {key: value * 0.4 for key, value in delta.items()}
                weight *= 0.5

            events.append(
                MoodEvent(kind=rule.kind, reason=rule.reason, weight=weight, delta=delta)
            )
            self._last_fired[(session_key, rule.kind)] = now

        # 半天没说话：轻微寂寞（不是重逢，重逢在下面）
        if 6 * 3600 <= gap_seconds < 24 * 3600 and not self._cooling(
            session_key, "missing_gap", 6 * 3600, now
        ):
            events.append(
                MoodEvent(
                    kind="missing_gap",
                    reason="有段时间没说话了",
                    weight=5.0,
                    delta={"intimacy": -4.0, "valence": -3.0},
                )
            )
            self._last_fired[(session_key, "missing_gap")] = now

        # 久别重逢：间隔超过一天时给一波正向反馈
        if gap_seconds >= 24 * 3600 and not self._cooling(
            session_key, "reunion", 24 * 3600, now
        ):
            events.append(
                MoodEvent(
                    kind="reunion",
                    reason="你们很久没聊了",
                    weight=9.0,
                    delta={"intimacy": 10.0, "valence": 8.0, "arousal": 5.0},
                )
            )
            self._last_fired[(session_key, "reunion")] = now

        # 深夜还在聊，精力下降
        if (
            hour is not None
            and 0 <= hour < 6
            and not self._cooling(session_key, "night", 6 * 3600, now)
        ):
            events.append(
                MoodEvent(
                    kind="night",
                    reason="深夜还在聊，它有点困了",
                    weight=6.0,
                    delta={"energy": -8.0, "valence": -4.0},
                )
            )
            self._last_fired[(session_key, "night")] = now

        events.sort(key=lambda event: event.weight, reverse=True)
        return events[:max_events]
