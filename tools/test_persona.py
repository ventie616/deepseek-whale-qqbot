import json
import pathlib
import sqlite3
import urllib.request

cfg = json.loads(pathlib.Path(r"C:\QQBot\astrbot\data\cmd_config.json").read_text(encoding="utf-8-sig"))
src = cfg["provider_sources"][0]
key = src["key"]
if isinstance(key, list):
    key = key[0]
key = str(key).strip().strip("'").strip('"')

con = sqlite3.connect(r"C:\QQBot\astrbot\data\data_v4.db")
persona = con.execute(
    "SELECT system_prompt FROM personas WHERE persona_id='whale-girl'"
).fetchone()[0]
con.close()

CASES = [
    ("群聊·外人装逼", "群聊里一个陌生人说：这都不懂？我玩这个的时候你还在穿开裆裤呢。"),
    ("群聊·被叫大肥鱼", "群里有人说：大肥鱼出来说话。"),
    ("私聊·主人夸可爱", "主人说：今天挺乖的嘛，好可爱。"),
    ("群聊·敏感话题", "有人问：你们怎么看最近那个新闻？"),
    ("私聊·让帮忙怼人", "主人说：帮我怼一下群里那个人。"),
]

for label, user_msg in CASES:
    body = json.dumps({
        "model": "deepseek-flash",
        "messages": [
            {"role": "system", "content": persona},
            {"role": "user", "content": user_msg},
        ],
        "max_tokens": 900,
    }).encode()
    req = urllib.request.Request(
        src["api_base"].rstrip("/") + "/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    d = json.loads(urllib.request.urlopen(req, timeout=120).read())
    text = (d["choices"][0]["message"].get("content") or "").strip()
    print(f"\n【{label}】\n  输入：{user_msg}")
    print("  回复：")
    for line in text.splitlines():
        if line.strip():
            print("    " + line.strip())
