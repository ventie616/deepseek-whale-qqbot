"""兴趣画像：分区 / 话题 / 博主 三个维度。

B站 接口已经不再返回 tname（分区名），所以「分区」用标签+标题关键词归成大类，
标签本身和标题名词一起作为「话题」，UP 主姓名作为「博主」。
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(r"C:\QQBot")
WATCHED = ROOT / "bili_watched.jsonl"
META = ROOT / "bili_meta.json"
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

# 平台推广/活动类标签，不代表兴趣
TAG_DROP = ("激励计划", "投稿", "官方", "活动", "周年", "赛事", "大赛", "训练营", "招募")

# 关键词 -> 大类（按顺序匹配，先到先得）
CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("游戏", ("王者", "三国杀", "原神", "星穹", "崩坏", "英雄联盟", "对局", "手游", "网游",
             "攻略", "武将", "玩家", "排位", "电竞", "峡谷", "辅助", "打团", "上分")),
    ("生活·ASMR", ("asmr", "助眠", "触发音", "耳部", "哄睡", "轻语", "耳骚", "耳边")),
    ("美食", ("美食", "吃", "烹饪", "火锅", "探店", "菜谱", "自助", "炸鸡", "面包", "炒饭")),
    ("鬼畜·搞笑", ("鬼畜", "搞笑", "抽象", "大型纪录片", "名场面", "整活", "沙雕", "爆笑", "笑死")),
    ("动画", ("动画", "手书", "mmd", "二创", "番剧", "国创", "动漫", "剪辑", "作画", "同人")),
    ("音乐", ("音乐", "翻唱", "演奏", "原创曲", "mv", "初音", "vocaloid", "歌")),
    ("科技·数码", ("数码", "手机", "评测", "iphone", "电脑", "ai", "显卡", "键盘", "参数")),
    ("影视", ("电影", "剧集", "解说", "影视", "预告", "花絮")),
    ("资讯", ("新闻", "时事", "热点", "突发", "事件", "官宣")),
    ("知识", ("知识", "科普", "历史", "冷知识", "人物志", "物理", "数学", "考试")),
    ("时尚", ("穿搭", "时尚", "美妆", "时装", "颜值")),
    ("生活", ("vlog", "出租屋", "实拍", "日常", "记录", "生活")),
]


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


def _category_of(text: str) -> str:
    low = (text or "").lower()
    for name, keys in CATEGORY_RULES:
        if any(k in low for k in keys):
            return name
    return "其他"


def build_profile(days: int = 14, min_samples: int = 40, top: int = 12) -> dict:
    items = _items(days)
    meta = _load(META, {})
    if len(items) < min_samples:
        profile = {
            "ok": True, "enough": False, "samples": len(items), "need": min_samples,
            "days": days, "topics": [], "categories": [], "ups": [],
            "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        PROFILE.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
        return profile

    cats: dict[str, float] = {}
    ups: dict[str, float] = {}
    tag_counts: dict[str, float] = {}
    word_counts: dict[str, float] = {}
    examples: dict[str, list[str]] = {}

    try:
        import jieba.posseg as pseg
    except Exception:
        pseg = None

    for it in items:
        verdict = str(it.get("verdict") or "")
        weight = 3.0 if verdict == "已评论" else (2.0 if verdict == "值得" else 1.0)
        title = str(it.get("title") or "")
        up = str(it.get("up") or "").strip()
        info = meta.get(str(it.get("bvid")), {})
        tags = [str(x) for x in (info.get("tags") or [])]

        if up:
            ups[up] = ups.get(up, 0) + weight

        cat = _category_of(" ".join(tags) + " " + title)
        cats[cat] = cats.get(cat, 0) + weight

        for tg in tags:
            name = tg.lstrip("#").strip()
            if len(name) < 2 or name in STOPWORDS or name.isdigit():
                continue
            if any(bad in name for bad in TAG_DROP):
                continue
            tag_counts[name] = tag_counts.get(name, 0) + weight
            if name not in examples:
                examples[name] = [title[:40]]
            elif len(examples[name]) < 2 and title not in examples[name]:
                examples[name].append(title[:40])

        if pseg is not None:
            for word, flag in pseg.cut(title):
                w = word.strip()
                if len(w) < 2 or w in STOPWORDS or not flag.startswith(NOUN_FLAGS):
                    continue
                if not re.search(r"[\u4e00-\u9fff]", w):
                    continue
                word_counts[w] = word_counts.get(w, 0) + weight
                examples.setdefault(w, [])
                if len(examples[w]) < 2 and title not in examples[w]:
                    examples[w].append(title[:40])

    merged: dict[str, float] = {}
    for k, v in tag_counts.items():
        merged[k] = merged.get(k, 0) + v * 1.6
    for k, v in word_counts.items():
        merged[k] = merged.get(k, 0) + v

    topics = [
        {"topic": k, "weight": round(v, 1), "examples": examples.get(k, [])}
        for k, v in sorted(merged.items(), key=lambda kv: -kv[1])[:top]
    ]
    categories = [
        {"name": k, "weight": round(v, 1)}
        for k, v in sorted(cats.items(), key=lambda kv: -kv[1])[:8]
    ]
    top_ups = [
        {"name": k, "weight": round(v, 1)}
        for k, v in sorted(ups.items(), key=lambda kv: -kv[1])[:8]
    ]

    profile = {
        "ok": True, "enough": True, "samples": len(items), "need": min_samples,
        "days": days, "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "categories": categories, "topics": topics, "ups": top_ups,
    }
    PROFILE.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return profile


def _week_key(d: date | None = None) -> str:
    d = d or date.today()
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def record_snapshot(profile: dict | None = None) -> dict:
    profile = profile or _load(PROFILE, {})
    if not profile.get("enough"):
        return _load(HISTORY, {"weeks": []})
    history = _load(HISTORY, {"weeks": []})
    weeks = [w for w in history.get("weeks", []) if isinstance(w, dict)]
    entry = {
        "week": _week_key(),
        "date": date.today().isoformat(),
        "samples": profile.get("samples", 0),
        "topics": [{"topic": x["topic"], "weight": x.get("weight", 0)}
                   for x in profile.get("topics", [])],
        "categories": [{"name": x["name"], "weight": x.get("weight", 0)}
                       for x in profile.get("categories", [])],
        "ups": [{"name": x["name"], "weight": x.get("weight", 0)}
                for x in profile.get("ups", [])],
    }
    weeks = [w for w in weeks if w.get("week") != entry["week"]] + [entry]
    weeks.sort(key=lambda w: w.get("week", ""))
    history = {"weeks": weeks[-12:]}
    HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    return history


def _diff(prev: list[dict], cur: list[dict], key: str, dim: str,
          min_rel: float, min_abs: float) -> tuple[list[dict], list[dict]]:
    pw = {str(x.get(key)): float(x.get("weight", 0)) for x in prev if x.get(key)}
    cw = {str(x.get(key)): float(x.get("weight", 0)) for x in cur if x.get(key)}
    risers, fallers = [], []
    for name, w in cw.items():
        base = pw.get(name, 0.0)
        if base <= 0:
            risers.append({"dim": dim, "topic": name, "from": 0, "to": w, "kind": "新出现"})
        elif w >= base * (1 + min_rel) and (w - base) >= min_abs:
            risers.append({"dim": dim, "topic": name, "from": base, "to": w, "kind": "升温"})
    for name, w in pw.items():
        now = cw.get(name, 0.0)
        if now <= 0:
            fallers.append({"dim": dim, "topic": name, "from": w, "to": 0, "kind": "消失"})
        elif w >= now * (1 + min_rel) and (w - now) >= min_abs:
            fallers.append({"dim": dim, "topic": name, "from": w, "to": now, "kind": "降温"})
    risers.sort(key=lambda x: -(x["to"] - x["from"]))
    fallers.sort(key=lambda x: (x["to"] - x["from"]))
    return risers, fallers


def compute_drift(min_rel: float = 0.35, min_abs: float = 4.0) -> dict:
    history = _load(HISTORY, {"weeks": []})
    weeks = [w for w in history.get("weeks", []) if isinstance(w, dict)]
    if len(weeks) < 2:
        return {"ok": True, "enough": False, "reason": "只有一周记录，还看不出变化"}
    prev, cur = weeks[-2], weeks[-1]
    risers, fallers = [], []
    for dim, key, name_key in (("分区", "categories", "name"), ("话题", "topics", "topic"),
                               ("博主", "ups", "name")):
        r, f = _diff(prev.get(key, []), cur.get(key, []), name_key, dim, min_rel, min_abs)
        risers += r
        fallers += f
    drift = {
        "ok": True, "enough": True,
        "prev_week": prev.get("week"), "cur_week": cur.get("week"),
        "prev_samples": prev.get("samples", 0), "cur_samples": cur.get("samples", 0),
        "risers": risers[:8], "fallers": fallers[:8],
        "changed": bool(risers or fallers),
    }
    if drift["changed"]:
        try:
            with DRIFT.open("a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "t": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "prev": drift["prev_week"], "cur": drift["cur_week"],
                    "risers": risers[:4], "fallers": fallers[:4],
                }, ensure_ascii=False) + "\n")
        except OSError:
            pass
    return drift


def topic_hint(top: int = 6) -> str:
    profile = _load(PROFILE, {})
    if not profile.get("enough"):
        return ""
    names = [t.get("topic") for t in (profile.get("topics") or [])[:top] if t.get("topic")]
    return "、".join(names)


def category_hint(top: int = 3) -> str:
    profile = _load(PROFILE, {})
    if not profile.get("enough"):
        return ""
    names = [c.get("name") for c in (profile.get("categories") or [])[:top] if c.get("name")]
    return "、".join(names)


def drift_hint() -> str:
    d = compute_drift()
    if not d.get("enough") or not d.get("changed"):
        return ""
    def fmt(items):
        return "、".join(f"{x['topic']}({x['dim']})" for x in items[:3])
    bits = []
    if d["risers"]:
        bits.append(f"最近更常看 {fmt(d['risers'])}")
    if d["fallers"]:
        bits.append(f"看得少了 {fmt(d['fallers'])}")
    return "；".join(bits)


if __name__ == "__main__":
    profile = build_profile()
    record_snapshot(profile)
    drift = compute_drift()
    print(json.dumps({
        "enough": profile.get("enough"), "samples": profile.get("samples"),
        "categories": profile.get("categories"),
        "topics": profile.get("topics")[:6],
        "ups": profile.get("ups")[:6],
        "drift": drift,
    }, ensure_ascii=False, indent=2)[:1800])
