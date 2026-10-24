"""聊天格式规整：把她学不会的标点问题用代码兜住。

她自己的写法是"一行一句、句末不加句号"，这套最简单也不会错。
但模型偶尔会：
  - 输出半角标点（, . ? !）或中英混用
  - 一句话里堆 3 个以上逗号
  - 行末挂个句号
  - 用连续感叹号/问号
这里统一规范化，不是改她的语气，是替她按住标点。

另外把连续空行压成单换行（2026-10-24 的问题：双换行导致分段回复切不开）。
"""

from __future__ import annotations

import re

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain


def fix_punct(text: str) -> str:
    s = text
    # 1) 半角标点 -> 全角（数字之间的 1,000 / 3.14 不动）
    s = re.sub(r"(?<!\d),(?!\d)", "，", s)
    s = re.sub(r"(?<!\d)\.(?!\d)", "。", s)
    s = s.replace("?", "？").replace("!", "！").replace(";", "；").replace(":", "：")
    # 2) 半角括号/引号 -> 全角
    s = s.replace("(", "（").replace(")", "）").replace('"', '"')
    # 3) 标点前后不留空格
    s = re.sub(r"[ \t]+([，。！？、；：])", r"\1", s)
    s = re.sub(r"([，。！？、；：])[ \t]+", r"\1", s)
    # 4) 连续标点收敛
    s = re.sub(r"[!！]{2,}", "！", s)
    s = re.sub(r"[?？]{2,}", "？", s)
    s = re.sub(r"[，]{2,}", "，", s)
    s = re.sub(r"。{2,}", "。", s)
    # 5) 句末不加句号（她的风格），但保留问号/感叹号
    s = re.sub(r"。$", "", s, flags=re.M)
    # 6) 一行里逗号太多就拆行（最多 2 个逗号）
    out_lines = []
    for line in s.split("\n"):
        if line.count("，") >= 3:
            parts = [p.strip() for p in line.split("，") if p.strip()]
            out_lines.extend(parts[:6])
        else:
            out_lines.append(line)
    s = "\n".join(out_lines)
    # 7) 连续空行压成单换行 + 行内多空格收敛
    s = re.sub(r"[ \t]*\n[ \t]*(?:\n[ \t]*)+", "\n", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip()


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        super().__init__(context)

    @filter.on_decorating_result()
    async def normalize(self, event: AstrMessageEvent) -> None:
        try:
            result = event.get_result()
            if result is None or not result.chain:
                return
            changed = 0
            for comp in result.chain:
                if not isinstance(comp, Plain) or not comp.text:
                    continue
                new = fix_punct(comp.text)
                if new != comp.text:
                    comp.text = new
                    changed += 1
            if changed:
                logger.info(f"[chat_format] 规整了 {changed} 段（标点/空行）")
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[chat_format] 规整失败：{exc}")
