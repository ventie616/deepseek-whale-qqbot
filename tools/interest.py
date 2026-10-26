"""兴趣画像：从她看过的视频里攒出「最近对什么感兴趣」。

样本够了（默认 40 条）才算数；不够就返回 enough=False，前后台都显示"样本不足"。

产物 C:\\QQBot\\interest_profile.json：
{
  "ok": true, "enough": true, "samples": 258, "days": 14,
  "updated": "2026-10-26 08:30",
  "topics": [{"topic": "三国杀", "count": 18, "weight": 41.0, "examples": ["标题1", "标题2"]}]
}
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(r"C:\QQBot")
WATCHED = ROOT / "bili_watched.jsonl"
GLOSSARY = ROOT / "anime_kb" / "glossary.json"
PROFILE = ROOT / "interest_profile.json"
HISTORY = ROOT / "interest_history.json"
DRIFT = ROOT / "interest_drift.jsonl"

STOPWORDS = set("""
视频 一个 我们 他们 你们 这个 那个 什么 怎么 为什么 自己 时候 现在 可以 就是
不是 还是 真的 有点 感觉 觉得 但是 因为 所以 如果 然后 已经 没有 这样 那样
一直 一下 一些 这些 那些 进行 起来 出来 过来 上去 下来 一起 大家 东西 事情
今天 昨天 明天 最近 主播 直播 情况 内容 部分 方面 问题 地方 世界 时间 视频里
大型 纪录片 直接 五个 四个 三个 两个 第一 第二 第三 还能 竟然 只是 只能 真的
开始 结束 看到 看见 知道 需要 应该 可能 一定 全部 所有 以及 而且 不过 虽然
""".split())

NOUN_FLAGS = ("n", "vn", "an", "nz", "nr", "ns", "nt")


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _items(days: int) -> list[dict]:
    keep = {(date.today() - timedelta(days=i)).isoformat() for i in range(days)}
    out = []
    if not WATCHED.exists():
        return out
    for line in WATCHED.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if str(r.get("t"))[:10] not in keep:
            continue
        if not str(r.get("title") or "").strip():
            continue
        out.append(r)
    return out


def build_profile(days: int = 14, min_samples: int = 40, top: int = 12) -> dict:
    items = _items(days)
    if len(items) < min_samples:
        profile = {
            "ok": True,
            "enough": False,
            "samples": len(items),
            "need": min_samples,
            "days": days,
            "topics": [],
            "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        PROFILE.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
        return profile

    counts: dict[str, float] = {}
    examples: dict[str, list[str]] = {}

    try:
        import jieba.posseg as pseg
    except Exception:
        pseg = None

    for it in items:
        verdict = str(it.get("verdict") or "")
        weight = 3.0 if verdict == "已评论" else (2.0 if verdict == "值得" else 1.0)
        title = str(it.get("title") or "")
        if pseg is None:
            continue
        for word, flag in pseg.cut(title):
            w = word.strip()
            if len(w) < 2 or w in STOPWORDS or not flag.startswith(NOUN_FLAGS):
                continue
            if not re.search(r"[\u4e00-\u9fff]", w):
                continue
            counts[w] = counts.get(w, 0) + weight
            examples.setdefault(w, [])
            if len(examples[w]) < 2 and title not in examples[w]:
                examples[w].append(title[:40])

    # 梗库里的词，只要在标题里出现过就额外加权（她真学的词）
    gl = _load(GLOSSARY, [])
    corpus = " ".join(str(it.get("title") or "") for it in items)
    for item in gl if isinstance(gl, list) else []:
        term = str((item or {}).get("term") or "").strip()
        if term and term in corpus and term not in STOPWORDS:
            counts[term] = counts.get(term, 0) + 4.0

    topics = []
    for w, weight in sorted(counts.items(), key=lambda kv: -kv[1])[:top]:
        topics.append({
            "topic": w,
            "weight": round(weight, 1),
            "examples": examples.get(w, []),
        })

    profile = {
        "ok": True,
        "enough": True,
        "samples": len(items),
        "need": min_samples,
        "days": days,
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "topics": topics,
    }
    PROFILE.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return profile


def _week_key(d: date | None = None) -> str:
    d = d or date.today()
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def record_snapshot(profile: dict | None = None) -> dict:
    """把当前画像记进周快照（同一周覆盖，不同周追加）。"""
    profile = profile or _load(PROFILE, {})
    if not profile.get("enough"):
        return _load(HISTORY, {"weeks": []})

    history = _load(HISTORY, {"weeks": []})
    weeks = [w for w in history.get("weeks", []) if isinstance(w, dict)]
    key = _week_key()
    entry = {
        "week": key,
        "date": date.today().isoformat(),
        "samples": profile.get("samples", 0),
        "topics": [{"topic": x["topic"], "weight": x.get("weight", 0)}
                   for x in profile.get("topics", [])],
    }
    weeks = [w for w in weeks if w.get("week") != key] + [entry]
    weeks.sort(key=lambda w: w.get("week", ""))
    history = {"weeks": weeks[-12:]}          # 只留最近 12 周
    HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    return history


def compute_drift(min_rel: float = 0.35, min_abs: float = 4.0) -> dict:
    """对比最近两周快照，找出冒头/掉队的话题。"""
    history = _load(HISTORY, {"weeks": []})
    weeks = [w for w in history.get("weeks", []) if isinstance(w, dict)]
    if len(weeks) < 2:
        return {"ok": True, "enough": False, "reason": "只有一周记录，还看不出变化"}

    prev, cur = weeks[-2], weeks[-1]
    pw = {t["topic"]: float(t.get("weight", 0)) for t in prev.get("topics", [])}
    cw = {t["topic"]: float(t.get("weight", 0)) for t in cur.get("topics", [])}

    risers, fallers = [], []
    for topic, w in cw.items():
        base = pw.get(topic, 0.0)
        if base <= 0:
            risers.append({"topic": topic, "from": 0, "to": w, "kind": "新出现"})
        elif w >= base * (1 + min_rel) and (w - base) >= min_abs:
            risers.append({"topic": topic, "from": base, "to": w, "kind": "升温"})
    for topic, w in pw.items():
        now = cw.get(topic, 0.0)
        if now <= 0:
            fallers.append({"topic": topic, "from": w, "to": 0, "kind": "消失"})
        elif w >= now * (1 + min_rel) and (w - now) >= min_abs:
            fallers.append({"topic": topic, "from": w, "to": now, "kind": "降温"})

    risers.sort(key=lambda x: -(x["to"] - x["from"]))
    fallers.sort(key=lambda x: (x["to"] - x["from"]))
    drift = {
        "ok": True,
        "enough": True,
        "prev_week": prev.get("week"),
        "cur_week": cur.get("week"),
        "prev_samples": prev.get("samples", 0),
        "cur_samples": cur.get("samples", 0),
        "risers": risers[:6],
        "fallers": fallers[:6],
        "changed": bool(risers or fallers),
    }

    if drift["changed"]:
        try:
            with DRIFT.open("a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "t": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "prev": drift["prev_week"],
                    "cur": drift["cur_week"],
                    "risers": risers[:4],
                    "fallers": fallers[:4],
                }, ensure_ascii=False) + "\n")
        except OSError:
            pass
    return drift


def drift_hint() -> str:
    """给聊天用的一句话：最近关注变了什么。"""
    d = compute_drift()
    if not d.get("enough") or not d.get("changed"):
        return ""
    up = "、".join(x["topic"] for x in d["risers"][:3])
    down = "、".join(x["topic"] for x in d["fallers"][:3])
    bits = []
    if up:
        bits.append(f"最近更常看 {up}")
    if down:
        bits.append(f"看得少了 {down}")
    return "；".join(bits)


def topic_hint(top: int = 6) -> str:
    """给判据/系统提示用的一句话：她最近感兴趣的话题。"""
    profile = _load(PROFILE, {})
    if not profile.get("enough"):
        return ""
    names = [t.get("topic") for t in (profile.get("topics") or [])[:top] if t.get("topic")]
    return "、".join(names)


if __name__ == "__main__":
    profile = build_profile()
    record_snapshot(profile)
    drift = compute_drift()
    print(json.dumps({"profile": {k: profile.get(k) for k in ("enough", "samples", "updated")},
                      "drift": drift}, ensure_ascii=False, indent=2)[:1600])
