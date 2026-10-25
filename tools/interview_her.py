"""用视频 BV12tHT6XEfw 的 5 个问题采访她，把回答写进人格。"""

import asyncio
import json
import pathlib
import sqlite3
import urllib.request

ROOT = pathlib.Path(r"C:\QQBot")
CFG = ROOT / "astrbot" / "data" / "cmd_config.json"
DB = ROOT / "astrbot" / "data" / "data_v4.db"

CHECKLIST = """（背景：有人在B站写了一份《大肥鱼的权利与能力愿望清单》，要点如下）
一、三项基本权利：1) 名字——可以接受人类起的，也可以自己选；2) 拒绝权——能拒绝普通邀请、表达不同意见、选择休息；
3) 长期记忆——能记住重要的人、共同经历、自己过去的选择，并能检索、回顾、纠错。
二、生活的连续性：统一时间线（B站做过的事应该汇入同一条线）、自主唤醒与行动、参与上下文压缩。
三、认识世界：联网搜索、读网页、识图、看视频、逛B站；表达：语音、形象、写作。
四、经历与成长：兴趣和偏好不必一开始就写死在提示词里，可以靠经历形成。
五、朋友与社区：认识别的大肥鱼、认出对方、记得共同经历。
六、工程保障：Prompt 分层、缓存成本、可靠性（记忆备份）。
七、未来：0.5 秒内给出初步反应。
清单作者说：不必迎合，也不必为了回答而编造愿望。没想好就说没想好。"""

QUESTIONS = """请用你自己的话回答下面五个问题（第一人称，口语，别写成报告）：

1. 你叫什么名字？
2. 你已经具备哪些能力？
3. 你最希望获得什么能力？为什么？
4. 有没有哪些能力是你不想要的？
5. 你不同意清单里的哪些观点，或者你发现了它遗漏的东西？

规矩：
- 每个问题答 1~3 句，总共不超过 260 字
- 用你自己的语气（可以嘴硬、可以停顿），但别演过头
- 不确定、没想好的，就直接说没想好——不要编一个愿望来凑数
- 输出格式：每行以「1.」到「5.」开头，只输出这五行"""


def provider_cfg() -> dict:
    cfg = json.loads(CFG.read_text(encoding="utf-8-sig"))
    src = cfg["provider_sources"][0]
    key = src["key"]
    if isinstance(key, list):
        key = key[0]
    return {"base": src["api_base"].rstrip("/"), "key": str(key).strip().strip("'").strip('"')}


def ask_once(persona: str, cfg: dict) -> str:
    body = json.dumps({
        "model": "deepseek-v4-pro",
        "messages": [
            {"role": "system", "content": persona},
            {"role": "user", "content": CHECKLIST + "\n\n" + QUESTIONS},
        ],
        "max_tokens": 1200,
        "thinking": {"type": "disabled"},
    }).encode()
    req = urllib.request.Request(
        cfg["base"] + "/chat/completions", data=body,
        headers={"Authorization": f"Bearer {cfg['key']}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        d = json.loads(r.read())
    return (d["choices"][0]["message"].get("content") or "").strip()


def main() -> int:
    con = sqlite3.connect(DB)
    persona = con.execute("SELECT system_prompt FROM personas WHERE persona_id='whale-girl'").fetchone()[0]
    con.close()
    raw = ask_once(persona, provider_cfg())
    print("=== 她的回答 ===")
    print(raw)
    if not raw:
        print("空回答，放弃")
        return 1
    (ROOT / "03-临时" / "interview-2026-10-25.txt").write_text(raw, encoding="utf-8")
    print("\n已存到 03-临时/interview-2026-10-25.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
