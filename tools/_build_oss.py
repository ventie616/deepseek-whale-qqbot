"""把 C:\\QQBot 里"我们写的代码"复制到 C:\\QQBot-open，并做脱敏。"""

import json
import pathlib
import re
import shutil

SRC = pathlib.Path(r"C:\QQBot")
DST = pathlib.Path(r"C:\QQBot-open")
PLUGINS = SRC / "astrbot" / "data" / "plugins"
OUR_PLUGINS = [
    "bili_pusher", "bili_tools", "sticker_sender", "group_memory", "chat_archive",
    "anime_kb", "browse_log", "proactive_dm", "feedback_learner", "bili_login_watch",
    "guard_report",
]
# 会被替换成占位符的个人标识
REPLACE = {
    "YOUR_QQ": "YOUR_QQ",
    "BOT_QQ": "BOT_QQ",
    "YOUR_BILI_UID": "YOUR_BILI_UID",
    "YOUR_BILI_MAIN_UID": "YOUR_BILI_MAIN_UID",
    "YOUR_BILI_NAME": "YOUR_BILI_NAME",
    "YOUR_NICKNAME": "YOUR_NICKNAME",
}

if DST.exists():
    shutil.rmtree(DST)
(DST / "plugins").mkdir(parents=True)
(DST / "tools").mkdir()
(DST / "scripts").mkdir()
(DST / "docs").mkdir()
(DST / "examples").mkdir()
(DST / "data" / "anime_kb").mkdir(parents=True)


def sanitize(text: str) -> str:
    for old, new in REPLACE.items():
        text = text.replace(old, new)
    return text


# ---------- 插件 ----------
copied = []
for name in OUR_PLUGINS:
    src = PLUGINS / name
    if not src.is_dir():
        print(f"  跳过（不存在）{name}")
        continue
    out = DST / "plugins" / name
    out.mkdir(parents=True, exist_ok=True)
    for fname in ("main.py", "metadata.yaml", ".astrbot-plugin"):
        f = src / fname
        if f.is_file():
            (out / fname).write_text(sanitize(f.read_text(encoding="utf-8")), encoding="utf-8")
    copied.append(name)
print(f"插件 {len(copied)} 个：{', '.join(copied)}")

# ---------- 工具 ----------
n = 0
for f in sorted((SRC / "tools").glob("*.py")):
    (DST / "tools" / f.name).write_text(sanitize(f.read_text(encoding="utf-8")), encoding="utf-8")
    n += 1
print(f"工具 {n} 个")

# ---------- bat ----------
bats = ["start.bat", "stop.bat", "_astrbot.bat", "_snowluma.bat", "guard.bat",
        "bili-login.bat", "connect-bili.bat", "import-stickers.bat",
        "panel-snowluma.bat", "panel-astrbot.bat", "panel-both.bat"]
m = 0
for name in bats:
    f = SRC / name
    if not f.exists():
        continue
    raw = f.read_bytes()
    text = sanitize(raw.decode("ascii", errors="replace"))
    (DST / "scripts" / name).write_bytes(text.encode("ascii"))
    m += 1
print(f"脚本 {m} 个")

# ---------- 数据（词库/作品库是我们自己的内容）----------
for name in ("glossary.json", "works.json"):
    f = SRC / "anime_kb" / name
    if f.is_file():
        data = json.loads(f.read_text(encoding="utf-8"))
        (DST / "data" / "anime_kb" / name).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
print("知识库数据已复制")

# ---------- 示例配置 ----------
examples = {
    "bili_push.example.json": {
        "enabled": True, "mode": "auto", "interval_minutes": 120, "jitter_minutes": 20,
        "bili_at_name": "YOUR_BILI_NAME", "bili_at_mid": "YOUR_BILI_MAIN_UID",
        "target_qq": "YOUR_QQ", "target_name": "YOUR_NICKNAME",
        "active_hours": [[12, 14], [16, 22]], "weekend_all_day": False,
        "vision_enable": True, "vision_mode": "always", "content_source": "subtitle_first",
        "strictness": "loose", "skip_repost": True, "dedupe_ratio": 0.82, "dedupe_days": 7,
        "link_read": True, "link_vision": True, "memory_inject": True, "memory_items": 5,
    },
    "bili_account.example.json": {
        "sessdata": "PASTE_SESSDATA_HERE",
        "bili_jct": "PASTE_BILI_JCT_HERE",
        "dedeuserid": "YOUR_BILI_UID",
    },
    "proactive_dm.example.json": {
        "enabled": True, "target_qq": "YOUR_QQ", "min_minutes": 45, "max_minutes": 90,
        "active_hours": [[8.5, 23.5]], "max_per_day": 14, "followup_minutes": 35,
        "max_followups_per_day": 2, "morning_ritual": True, "night_ritual": True,
    },
}
for name, obj in examples.items():
    (DST / "examples" / name).write_text(
        json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8"
    )

# ---------- 人格模板 ----------
try:
    import sqlite3

    con = sqlite3.connect(SRC / "astrbot" / "data" / "data_v4.db")
    row = con.execute(
        "SELECT system_prompt FROM personas WHERE persona_id='whale-girl'"
    ).fetchone()
    con.close()
    if row and row[0]:
        (DST / "examples" / "persona-whale-girl.txt").write_text(
            sanitize(row[0]), encoding="utf-8"
        )
        print("人格模板已导出")
except Exception as exc:
    print("人格导出失败:", exc)

# ---------- .gitignore ----------
(DST / ".gitignore").write_text('''# 运行数据（含个人信息，绝对不要提交）
data/
logs/
*.log
chat_archive/
reports/
previews/
downloads/
sticker_inbox/
stickers/

# 密钥
bili_account.json
token.txt
*.session
.env

# 运行状态
*state.json
*heartbeat.json
watchdog_restart.json
watchdog.pause
watchdog_pause

# 依赖 / 第三方
venv/
__pycache__/
*.pyc
snowluma/
node_modules/
''', encoding="utf-8")

print(f"\n完成：{DST}")
files = [p for p in DST.rglob("*") if p.is_file()]
print(f"共 {len(files)} 个文件 / {sum(f.stat().st_size for f in files)/1024:.0f} KB")
