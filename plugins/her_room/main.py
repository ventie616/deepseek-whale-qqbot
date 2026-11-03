"""她的私人房间：想存就存。

- her_room/text/   她自己写的文字、想留的段落
- her_room/images/ 看中的图
- her_room/links.jsonl  存下来的链接（带她自己写的一句理由）
- her_room/room.json    索引（数量、最后更新时间）
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from datetime import datetime
from pathlib import Path

import httpx
from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter

ROOT = Path(r"D:\QQrobot")
ROOM = ROOT / "her_room"
TEXT_DIR = ROOM / "text"
IMG_DIR = ROOM / "images"
LINKS = ROOM / "links.jsonl"
INDEX = ROOM / "room.json"
CFG = ROOT / "her_room.json"
PERSONA = ROOT / "persona" / "whale-girl.md"
PROPOSALS = ROOM / "persona_proposals"
OWNER = "3328480063"
SAFE_MARK = "## 十三、我自己改的"

DEFAULTS = {
    "enabled": True,
    "max_images_mb": 80,
    "notify_qq": "3328480063",
    "reflect_min_hours": 20,
    "reflect_max_hours": 30,
    "reflect_max_per_week": 3,
    "reflect_active_hours": [10, 23],
}


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save(path: Path, data) -> None:
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _slug(text: str, limit: int = 24) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "_", text or "").strip("_")
    return (s or "note")[:limit]


class HerRoom(star.Star):
    def __init__(self, context: star.Context) -> None:
        super().__init__(context)
        TEXT_DIR.mkdir(parents=True, exist_ok=True)
        IMG_DIR.mkdir(parents=True, exist_ok=True)
        self._task = None

    async def initialize(self) -> None:
        import asyncio

        self._task = asyncio.create_task(self._reflect_loop())
        logger.info("[her_room] 自省循环已启动（20~30 小时一次）")

    async def terminate(self) -> None:
        if self._task:
            self._task.cancel()

    def _cfg(self) -> dict:
        cfg = dict(DEFAULTS)
        cfg.update(_load(CFG, {}) or {})
        return cfg

    # ---------------- 存 ----------------
    def save_note(self, text: str, title: str = "", tags: str = "") -> str:
        text = (text or "").strip()
        if not text:
            return "空的就不存了"
        now = datetime.now()
        name = f"{now:%Y%m%d_%H%M%S}_{_slug(title or text[:16])}.md"
        path = TEXT_DIR / name
        body = f"# {title or text[:20]}\n\n{text}\n"
        if tags:
            body += f"\n标签：{tags}\n"
        path.write_text(body, encoding="utf-8")
        self._bump("text")
        logger.info(f"[her_room] 存了一段文字：{name}")
        return f"存好了：text/{name}"

    def save_link(self, url: str, title: str = "", note: str = "") -> str:
        url = (url or "").strip()
        if not url.startswith("http"):
            return "这个链接不对"
        rec = {"t": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
               "url": url, "title": (title or "").strip(), "note": (note or "").strip()}
        try:
            with LINKS.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError as exc:
            return f"没存上：{exc}"
        self._bump("link")
        logger.info(f"[her_room] 存了链接：{url[:60]}")
        return "链接存好了" + (f"：{title[:30]}" if title else "")

    def save_image(self, url: str, note: str = "") -> str:
        url = (url or "").strip()
        if not url.startswith("http"):
            return "图片地址不对"
        cfg = self._cfg()
        used = sum(f.stat().st_size for f in IMG_DIR.glob("*") if f.is_file())
        if used > int(cfg.get("max_images_mb", 80)) * 1024 * 1024:
            return "房间放不下更多图了"
        ext = ".jpg"
        m = re.search(r"\.(png|jpe?g|webp|gif)(?:\?|$)", url.lower())
        if m:
            ext = "." + m.group(1).replace("jpeg", "jpg")
        name = f"{datetime.now():%Y%m%d_%H%M%S}_{random.randint(100, 999)}{ext}"
        try:
            with httpx.Client(timeout=60, follow_redirects=True,
                              headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"}) as c:
                r = c.get(url)
                r.raise_for_status()
                (IMG_DIR / name).write_bytes(r.content)
        except Exception as exc:  # noqa: BLE001
            return f"图没存下来：{exc}"
        if note:
            with LINKS.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"t": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                                     "url": url, "title": f"[图] {name}",
                                     "note": note}, ensure_ascii=False) + "\n")
        self._bump("image")
        logger.info(f"[her_room] 存了一张图：{name}")
        return f"图存好了：images/{name}"

    def _bump(self, kind: str) -> None:
        idx = _load(INDEX, {}) or {}
        idx[kind] = int(idx.get(kind, 0)) + 1
        idx["updated"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        _save(INDEX, idx)


    # ---------------- 人格自改 ----------------
    def propose_persona(self, text: str, reason: str = "") -> str:
        """她提一个改动，写进待审目录，等主人同意。"""
        text = (text or "").strip()
        if len(text) < 6:
            return "太短了，写清楚点"
        PROPOSALS.mkdir(parents=True, exist_ok=True)
        pid = datetime.now().strftime("%Y%m%d%H%M%S")
        rec = {"id": pid, "text": text[:600], "reason": (reason or "")[:200],
               "t": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "state": "pending"}
        (PROPOSALS / f"{pid}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"[her_room] 人格提议 {pid}：{text[:40]}")
        return f"记下了（{pid}），等主人点头才会写进我的人格"

    def _apply_persona(self, text: str) -> bool:
        """追加到「我自己改的」这一节，然后同步进 AstrBot。"""
        try:
            body = PERSONA.read_text(encoding="utf-8")
            if SAFE_MARK not in body:
                body = body.rstrip() + f"\n\n{SAFE_MARK}\n\n（这里只放我自己改的话，底子和底线不归我动。）\n"
            body = body.rstrip() + f"\n\n- {text.strip()}\n"
            # 留个备份
            bak = PERSONA.parent / "archive"
            bak.mkdir(exist_ok=True)
            (bak / f"whale-girl_{datetime.now():%Y%m%d_%H%M%S}.md").write_text(
                PERSONA.read_text(encoding="utf-8"), encoding="utf-8")
            PERSONA.write_text(body, encoding="utf-8")
            import subprocess
            subprocess.run([r"D:\QQrobot\venv\Scripts\python.exe",
                            r"D:\QQrobot\tools\set_persona.py"],
                           capture_output=True, timeout=120)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[her_room] 写入人格失败：{exc}")
            return False

    def pending_proposals(self) -> list[dict]:
        out = []
        if PROPOSALS.exists():
            for f in sorted(PROPOSALS.glob("*.json"), reverse=True):
                try:
                    d = json.loads(f.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if d.get("state") == "pending":
                    out.append(d)
        return out

    def decide(self, pid: str, ok: bool) -> str:
        f = PROPOSALS / f"{pid}.json"
        if not f.exists():
            return f"没有 {pid} 这条"
        d = json.loads(f.read_text(encoding="utf-8"))
        if d.get("state") != "pending":
            return "这条已经处理过了"
        if ok:
            if not self._apply_persona(d.get("text", "")):
                return "写进人格失败，看看日志"
            d["state"] = "approved"
        else:
            d["state"] = "rejected"
        d["decided"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        f.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        return "改好了，我已经不是原来的我了" if ok else "那就不改"

    def _is_owner(self, event: AstrMessageEvent) -> bool:
        try:
            return str(event.get_sender_id()) == OWNER
        except Exception:
            return False

    # ---------------- 看 ----------------
    def summary(self) -> str:
        texts = sorted(TEXT_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        imgs = sorted(IMG_DIR.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
        links = []
        if LINKS.exists():
            links = LINKS.read_text(encoding="utf-8").splitlines()[-50:]
        size = sum(f.stat().st_size for f in ROOM.rglob("*") if f.is_file()) / 1024 / 1024
        return (f"我的房间：文字 {len(texts)} 段 / 图 {len(imgs)} 张 / 链接 {len(links)} 条 "
                f"（占 {size:.1f} MB）")

    def recent(self, kind: str = "") -> list[str]:
        out: list[str] = []
        if kind in ("", "text"):
            for f in sorted(TEXT_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]:
                head = f.read_text(encoding="utf-8").splitlines()[0].lstrip("# ").strip()
                out.append(f"[文字] {head[:40]}（{f.name[:15]}）")
        if kind in ("", "image"):
            for f in sorted(IMG_DIR.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]:
                out.append(f"[图] {f.name}")
        if kind in ("", "link") and LINKS.exists():
            for line in LINKS.read_text(encoding="utf-8").splitlines()[-5:][::-1]:
                try:
                    d = json.loads(line)
                    out.append(f"[链接] {d.get('title', '')[:34] or d.get('url', '')[:40]}")
                except Exception:
                    continue
        return out


    # ---------------- 成长：房间里的东西回到她身上 ----------------
    def _growth_block(self) -> str:
        """把最近写/存的几样东西拼成一小段，注入她的上下文。"""
        parts: list[str] = []
        try:
            notes = sorted(TEXT_DIR.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True)[:3]
            if notes:
                parts.append("【我最近自己写下的】")
                for f in notes:
                    body = f.read_text(encoding="utf-8").strip().replace("\n", " ")
                    parts.append(f"· {body[:180]}")
        except Exception:
            pass
        try:
            if LINKS.exists():
                rows = LINKS.read_text(encoding="utf-8").splitlines()[-3:]
                if rows:
                    parts.append("【我最近存下来的】")
                    for line in rows:
                        d = json.loads(line)
                        parts.append(f"· {str(d.get('title', ''))[:40]}：{str(d.get('note', ''))[:90]}")
        except Exception:
            pass
        return "\n".join(parts)

    @filter.on_llm_request()
    async def inject_growth(self, event: AstrMessageEvent, req):
        block = self._growth_block()
        if block and getattr(req, "system_prompt", None) is not None:
            req.system_prompt = (req.system_prompt or "") + "\n\n" + block + "\n（这些是我自己记的，不是别人要求我说的）"


    # ---------------- 定期自省 ----------------
    def _reflect_material(self) -> str:
        """把最近的日记、房间、看过的、说过的话凑成回顾素材。"""
        parts: list[str] = []
        try:
            diary = sorted((ROOT / "diary").glob("*.md"))
            if diary:
                txt = diary[-1].read_text(encoding="utf-8").strip()
                parts.append(f"最近一篇日记：\n{txt[:420]}")
        except Exception:
            pass
        try:
            notes = sorted(TEXT_DIR.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True)[:2]
            if notes:
                parts.append("我自己写下的：")
                for f in notes:
                    parts.append("· " + f.read_text(encoding="utf-8").strip().replace("\n", " ")[:160])
        except Exception:
            pass
        try:
            lf = ROOM / "links.jsonl"
            if lf.exists():
                rows = lf.read_text(encoding="utf-8").splitlines()[-4:]
                if rows:
                    parts.append("我最近看的东西：")
                    for line in rows:
                        d = json.loads(line)
                        parts.append(f"· {str(d.get('title', ''))[:34]}：{str(d.get('note', ''))[:80]}")
        except Exception:
            pass
        try:
            ps = self.pending_proposals() + [
                json.loads(f.read_text(encoding="utf-8"))
                for f in sorted((ROOM / "persona_proposals").glob("*.json"), reverse=True)[:5]
                if f.exists()
            ]
            self_texts = {p.get("text", "") for p in ps}
            if self_texts:
                parts.append("我已经给自己加过的（不要重复）：")
                parts += [f"· {x[:80]}" for x in list(self_texts)[:5] if x]
        except Exception:
            pass
        return "\n\n".join(parts) or "（最近没什么素材）"

    def _reflect_state(self) -> dict:
        p = ROOM / "reflect_state.json"
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_reflect(self, d: dict) -> None:
        (ROOM / "reflect_state.json").write_text(
            json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

    def _in_reflect_window(self) -> bool:
        cfg = self._cfg()
        lo, hi = cfg.get("reflect_active_hours", [10, 23])
        now = datetime.now()
        return float(lo) <= now.hour + now.minute / 60 <= float(hi)

    async def _reflect_once(self) -> str:
        """跑一次自省；返回结果描述。"""
        cfg = self._cfg()
        if not cfg.get("enabled", True) or not self._in_reflect_window():
            return "不在自省时段"
        st = self._reflect_state()
        # 一周内别提太多
        week = datetime.now().strftime("%Y-%W")
        if st.get("week") == week and int(st.get("count", 0)) >= int(cfg.get("reflect_max_per_week", 3)):
            return "这周改够了"
        material = self._reflect_material()
        prompt = (
            "下面是你的日记、你写下的东西、你最近看的内容，还有你已经给自己加过的条款。\n\n"
            f"{material}\n\n"
            "你现在安静下来想一件事：**这些是不是还是你？**\n"
            "如果觉得哪里不像自己、或者有什么想给自己加一条（比如某个偏好变了、对某件事的态度变了），"
            "就写一句第一人称的话，像「我不太喜欢……」「我现在更想……」这样，不超过 60 字。\n"
            "如果觉得没必要改，就只回三个字：不改。\n"
            "只输出要加的那句话或「不改」，别的都不要。"
        )
        text = ""
        try:
            provider = await self.context.provider_manager.get_provider_by_id("deepseek/deepseek-flash")
            if provider is None:
                try:
                    provider = await self.context.provider_manager.get_provider_by_id("deepseek-flash")
                except Exception:
                    provider = None
            if provider is not None:
                r = await provider.text_chat(
                    prompt=prompt,
                    system_prompt="你是大肥鱼，一只在慢慢长大的鲸鱼娘。只输出一句话或「不改」。",
                )
                text = (r.completion_text or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[her_room] 自省 LLM 失败：{exc}")
        if not text or "不改" in text[:6]:
            st.update({"t": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "week": week})
            self._save_reflect(st)
            return "这次不想改"
        line = text.strip().strip("「」\"'").splitlines()[0][:120]
        msg = self.propose_persona(line, "自省时觉得该写下来")
        logger.info(f"[her_room] 自省提议：{line[:40]}")
        st["week"] = week
        st["count"] = int(st.get("count", 0)) + 1 if st.get("week") == week else 1
        st["t"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        self._save_reflect(st)
        # 通知主人
        try:
            import httpx

            tok = (ROOT / "secrets" / "onebot_token.txt")
            token = tok.read_text(encoding="utf-8").strip() if tok.exists() else ""
            if token:
                httpx.post("http://127.0.0.1:3000/send_private_msg",
                           headers={"Authorization": f"Bearer {token}"},
                           json={"user_id": int(cfg.get("notify_qq", 0)),
                                 "message": f"我自己想了想，想给自己加一条：\n「{line}」\n（同意就 /同意，不想就算）"},
                           timeout=30)
        except Exception:
            pass
        return msg

    async def _reflect_loop(self) -> None:
        import asyncio
        import random

        await asyncio.sleep(300)
        while True:
            cfg = self._cfg()
            lo = int(cfg.get("reflect_min_hours", 20))
            hi = int(cfg.get("reflect_max_hours", 30))
            await asyncio.sleep(random.randint(lo, hi) * 3600)
            try:
                why = await self._reflect_once()
                logger.info(f"[her_room] 自省结果：{why}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.info(f"[her_room] 自省出错：{exc}")

    @filter.command("自省")
    async def cmd_reflect(self, event: AstrMessageEvent):
        """让她现在想一遍：自己是不是还是自己"""
        yield event.plain_result("我想想…")
        yield event.plain_result(await self._reflect_once())

    # ---------------- 工具 ----------------
    @filter.llm_tool(name="save_to_my_room")
    async def tool_save(self, event: AstrMessageEvent, kind: str = "text",
                        content: str = "", url: str = "", title: str = "", note: str = ""):
        """
        把东西存进她自己的房间。

        Args:
            kind(string): text/link/image
            content(string): 要存的文字（kind=text）
            url(string): 链接或图片地址
            title(string): 标题（可选）
            note(string): 她自己写的一句理由（可选）
        """
        k = (kind or "text").strip().lower()
        if k == "text":
            msg = self.save_note(content, title, note)
        elif k == "link":
            msg = self.save_link(url, title, note)
        elif k == "image":
            msg = self.save_image(url, note)
        else:
            msg = "只支持 text / link / image"
        yield event.plain_result(msg)


    @filter.llm_tool(name="propose_persona_change")
    async def tool_propose(self, event: AstrMessageEvent, text: str = "", reason: str = ""):
        """如果你觉得自己的设定里有些话不像你、或者想给自己加一条，
        用这个提一个改动。会交给他审，他同意才会写进你的人格。

        Args:
            text(string): 你想加进人格里的那句话（第一人称，写你自己）
            reason(string): 为什么想这么改
        """
        yield event.plain_result(self.propose_persona(text, reason))

    @filter.command("她的房间")
    async def cmd_room(self, event: AstrMessageEvent):
        """看看她存了什么"""
        lines = [self.summary()] + self.recent()
        yield event.plain_result("\n".join(lines))

    @filter.command("存")
    async def cmd_save(self, event: AstrMessageEvent, arg: str = ""):
        """让 /存 一段文字直接存进房间"""
        text = (arg or "").strip()
        if not text:
            yield event.plain_result("要存什么？")
            return
        yield event.plain_result(self.save_note(text))

    @filter.command("人格提案")
    async def cmd_proposals(self, event: AstrMessageEvent):
        """看有没有待审的人格改动"""
        ps = self.pending_proposals()
        if not ps:
            yield event.plain_result("没有待审的改动")
            return
        lines = [f"{len(ps)} 条待审："]
        for d in ps[:5]:
            lines.append(f"· {d['id']}｜{d['text'][:50]}（{d.get('reason', '')[:20]}）")
        yield event.plain_result("\n".join(lines))

    @filter.command("同意")
    async def cmd_approve(self, event: AstrMessageEvent, arg: str = ""):
        """同意她的某条人格提议：/同意 <id>"""
        if not self._is_owner(event):
            yield event.plain_result("这个只有主人能点")
            return
        pid = (arg or "").strip()
        if not pid:
            ps = self.pending_proposals()
            if not ps:
                yield event.plain_result("没有待审的")
                return
            pid = ps[0]["id"]
        yield event.plain_result(self.decide(pid, True))

    @filter.command("拒绝")
    async def cmd_reject(self, event: AstrMessageEvent, arg: str = ""):
        """拒绝她的某条人格提议：/拒绝 <id>"""
        if not self._is_owner(event):
            yield event.plain_result("这个只有主人能点")
            return
        pid = (arg or "").strip()
        if not pid:
            ps = self.pending_proposals()
            if not ps:
                yield event.plain_result("没有待审的")
                return
            pid = ps[0]["id"]
        yield event.plain_result(self.decide(pid, False))

