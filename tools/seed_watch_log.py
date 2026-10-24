"""从历史日报回填 bili_watched.jsonl，让浏览记录一开始就有内容。"""

import json
import pathlib
import re
from datetime import datetime

REPORTS = pathlib.Path(r"C:\QQBot\reports")
OUT = pathlib.Path(r"C:\QQBot\bili_watched.jsonl")

recs = []
for path in sorted(REPORTS.glob("bili-*.md")):
    m = re.search(r"bili-(\d{4}-\d{2}-\d{2})\.md", path.name)
    if not m:
        continue
    day = m.group(1)
    text = path.read_text(encoding="utf-8", errors="replace")
    blocks = re.split(r"\n## ", text)
    for block in blocks[1:]:
        head = block.splitlines()[0].strip()
        tm = re.search(r"(\d{1,2}:\d{2})", head)
        time_str = tm.group(1) if tm else "00:00"
        ts = f"{day}T{time_str.zfill(5)}:00"

        if "已发" in head:
            mbv = re.search(r"\*\*(BV[\w]+)\*\* 《(.+?)》", block)
            if not mbv:
                continue
            up = re.search(r"UP：(.+?)｜时长 (\d+) 分钟", block)
            why = re.search(r"为什么值得：(.+)", block)
            cmt = re.search(r"评论：(.+)", block)
            recs.append(
                {
                    "t": ts,
                    "bvid": mbv.group(1),
                    "title": mbv.group(2),
                    "up": up.group(1).strip() if up else "",
                    "duration": (int(up.group(2)) * 60) if up else 0,
                    "verdict": "已评论",
                    "reason": why.group(1).strip() if why else "",
                    "comment": cmt.group(1).strip() if cmt else "",
                    "pic": "",
                }
            )
        # 无论哪种块，里面的省线条目都记为跳过
        for line in block.splitlines():
            ms = re.match(r"^\s*-\s+(BV[\w]+) 《(.+?)》\s*——\s*(.+)", line)
            if not ms:
                continue
            recs.append(
                {
                    "t": ts,
                    "bvid": ms.group(1),
                    "title": ms.group(2),
                    "up": "",
                    "duration": 0,
                    "verdict": "跳过",
                    "reason": ms.group(3).strip(),
                    "comment": "",
                    "pic": "",
                }
            )

# 同 bvid 只保留最后一条（已评论优先）
final = {}
for r in recs:
    key = r["bvid"]
    if key not in final or r["verdict"] == "已评论":
        final[key] = r

with OUT.open("w", encoding="utf-8") as f:
    for r in sorted(final.values(), key=lambda x: x["t"]):
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

print(f"回填 {len(final)} 条 -> {OUT}")
from collections import Counter
print("判定分布:", dict(Counter(r["verdict"] for r in final.values())))
