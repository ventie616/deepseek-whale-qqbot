"""每日心情：让她每天状态不一样，但大部分时间是开心的。

- 状态文件：C:\\QQBot\\mood.json
- 每天第一次被用到时掷一次（加权：开心 65% / 平静 18% / 有一点累 10% / 有点丧 5% / 炸毛 2%）
- 注入系统提示，影响语气但不改变人格
- 指令：/心情（看今天）、/心情 换（重掷，一天一次）
"""

from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, date
from pathlib import Path

from astrbot.api import logger, star


try:  # 易变注入挂到用户消息后，让 system_prompt 保持稳定（缓存友好）
    from astrbot.core.agent.message import TextPart as _TextPart
except Exception:  # pragma: no cover
    _TextPart = None


def _extra(req, text: str) -> None:
    if _TextPart is not None and hasattr(req, "extra_user_content_parts"):
        req.extra_user_content_parts.append(_TextPart(text=text))
    else:
        req.system_prompt += text
from astrbot.api.event import AstrMessageEvent, filter

STATE = Path(r"C:\QQBot\mood.json")

# (键, 权重, 一句话, 语气提示)
# 大部分时间是开心的：开心/元气 合计 78%
POOL = [
    ("开心", 50, "今天心情很好，尾巴都在晃", "话多一点，愿意分享，看到什么都想给他看"),
    ("元气", 28, "今天特别有精神，想找点事干", "说话有劲，爱接话，会主动提议一起做点什么"),
    ("平静", 12, "今天很平静，懒懒的", "话少但温和，不冷淡，慢慢说"),
    ("有点累", 5, "今天有点累，趴着不想动", "话更短，但不是生气；被关心会软下来"),
    ("有点丧", 3, "今天有点丧，不太想说话", "只回一两句，不迁怒对方；被问会说'没事，就是有点闷'"),
    ("炸毛", 2, "今天莫名其妙有点烦", "语气冲一点，但对方一哄就好"),
]


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        super().__init__(context)
        self._cache: dict | None = None

    # ---------------- 状态 ----------------
    def _today(self) -> str:
        return date.today().isoformat()

    def _roll(self) -> dict:
        keys = [p[0] for p in POOL]
        weights = [p[1] for p in POOL]
        key = random.choices(keys, weights=weights, k=1)[0]
        item = next(p for p in POOL if p[0] == key)
        note = item[2]
        # 加一点随机化，别每天一模一样
        if key in ("开心", "元气") and random.random() < 0.5:
            note = random.choice([
                "今天心情很好，刚刷到个特别离谱的视频",
                "今天心情很好，想吃番茄牛肉炒饭",
                "今天心情不错，尾巴有点晃",
                "今天精神很好，想找他说话",
            ])
        return {
            "date": self._today(),
            "mood": key,
            "note": note,
            "hint": item[3],
            "rolled": datetime.now().strftime("%H:%M"),
        }

    def _load(self) -> dict:
        if self._cache and self._cache.get("date") == self._today():
            return self._cache
        try:
            data = json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if data.get("date") != self._today():
            data = self._roll()
            self._save(data)
            logger.info(f"[mood] 今日心情：{data['mood']} —— {data['note']}")
        self._cache = data
        return data

    def _save(self, data: dict) -> None:
        try:
            STATE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.info(f"[mood] 保存失败：{exc}")

    # ---------------- 注入 ----------------
    @filter.on_llm_request()
    async def inject_mood(self, event: AstrMessageEvent, req) -> None:
        try:
            d = self._load()
        except Exception:
            return
        _extra(req, (
            f"\n\n【今天的心情】{d.get('note')}"
            f"\n（{d.get('hint')}。心情只影响语气，不改变你是谁；"
            "心情不好也不许对主人凶。）"
        ))

    # ---------------- 指令 ----------------
    @filter.command("心情")
    async def mood_cmd(self, event: AstrMessageEvent, act: str = ""):
        """看今天的心情（/心情 换 可重掷一次）"""
        act = (act or "").strip()
        if act in ("换", "重掷", "reroll"):
            d = self._load()
            if d.get("rerolled"):
                yield event.plain_result("今天已经换过一次了，就用这个吧")
                return
            new = self._roll()
            new["rerolled"] = True
            self._cache = new
            self._save(new)
            yield event.plain_result(f"换好了：{new['note']}")
            return
        d = self._load()
        yield event.plain_result(f"今天：{d.get('mood')}｜{d.get('note')}（{d.get('rolled')} 定的）")
