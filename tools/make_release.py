r"""把仓库打成干净的发布包（不带 .git、不带运行数据）。

用法：
    python tools/make_release.py                 # 输出到仓库上一级/QQBot-open-release.zip
    python tools/make_release.py --out D:\x.zip
"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
NAME = "QQBot-open"


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "-c", "core.quotepath=false", "ls-files"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout
    files = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith('"') and line.endswith('"'):
            line = line[1:-1]
        files.append(line)
    return files


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT.parent / f"{NAME}-release.zip"))
    args = ap.parse_args()
    out = pathlib.Path(args.out)

    files = tracked_files()
    if not files:
        print("git ls-files 为空，先确认在仓库根目录")
        return 1

    bad = [f for f in files if f == ".git" or f.startswith(".git/") or "/.git/" in f]
    if bad:
        print("⚠️ 跟踪列表里出现 .git 文件，停止")
        return 1

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in files:
            p = ROOT / f
            if p.is_file():
                z.write(p, f"{NAME}/{f}")

    with zipfile.ZipFile(out) as z:
        names = z.namelist()

    print(f"已生成 {out}")
    print(f"  文件 {len(names)} 个 / {out.stat().st_size/1024:.0f} KB")
    print(f"  含 .git: {'是（异常）' if any('.git/' in n for n in names) else '否'}")
    # 顺手查一下包里有没有明显敏感串
    patterns = ("YOUR_QQ", "YOUR_BILI_UID", "BOT_QQ", "YOUR_BILI_NAME")
    hits = []
    with zipfile.ZipFile(out) as z:
        for n in names:
            if n.endswith(("make_release.py",)):
                continue  # 扫描器自己带着这些串
            if n.endswith((".py", ".md", ".json", ".html", ".bat", ".txt")):
                t = z.read(n).decode("utf-8", "replace")
                for pat in patterns:
                    if pat in t:
                        hits.append(f"{n}: {pat}")
                if re.search(r"SESSDATA=[0-9a-f%]{20,}", t):
                    hits.append(f"{n}: 疑似 SESSDATA 值")
    if hits:
        print("⚠️ 包里有敏感串：")
        for h in hits[:10]:
            print("  ", h)
        return 1
    print("  脱敏：未发现敏感串")
    return 0


if __name__ == "__main__":
    sys.exit(main())
