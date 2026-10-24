"""把仓库里的 C:\\QQBot 统一改成别的目录。

用法：
    python tools\\relocate.py D:\\apps\\QQBot
"""

from __future__ import annotations

import sys
from pathlib import Path

OLD = r"C:\QQBot"
TEXT_EXT = {".py", ".json", ".md", ".bat", ".yaml", ".txt", ".ps1"}


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    new = sys.argv[1].rstrip("\\/")
    if not new:
        print("目标路径不能为空")
        return 1
    root = Path(__file__).resolve().parent.parent
    changed = 0
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in TEXT_EXT:
            continue
        if any(part in {".git", "venv", "__pycache__"} for part in p.parts):
            continue
        try:
            raw = p.read_bytes()
        except OSError:
            continue
        text = raw.decode("utf-8", errors="replace")
        if OLD not in text:
            continue
        note = ""
        if p.suffix.lower() == ".bat":
            note = "  ⚠️ .bat 必须是纯 ASCII + CRLF"
        p.write_bytes(text.replace(OLD, new).encode("utf-8"))
        changed += 1
        print(f"  改写 {p.relative_to(root)}{note}")
    print(f"\n完成：{changed} 个文件从 {OLD} 指向 {new}")
    print("别忘了同时移动运行数据目录，并检查 .bat 的编码/换行。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
