"""判断当前是不是 DeepSeek 的谷值（空闲）时段。

规则（按官方价格页）：
  工作日 9:00-12:00、14:00-18:00 = 高峰；
  工作日其余时间、周末、法定节假日 = 空闲（谷值，半价）。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

ROOT = Path(r"D:\QQrobot")
CFG = ROOT / "offpeak.json"

DEFAULT_HOLIDAYS: list[str] = []


def _cfg() -> dict:
    try:
        return json.loads(CFG.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def is_offpeak(now: datetime | None = None) -> tuple[bool, str]:
    """返回 (是否谷值, 原因)。"""
    cfg = _cfg()
    if not cfg.get("enabled", True):
        return True, "限制已关"
    dt = now or datetime.now()
    iso = dt.date().isoformat()
    if iso in set(cfg.get("holidays") or DEFAULT_HOLIDAYS):
        return True, "节假日"
    if dt.weekday() >= 5:
        return True, "周末"
    h = dt.hour + dt.minute / 60
    if 9 <= h < 12 or 14 <= h < 18:
        return False, "工作日高峰"
    return True, "谷值时段"


def describe() -> str:
    ok, why = is_offpeak()
    cfg = _cfg()
    return (f"现在{'是' if ok else '不是'}谷值（{why}）\n"
            f"工作日高峰 9-12 / 14-18，其余全算谷值；周末与节假日全天谷值\n"
            f"周末自动算谷值，节假日需在 offpeak.json 里登记（当前 {len(cfg.get('holidays') or [])} 个）")
