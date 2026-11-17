"""心情状态模型：连续量、惰性衰减与标签映射。

设计要点：
- 所有维度取值 0-100，界面上直观显示；
- 不做后台定时器，读取时按时间差一次性补齐衰减（惰性衰减），重启不丢状态；
- 负面情绪不会瞬间消失，只会随时间向基线缓慢回归。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime

try:  # 按东八区判断昼夜，失败时退回系统本地时区
    from zoneinfo import ZoneInfo

    _TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover
    _TZ = None

# 各维度的基线值，所有变化最终都会回归到这里
BASELINE_VALENCE = 60.0  # 愉悦
BASELINE_AROUSAL = 55.0  # 活跃
BASELINE_ENERGY = 70.0  # 精力
BASELINE_INTIMACY = 50.0  # 亲密

# 默认半衰期（小时）
DEFAULT_HALF_LIVES = {
    "valence": 3.0,
    "arousal": 3.0,
    "energy": 2.0,
    "intimacy": 168.0,
    "jealousy": 1.5,
}


@dataclass
class MoodState:
    """单个私聊会话的心情状态。"""

    session_key: str
    valence: float = BASELINE_VALENCE
    arousal: float = BASELINE_AROUSAL
    energy: float = BASELINE_ENERGY
    intimacy: float = BASELINE_INTIMACY
    jealousy: float = 0.0
    label: str = "平静"
    enabled: bool = True
    updated_at: float = field(default_factory=time.time)
    last_interact_at: float = field(default_factory=time.time)


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def local_hour(ts: float) -> int:
    """返回东八区的小时数（0-23）。"""
    if _TZ is not None:
        return datetime.fromtimestamp(ts, _TZ).hour
    return datetime.fromtimestamp(ts).hour


def energy_target(hour: int) -> float:
    """精力在当前时段回归的目标值：深夜很低，午后有个小低谷。"""
    if 0 <= hour < 6:
        return 25.0
    if 13 <= hour < 15:
        return 55.0
    if 22 <= hour < 24:
        return 45.0
    return 78.0


def _toward(
    value: float, target: float, hours: float, half_life: float
) -> float:
    """按半衰期把 value 拉向 target。"""
    if half_life <= 0 or hours <= 0:
        return value
    return target + (value - target) * (0.5 ** (hours / half_life))


DEFAULT_CARRY_WEIGHT = 0.55      # 昨天残留多少进今天
DEFAULT_NEGATIVE_SLOWDOWN = 1.8  # 负面情绪衰减慢几倍


DEFAULT_CARRY_POSITIVE = 0.50   # 正面情绪跨天保留比例
DEFAULT_CARRY_NEGATIVE = 0.75   # 负面情绪跨天保留比例（更黏）


def _lerp(base: float, value: float, weight: float) -> float:
    return clamp(base + (value - base) * weight)


def apply_daily_carry(
    state: MoodState,
    weight: float = DEFAULT_CARRY_WEIGHT,
    positive_weight: float | None = None,
    negative_weight: float | None = None,
) -> None:
    """跨天时，把昨天的状态当作今天的「基础参考」。

    正面和负面可以给不同权重：正面睡一觉淡一半，负面带得更久。
    """
    try:
        w = float(weight)
    except (TypeError, ValueError):
        w = DEFAULT_CARRY_WEIGHT
    w = min(1.0, max(0.0, w))
    pos = w if positive_weight is None else min(1.0, max(0.0, float(positive_weight)))
    neg = w if negative_weight is None else min(1.0, max(0.0, float(negative_weight)))

    # 愉悦：高于基线算正面，低于基线算负面
    vw = pos if state.valence >= BASELINE_VALENCE else neg
    state.valence = _lerp(BASELINE_VALENCE, state.valence, vw)

    # 活跃：同理（低于基线＝没精神，算负面）
    aw = pos if state.arousal >= BASELINE_AROUSAL else neg
    state.arousal = _lerp(BASELINE_AROUSAL, state.arousal, aw)

    # 亲密：高亲密算正面，低亲密（疏远）算负面
    iw = pos if state.intimacy >= BASELINE_INTIMACY else neg
    state.intimacy = _lerp(BASELINE_INTIMACY, state.intimacy, iw)

    # 吃醋永远是负面
    state.jealousy = clamp(state.jealousy * neg)


def apply_decay(state: MoodState, now: float, half_lives: dict | None = None,
                max_hours: float | None = None) -> None:
    """把 state 的时间推进到 now，期间的变化用指数衰减补齐。

    max_hours: 跨天时用来截断过夜时长，避免睡一觉负面情绪被抹平。
    """
    half_lives = half_lives or DEFAULT_HALF_LIVES
    hours = (now - state.updated_at) / 3600.0
    if max_hours is not None and hours > max_hours:
        hours = max_hours
    if hours <= 0:
        state.updated_at = now
        return

    # 负面情绪衰减更慢（难过不会睡一觉就好）
    slowdown = float(
        (half_lives or {}).get("negative_slowdown", DEFAULT_NEGATIVE_SLOWDOWN) or 1.0
    )
    valence_hl = half_lives["valence"]
    if state.valence < BASELINE_VALENCE and slowdown > 1.0:
        valence_hl = valence_hl * slowdown
    state.valence = clamp(
        _toward(state.valence, BASELINE_VALENCE, hours, valence_hl)
    )
    state.arousal = clamp(
        _toward(state.arousal, BASELINE_AROUSAL, hours, half_lives["arousal"])
    )
    state.energy = clamp(
        _toward(
            state.energy, energy_target(local_hour(now)), hours, half_lives["energy"]
        )
    )
    state.intimacy = clamp(
        _toward(state.intimacy, BASELINE_INTIMACY, hours, half_lives["intimacy"])
    )
    jealous_hl = half_lives["jealousy"]
    if state.jealousy >= 10.0 and slowdown > 1.0:
        jealous_hl = jealous_hl * min(2.0, slowdown)
    state.jealousy = clamp(
        _toward(state.jealousy, 0.0, hours, jealous_hl)
    )
    state.updated_at = now


def apply_event(state: MoodState, event) -> None:
    """把一条情绪事件的增量写入状态。"""
    for key, delta in event.delta.items():
        if hasattr(state, key):
            setattr(state, key, clamp(getattr(state, key) + delta))
    state.valence = clamp(state.valence)
    state.arousal = clamp(state.arousal)
    state.energy = clamp(state.energy)
    state.intimacy = clamp(state.intimacy)
    state.jealousy = clamp(state.jealousy)


def derive_label(state: MoodState) -> str:
    """由连续量推导出一个可展示、可驱动语气的心情标签。"""
    if state.jealousy >= 28.0:
        return "吃醋"
    # 强负面：愤怒（低愉悦 + 高唤起）
    if state.valence < 25.0 and state.arousal >= 70.0:
        return "生气"
    # 焦虑：唤起高、精力还在，但心里不安
    if state.arousal >= 68.0 and state.valence < 55.0:
        return "焦虑"
    # 难过：又丧又没劲
    if state.valence < 30.0 and state.energy < 45.0:
        return "难过"
    if state.valence < 30.0 and state.arousal >= 60.0:
        return "烦躁"
    if state.valence < 35.0:
        return "委屈"
    # 失望：不生气、不焦虑，就是提不起期待
    if state.valence < 45.0 and state.arousal < 45.0:
        return "失望"
    if state.valence < 48.0:
        return "低落"
    # 寂寞：关系感掉下来了，但情绪没崩
    if state.intimacy < 35.0 and state.valence < 62.0 and state.energy >= 40.0:
        return "寂寞"
    if state.energy < 35.0:
        return "困了"
    if state.energy < 50.0 and state.valence < 65.0:
        return "无聊"
    if state.intimacy >= 70.0 and state.valence >= 60.0 and state.arousal >= 55.0:
        return "撒娇"
    if state.valence >= 75.0 and state.arousal >= 60.0:
        return "开心"
    return "平静"


def intensity_level(state: MoodState) -> str:
    """情绪偏离基线的程度，分轻/中/强三档，用来限制表演幅度。"""
    score = max(
        abs(state.valence - BASELINE_VALENCE) / 60.0,
        abs(state.arousal - BASELINE_AROUSAL) / 50.0,
        abs(state.energy - BASELINE_ENERGY) / 55.0,
        state.jealousy / 55.0,
    )
    if score >= 0.66:
        return "强"
    if score >= 0.33:
        return "中"
    return "轻"


def intimacy_desc(value: float) -> str:
    """把亲密度翻译成一句关系描述。"""
    if value < 30.0:
        return "有点陌生"
    if value < 50.0:
        return "还算熟悉"
    if value < 70.0:
        return "挺亲近"
    if value < 85.0:
        return "很黏你"
    return "离不开你"
