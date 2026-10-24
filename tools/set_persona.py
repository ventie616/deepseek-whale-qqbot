"""把 persona\\whale-girl.md 同步进 AstrBot 的人格库。

用法：
    python tools\\set_persona.py            # 写入 DB
    python tools\\set_persona.py --check    # 只对比，不写
"""

from __future__ import annotations

import argparse
import datetime
import pathlib
import re
import sqlite3
import sys

ROOT = pathlib.Path(r"C:\QQBot")
SRC = ROOT / "persona" / "whale-girl.md"
DB = ROOT / "astrbot" / "data" / "data_v4.db"
PERSONA_ID = "whale-girl"


def load_markdown() -> str:
    raw = SRC.read_text(encoding="utf-8")
    # 去掉 markdown 结构，只留给人格用的正文
    lines = []
    for line in raw.splitlines():
        s = line.rstrip()
        if s.startswith("# "):
            lines.append(s[2:].strip())
            continue
        if s.startswith("> ") or s.strip() == "---":
            continue
        s = re.sub(r"^#{2,4}\s*", "", s)
        s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
        s = re.sub(r"`([^`]+)`", r"\1", s)
        lines.append(s)
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    if not SRC.exists():
        print(f"找不到 {SRC}")
        return 1
    text = load_markdown()
    print(f"读入 {SRC.name}: {len(text)} 字 / {len(text.splitlines())} 行")

    con = sqlite3.connect(DB)
    row = con.execute("SELECT system_prompt FROM personas WHERE persona_id=?", (PERSONA_ID,)).fetchone()
    if row is None:
        print("DB 里没有这个 persona_id，先建一个空的？请用面板创建 whale-girl")
        con.close()
        return 1
    old = row[0] or ""
    print(f"DB 现有: {len(old)} 字")
    if args.check:
        print("一致" if old.strip() == text.strip() else "不一致（跑不带 --check 的版本写入）")
        con.close()
        return 0

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
    con.execute(
        "UPDATE personas SET system_prompt=?, updated_at=? WHERE persona_id=?",
        (text, now, PERSONA_ID),
    )
    con.commit()
    con.close()
    print(f"已写入，现在 {len(text)} 字")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
