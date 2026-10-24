"""把日报里的历史记录导入 bili_memory.json，作为机器人对"刷到过什么"的长期记忆。"""

import json
import pathlib
import re

REPORT_DIR = pathlib.Path(r"C:\QQBot\reports")
MEMORY = pathlib.Path(r"C:\QQBot\bili_memory.json")

entries = []
for path in sorted(REPORT_DIR.glob("bili-*.md")):
    text = path.read_text(encoding="utf-8", errors="replace")
    blocks = text.split("\n## ")
    for block in blocks[1:]:
        head = block.splitlines()[0]           # "13:33 ✅ 已发"
        m_bv = re.search(r"\*\*(BV[\w]+)\*\* 《(.+?)》", block)
        if not m_bv:
            continue
        m_up = re.search(r"UP：(.+?)｜时长 (\d+) 分钟", block)
        m_why = re.search(r"为什么值得：(.+)", block)
        m_cmt = re.search(r"评论：(.+)", block)
        entries.append(
            {
                "time": head.strip(),
                "bvid": m_bv.group(1),
                "title": m_bv.group(2),
                "up": (m_up.group(1).strip() if m_up else ""),
                "duration": int(m_up.group(2)) if m_up else 0,
                "why": (m_why.group(1).strip() if m_why else ""),
                "comment": (m_cmt.group(1).strip() if m_cmt else ""),
                "source": path.name,
            }
        )

# 去重（同 BV 只留最新一条）
seen = {}
for e in entries:
    seen[e["bvid"]] = e
items = sorted(seen.values(), key=lambda x: x["bvid"])
MEMORY.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

print(f"导入 {len(items)} 条记忆 -> {MEMORY}")
for e in items:
    print(f"  {e['bvid']} 《{e['title'][:26]}》｜{e['why']}")
