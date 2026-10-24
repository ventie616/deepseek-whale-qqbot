"""提交前自检：扫描仓库里有没有密钥 / 个人信息。发现就退出码 1。

用法：
    python tools\\check_secrets.py            # 扫当前仓库
    python tools\\check_secrets.py C:\\path   # 扫指定目录
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent

TEXT_EXT = {".py", ".json", ".md", ".bat", ".yaml", ".yml", ".txt", ".jsonl", ".ps1", ".toml", ".cfg", ".ini"}
SKIP_DIRS = {".git", "venv", "node_modules", "__pycache__", "data", "logs", "chat_archive", "reports", "stickers", "downloads"}

PATTERNS = {
    "B站 SESSDATA": re.compile(r"SESSDATA=[0-9a-f%]{10,}"),
    "bili_jct": re.compile(r"bili_jct[\"']?\s*[:=]\s*[\"']?[0-9a-f]{32}"),
    "32位 token/hash": re.compile(r"\b[0-9a-f]{32}\b"),
    "QQ 号（10-11 位）": re.compile(r"\b[1-9]\d{9,10}\b"),
    "B站 UID（16 位）": re.compile(r"\b[1-9]\d{15}\b"),
    "本机用户名路径": re.compile(r"C:\\\\Users\\\\[A-Za-z0-9_.-]+"),
    "密码字段": re.compile(r"(?:password|passwd|密码)[\"']?\s*[:=]\s*[\"'][^\"']{4,}[\"']", re.I),
    "私钥": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "OpenAI/DeepSeek Key": re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
}

# 允许出现的占位符 / 示例值
ALLOW = re.compile(
    r"(YOUR_QQ|BOT_QQ|YOUR_BILI_UID|YOUR_BILI_MAIN_UID|YOUR_BILI_NAME|YOUR_NICKNAME|"
    r"PASTE_SESSDATA_HERE|PASTE_BILI_JCT_HERE|<[^>]{1,30}>|127\.0\.0\.1|0\.0\.0\.0)"
)

hits: list[tuple[str, str, int, str]] = []
for p in ROOT.rglob("*"):
    if not p.is_file() or p.suffix.lower() not in TEXT_EXT:
        continue
    if any(part in SKIP_DIRS for part in p.parts):
        continue
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        continue
    for i, line in enumerate(lines, 1):
        if ALLOW.search(line):
            continue
        for name, pat in PATTERNS.items():
            m = pat.search(line)
            if m:
                hits.append((str(p.relative_to(ROOT)), name, i, m.group(0)[:60]))

if not hits:
    print(f"✅ 干净：{ROOT} 没有发现密钥或个人标识")
    raise SystemExit(0)

print(f"⚠️  发现 {len(hits)} 处可疑内容：\n")
for file, name, line, sample in hits:
    print(f"  {file}:{line}  [{name}]  {sample}")
print("\n处理建议：")
print("  1) 密钥类：改成 examples/ 里的占位符，或加到 .gitignore")
print("  2) 个人标识：换成 YOUR_QQ / YOUR_BILI_UID 这类占位符")
print("  3) 误报：在 ALLOW 正则里加白名单")
raise SystemExit(1)
