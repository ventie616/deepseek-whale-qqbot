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
    # 带标签的整行（URL / 视频标题行 / 我在下面说）不动
    if s.startswith(("https://", "http://", "为什么值得看：", "我在下面说：", "《", "UP：", "这轮没发出去")):
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

    # 0) 先把已经被压坏的 URL 修回来（https：//www。xxx。com）
    s = re.sub(r"https?：//", lambda m: m.group(0).replace("：", ":"), s)
    s = re.sub(r"(?<=[A-Za-z0-9])\。([A-Za-z]{2,})", r".\1", s)

    # 0.1) 然后把 URL 挖出来保护起来，避免冒号/点被转成全角
    urls = re.findall(r"https?://[^\s\u4e00-\u9fff]+", s)
    holders = []
    for i, u in enumerate(urls):
        token = f"\x00URL{i}\x00"
        holders.append((token, u))
        s = s.replace(u, token)

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

    # 4) 行内空格断句 -> 逗号（书名号标题行、URL 行不动）
    fixed_lines = []
    for line in s.split("\n"):
        if line.lstrip().startswith(("《", "http://", "https://")):
            fixed_lines.append(line)
            continue
        line = re.sub(r"(?<=[\u4e00-\u9fff]) (?=[\u4e00-\u9fff])", "，", line)
        line = re.sub(r"(?<=[\u4e00-\u9fff][0-9]) (?=[\u4e00-\u9fff])", "，", line)
        fixed_lines.append(line)
    s = "\n".join(fixed_lines)

    # 5) 句末不加句号
    s = re.sub(r"。$", "", s, flags=re.M)

    # 6) 一行逗号 >=3 就拆行（主动消息不拆，它本来就是一整句）
    out = []
    for line in s.split("\n"):
        if line.lstrip().startswith(("《", "http://", "https://")):
            out.append(line)
            continue
        if split_long_lines and line.count("，") >= 3:
            parts = [p.strip() for p in line.split("，") if p.strip()]
            out.extend(parts[:6])
        else:
            out.append(line)
    s = "\n".join(out)

    # 7) 空行压单换行 + 行内多空格收敛
    s = re.sub(r"[ \t]*\n[ \t]*(?:\n[ \t]*)+", "\n", s)
    s = re.sub(r"[ \t]{2,}", " ", s)

    # 7.5) 还原 URL
    for token, u in holders:
        s = s.replace(token, u)

    # 8) 行尾标点（问句 ？ / 强情绪 ！）
    if end_punct:
        s = "\n".join(add_line_end_punct(x) for x in s.split("\n"))
    return s.strip()
