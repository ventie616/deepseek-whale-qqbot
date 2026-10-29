"""把代码里残留的 D:\\QQrobot 全部改成 D:\\QQrobot，改之前留一份备份。"""
import pathlib
import re
import shutil
import time

ROOT = pathlib.Path(r"D:\QQrobot")
BACKUP = ROOT / "02-备份" / f"pathfix-{time.strftime('%Y%m%d-%H%M%S')}"

SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", "venv", "snowluma", "stickers",
    "voice_out", "downloads", "backup", "02-备份", "_disabled_plugins",
    "logs", "reports", "chat_archive", "diary", "sticker_inbox",
}
EXTS = {".py", ".json", ".bat", ".html", ".js", ".css", ".md", ".txt",
        ".yaml", ".yml", ".url", ".cmd", ".ps1"}

# 不同写法的替换规则（顺序重要：先长后短）
RULES = [
    (re.compile(r"C:\\\\QQ[Bb]ot"), r"D:\\\\QQrobot"),
    (re.compile(r"C:\\QQ[Bb]ot"), r"D:\\QQrobot"),
    (re.compile(r"C:/QQ[Bb]ot"), "D:/QQrobot"),
    (re.compile(r"C%3A%5CQQ[Bb]ot", re.I), "D%3A%5CQQrobot"),
    (re.compile(r"C%3A/QQ[Bb]ot", re.I), "D%3A/QQrobot"),
]

changed = []
total = 0
for p in ROOT.rglob("*"):
    if not p.is_file():
        continue
    if any(part in SKIP_DIRS for part in p.relative_to(ROOT).parts):
        continue
    if p.suffix.lower() not in EXTS:
        continue
    try:
        s = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        continue
    if not re.search(r"C:[\\/]+QQ[Bb]ot", s, re.I):
        continue
    new = s
    for pat, rep in RULES:
        new = pat.sub(rep, new)
    if new == s:
        continue
    # 备份
    rel = p.relative_to(ROOT)
    dst = BACKUP / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p, dst)
    p.write_text(new, encoding="utf-8", newline="")
    n = len(re.findall(r"C:[\\/]+QQ[Bb]ot", s, re.I))
    total += n
    changed.append(str(rel))

print(f"改动文件 {len(changed)} 个，替换 {total} 处")
print("备份在:", BACKUP)
for c in changed[:80]:
    print("  ", c)
