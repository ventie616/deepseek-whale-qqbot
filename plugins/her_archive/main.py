"""她的一天：把房间 / 读书 / 翻唱 / 上网 / 说说 汇总成按天的档案。

定时（默认每 30 分钟）重新生成当天档案：
  D:\QQrobot\her_archive\YYYY-MM-DD.md

和日记的区别：
  日记 = 她写的第一人称反思（一天一次）
  档案 = 客观流水（谁、什么时候、存了什么、读了什么、唱了什么），可随时读
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter

ROOT = Path(r"D:\QQrobot")
ARCHIVE = ROOT / "her_archive"
CFG = ROOT / "her_archive.json"
ROOM = ROOT / "her_room"
KNOW = ROOM / "knowledge"
COVERS = ROOT / "covers"
DIARY = ROOT / "diary"

DEFAULTS = {
    "enabled": True,
    "interval_minutes": 30,
    "inject_summary": True,
    "notify_qq": "3328480063",
}


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _day(y: int, m: int, d: int) -> date:
    return date(y, m, d)


def _of_day(ts: float, day: date) -> bool:
    return datetime.fromtimestamp(ts).date() == day


def _jsonl(path: Path) -> list[dict]:
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except Exception:
        return path.name


class HerArchive(star.Star):
    def __init__(self, context: star.Context) -> None:
        super().__init__(context)
        ARCHIVE.mkdir(parents=True, exist_ok=True)
        self._task = None

    def _cfg(self) -> dict:
        cfg = dict(DEFAULTS)
        cfg.update(_load(CFG, {}) or {})
        return cfg

    # ---------------- 生成一天的档案 ----------------
    def build(self, day: date | None = None) -> str:
        day = day or date.today()
        stamp = day.isoformat()
        lines: list[str] = [f"# {stamp} · 她的一天", ""]
        counts = {"texts": 0, "links": 0, "images": 0, "reads": 0, "covers": 0, "wander": 0, "qzone": 0}

        # 1) 房间：她自己写的
        texts = []
        if (ROOM / "text").exists():
            for f in sorted((ROOM / "text").glob("*.md"), key=lambda p: p.stat().st_mtime):
                if not _of_day(f.stat().st_mtime, day):
                    continue
                body = f.read_text(encoding="utf-8", errors="replace").strip()
                head = body.splitlines()[0].lstrip("# ").strip() if body else f.stem
                texts.append((f.stat().st_mtime, head, _rel(f)))
        if texts:
            lines.append("## 自己写的")
            for ts, head, path in texts:
                lines.append(f"- {datetime.fromtimestamp(ts):%H:%M} {head}（`{path}`）")
            lines.append("")
            counts["texts"] = len(texts)

        # 2) 房间：存下的链接
        links = [d for d in _jsonl(ROOM / "links.jsonl")
                 if str(d.get("t", ""))[:10] == stamp]
        if links:
            lines.append("## 存下来的")
            for d in links:
                note = (d.get("note") or "").strip()
                score = d.get("score")
                extra = f" ·{score}分" if score else ""
                lines.append(f"- {str(d.get('t'))[11:16]} {d.get('title') or d.get('url')}{extra}"
                             + (f"｜{note[:60]}" if note else ""))
            lines.append("")
            counts["links"] = len(links)

        # 3) 图
        imgs = [f for f in (ROOM / "images").glob("*") if f.is_file() and _of_day(f.stat().st_mtime, day)] \
            if (ROOM / "images").exists() else []
        if imgs:
            lines.append("## 存的图")
            for f in sorted(imgs, key=lambda x: x.stat().st_mtime):
                lines.append(f"- {datetime.fromtimestamp(f.stat().st_mtime):%H:%M} {_rel(f)}")
            lines.append("")
            counts["images"] = len(imgs)

        # 4) 读过的书
        reads = []
        for d in _jsonl(KNOW / "index.jsonl"):
            if str(d.get("t", ""))[:10] == stamp:
                reads.append(d)
        if reads:
            lines.append("## 读过的")
            for d in reads:
                lines.append(f"- {str(d.get('t'))[11:16]} 《{d.get('title', '')}》")
                if d.get("summary"):
                    lines.append(f"  - {str(d['summary'])[:100]}")
            lines.append("")
            counts["reads"] = len(reads)

        # 5) 上网看到的东西
        wander = [d for d in _jsonl(ROOT / "web_wander.jsonl")
                  if str(d.get("t", ""))[:10] == stamp]
        if wander:
            lines.append("## 上网看到的")
            for d in wander[-8:]:
                sc = d.get("score")
                lines.append(f"- {str(d.get('t'))[11:16]} [{d.get('source', '')}] "
                             f"{str(d.get('title', ''))[:40]}" + (f" ·{sc}分" if sc else ""))
            lines.append("")
            counts["wander"] = len(wander)

        # 6) 翻唱
        covers = [f for f in COVERS.glob("*.mp3") if _of_day(f.stat().st_mtime, day)] \
            if COVERS.exists() else []
        if covers:
            lines.append("## 唱过的")
            for f in sorted(covers, key=lambda x: x.stat().st_mtime):
                lines.append(f"- {datetime.fromtimestamp(f.stat().st_mtime):%H:%M} `covers/{f.name}`")
            lines.append("")
            counts["covers"] = len(covers)

        # 7) 说说
        posts = [d for d in _jsonl(ROOT / "qzone_posts.jsonl")
                 if str(d.get("t", ""))[:10] == stamp]
        if posts:
            lines.append("## 发过的说说")
            for d in posts:
                lines.append(f"- {str(d.get('t'))[11:16]} {str(d.get('content', ''))[:80]}")
            lines.append("")
            counts["qzone"] = len(posts)

        # 8) 日记（只给链接，不复制）
        diary = DIARY / f"{stamp}.md"
        if diary.exists():
            lines.append("## 日记")
            lines.append(f"- 她写了一篇：`diary/{stamp}.md`")
            lines.append("")

        # 9) 统计
        lines.insert(1, (f"> 今天：写了 {counts['texts']} 段 · 存了 {counts['links']} 条"
                         f" · 读了 {counts['reads']} 篇 · 唱了 {counts['covers']} 首"
                         f" · 上网 {counts['wander']} 次 · 说说 {counts['qzone']} 条"))
        lines.insert(2, "")

        body = "\n".join(lines).rstrip() + "\n"
        (ARCHIVE / f"{stamp}.md").write_text(body, encoding="utf-8")
        return body

    def read(self, stamp: str = "") -> str:
        stamp = stamp or date.today().isoformat()
        p = ARCHIVE / f"{stamp}.md"
        if not p.exists():
            self.build(datetime.strptime(stamp, "%Y-%m-%d").date())
        try:
            return p.read_text(encoding="utf-8")
        except OSError:
            return ""

    def days(self, limit: int = 30) -> list[str]:
        return [f.stem for f in sorted(ARCHIVE.glob("*.md"), reverse=True)][:limit]

    def summary(self, day: date | None = None) -> str:
        """一小段摘要，给上下文用。"""
        body = self.read((day or date.today()).isoformat())
        for line in body.splitlines():
            if line.startswith("> 今天："):
                return line.lstrip("> ").strip()
        return ""

    # ---------------- 循环 ----------------
    async def initialize(self) -> None:
        self._task = asyncio.create_task(self._loop())
        logger.info("[her_archive] 已启动：按天归档")

    async def terminate(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        await asyncio.sleep(60)
        while True:
            cfg = self._cfg()
            try:
                if cfg.get("enabled", True):
                    self.build(date.today())
                    # 跨天补前一天
                    y = date.today() - timedelta(days=1)
                    if not (ARCHIVE / f"{y.isoformat()}.md").exists():
                        self.build(y)
            except Exception as exc:  # noqa: BLE001
                logger.info(f"[her_archive] 归档失败：{exc}")
            await asyncio.sleep(max(5, int(cfg.get("interval_minutes", 30))) * 60)

    # ---------------- 给模型用 ----------------
    @filter.on_llm_request()
    async def inject(self, event: AstrMessageEvent, req):
        cfg = self._cfg()
        if not cfg.get("inject_summary", True):
            return
        try:
            y = date.today() - timedelta(days=1)
            line = self.summary(y)
            if line:
                req.system_prompt = (req.system_prompt or "") + f"\n\n【昨天】{line}"
        except Exception:
            pass

    @filter.llm_tool(name="read_archive")
    async def tool_read(self, event: AstrMessageEvent, day: str = ""):
        """回忆自己某一天做了什么（写/存/读/唱/上网/说说）。

        Args:
            day(string): 日期 YYYY-MM-DD，留空表示今天
        """
        text = self.read(day.strip())
        return text[:1500] if text else "那天没有记录"

    @filter.command("她的一天", alias={"档案"})
    async def cmd_archive(self, event: AstrMessageEvent, arg: str = ""):
        """看她某一天做了什么：/她的一天 [YYYY-MM-DD]"""
        a = (arg or "").strip()
        if not a:
            days = self.days(7)
            if not days:
                yield event.plain_result("还没有档案")
                return
            a = days[0]
        text = self.read(a)
        if not text:
            yield event.plain_result(f"{a} 没有记录")
            return
        yield event.plain_result(text[:1800])

    @filter.command("记录的日子", alias={"档案列表"})
    async def cmd_days(self, event: AstrMessageEvent):
        days = self.days(14)
        yield event.plain_result("\n".join(days) if days else "还没有档案")
