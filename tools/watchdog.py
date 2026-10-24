"""AstrBot 守护进程：端口探活，挂了自动拉起，并留一条待通知标记。

用法：
    python tools\\watchdog.py        （或双击 guard.bat）
停止守护：
    删除 watchdog.pid 或关掉它的窗口；想临时让它别管，建个空文件 watchdog_pause
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(r"C:\QQBot")
BAT = ROOT / "_astrbot.bat"
LOG_DIR = ROOT / "logs"
LOG = LOG_DIR / "watchdog.log"
HEARTBEAT = ROOT / "watchdog_heartbeat.json"
FLAG = ROOT / "watchdog_restart.json"
PAUSE = ROOT / "watchdog_pause"
LOCK_PORT = 45999
PORT = 6185
INTERVAL = 30
FAILS_TO_RESTART = 2
BOOT_TIMEOUT = 150
WINDOW = 900
MAX_RESTARTS = 3


def log(msg: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n"
    try:
        if LOG.exists() and LOG.stat().st_size > 2 * 1024 * 1024:
            LOG.write_text("", encoding="utf-8")
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass
    print(line.strip(), flush=True)


def port_up(timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=timeout):
            return True
    except OSError:
        return False


_MUTEX = None


def acquire_lock():
    """命名互斥体保证单实例（端口锁有竞态，不够硬）。"""
    global _MUTEX
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel32.CreateMutexW(None, False, "Global\\QQBotWatchdogMutex")
        if not handle:
            return None
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            return None
        _MUTEX = handle
        return handle
    except Exception:
        # 退化：用端口锁
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", LOCK_PORT))
            s.listen(1)
            return s
        except OSError:
            return None


def start_astrbot() -> None:
    subprocess.Popen(
        f'start /min "" "{BAT}"',
        shell=True,
        cwd=str(ROOT),
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )


def main() -> int:
    lock = acquire_lock()
    if lock is None:
        log("已有守护进程在跑（锁端口被占用），退出")
        return 1
    (ROOT / "watchdog.pid").write_text(str(os.getpid()), encoding="utf-8")
    log(f"守护启动 pid={os.getpid()} 探活端口={PORT} 间隔={INTERVAL}s")

    fails = 0
    restarts: list[float] = []
    while True:
        try:
            if PAUSE.exists():
                HEARTBEAT.write_text(
                    json.dumps({"at": datetime.now().isoformat(timespec="seconds"),
                                "paused": True}, ensure_ascii=False),
                    encoding="utf-8",
                )
                time.sleep(INTERVAL)
                continue

            up = port_up()
            HEARTBEAT.write_text(
                json.dumps(
                    {
                        "at": datetime.now().isoformat(timespec="seconds"),
                        "port_up": up,
                        "fails": fails,
                        "restarts_recent": len([t for t in restarts if time.time() - t < WINDOW]),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            if up:
                if fails >= FAILS_TO_RESTART:
                    log("端口恢复")
                fails = 0
                time.sleep(INTERVAL)
                continue

            fails += 1
            log(f"探活失败（{fails}/{FAILS_TO_RESTART}）")
            if fails < FAILS_TO_RESTART:
                time.sleep(INTERVAL)
                continue

            now = time.time()
            recent = [t for t in restarts if now - t < WINDOW]
            if len(recent) >= MAX_RESTARTS:
                log(f"{WINDOW//60} 分钟内已重启 {len(recent)} 次，暂停重启（避免死循环），等人工处理")
                time.sleep(INTERVAL)
                continue

            down_since = datetime.now().isoformat(timespec="seconds")
            FLAG.write_text(
                json.dumps({"at": down_since, "reason": "端口无响应"}, ensure_ascii=False),
                encoding="utf-8",
            )
            log("判定 AstrBot 已停，开始拉起")
            start_astrbot()
            restarts.append(now)

            deadline = time.time() + BOOT_TIMEOUT
            ok = False
            while time.time() < deadline:
                time.sleep(5)
                if port_up():
                    ok = True
                    break
            log("拉起成功，端口已通" if ok else f"拉起后 {BOOT_TIMEOUT}s 仍不通，下次再试")
            fails = 0 if ok else FAILS_TO_RESTART - 1
            time.sleep(INTERVAL)
        except KeyboardInterrupt:
            log("收到中断，守护退出")
            return 0
        except Exception as exc:  # noqa: BLE001
            log(f"守护循环异常：{exc}")
            time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
