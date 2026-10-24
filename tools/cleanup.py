"""清理 C:\\QQBot 里的残留/缓存文件。默认只预演，加 --apply 才真删。

分级：
  A 安全：下载包、临时媒体缓存、预览图、备份、项目内 __pycache__
  B 需确认：主号残留数据、表情原图存档、重复表情副本
"""

import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(r"C:\QQBot")
APPLY = "--apply" in sys.argv
TIER = "A"
for a in sys.argv:
    if a.upper() in ("A", "B", "AB"):
        TIER = a.upper()


def size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def dir_size(p: Path) -> int:
    return sum(size(f) for f in p.rglob("*") if f.is_file()) if p.exists() else 0


targets: list[tuple[str, Path, str]] = []

# ---- A 级 ----
for name in ("meme-pack.zip", "SnowLuma-v2.0.3-win-x64.zip"):
    p = ROOT / "downloads" / name
    if p.exists():
        targets.append(("A", p, "下载包，已解压/已导入"))

temp = ROOT / "astrbot" / "data" / "temp"
if temp.exists():
    targets.append(("A", temp, "AstrBot 聊天图片缓存"))

prev = ROOT / "previews"
if prev.exists():
    targets.append(("A", prev, "预览图，可重新生成"))

for p in (
    ROOT / "_backup_sticker_sender.py",
    ROOT / "astrbot" / "data" / "plugins" / "chat_archive" / "main.py.bak",
):
    if p.exists():
        targets.append(("A", p, "改代码前的备份"))

for d in ROOT.rglob("__pycache__"):
    if d.is_dir() and "venv" not in d.parts:
        targets.append(("A", d, "Python 字节码缓存"))

# ---- B 级 ----
main_acc = ROOT / "snowluma" / "data" / "YOUR_QQ"
if main_acc.exists():
    targets.append(("B", main_acc, "主号 YOUR_QQ 的残留数据（误注入时产生，机器人不用它）"))

imported = ROOT / "stickers" / "_imported"
if imported.exists():
    targets.append(("B", imported, "表情原图存档（导入前的原始文件）"))

# 重复表情：同哈希，保留第一份
groups: dict[str, list[Path]] = {}
for p in (ROOT / "stickers").rglob("*"):
    if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"} \
            and "_imported" not in p.parts:
        try:
            h = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            continue
        groups.setdefault(h, []).append(p)
for h, files in groups.items():
    for extra in sorted(files)[1:]:
        targets.append(("B", extra, "重复表情副本（同图不同名）"))

wanted = {"A"} if TIER == "A" else {"A", "B"} if TIER == "AB" else {"B"}
sel = [t for t in targets if t[0] in wanted]

by_tier: dict[str, int] = {}
for tier, path, _ in sel:
    s = dir_size(path) if path.is_dir() else size(path)
    by_tier[tier] = by_tier.get(tier, 0) + s

print(f"模式：{'执行删除' if APPLY else '预演（加 --apply 才删）'} | 范围 {TIER}\n")
for tier, path, note in sorted(sel, key=lambda t: -dir_size(t[1]) if t[1].is_dir() else -size(t[1])):
    s = dir_size(path) if path.is_dir() else size(path)
    rel = path.relative_to(ROOT)
    print(f"  [{tier}] {s/1024/1024:8.2f} MB  {rel}  —— {note}")

print(f"\n合计可回收 {sum(by_tier.values())/1024/1024:.1f} MB"
      f"（A {'%.1f' % (by_tier.get('A',0)/1024/1024)} MB｜B {'%.1f' % (by_tier.get('B',0)/1024/1024)} MB）")

if not APPLY:
    print("\n未改动任何文件。")
    raise SystemExit(0)

freed = 0
failed = 0
for tier, path, _ in sel:
    s = dir_size(path) if path.is_dir() else size(path)
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        freed += s
    except OSError as exc:
        failed += 1
        print(f"  跳过 {path.name}：{exc}")
print(f"\n完成：释放 {freed/1024/1024:.1f} MB，失败 {failed} 个")
