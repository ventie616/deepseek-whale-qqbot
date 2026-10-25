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
    # 只用通用规则，不在代码里写死任何个人标识
    rules = [
        (r"SESSDATA=[0-9a-f%]{20,}", "疑似 B站 SESSDATA 值"),
        (r"bili_jct[\"']?\s*[:=]\s*[\"']?[0-9a-f]{32}", "疑似 bili_jct"),
        (r"C:\\\\Users\\\\[A-Za-z0-9_.-]+", "本机用户名路径"),
        (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "私钥"),
        (r"\bsk-[A-Za-z0-9]{20,}\b", "API Key"),
        (r"\b[1-9]\d{9,10}\b", "疑似 QQ 号（10-11 位）"),
    ]
    # 可选：本地名单（不进仓库），一行一个要额外拦的串
    local = ROOT.parent / "secret_ids.txt"
    extra = []
    if local.is_file():
        extra = [x.strip() for x in local.read_text(encoding="utf-8").splitlines()
                 if x.strip() and not x.startswith("#")]

    hits = []
    with zipfile.ZipFile(out) as z:
        for n in names:
            if n.endswith("make_release.py"):
                continue
            if not n.endswith((".py", ".md", ".json", ".html", ".bat", ".txt")):
                continue
            t = z.read(n).decode("utf-8", "replace")
            for pat, why in rules:
                if re.search(pat, t):
                    hits.append(f"{n}: {why}")
            for pat in extra:
                if pat in t:
                    hits.append(f"{n}: 命中本地名单")
    if hits:
        print("⚠️ 包里有敏感串：")
        for h in hits[:10]:
            print("  ", h)
        return 1
    print("  脱敏：未发现敏感串")
    return 0


if __name__ == "__main__":
    sys.exit(main())
