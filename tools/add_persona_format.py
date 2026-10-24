import datetime
import sqlite3

con = sqlite3.connect(r"C:\QQBot\astrbot\data\data_v4.db")
cur = con.cursor()
prompt = cur.execute(
    "SELECT system_prompt FROM personas WHERE persona_id='whale-girl'"
).fetchone()[0]

EXTRA = """
【硬性格式（违反即错）】
- 必须换行分条：1~3 条，每条不超过 30 字。超过 30 字就换行拆成下一条。
- 禁止输出一整段长文；禁止用"再说了""另外""总之"这类连接词凑长度。
- 不许用句号结尾，感叹号最多一个。
- 反例（错）："哦，你玩得久就代表你懂了？这逻辑本鲸用尾巴划拉两下都比你强。再说了，穿开裆裤又不丢人，倒是你这年纪都活到嘴上了。"
- 正例（对）：
  哦，玩得久就等于懂
  这逻辑尾巴划拉两下都比你强
"""

if "【硬性格式（违反即错）】" not in prompt:
    prompt = prompt.rstrip() + "\n" + EXTRA
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
    cur.execute(
        "UPDATE personas SET system_prompt=?, updated_at=? WHERE persona_id='whale-girl'",
        (prompt, now),
    )
    con.commit()
    print("已追加格式约束，人格长度:", len(prompt))
else:
    print("已存在格式约束")
con.close()
