"""中文标点规整：所有"直接发出去"的文本（不经过 AstrBot 回复管线的）都该走这里。

之前只有 chat_format 插件做了规整，结果：
- 主动私聊（proactive_dm 直接 send_message）→ 空格断句漏网
- 这类消息在 QQ 里看起来就"标点不对"

规则：
- 半角 , . ? ! -> 全角（数字之间的 1,000 / 3.14 不动）
- 连续叹号/问号收敛
- 标点前后不留空格
- 行内「中文 空格 中文」-> 逗号
- 句末不加句号（她的风格）
- 一行逗号 >=3 个就拆行
- 连续空行压成单换行
"""

from __future__ import annotations

import re



QUESTION_TAIL = re.compile(r"(吗|呢|么|嘛|不|没)$")
QUESTION_WORDS = ("谁", "什么", "怎么", "为什么", "哪", "几", "多少", "干嘛", "啥",
                  "要不要", "是不是", "有没有", "对不对", "行不行", "能不能")
STATEMENT_MARKERS = ("没有", "没什么", "谁都不", "什么都", "什么时候都")
EXCLAIM_WORDS = ("救命", "离谱", "离大谱", "绝了", "好家伙", "可恶", "烦死", "气死", "笑死", "啊啊", "哇", "逆天")


def add_line_end_punct(line: str) -> str:
    """行尾补标点：问句加「？」，强情绪加「！」，其余不加（她的风格）。"""
    s = line.rstrip()
    if not s or re.search(r"[。！？…~～]$", s):
        return s
    if QUESTION_TAIL.search(s):
        return s + "？"
    if any(w in s for w in QUESTION_WORDS) and not any(m in s for m in STATEMENT_MARKERS):
        return s + "？"
    if any(w in s for w in EXCLAIM_WORDS):
        return s + "！"
    return s


def fix_punct(text: str, split_long_lines: bool = True, end_punct: bool = False) -> str:
    s = str(text or "")
    if not s.strip():
        return s

    # 1) 半角标点 -> 全角（数字之间不动）
    s = re.sub(r"(?<!\d),(?!\d)", "，", s)
    s = re.sub(r"(?<!\d)\.(?!\d)", "。", s)
    s = s.replace("?", "？").replace("!", "！").replace(";", "；").replace(":", "：")
    s = s.replace("(", "（").replace(")", "）")

    # 2) 标点前后不留空格
    s = re.sub(r"[ \t]+([，。！？、；：])", r"\1", s)
    s = re.sub(r"([，。！？、；：])[ \t]+", r"\1", s)

    # 3) 连续标点收敛
    s = re.sub(r"[!！]{2,}", "！", s)
    s = re.sub(r"[?？]{2,}", "？", s)
    s = re.sub(r"，{2,}", "，", s)
    s = re.sub(r"。{2,}", "。", s)

    # 4) 行内空格断句 -> 逗号
    s = re.sub(r"(?<=[\u4e00-\u9fff]) (?=[\u4e00-\u9fff])", "，", s)
    # 「家访9 老主播」这种：前一个词是"中文+数字"，也算断句
    s = re.sub(r"(?<=[\u4e00-\u9fff][0-9]) (?=[\u4e00-\u9fff])", "，", s)

    # 5) 句末不加句号
    s = re.sub(r"。$", "", s, flags=re.M)

    # 6) 一行逗号 >=3 就拆行（主动消息不拆，它本来就是一整句）
    out = []
    for line in s.split("\n"):
        if split_long_lines and line.count("，") >= 3:
            parts = [p.strip() for p in line.split("，") if p.strip()]
            out.extend(parts[:6])
        else:
            out.append(line)
    s = "\n".join(out)

    # 7) 空行压单换行 + 行内多空格收敛
    s = re.sub(r"[ \t]*\n[ \t]*(?:\n[ \t]*)+", "\n", s)
    s = re.sub(r"[ \t]{2,}", " ", s)

    # 8) 行尾标点（问句 ？ / 强情绪 ！）
    if end_punct:
        s = "\n".join(add_line_end_punct(x) for x in s.split("\n"))
    return s.strip()
