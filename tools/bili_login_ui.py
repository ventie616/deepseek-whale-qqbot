"""B站 登录面板：本地网页，粘贴 cookie 即可验证并保存。

启动：双击 C:\\QQBot\\bili-login.bat（或 python tools\\bili_login_ui.py）
访问：http://127.0.0.1:8777
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ACCOUNT = Path(r"C:\QQBot\bili_account.json")
STATE = Path(r"C:\QQBot\bili_login_state.json")
EDGE_READER = Path(r"C:\QQBot\tools\read_edge_cookie.py")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HOST, PORT = "127.0.0.1", 8777


# ---------------- 逻辑 ----------------
def load_account() -> dict:
    try:
        return json.loads(ACCOUNT.read_text(encoding="utf-8"))
    except Exception:
        return {}


def mask(v: str) -> str:
    v = str(v or "")
    return f"{v[:8]}…{v[-6:]}" if len(v) > 18 else ("已设置" if v else "未设置")


def parse_cookie(raw: str) -> dict:
    """支持整段 'SESSDATA=..; bili_jct=..; DedeUserID=..' 或分行粘贴。"""
    out = {}
    raw = raw or ""
    for key, field in (
        ("SESSDATA", "sessdata"),
        ("bili_jct", "bili_jct"),
        ("DedeUserID", "dedeuserid"),
    ):
        m = re.search(rf"{key}\s*[=:]\s*([^;\s,]+)", raw, re.I)
        if m:
            out[field] = m.group(1).strip().strip('"\'')
    return out


def check(acc: dict | None = None) -> dict:
    acc = acc or load_account()
    if not acc.get("sessdata"):
        return {"ok": False, "reason": "还没有保存任何 cookie"}
    cookie = (
        f"SESSDATA={acc.get('sessdata','')}; bili_jct={acc.get('bili_jct','')}; "
        f"DedeUserID={acc.get('dedeuserid','')}"
    )
    hdr = {"User-Agent": UA, "Referer": "https://www.bilibili.com/", "Cookie": cookie}
    try:
        req = urllib.request.Request("https://api.bilibili.com/x/web-interface/nav", headers=hdr)
        d = json.loads(urllib.request.urlopen(req, timeout=20).read())
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"网络请求失败：{exc}"}
    data = d.get("data") or {}
    if d.get("code") != 0 or not data.get("isLogin"):
        return {"ok": False, "reason": f"未登录（code={d.get('code')} {d.get('message')}）"}
    return {
        "ok": True,
        "name": data.get("uname"),
        "mid": data.get("mid"),
        "level": (data.get("level_info") or {}).get("current_level"),
        "coins": (data.get("money") or ""),
        "vip": ((data.get("vipStatus") or 0) == 1),
    }


def check_csrf(acc: dict) -> dict:
    """给不存在的视频发一条测试评论，只为验证 csrf 是否有效（不可见）。"""
    cookie = (
        f"SESSDATA={acc.get('sessdata','')}; bili_jct={acc.get('bili_jct','')}; "
        f"DedeUserID={acc.get('dedeuserid','')}"
    )
    hdr = {
        "User-Agent": UA, "Referer": "https://www.bilibili.com/", "Cookie": cookie,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    form = urllib.parse.urlencode(
        {"oid": 999999999999, "type": 1, "message": "csrf-probe", "plat": 1, "csrf": acc.get("bili_jct", "")}
    ).encode()
    try:
        req = urllib.request.Request("https://api.bilibili.com/x/v2/reply/add", data=form, headers=hdr)
        d = json.loads(urllib.request.urlopen(req, timeout=20).read())
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"请求失败：{exc}"}
    code = d.get("code")
    if code == -111:
        return {"ok": False, "reason": "CSRF 校验失败，bili_jct 不对"}
    return {"ok": True, "reason": f"csrf 有效（code={code}）"}


def save(acc: dict) -> None:
    ACCOUNT.write_text(json.dumps(acc, ensure_ascii=False, indent=2), encoding="utf-8")


def read_edge() -> dict:
    if not EDGE_READER.exists():
        return {"ok": False, "reason": "找不到 tools/read_edge_cookie.py"}
    try:
        p = subprocess.run(
            [sys.executable, str(EDGE_READER)],
            capture_output=True, text=True, timeout=60,
            encoding="utf-8", errors="replace",
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"执行失败：{exc}"}
    out = (p.stdout or "") + (p.stderr or "")
    acc = load_account()
    if acc.get("sessdata"):
        r = check(acc)
        return {"ok": bool(r.get("ok")), "reason": r.get("reason") or f"读取成功：{r.get('name')}",
                "raw": out[-600:]}
    return {"ok": False, "reason": "没读到 cookie（Edge 是否已关闭并登录？）", "raw": out[-600:]}


# ---------------- 页面 ----------------
PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>B站 登录面板</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;padding:32px 20px;background:#0d0f13;color:#e6e8ee;
     font:14px/1.6 "Segoe UI",system-ui,sans-serif}
.wrap{max-width:760px;margin:0 auto}
h1{font-size:21px;margin:0 0 6px}
.sub{color:#868e9c;font-size:13px;margin-bottom:22px}
.card{background:#161920;border:1px solid #232833;border-radius:12px;padding:18px 20px;margin-bottom:16px}
.status{display:flex;align-items:center;gap:10px;font-size:15px;margin-bottom:6px}
.dot{width:9px;height:9px;border-radius:50%;background:#888}
.dot.ok{background:#42d17a;box-shadow:0 0 8px #42d17a}
.dot.bad{background:#e0574c;box-shadow:0 0 8px #e0574c}
.kv{color:#9aa2b0;font-size:13px}
label{display:block;font-size:12.5px;color:#9aa2b0;margin:14px 0 6px}
input,textarea{width:100%;background:#0f1218;border:1px solid #2a3040;border-radius:8px;
  color:#e6e8ee;padding:10px 12px;font:13px/1.5 ui-monospace,Consolas,monospace;outline:none}
input:focus,textarea:focus{border-color:#3f6fd6}
textarea{min-height:86px;resize:vertical}
.row{display:flex;gap:10px;flex-wrap:wrap;margin-top:16px}
button{background:#2f5fd0;border:0;color:#fff;padding:10px 18px;border-radius:8px;
  font-size:13.5px;cursor:pointer}
button.ghost{background:#1c2230;border:1px solid #2c3446;color:#c9d0dc}
button:hover{filter:brightness(1.12)}
button:disabled{opacity:.5;cursor:default}
pre{background:#0f1218;border:1px solid #232833;border-radius:8px;padding:10px 12px;
    font-size:12px;color:#9aa2b0;white-space:pre-wrap;word-break:break-all;margin:10px 0 0}
.msg{margin-top:14px;padding:11px 13px;border-radius:8px;font-size:13.5px;display:none}
.msg.ok{display:block;background:#12291b;border:1px solid #1f5233;color:#78e3a1}
.msg.bad{display:block;background:#2a1717;border:1px solid #5a2a26;color:#f0918a}
.hint{font-size:12.5px;color:#7d8593;margin-top:8px}
.hint b{color:#9aa2b0;font-weight:600}
code{background:#1a1f29;padding:1px 6px;border-radius:5px;font-size:12px}
</style></head><body><div class="wrap">
<h1>B站 登录面板</h1>
<div class="sub">只在本机运行（127.0.0.1），凭证只写到 C:\\QQBot\\bili_account.json</div>

<div class="card">
  <div class="status"><span id="dot" class="dot"></span><span id="title">正在检查…</span></div>
  <div class="kv" id="detail"></div>
  <div class="row"><button class="ghost" onclick="doCheck()">重新检查</button></div>
</div>

<div class="card">
  <label>粘贴整段 Cookie（推荐）</label>
  <textarea id="raw" placeholder="SESSDATA=xxxx%2Cxxx...; bili_jct=xxxxxxxx; DedeUserID=1234"></textarea>
  <div class="hint">或分别填写下面三项。当前：<b id="cur"></b></div>

  <label>SESSDATA</label>
  <input id="sessdata" placeholder="5e438c23%2C1808…">
  <label>bili_jct（32 位）</label>
  <input id="jct" placeholder="dc405424609c…">
  <label>DedeUserID</label>
  <input id="uid" placeholder="YOUR_BILI_UID">

  <div class="row">
    <button onclick="doSave()">验证并保存</button>
    <button class="ghost" onclick="doCheck(false)">只验证不保存</button>
    <button class="ghost" onclick="doEdge()">从 Edge 读一次</button>
  </div>
  <div id="msg" class="msg"></div>
  <pre id="rawout" style="display:none"></pre>
</div>

<div class="card">
  <div class="status"><span class="dot"></span><span>怎么拿 cookie</span></div>
  <div class="hint">
    1. 浏览器登录 B站 小号<br>
    2. 按 <code>F12</code> → <b>Application / 应用</b> → <b>Cookies</b> → <code>https://www.bilibili.com</code><br>
    3. 分别复制 <code>SESSDATA</code>、<code>bili_jct</code>、<code>DedeUserID</code> 三个值粘到上面<br>
    4. 点「验证并保存」——会立刻告诉你登没登上、是哪个人
  </div>
</div>
</div>
<script>
const $ = id => document.getElementById(id);
function setMsg(text, ok){ const m=$('msg'); m.textContent=text; m.className='msg '+(ok?'ok':'bad'); }
async function api(path, body){
  const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body||{})});
  return r.json();
}
function render(st){
  $('dot').className = 'dot ' + (st.ok ? 'ok' : 'bad');
  $('title').textContent = st.ok ? ('已登录：' + (st.name||'')) : '未登录';
  $('detail').textContent = st.ok
    ? `UID ${st.mid} · Lv${st.level} · 硬币 ${st.coins||'-'} · 检查于 ${st.at||''}`
    : (st.reason || '');
  if (st.account) $('cur').textContent = `SESSDATA ${st.account.sessdata} / jct ${st.account.jct} / uid ${st.account.uid}`;
}
async function doCheck(useSaved=true){
  const st = await api('/check', {});
  render(st);
  if(!useSaved) setMsg(st.ok ? '验证通过（未保存）' : '验证失败：'+st.reason, st.ok);
}
async function doSave(){
  const body = {raw:$('raw').value, sessdata:$('sessdata').value, bili_jct:$('jct').value, dedeuserid:$('uid').value};
  const st = await api('/save', body);
  render(st);
  setMsg(st.ok ? ('保存成功：' + st.name + '（UID ' + st.mid + '）') : ('失败：' + st.reason), st.ok);
  if (st.csrf) setMsg((st.ok?'保存成功：'+st.name+'　':'失败：'+st.reason+'　') + '｜' + st.csrf, st.ok && st.csrf_ok);
}
async function doEdge(){
  setMsg('正在读 Edge…', true);
  const st = await api('/edge', {});
  render(st);
  if (st.raw) { $('rawout').style.display='block'; $('rawout').textContent = st.raw; }
  setMsg(st.ok ? '读取成功并已登录' : ('失败：' + st.reason), st.ok);
}
fetch('/status').then(r=>r.json()).then(render);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: dict) -> None:
        self._send(200, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def log_message(self, fmt, *args):  # 静默访问日志
        pass

    def _status_payload(self, result: dict) -> dict:
        acc = load_account()
        payload = dict(result)
        payload["at"] = datetime.now().strftime("%H:%M:%S")
        payload["account"] = {
            "sessdata": mask(acc.get("sessdata")),
            "jct": mask(acc.get("bili_jct")),
            "uid": acc.get("dedeuserid") or "未设置",
        }
        return payload

    def do_GET(self):
        if self.path.startswith("/status"):
            self._json(self._status_payload(check()))
            return
        self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            body = {}
        if self.path.startswith("/check"):
            self._json(self._status_payload(check()))
            return
        if self.path.startswith("/edge"):
            self._json(self._status_payload(read_edge()))
            return
        if self.path.startswith("/save"):
            acc = load_account()
            parsed = parse_cookie(body.get("raw") or "")
            for field in ("sessdata", "bili_jct", "dedeuserid"):
                v = str(body.get(field) or "").strip()
                if v:
                    parsed[field] = v
            acc.update(parsed)
            if not acc.get("sessdata"):
                self._json(self._status_payload({"ok": False, "reason": "没解析到 SESSDATA，请粘贴后再保存"}))
                return
            r = check(acc)
            if not r.get("ok"):
                self._json(self._status_payload({**r, "reason": "验证未通过，未保存：" + str(r.get("reason"))}))
                return
            save(acc)
            payload = self._status_payload(r)
            if acc.get("bili_jct"):
                c = check_csrf(acc)
                payload["csrf"] = c.get("reason")
                payload["csrf_ok"] = bool(c.get("ok"))
            self._json(payload)
            return
        self._json({"ok": False, "reason": "未知接口"})


def main() -> int:
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print(f"B站 登录面板已启动：{url}")
    print("关闭这个窗口即停止服务")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
