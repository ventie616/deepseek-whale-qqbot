"""浏览记录查看器：/刷了什么 看文字版，/刷记录 生成可点的网页。

数据源：D:\\QQrobot\\bili_watched.jsonl（bili_pusher 每次刷都会追加，含被拒绝的）
"""

from __future__ import annotations

import html
import json
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter

WATCH = Path(r"D:\QQrobot\bili_watched.jsonl")
PIC_CACHE = Path(r"D:\QQrobot\bili_pic_cache.json")
OUT_HTML = Path(r"D:\QQrobot\reports\browse.html")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        super().__init__(context)

    # ---------------- 数据 ----------------
    def _load(self, days: int) -> list[dict]:
        if not WATCH.exists():
            return []
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00")
        out = []
        try:
            for line in WATCH.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if (r.get("t") or "") >= cutoff:
                    out.append(r)
        except OSError:
            pass
        return out

    def _pics(self) -> dict:
        try:
            return json.loads(PIC_CACHE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_pics(self, d: dict) -> None:
        try:
            PIC_CACHE.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def _fetch_pic(self, bvid: str) -> str:
        try:
            url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"})
            d = json.loads(urllib.request.urlopen(req, timeout=12).read())
            return ((d.get("data") or {}).get("pic")) or ""
        except Exception:
            return ""

    # ---------------- 网页 ----------------
    def _render_html(self, recs: list[dict]) -> Path:
        pics = self._pics()
        need = [r["bvid"] for r in recs if not r.get("pic") and r.get("bvid") and r["bvid"] not in pics][:60]
        for bvid in need:
            pics[bvid] = self._fetch_pic(bvid)
        self._save_pics(pics)

        by_day: dict[str, list[dict]] = {}
        for r in recs:
            day = (r.get("t") or "")[:10]
            by_day.setdefault(day, []).append(r)

        def card(r: dict) -> str:
            bvid = html.escape(r.get("bvid") or "")
            pic = r.get("pic") or pics.get(r.get("bvid"), "")
            pic = html.escape(pic.replace("http://", "https://"))
            verdict = r.get("verdict") or ""
            cls = {"已评论": "ok", "值得": "ok", "跳过": "skip"}.get(verdict, "skip")
            cmt = r.get("comment") or ""
            cmt_html = f'<div class="cmt">💬 {html.escape(cmt)}</div>' if cmt else ""
            tm = (r.get("t") or "")[11:16]
            dur = int(r.get("duration") or 0)
            return f"""
            <a class="card" href="https://www.bilibili.com/video/{bvid}" target="_blank" rel="noreferrer">
              <div class="cover"><img loading="lazy" src="{pic}" alt=""></div>
              <div class="body">
                <div class="meta"><span class="badge {cls}">{html.escape(verdict)}</span>
                  <span class="time">{tm}</span>
                  <span class="dur">{dur // 60}:{dur % 60:02d}</span></div>
                <div class="title">{html.escape((r.get('title') or '')[:52])}</div>
                <div class="up">{html.escape(r.get('up') or '')}</div>
                <div class="why">{html.escape((r.get('reason') or '')[:60])}</div>
                {cmt_html}
              </div>
            </a>"""

        sections = []
        for day in sorted(by_day, reverse=True):
            items = sorted(by_day[day], key=lambda x: x.get("t") or "", reverse=True)
            ok = sum(1 for i in items if i.get("verdict") in ("已评论", "值得"))
            sections.append(
                f'<h2>{day} <small>共 {len(items)} 条 · 值得 {ok} · 跳过 {len(items) - ok}</small></h2>'
                f'<div class="grid">{"".join(card(r) for r in items)}</div>'
            )

        total = len(recs)
        ok_total = sum(1 for r in recs if r.get("verdict") in ("已评论", "值得"))
        page = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>她刷过的 B站</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{{color-scheme:dark}}
body{{margin:0;padding:28px;background:#0e1014;color:#e7e9ee;font:14px/1.55 "Segoe UI",system-ui,sans-serif}}
h1{{font-size:20px;margin:0 0 6px}}
.sub{{color:#8b93a1;font-size:13px;margin-bottom:22px}}
h2{{font-size:15px;margin:26px 0 12px;color:#cfd4de;border-left:3px solid #4c8dff;padding-left:9px}}
h2 small{{color:#7d8593;font-weight:400;margin-left:8px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px}}
.card{{display:flex;flex-direction:column;background:#171a21;border:1px solid #232833;border-radius:11px;overflow:hidden;text-decoration:none;color:inherit;transition:.15s}}
.card:hover{{border-color:#3d6fd1;transform:translateY(-2px)}}
.cover{{aspect-ratio:16/9;background:#0b0d11}}
.cover img{{width:100%;height:100%;object-fit:cover;display:block}}
.body{{padding:10px 11px 12px}}
.meta{{display:flex;gap:8px;align-items:center;font-size:11px;color:#79818f;margin-bottom:6px}}
.badge{{padding:1px 7px;border-radius:20px;font-size:11px}}
.badge.ok{{background:#123a22;color:#5ee08a}}
.badge.skip{{background:#2a1d1d;color:#c98b8b}}
.title{{font-size:13.5px;line-height:1.4;margin-bottom:4px}}
.up{{font-size:12px;color:#7f8794}}
.why{{font-size:12px;color:#98a0ae;margin-top:6px}}
.cmt{{font-size:12px;color:#8fc2ff;margin-top:8px;border-top:1px solid #232833;padding-top:7px}}
</style></head><body>
<h1>她刷过的 B站</h1>
<div class="sub">共 {total} 条 · 值得 {ok_total} · 跳过 {total - ok_total} · 生成于 {datetime.now():%Y-%m-%d %H:%M}</div>
{''.join(sections) or '<p class="sub">还没有记录</p>'}
</body></html>"""
        OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
        OUT_HTML.write_text(page, encoding="utf-8")
        return OUT_HTML

    # ---------------- 指令 ----------------
    @filter.command("刷了什么")
    async def what_watched(self, event: AstrMessageEvent, days: str = "1"):
        try:
            n = max(1, min(30, int(days)))
        except Exception:
            n = 1
        recs = self._load(n)
        if not recs:
            yield event.plain_result(f"最近 {n} 天她没有刷到记录")
            return
        by_day: dict[str, list[dict]] = {}
        for r in recs:
            by_day.setdefault((r.get("t") or "")[:10], []).append(r)
        lines = []
        for day in sorted(by_day, reverse=True):
            items = sorted(by_day[day], key=lambda x: x.get("t") or "", reverse=True)
            lines.append(f"【{day}】{len(items)} 条")
            for r in items[:12]:
                mark = "✅" if r.get("verdict") in ("已评论", "值得") else "·"
                lines.append(f"{mark} {r.get('bvid')} {(r.get('title') or '')[:22]}")
                lines.append(f"    {r.get('reason') or ''}")
        lines.append(f"共 {len(recs)} 条，完整带封面的页面：/刷记录")
        yield event.plain_result("\n".join(lines))

    @filter.command("刷记录")
    async def watched_page(self, event: AstrMessageEvent, days: str = "7"):
        try:
            n = max(1, min(90, int(days)))
        except Exception:
            n = 7
        recs = self._load(n)
        if not recs:
            yield event.plain_result(f"最近 {n} 天没有记录")
            return
        path = self._render_html(recs)
        yield event.plain_result(f"生成了 {len(recs)} 条记录的页面：\n{path}\n用浏览器打开就能看（带封面，可点进视频）")

    @filter.llm_tool(name="browse_history")
    async def browse_history(self, event: AstrMessageEvent, days: int = 1):
        """查她最近刷过哪些 B站 视频（包括看过但没评论的）。

        Args:
            days(number): 往前查几天，默认 1
        """
        n = max(1, min(30, int(days or 1)))
        recs = self._load(n)
        if not recs:
            return f"最近 {n} 天没刷到记录"
        lines = []
        for r in sorted(recs, key=lambda x: x.get("t") or "", reverse=True)[:20]:
            lines.append(
                f"{(r.get('t') or '')[5:16].replace('T', ' ')} {r.get('bvid')} "
                f"{(r.get('title') or '')[:24]}｜{r.get('verdict')}：{r.get('reason') or ''}"
            )
        return "\n".join(lines)
