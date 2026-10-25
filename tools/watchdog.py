"""AstrBot / SnowLuma 守护：端口探活，挂了自动拉起，并留待通知标记。

- 同时看 6185（AstrBot）和 5099（SnowLuma）
- 连续 2 次失败才算停（避免网络抖动）
- 拉起前写 watchdog_restart.json，由 guard_report 插件负责通知
- 15 分钟内最多拉 3 次，避免崩溃循环
- 命名互斥体保证单实例

用法：python tools\\watchdog.py  或双击 guard.bat
暂停：建空文件 watchdog_pause
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
LOG_DIR = ROOT / "logs"
LOG = LOG_DIR / "watchdog.log"
HEARTBEAT = ROOT / "watchdog_heartbeat.json"
FLAG = ROOT / "watchdog_restart.json"
PAUSE = ROOT / "watchdog_pause"
INTERVAL = 30
FAILS_TO_RESTART = 2
BOOT_TIMEOUT = 150
WINDOW = 900
MAX_RESTARTS = 3

SERVICES = {
    6185: {"name": "AstrBot", "bat": ROOT / "_astrbot.bat"},
    5099: {"name": "SnowLuma", "bat": ROOT / "_snowluma.bat"},
}


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


def port_up(port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


_MUTEX = None


def acquire_lock():
    global _MUTEX
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel32.CreateMutexW(None, False, "Global\\QQBotWatchdogMutex")
        if not handle or ctypes.get_last_error() == 183:
            return None
        _MUTEX = handle
        return handle
    except Exception:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", 45999))
            s.listen(1)
            return s
        except OSError:
            return None


def start_service(bat: Path) -> bool:
    try:
        subprocess.Popen(
            f'start /min "" "{bat}"', shell=True, cwd=str(ROOT),
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        return True
    except Exception as exc:  # noqa: BLE001
        log(f"拉起 {bat.name} 失败：{exc}")
        return False


def write_flag(details: list[str], when: str) -> None:
    try:
        FLAG.write_text(
            json.dumps({"at": when, "reason": "；".join(details)}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        log(f"写待通知标记失败：{exc}")


def main() -> int:
    lock = acquire_lock()
    if lock is None:
        log("已有守护进程在跑（互斥体被占用），退出")
        return 1
    (ROOT / "watchdog.pid").write_text(str(os.getpid()), encoding="utf-8")
    log(f"守护启动 pid={os.getpid()} 监控端口={list(SERVICES)} 间隔={INTERVAL}s")

    fails = {p: 0 for p in SERVICES}
    down_since: dict[int, str] = {}
    restarts: list[float] = []

    while True:
        try:
            if PAUSE.exists():
                HEARTBEAT.write_text(
                    json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "paused": True},
                               ensure_ascii=False),
                    encoding="utf-8",
                )
                time.sleep(INTERVAL)
                continue

            state = {}
            for port, svc in SERVICES.items():
                up = port_up(port)
                state[port] = up
                if up:
                    if fails[port] >= FAILS_TO_RESTART and port in down_since:
                        log(f"{svc['name']} 端口恢复（{down_since[port]} 起中断）")
                        write_flag([f"{svc['name']} 短暂中断后自行恢复（可能你手动重启了）"],
                                   down_since[port])
                        down_since.pop(port, None)
                    fails[port] = 0
                else:
                    fails[port] += 1
                    if fails[port] == 1:
                        down_since.setdefault(port, datetime.now().isoformat(timespec="seconds"))
                    log(f"{svc['name']} 探活失败（{fails[port]}/{FAILS_TO_RESTART}）")

            HEARTBEAT.write_text(
                json.dumps(
                    {
                        "at": datetime.now().isoformat(timespec="seconds"),
                        "ports": {str(k): v for k, v in state.items()},
                        "fails": {str(k): v for k, v in fails.items()},
                        "restarts_recent": len([t for t in restarts if time.time() - t < WINDOW]),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            to_start = [p for p in SERVICES if fails[p] >= FAILS_TO_RESTART]
            if to_start:
                now = time.time()
                recent = [t for t in restarts if now - t < WINDOW]
                if len(recent) >= MAX_RESTARTS:
                    log(f"{WINDOW//60} 分钟内已重启 {len(recent)} 次，暂停（避免死循环）")
                else:
                    when = min((down_since.get(p) for p in to_start), default=None) or \
                        datetime.now().isoformat(timespec="seconds")
                    details = [f"{SERVICES[p]['name']} 无响应，已自动拉起" for p in to_start]
                    if 5099 in to_start:
                        details.append("SnowLuma 重启后需要去 5099 手动点「注入」")
                    write_flag(details, when)
                    for p in to_start:
                        log(f"判定 {SERVICES[p]['name']} 已停，开始拉起")
                        start_service(SERVICES[p]["bat"])
                        fails[p] = 0
                        down_since.pop(p, None)
                    restarts.append(now)

                    deadline = time.time() + BOOT_TIMEOUT
                    ok = False
                    while time.time() < deadline:
                        time.sleep(5)
                        if all(port_up(p) for p in to_start):
                            ok = True
                            break
                    log("拉起成功，端口已通" if ok else f"拉起后 {BOOT_TIMEOUT}s 仍不通")
            time.sleep(INTERVAL)
        except KeyboardInterrupt:
            log("收到中断，守护退出")
            return 0
        except Exception as exc:  # noqa: BLE001
            log(f"守护循环异常：{exc}")
            time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
