"""统一时间线：把"我在 B站 干的事"和"我在 QQ 聊的天"并成一条线，让她能查。

对着"数字生命清单"第二条（生活的连续性：不同窗口的事件应汇入同一条时间线）。

数据来源：
- chat_archive/          QQ 私聊 + 群聊原文（含她自己的回复）
- bili_activity.jsonl    B站 评论 / 推送
- mood.json              当天心情
- bili_watched.jsonl     看过的视频（只取有评论的，避免和 activity 重复）

用法：
- LLM 工具 timeline(when="今天")：她自己查"我上午在干嘛"
- 指令 /时间线 [今天|昨天|2026-10-24|最近3小时]
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter

ROOT = Path(r"C:\QQBot")
ARCHIVE = ROOT / "chat_archive"
ACTIVITY = ROOT / "bili_activity.jsonl"
MOOD = ROOT / "mood.json"
WATCHED = ROOT / "bili_watched.jsonl"

KIND_LABEL = {
    "comment": "B站评论",
    "push": "B站推送",
    "watch": "刷B站",
}


def _day_of(token: str) -> list[str]:
    """把 when 解析成日期列表（YYYY-MM-DD）。"""
    t = (token or "今天").strip()
    today = date.today()
    if t in ("今天", "", "today"):
        return [today.isoformat()]
    if t in ("昨天", "yesterday"):
        return [(today - timedelta(days=1)).isoformat()]
    if t in ("前天",):
        return [(today - timedelta(days=2)).isoformat()]
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        return [f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"]
    m = re.search(r"(\d{1,2})月(\d{1,2})日?", t)
    if m:
        return [f"{today.year:04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"]
    if "最近" in t or "过去" in t:
        m = re.search(r"(\d+)\s*(小时|天)", t)
        n = int(m.group(1)) if m else 3
        unit = m.group(2) if m else "小时"
        days = n if unit == "天" else max(1, round(n / 24))
        return [(today - timedelta(days=i)).isoformat() for i in range(days)]
    return [today.isoformat()]


def _load_chat(days: list[str]) -> list[dict]:
    events = []
    if not ARCHIVE.exists():
        return events
    for d in ARCHIVE.iterdir():
        if not d.is_dir():
            continue
        who = "私聊" if "FriendMessage" in d.name else "群聊"
        for day in days:
            f = d / f"{day}.jsonl"
            if not f.is_file():
                continue
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                t = str(r.get("t") or "").replace("T", " ")
                if not t:
                    continue
                role = r.get("role")
                name = "她" if role == "bot" else (r.get("name") or "对方")
                text = str(r.get("text") or "").replace("\n", " ").strip()
                if not text:
                    continue
                events.append({
                    "t": t,
                    "kind": who,
                    "who": name,
                    "text": text[:80],
                })
    return events


def _load_activity(days: list[str]) -> list[dict]:
    events = []
    if ACTIVITY.exists():
        for line in ACTIVITY.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            t = str(d.get("t") or "")[:19].replace("T", " ")
            if not t or t[:10] not in days:
                continue
            kind = KIND_LABEL.get(str(d.get("kind")), "B站")
            title = str(d.get("title") or "").strip()
            body = str(d.get("text") or "").replace("\n", " ").strip()
            events.append({
                "t": t,
                "kind": kind,
                "who": "她",
                "text": (f"《{title}》" if title else "") + body[:70],
            })
    return events


def _load_mood(days: list[str]) -> list[dict]:
    events = []
    try:
        d = json.loads(MOOD.read_text(encoding="utf-8"))
    except Exception:
        return events
    if d.get("date") in days:
        events.append({
            "t": f"{d['date']} {d.get('rolled') or '00:00'}",
            "kind": "心情",
            "who": "她",
            "text": f"{d.get('mood')}：{d.get('note')}",
        })
    return events


def _cutoff(when: str) -> datetime | None:
    """最近 N 小时这种，用精确时间点过滤。"""
    m = re.search(r"最近\s*(\d+)\s*小时", when or "")
    if m:
        return datetime.now() - timedelta(hours=int(m.group(1)))
    return None


def build(when: str = "今天", limit: int = 40, keyword: str = "") -> dict:
    days = _day_of(when)
    events = _load_chat(days) + _load_activity(days) + _load_mood(days)
    cut = _cutoff(when)
    if cut:
        kept = []
        for e in events:
            try:
                ts = datetime.strptime(e["t"][:19], "%Y-%m-%d %H:%M:%S")
            except Exception:
                continue
            if ts >= cut:
                kept.append(e)
        events = kept
    if keyword:
        kw = keyword.strip()
        events = [e for e in events if kw in e["text"] or kw in e["kind"]]
    events.sort(key=lambda e: e["t"])
    total = len(events)
    if len(events) > limit:
        # 保留首尾，中间省略
        head = limit // 2
        tail = limit - head
        events = events[:head] + [{"t": "", "kind": "…", "who": "", "text": f"（省略 {total - limit} 条）"}] + events[-tail:]
    lines = []
    for e in events:
        hhmm = e["t"][11:16] if len(e["t"]) >= 16 else e["t"]
        if e["kind"] == "…":
            lines.append(e["text"])
        elif e["kind"] in ("私聊", "群聊"):
            lines.append(f"{hhmm} [{e['kind']}] {e['who']}：{e['text']}")
        else:
            lines.append(f"{hhmm} [{e['kind']}] {e['text']}")
    return {"days": days, "total": total, "lines": lines}


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        super().__init__(context)

    @filter.llm_tool(name="timeline")
    async def timeline_tool(self, event: AstrMessageEvent, when: str = "今天", keyword: str = ""):
        """查自己的时间线：今天/昨天/某天在 QQ 聊了什么、在 B站 做了什么。

        Args:
            when(string): 今天 / 昨天 / 前天 / 2026-10-24 / 最近3小时
            keyword(string): 只看包含这个词的事，可留空
        """
        d = build(when, keyword=keyword)
        if not d["total"]:
            return f"{when}没有记录"
        head = f"{'、'.join(d['days'])} 共 {d['total']} 条"
        return head + "\n" + "\n".join(d["lines"])

    @filter.command("时间线")
    async def timeline_cmd(self, event: AstrMessageEvent, when: str = "今天"):
        """看某天的时间线（QQ + B站 合在一起）"""
        d = build(when, limit=60)
        if not d["total"]:
            yield event.plain_result(f"{when}没有记录")
            return
        text = f"{'、'.join(d['days'])}：{d['total']} 条\n" + "\n".join(d["lines"])
        yield event.plain_result(text[:1800])
