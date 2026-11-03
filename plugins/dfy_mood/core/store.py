"""SQLite 持久化：心情状态与情绪事件日志。"""

from __future__ import annotations

import json
import os
import sqlite3
import threading

try:
    from .state import MoodState
except ImportError:  # 兼容以顶层模块方式加载
    from state import MoodState


_SCHEMA = """
CREATE TABLE IF NOT EXISTS mood_state (
    session_key TEXT PRIMARY KEY,
    valence REAL NOT NULL,
    arousal REAL NOT NULL,
    energy REAL NOT NULL,
    intimacy REAL NOT NULL,
    jealousy REAL NOT NULL DEFAULT 0,
    label TEXT NOT NULL DEFAULT '平静',
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at REAL NOT NULL,
    last_interact_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS mood_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key TEXT NOT NULL,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    reason TEXT NOT NULL,
    delta TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mood_event_session
    ON mood_event (session_key, ts DESC);
"""


class MoodStore:
    """线程安全的简单存储，数据量很小，直接读写 SQLite。"""

    def __init__(self, db_path: str) -> None:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def get(self, session_key: str) -> MoodState:
        """取出状态；不存在时创建一份基线状态并落库。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT valence, arousal, energy, intimacy, jealousy, label,"
                " enabled, updated_at, last_interact_at"
                " FROM mood_state WHERE session_key = ?",
                (session_key,),
            ).fetchone()

        if row is not None:
            return MoodState(
                session_key=session_key,
                valence=row[0],
                arousal=row[1],
                energy=row[2],
                intimacy=row[3],
                jealousy=row[4],
                label=row[5],
                enabled=bool(row[6]),
                updated_at=row[7],
                last_interact_at=row[8],
            )

        state = MoodState(session_key=session_key)
        self.save(state)
        return state

    def save(self, state: MoodState) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO mood_state"
                " (session_key, valence, arousal, energy, intimacy, jealousy,"
                "  label, enabled, updated_at, last_interact_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    state.session_key,
                    state.valence,
                    state.arousal,
                    state.energy,
                    state.intimacy,
                    state.jealousy,
                    state.label,
                    int(state.enabled),
                    state.updated_at,
                    state.last_interact_at,
                ),
            )
            self._conn.commit()

    def log_event(self, session_key: str, event, ts: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO mood_event (session_key, ts, kind, reason, delta)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    session_key,
                    ts,
                    event.kind,
                    event.reason,
                    json.dumps(event.delta, ensure_ascii=False),
                ),
            )
            self._conn.commit()

    def recent_events(self, session_key: str, limit: int = 5) -> list[tuple]:
        """返回 [(ts, kind, reason, delta_text), ...]，按时间倒序。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, kind, reason, delta FROM mood_event"
                " WHERE session_key = ? ORDER BY ts DESC LIMIT ?",
                (session_key, max(1, limit)),
            ).fetchall()
        return rows

    def close(self) -> None:
        with self._lock:
            self._conn.close()
