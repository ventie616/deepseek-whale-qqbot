"""从 Edge 读 B 站 cookie，写成 AstrBot 的 bili_account.json，并验证登录态。

前提：Edge 已完全退出（cookie 数据库被独占锁，运行中读不到）。
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

import win32crypt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
TARGET = Path(r"C:\QQBot\bili_account.json")
WANT = {"SESSDATA", "bili_jct", "DedeUserID"}


def edge_dirs() -> list[Path]:
    base = Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Edge" / "User Data"
    dirs = []
    if (base / "Default").is_dir():
        dirs.append(base / "Default")
    for prof in sorted(base.glob("Profile *")):
        if prof.is_dir():
            dirs.append(prof)
    return dirs


def master_key(ud: Path) -> bytes:
    state = json.loads((ud / "Local State").read_text(encoding="utf-8"))
    raw = base64.b64decode(state["os_crypt"]["encrypted_key"])
    return win32crypt.CryptUnprotectData(raw[5:], None, None, None, 0)[1]


def decrypt(key: bytes, blob: bytes) -> str:
    if not blob:
        return ""
    if blob[:3] in (b"v10", b"v11"):
        return AESGCM(key).decrypt(blob[3:15], blob[15:], None).decode("utf-8", "replace")
    if blob[:3] == b"v20":
        raise RuntimeError("该 cookie 使用 App-Bound 加密(v20)，需要换方式")
    return blob.decode("utf-8", "replace")


def read_cookies() -> dict[str, str]:
    ud = Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Edge" / "User Data"
    key = master_key(ud)
    found: dict[str, str] = {}
    for prof in edge_dirs():
        src = prof / "Network" / "Cookies"
        if not src.exists():
            continue
        tmp = Path(os.environ["TEMP"]) / f"edge_ck_{prof.name.replace(' ', '_')}.db"
        try:
            shutil.copy2(src, tmp)
        except PermissionError:
            print(f"[跳过] {prof.name}: cookie 库被占用（Edge 还开着？）")
            continue
        try:
            con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
            rows = con.execute(
                "SELECT name, value, encrypted_value FROM cookies "
                "WHERE host_key LIKE '%bilibili%' AND name IN (?,?,?)",
                tuple(WANT),
            ).fetchall()
            con.close()
        except Exception as exc:  # noqa: BLE001
            print(f"[跳过] {prof.name}: 读取失败 {exc}")
            continue
        for name, plain, blob in rows:
            try:
                val = plain or decrypt(key, blob)
            except Exception:  # noqa: BLE001
                continue
            if val:
                found.setdefault(name, val)
        if found.get("SESSDATA"):
            print(f"[命中] {prof.name} 找到了 B 站 cookie")
            break
    return found


def verify(cookies: dict[str, str]) -> str | None:
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
    req = urllib.request.Request(
        "https://api.bilibili.com/x/space/myinfo",
        headers={"User-Agent": UA, "Cookie": cookie_header, "Referer": "https://www.bilibili.com/"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        d = json.loads(resp.read())
    if d.get("code") != 0:
        return None
    u = d["data"]
    return f"{u.get('name')} (UID {u.get('mid')}, Lv{u.get('level')}, 硬币 {u.get('coins')})"


def main() -> int:
    cookies = read_cookies()
    if not cookies.get("SESSDATA"):
        print("\n没读到 SESSDATA。八成是 Edge 还在后台运行：")
        print("  任务管理器里结束所有 msedge.exe，或托盘退出 Edge，再跑一次。")
        return 1

    data = {
        "sessdata": cookies.get("SESSDATA", ""),
        "bili_jct": cookies.get("bili_jct", ""),
        "dedeuserid": cookies.get("DedeUserID", ""),
    }
    TARGET.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[写入] {TARGET}")
    for k, v in data.items():
        print(f"  {k:12s} len={len(v):4d}  {v[:6] + '…' + v[-4:] if len(v) > 12 else v}")

    print("\n[验证] 调用 B 站 myinfo ...")
    try:
        who = verify(data)
    except Exception as exc:  # noqa: BLE001
        print(f"  验证失败：{type(exc).__name__}: {exc}")
        return 2
    if who:
        print(f"  登录成功：{who}")
        return 0
    print("  cookie 无效或已过期（B 站返回未登录），请重新登录后重试。")
    return 2


if __name__ == "__main__":
    sys.exit(main())
