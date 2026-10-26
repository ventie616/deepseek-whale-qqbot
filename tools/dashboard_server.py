"""本地控制台服务：给 00-控制台.html 提供实时状态和操作接口。

只绑 127.0.0.1，写操作要求 X-Token 头（防跨站请求）。

    python tools\\dashboard_server.py [--port 8765]

接口：
    GET  /                      控制台页面
    GET  /api/status            状态（心情/配置/规则/计数/服务/活动）
    POST /api/config/bili       改 bili_push.json（白名单字段）
    POST /api/config/dm         改 proactive_dm.json（白名单字段）
    POST /api/mood/reroll       重掷心情
    POST /api/mood/set          指定心情 {"mood": "开心"}
    POST /api/rules/delete      {"id": "r12345"}
    POST /api/service           {"action": "start|stop|restart|guard"}
    GET  /api/logs?name=watchdog&lines=60
    GET  /api/report?date=2026-10-24
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import random
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import threading
import time
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.request
from urllib.parse import parse_qs, quote, urlparse

ROOT = pathlib.Path(r"C:\QQBot")
DASH = ROOT / "dashboard" / "index.html"
TOKEN_FILE = ROOT / "dashboard" / ".token"
TRIGGER = ROOT / "bili_trigger.json"
TRIGGER_RESULT = ROOT / "bili_trigger_result.json"
TOKEN = ""
LAST_BRUSH: dict = {}

BILI_KEYS = {
    "enabled", "mode", "interval_minutes", "jitter_minutes", "active_hours",
    "strictness", "vision_enable", "vision_mode", "memory_inject", "memory_items",
    "skip_repost", "dedupe_ratio", "dedupe_days",
}
DM_KEYS = {"enabled", "min_minutes", "max_minutes", "max_per_day", "followup_minutes"}

MOODS = ["开心", "元气", "平静", "有点累", "有点丧", "炸毛"]
MOOD_TEXT = {
    "开心": ("今天心情很好，尾巴都在晃", "话多一点，愿意分享，看到什么都想给他看"),
    "元气": ("今天特别有精神，想找点事干", "说话有劲，爱接话，会主动提议一起做点什么"),
    "平静": ("今天很平静，懒懒的", "话少但温和，不冷淡，慢慢说"),
    "有点累": ("今天有点累，趴着不想动", "话更短，但不是生气；被关心会软下来"),
    "有点丧": ("今天有点丧，不太想说话", "只回一两句，不迁怒对方；被问会说'没事，就是有点闷'"),
    "炸毛": ("今天莫名其妙有点烦", "语气冲一点，但对方一哄就好"),
}


def read_json(path: pathlib.Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: pathlib.Path, data) -> bool:
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def port_up(port: int, timeout: float = 0.8) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def count_comments_today() -> int:
    p = ROOT / "bili_watched.jsonl"
    if not p.exists():
        return 0
    today = date.today().isoformat()
    seen = set()
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if str(d.get("t", "")).startswith(today) and d.get("comment") and d.get("bvid") not in seen:
            seen.add(d.get("bvid"))
    return len(seen)


def status() -> dict:
    push = read_json(ROOT / "bili_push.json", {}) or {}
    dm = read_json(ROOT / "proactive_dm.json", {}) or {}
    mood = read_json(ROOT / "mood.json", {}) or {}
    rules = (read_json(ROOT / "learned_rules.json", {}) or {}).get("rules") or []
    mem = read_json(ROOT / "bili_memory.json", []) or []
    idx = read_json(ROOT / "stickers" / "index.json", {}) or {}
    clips = read_json(ROOT / "voice_clips" / "index.json", {}) or {}
    hb = read_json(ROOT / "watchdog_heartbeat.json", {}) or {}
    acts = []
    ap = ROOT / "bili_activity.jsonl"
    if ap.exists():
        for line in ap.read_text(encoding="utf-8", errors="replace").splitlines()[-6:]:
            try:
                acts.append(json.loads(line))
            except Exception:
                continue
    return {
        "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mood": mood,
        "push": push,
        "dm": dm,
        "rules": [
            {k: r.get(k) for k in ("id", "rule", "scope", "weight", "status")} for r in rules
        ],
        "counts": {
            "stickers": len(idx.get("items") or []),
            "clips": len(clips.get("clips") or []),
            "fulls": len(clips.get("fulls") or []),
            "memory": len(mem),
            "rules": len(rules),
            "today_comments": count_comments_today(),
        },
        "services": {
            "astrbot": port_up(6185),
            "snowluma": port_up(5099),
            "watchdog": port_up(45999) or (ROOT / "watchdog_heartbeat.json").exists(),
        },
        "heartbeat": hb,
        "activity": acts,
        "bili": {
            "has_cookie": bool((read_json(ROOT / "bili_account.json", {}) or {}).get("sessdata")),
            "uid": str((read_json(ROOT / "bili_account.json", {}) or {}).get("dedeuserid") or ""),
            "last_check": (read_json(ROOT / "bili_login_state.json", {}) or {}).get("last_ok"),
        },
    }


sys.path.insert(0, str(ROOT / "tools"))
try:
    import audio_lib  # noqa: E402
except Exception:  # noqa: BLE001
    audio_lib = None

JOBS: dict[str, dict] = {}


def audio_list() -> dict:
    if audio_lib is None:
        return {"ok": False, "error": "audio_lib 不可用"}
    songs = audio_lib.list_songs()
    return {
        "ok": True,
        "songs": songs,
        "totals": {
            "songs": len(songs),
            "clips": sum(len(s["clips"]) for s in songs),
            "fulls": sum(1 for s in songs if s["full"]),
            "mb": round(sum(
                (s["full"]["size_kb"] if s["full"] else 0) + sum(c["size_kb"] or 0 for c in s["clips"])
                for s in songs
            ) / 1024, 1),
        },
    }


def run_import(job_id: str, mode: str, payload: dict) -> None:
    job = JOBS[job_id]
    job["state"] = "running"

    def log(msg: str) -> None:
        job["log"].append(str(msg))
        job["updated"] = time.time()

    try:
        if mode == "url":
            res = audio_lib.add_from_url(payload["url"], payload.get("name") or "", log=log)
        elif mode == "bvid":
            text = str(payload.get("bvid") or "")
            if text.startswith(("http://", "https://")):
                res = audio_lib.add_from_url(text, payload.get("name") or "", log=log)
            else:
                res = audio_lib.add_from_bvid(text, payload.get("name") or "", log=log)
        else:
            src = pathlib.Path(payload["path"])
            res = audio_lib.add_audio(src, payload.get("name") or src.stem, log=log)
            if payload.get("cleanup"):
                src.unlink(missing_ok=True)
        job["result"] = res
        job["state"] = "done"
    except Exception as exc:  # noqa: BLE001
        job["state"] = "error"
        job["error"] = f"{type(exc).__name__}: {exc}"
        log(job["error"])
    finally:
        job["updated"] = time.time()


def onebot_token() -> str:
    try:
        cfg = json.loads((ROOT / "snowluma" / "config" / "onebot.json").read_text(encoding="utf-8"))
        for s in (cfg.get("networks") or {}).get("httpServers") or []:
            if s.get("accessToken"):
                return str(s["accessToken"])
    except Exception:
        pass
    try:
        return (ROOT / "token.txt").read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def send_qq(text: str) -> dict:
    """走 OneBot HTTP 直接发一条私聊，验证通道是否通。"""
    push = read_json(ROOT / "bili_push.json", {}) or {}
    qq = str(push.get("target_qq") or "").strip()
    token = onebot_token()
    if not qq:
        return {"ok": False, "error": "bili_push.json 里没有 target_qq"}
    payload = json.dumps({"user_id": int(qq), "message": [{"type": "text", "data": {"text": text}}]}).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:3000/send_private_msg",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read())
        return {"ok": d.get("status") == "ok" or d.get("retcode") == 0, "raw": d}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def bili_account() -> dict:
    acc = read_json(ROOT / "bili_account.json", {}) or {}
    state = read_json(ROOT / "bili_login_state.json", {}) or {}
    out = {
        "has_cookie": bool(acc.get("sessdata")),
        "uid": str(acc.get("dedeuserid") or ""),
        "name": "",
        "level": 0,
        "logged_in": False,
        "message": "",
        "last_check": state.get("last_ok"),
        "last_check_at": state.get("checked_at") or state.get("at") or "",
        "space": "",
    }
    if out["uid"]:
        out["space"] = f"https://space.bilibili.com/{out['uid']}"
    if not out["has_cookie"]:
        out["message"] = "还没填 cookie"
        return out
    cookie = f"SESSDATA={acc.get('sessdata','')}; bili_jct={acc.get('bili_jct','')}; DedeUserID={out['uid']}"
    req = urllib.request.Request(
        "https://api.bilibili.com/x/web-interface/nav",
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
            "Referer": "https://www.bilibili.com/",
            "Cookie": cookie,
        },
    )
    d = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                d = json.loads(r.read())
            if d.get("code") in (-412, -509, -799):
                time.sleep(1.5 + attempt)
                d = None
                continue
            break
        except Exception as exc:  # noqa: BLE001
            out["message"] = f"查询失败：{type(exc).__name__}（第 {attempt + 1} 次）"
            time.sleep(1.5 + attempt)
    if d is None:
        return out
    data = d.get("data") or {}
    out["logged_in"] = bool(d.get("code") == 0 and data.get("isLogin"))
    out["name"] = data.get("uname") or ""
    out["level"] = int((data.get("level_info") or {}).get("current_level") or 0)
    out["message"] = "登录正常" if out["logged_in"] else f"未登录（code {d.get('code')}）"
    if out["logged_in"]:
        out["mid"] = data.get("mid")
    return out


def recent_commented(limit: int = 5) -> list[dict]:
    p = ROOT / "bili_watched.jsonl"
    if not p.exists():
        return []
    seen, rows = set(), []
    for line in reversed(p.read_text(encoding="utf-8", errors="replace").splitlines()):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        bv = d.get("bvid")
        if not bv or bv in seen or not d.get("comment"):
            continue
        seen.add(bv)
        rows.append({
            "bvid": bv,
            "title": d.get("title") or "",
            "comment": d.get("comment") or "",
            "t": d.get("t") or "",
            "url": f"https://www.bilibili.com/video/{bv}",
        })
        if len(rows) >= limit:
            break
    return rows


def start_login_ui() -> dict:
    """B站 cookie 登录面板（127.0.0.1:8777），没跑就拉起来。"""
    if port_up(8777):
        return {"ok": True, "url": "http://127.0.0.1:8777/", "already": True}
    try:
        subprocess.Popen(
            [str(ROOT / "venv" / "Scripts" / "pythonw.exe"), str(ROOT / "tools" / "bili_login_ui.py")],
            cwd=str(ROOT),
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            close_fds=True,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    for _ in range(30):
        if port_up(8777):
            return {"ok": True, "url": "http://127.0.0.1:8777/", "already": False}
        time.sleep(0.2)
    return {"ok": True, "url": "http://127.0.0.1:8777/", "already": False, "slow": True}


TOKEN_LOG = ROOT / "token_usage.jsonl"
TOKEN_PRICES = {
    "deepseek-v4-pro": {"miss": 2.0, "hit": 0.5, "out": 8.0},
    "deepseek-flash": {"miss": 0.5, "hit": 0.1, "out": 2.0},
    "_default": {"miss": 2.0, "hit": 0.5, "out": 8.0},
}


def token_stats(days: int = 14) -> dict:
    """读 token_usage.jsonl，聚合出今天/按天/按小时/按来源。金额是按公开价估的。"""
    from datetime import date, datetime, timedelta

    today = date.today()
    today_s = today.isoformat()
    day_list = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    def _zero():
        return {"prompt": 0, "completion": 0, "cached": 0, "total": 0, "calls": 0, "cost": 0.0}

    agg = {
        "today": _zero(),
        "hourly": [0] * 24,
        "by_model": {},
        "by_source": {},
        "daily": {d: 0 for d in day_list},
        # 对话 vs 刷B站 分开
        "cats": {
            "chat": {"today": _zero(), "daily": {d: 0 for d in day_list}, "hourly": [0] * 24},
            "bili": {"today": _zero(), "daily": {d: 0 for d in day_list}, "hourly": [0] * 24},
        },
    }
    if not TOKEN_LOG.exists():
        return {"ok": True, "empty": True, **agg}

    for line in TOKEN_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        ts = str(r.get("t") or "")
        day = ts[:10]
        total = int(r.get("total") or 0)
        cat = "bili" if str(r.get("source")) == "B站" else "chat"
        if day in agg["daily"]:
            agg["daily"][day] += total
            agg["cats"][cat]["daily"][day] += total
        if day != today_s:
            continue
        prompt = int(r.get("prompt") or 0)
        completion = int(r.get("completion") or 0)
        cached = min(int(r.get("cached") or 0), prompt)
        t = agg["today"]
        t["prompt"] += prompt
        t["completion"] += completion
        t["cached"] += cached
        t["total"] += total
        t["calls"] += 1
        price = TOKEN_PRICES.get(str(r.get("model") or ""), TOKEN_PRICES["_default"])
        t["cost"] += (
            (prompt - cached) / 1e6 * price["miss"]
            + cached / 1e6 * price["hit"]
            + completion / 1e6 * price["out"]
        )
        try:
            hour = int(ts[11:13])
            agg["hourly"][hour] += total
            agg["cats"][cat]["hourly"][hour] += total
        except Exception:
            pass
        c = agg["cats"][cat]["today"]
        c["prompt"] += prompt
        c["completion"] += completion
        c["cached"] += cached
        c["total"] += total
        c["calls"] += 1
        c["cost"] += (
            (prompt - cached) / 1e6 * price["miss"]
            + cached / 1e6 * price["hit"]
            + completion / 1e6 * price["out"]
        )
        m = str(r.get("model") or "?")
        src = str(r.get("source") or "?")
        agg["by_model"][m] = agg["by_model"].get(m, 0) + total
        agg["by_source"][src] = agg["by_source"].get(src, 0) + total

    agg["today"]["cost"] = round(agg["today"]["cost"], 4)
    for cat in ("chat", "bili"):
        agg["cats"][cat]["today"]["cost"] = round(agg["cats"][cat]["today"]["cost"], 4)
        agg["cats"][cat]["daily_list"] = [
            {"d": d, "total": agg["cats"][cat]["daily"][d]} for d in day_list
        ]
    agg["daily_list"] = [{"d": d, "total": agg["daily"][d]} for d in day_list]
    agg["by_model"] = sorted(
        [{"name": k, "total": v} for k, v in agg["by_model"].items()], key=lambda x: -x["total"]
    )
    agg["by_source"] = sorted(
        [{"name": k, "total": v} for k, v in agg["by_source"].items()], key=lambda x: -x["total"]
    )
    return {"ok": True, **agg}


BILI_WATCHED = ROOT / "bili_watched.jsonl"
BILI_MEMORY = ROOT / "bili_memory.json"
BILI_RECENT = ROOT / "bili_recent.json"
GLOSSARY = ROOT / "anime_kb" / "glossary.json"


def bili_history(days: int = 7) -> dict:
    """近 N 天的 B站 浏览记录，按天分组、新的在前。"""
    from datetime import date, timedelta

    keep = {(date.today() - timedelta(days=i)).isoformat() for i in range(days)}
    groups: dict[str, list] = {}
    if BILI_WATCHED.exists():
        for line in BILI_WATCHED.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            day = str(r.get("t"))[:10]
            if day not in keep:
                continue
            groups.setdefault(day, []).append({
                "t": str(r.get("t"))[11:16],
                "title": str(r.get("title") or "")[:60],
                "bvid": r.get("bvid") or "",
                "verdict": r.get("verdict") or "",
                "reason": str(r.get("reason") or "")[:50],
                "comment": str(r.get("comment") or "")[:80],
            })
    out = []
    for day in sorted(groups, reverse=True):
        items = groups[day]
        out.append({
            "day": day,
            "count": len(items),
            "commented": sum(1 for x in items if x["verdict"] == "已评论"),
            "items": items[-40:],
        })
    return {"ok": True, "days": out}


STOPWORDS = set("""
视频 一个 我们 他们 你们 这个 那个 什么 怎么 为什么 自己 时候 现在 可以 就是
不是 还是 真的 有点 感觉 觉得 但是 因为 所以 如果 然后 已经 没有 这样 那样
一直 一下 一些 这些 那些 进行 起来 出来 过来 上去 下来 一起 大家 东西 事情
今天 昨天 明天 最近 主播 直播 情况 内容 部分 方面 问题 地方 世界 时间 视频里
大型 纪录片 直接 五个 四个 三个 两个 第一 第二 第三 还能 竟然 只是 只能 真的
开始 结束 看到 看见 知道 需要 应该 可能 一定 全部 所有 以及 而且 不过 虽然
""".split())

NOUN_FLAGS = ("n", "vn", "an", "nz", "nr", "ns", "nt")


def interest_words(days: int = 14, top: int = 46) -> dict:
    """从最近看过的视频标题 + 弹幕梗里算兴趣词。"""
    import re as _re
    from datetime import date, timedelta

    keep = {(date.today() - timedelta(days=i)).isoformat() for i in range(days)}
    titles: list[str] = []

    if BILI_WATCHED.exists():
        for line in BILI_WATCHED.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if str(r.get("t"))[:10] not in keep:
                continue
            title = str(r.get("title") or "")
            if not title:
                continue
            weight = 2 if r.get("verdict") in ("值得", "已评论") else 1
            titles.extend([title] * weight)

    for path in (BILI_MEMORY, BILI_RECENT):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for m in data if isinstance(data, list) else []:
            title = str(m.get("title") or "")
            if title:
                titles.append(title)

    text = " ".join(titles)
    counts: dict[str, float] = {}

    try:
        import jieba.posseg as pseg

        for w, flag in pseg.cut(text):
            w = w.strip()
            if len(w) < 2 or w in STOPWORDS:
                continue
            if not flag.startswith(NOUN_FLAGS):
                continue
            if not _re.search(r"[\u4e00-\u9fff]", w):
                continue
            if _re.fullmatch(r"[\d一二三四五六七八九十百千]+", w):
                continue
            counts[w] = counts.get(w, 0) + 1
    except Exception:
        for run in _re.findall(r"[\u4e00-\u9fff]{2,}", text):
            for n in (2, 3):
                for i in range(len(run) - n + 1):
                    w = run[i:i + n]
                    if w not in STOPWORDS:
                        counts[w] = counts.get(w, 0) + 0.5

    # 弹幕梗库：出现在标题里就加权（这是她真学的词）
    try:
        gl = json.loads(GLOSSARY.read_text(encoding="utf-8"))
        for item in gl if isinstance(gl, list) else []:
            term = str((item or {}).get("term") or "").strip()
            if term and term in text and any(_re.search(r"[\u4e00-\u9fff]", term) for _ in [0]):
                counts[term] = counts.get(term, 0) + 3
    except Exception:
        pass

    words = sorted(counts.items(), key=lambda kv: -kv[1])[:top]
    if not words:
        return {"ok": True, "words": []}
    hi = words[0][1]
    return {
        "ok": True,
        "days": days,
        "words": [{"w": w, "n": round(n, 1), "size": round(12 + 26 * (n / hi), 1)}
                  for w, n in words],
    }


def interest_profile(force: bool = False) -> dict:
    """兴趣画像：从 bili_watched 算，6 小时内复用。"""
    import sys

    sys.path.insert(0, str(ROOT / "tools"))
    from interest import build_profile

    profile_path = ROOT / "interest_profile.json"
    if not force and profile_path.is_file():
        try:
            old = json.loads(profile_path.read_text(encoding="utf-8"))
            from datetime import datetime as _dt

            age = (_dt.now() - _dt.strptime(old.get("updated", ""), "%Y-%m-%d %H:%M")).total_seconds()
            if 0 <= age < 6 * 3600:
                return old
        except Exception:
            pass
    return build_profile(days=14, min_samples=40)


DIARY_DIR = ROOT / "diary"
DIARY_TRIGGER = ROOT / "diary_trigger.json"
DIARY_RESULT = ROOT / "diary_trigger_result.json"


def diary_list() -> list[dict]:
    """列出所有日记，新的在前。"""
    if not DIARY_DIR.is_dir():
        return []
    out = []
    for f in sorted(DIARY_DIR.glob("*.md"), reverse=True):
        try:
            text = f.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        out.append({
            "date": f.stem,
            "chars": len(text),
            "preview": text.replace("\n", " ")[:40],
        })
    return out


def diary_read(day: str) -> dict:
    f = DIARY_DIR / f"{day}.md"
    if not f.is_file():
        return {"ok": False, "error": f"{day} 没有日记"}
    try:
        text = f.read_text(encoding="utf-8").strip()
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    lines = [x for x in text.splitlines() if x.strip()]
    return {"ok": True, "date": day, "text": text, "lines": lines, "chars": len(text)}


class Handler(BaseHTTPRequestHandler):
    server_version = "WhaleConsole/1.0"

    def log_message(self, fmt, *args):  # 安静点
        pass

    # ---------- 基础 ----------
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _authorized(self) -> bool:
        return self.headers.get("X-Token") == TOKEN

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    # ---------- GET ----------
    def do_GET(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        path = u.path
        if path in ("/", "/index.html"):
            if not DASH.exists():
                self._send(500, b"dashboard/index.html missing", "text/plain; charset=utf-8")
                return
            html = DASH.read_text(encoding="utf-8").replace("__TOKEN__", TOKEN)
            self._send(200, html.encode(), "text/html; charset=utf-8")
            return
        if path == "/api/status":
            self._json(status())
            return
        if path == "/api/interest":
            q = parse_qs(u.query)
            force = (q.get("rebuild") or ["0"])[0] == "1"
            self._json(interest_profile(force))
            return
        if path == "/api/bili/history":
            q = parse_qs(u.query)
            days = int((q.get("days") or ["7"])[0])
            self._json(bili_history(max(1, min(days, 30))))
            return
        if path == "/api/wordcloud":
            q = parse_qs(u.query)
            days = int((q.get("days") or ["14"])[0])
            self._json(interest_words(max(1, min(days, 60))))
            return
        if path == "/api/diary":
            q = parse_qs(u.query)
            day = (q.get("date") or [""])[0].strip()
            if day:
                self._json(diary_read(day))
            else:
                self._json({"ok": True, "entries": diary_list()})
            return
        if path == "/api/tokens":
            self._json(token_stats())
            return
        if path == "/api/logs":
            q = parse_qs(u.query)
            name = (q.get("name") or ["watchdog"])[0]
            lines = int((q.get("lines") or ["60"])[0])
            fp = {
                "watchdog": ROOT / "logs" / "watchdog.log",
                "astrbot": None,
            }.get(name, ROOT / "logs" / "watchdog.log")
            if name == "astrbot":
                cand = ROOT / "astrbot" / "data" / "data" / "logs"
                if cand.is_file():
                    fp = cand                      # AstrBot 的日志就是个文件，不是目录
                elif cand.is_dir():
                    files = [x for x in cand.rglob("*") if x.is_file()]
                    fp = max(files, key=lambda x: x.stat().st_mtime) if files else None
                else:
                    fp = None
            if not fp or not fp.exists():
                self._json({"ok": False, "text": "（没有日志文件）"})
                return
            text = fp.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
            clean = [re.sub(r"\x1b\[[0-9;]*m", "", x) for x in text]
            self._json({"ok": True, "file": str(fp), "text": "\n".join(clean)})
            return
        if path == "/api/audio":
            self._json(audio_list())
            return
        if path == "/api/audio/job":
            q = parse_qs(u.query)
            jid = (q.get("id") or [""])[0]
            job = JOBS.get(jid)
            if not job:
                self._json({"state": "gone"})
                return
            self._json({"state": job["state"], "log": job["log"][-40:],
                        "result": job.get("result"), "error": job.get("error")})
            return
        if path == "/api/audio/play":
            q = parse_qs(u.query)
            rel = (q.get("file") or [""])[0]
            fp = audio_lib.play_path(rel) if audio_lib else None
            if fp is None:
                self._json({"ok": False, "error": "not found"}, 404)
                return
            data = fp.read_bytes()
            rng = self.headers.get("Range")
            if rng and rng.startswith("bytes="):
                try:
                    a, b = rng[6:].split("-", 1)
                    start = int(a or 0)
                    end = int(b) if b else len(data) - 1
                    end = min(end, len(data) - 1)
                    chunk = data[start:end + 1]
                    self.send_response(206)
                    self.send_header("Content-Type", "audio/mpeg")
                    self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
                    self.send_header("Content-Length", str(len(chunk)))
                    self.send_header("Accept-Ranges", "bytes")
                    self.end_headers()
                    self.wfile.write(chunk)
                    return
                except Exception:
                    pass
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/bili":
            data = bili_account()
            data["recent"] = recent_commented(5)
            self._json(data)
            return
        if path == "/api/action/brush":
            q = parse_qs(u.query)
            want = (q.get("id") or [""])[0]
            res = read_json(TRIGGER_RESULT, {}) or {}
            if not res:
                # 刚触发、插件还没写 running：给它 10 分钟窗口
                if want and LAST_BRUSH.get("id") == want and time.time() - LAST_BRUSH.get("at", 0) < 600:
                    self._json({"state": "starting", "pending": TRIGGER.exists()})
                else:
                    self._json({"state": "idle"})
                return
            if want and str(res.get("id")) != want:
                self._json({"state": "stale", "result": res})
                return
            self._json({"state": res.get("state") or "idle", "result": res,
                        "pending": TRIGGER.exists()})
            return
        if path == "/api/report":
            q = parse_qs(u.query)
            day = (q.get("date") or [date.today().isoformat()])[0]
            fp = ROOT / "reports" / f"bili-{day}.md"
            if not fp.exists():
                self._json({"ok": False, "text": f"没有 {day} 的日报"})
                return
            self._json({"ok": True, "text": fp.read_text(encoding="utf-8", errors="replace")})
            return
        self._json({"ok": False, "error": "not found"}, 404)

    # ---------- POST ----------
    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            self._json({"ok": False, "error": "bad token"}, 403)
            return
        u = urlparse(self.path)

        # 二进制上传必须先处理：否则 _body() 会把请求体当 JSON 读掉
        if u.path == "/api/diary/rewrite":
            if not self._authorized():
                self._json({"ok": False, "error": "token"}, 403)
                return
            try:
                DIARY_TRIGGER.write_text(
                    json.dumps({"action": "rewrite", "at": __import__("time").time()},
                               ensure_ascii=False),
                    encoding="utf-8",
                )
                self._json({"ok": True, "msg": "已通知她重写，十几秒后刷新"})
            except OSError as exc:
                self._json({"ok": False, "error": str(exc)}, 500)
            return
        if u.path == "/api/audio/upload":
            q = parse_qs(urlparse(self.path).query)
            name = (q.get("name") or ["upload.m4a"])[0]
            safe = re.sub(r"[^\w.\-\u4e00-\u9fff]", "_", name)[-60:] or "upload.m4a"
            dst = ROOT / "03-临时" / f"upload_{int(time.time())}_{safe}"
            dst.parent.mkdir(parents=True, exist_ok=True)
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > 200 * 1024 * 1024:
                self._json({"ok": False, "error": "文件为空或超过 200MB"}, 400)
                return
            with dst.open("wb") as f:
                left = n
                while left > 0:
                    chunk = self.rfile.read(min(1 << 20, left))
                    if not chunk:
                        break
                    f.write(chunk)
                    left -= len(chunk)
            self._json({"ok": True, "path": str(dst), "bytes": dst.stat().st_size,
                        "name": dst.name})
            return

        body = self._body()

        if u.path == "/api/config/bili":
            cfg = read_json(ROOT / "bili_push.json", {}) or {}
            for k, v in body.items():
                if k in BILI_KEYS:
                    cfg[k] = v
            self._json({"ok": write_json(ROOT / "bili_push.json", cfg), "config": cfg})
            return

        if u.path == "/api/config/dm":
            cfg = read_json(ROOT / "proactive_dm.json", {}) or {}
            for k, v in body.items():
                if k in DM_KEYS:
                    cfg[k] = v
            self._json({"ok": write_json(ROOT / "proactive_dm.json", cfg), "config": cfg})
            return

        if u.path == "/api/mood/reroll":
            keys = list(MOOD_TEXT.keys())
            weights = [50, 28, 12, 5, 3, 2]
            m = random.choices(keys, weights=weights, k=1)[0]
            note, hint = MOOD_TEXT[m]
            data = {"date": date.today().isoformat(), "mood": m, "note": note, "hint": hint,
                    "rolled": datetime.now().strftime("%H:%M"), "rerolled": True}
            self._json({"ok": write_json(ROOT / "mood.json", data), "mood": data})
            return

        if u.path == "/api/mood/set":
            m = str(body.get("mood") or "")
            if m not in MOOD_TEXT:
                self._json({"ok": False, "error": "未知心情"}, 400)
                return
            note, hint = MOOD_TEXT[m]
            data = {"date": date.today().isoformat(), "mood": m, "note": note, "hint": hint,
                    "rolled": datetime.now().strftime("%H:%M"), "manual": True}
            self._json({"ok": write_json(ROOT / "mood.json", data), "mood": data})
            return

        if u.path == "/api/rules/delete":
            rid = str(body.get("id") or "")
            data = read_json(ROOT / "learned_rules.json", {}) or {}
            before = len(data.get("rules") or [])
            data["rules"] = [r for r in (data.get("rules") or []) if r.get("id") != rid]
            self._json({"ok": write_json(ROOT / "learned_rules.json", data),
                        "removed": before - len(data["rules"])})
            return

        if u.path == "/api/bili/check":
            d = bili_account()
            write_json(
                ROOT / "bili_login_state.json",
                {
                    "last_check": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "last_ok": bool(d.get("logged_in")),
                    "last_status": "ok" if d.get("logged_in") else "expired",
                },
            )
            d["recent"] = recent_commented(3)
            self._json(d)
            return

        if u.path == "/api/audio/import":
            mode = str(body.get("mode") or "bvid")
            payload = body if mode in ("bvid", "url") else {
                "path": body.get("path"), "name": body.get("name"), "cleanup": True,
            }
            if mode == "url" and not str(body.get("url") or "").strip():
                self._json({"ok": False, "error": "缺网址"}, 400)
                return
            if mode == "bvid" and not str(body.get("bvid") or "").strip():
                self._json({"ok": False, "error": "缺 BV 号或链接"}, 400)
                return
            if mode == "file" and not payload.get("path"):
                self._json({"ok": False, "error": "缺文件"}, 400)
                return
            if audio_lib is None:
                self._json({"ok": False, "error": "audio_lib 不可用"}, 500)
                return
            jid = f"a{int(time.time() * 1000)}"
            JOBS[jid] = {"state": "queued", "log": [], "updated": time.time()}
            threading.Thread(target=run_import, args=(jid, mode, payload), daemon=True).start()
            self._json({"ok": True, "id": jid})
            return

        if u.path == "/api/audio/delete":
            song = str(body.get("song") or "")
            if not song:
                self._json({"ok": False, "error": "缺歌名"}, 400)
                return
            removed = audio_lib.delete_song(song) if audio_lib else 0
            self._json({"ok": True, "removed": removed})
            return

        if u.path == "/api/bili/login-ui":
            self._json(start_login_ui())
            return

        if u.path == "/api/action/brush":
            mode = str(body.get("mode") or "dry")
            if mode not in ("dry", "auto", "dm"):
                mode = "dry"
            tid = str(int(time.time()))
            TRIGGER_RESULT.unlink(missing_ok=True)
            LAST_BRUSH["id"] = tid
            LAST_BRUSH["at"] = time.time()
            write_json(TRIGGER, {"id": tid, "mode": mode, "at": datetime.now().isoformat(timespec="seconds")})
            self._json({"ok": True, "id": tid, "mode": mode})
            return

        if u.path == "/api/action/testmsg":
            text = str(body.get("text") or "").strip() or "通道测试：能收到就是通的"
            self._json(send_qq(text))
            return

        if u.path == "/api/service":
            action = str(body.get("action") or "")
            bat = {
                "start": "start.bat",
                "stop": "stop.bat",
                "restart": "_astrbot.bat",
                "guard": "guard.bat",
            }.get(action)
            if not bat:
                self._json({"ok": False, "error": "未知动作"}, 400)
                return
            if action == "restart":
                self._kill_port(6185)
                time.sleep(2)
            try:
                subprocess.Popen(
                    ["cmd", "/c", str(ROOT / bat)],
                    cwd=str(ROOT),
                    creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                    close_fds=True,
                )
                self._json({"ok": True, "action": action})
            except Exception as exc:  # noqa: BLE001
                self._json({"ok": False, "error": str(exc)}, 500)
            return

        self._json({"ok": False, "error": "not found"}, 404)

    @staticmethod
    def _kill_port(port: int) -> None:
        try:
            out = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True, timeout=20
            ).stdout
            pids = set()
            for line in out.splitlines():
                if f":{port} " in line and "LISTENING" in line:
                    pids.add(line.split()[-1])
            for pid in pids:
                subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True, timeout=15)
        except Exception:
            pass


def open_dashboard_when_ready(port: int) -> None:
    for _ in range(40):
        if port_up(port):
            try:
                os.startfile(f"http://127.0.0.1:{port}/")
            except Exception:
                pass
            return
        time.sleep(0.15)


def main() -> int:
    global TOKEN
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    if TOKEN_FILE.exists():
        TOKEN = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if not TOKEN:
        TOKEN = secrets.token_urlsafe(16)
        TOKEN_FILE.write_text(TOKEN, encoding="utf-8")

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"控制台: http://127.0.0.1:{args.port}/  (Ctrl+C 退出)", flush=True)
    if not args.no_open:
        threading.Thread(target=open_dashboard_when_ready, args=(args.port,), daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
