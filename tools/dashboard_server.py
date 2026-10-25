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
import threading
import time
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = pathlib.Path(r"C:\QQBot")
DASH = ROOT / "dashboard" / "index.html"
TOKEN_FILE = ROOT / "dashboard" / ".token"
TOKEN = ""

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
    }


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
