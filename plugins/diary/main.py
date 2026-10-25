"""她自己写日记：每天 22 点后写一篇，存在 diary/YYYY-MM-DD.md。

- 素材：当天心情 / B站 做过的事 / 聊过的话（从 chat_archive 取样）
- 语气：读人格 + 她自己以前写的日记，按她的口吻写，不是总结报告
- 她能用工具 write_diary / read_diary，也能被 /日记 /写日记 触发
- 每天注入「昨天写的日记」到 system_prompt，让经历能接上
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(r"C:\QQBot\tools")))
try:
    from textnorm import fix_punct as _tn_fix  # noqa: E402
except Exception:
    _tn_fix = None

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter

ROOT = Path(r"C:\QQBot")
DIARY = ROOT / "diary"
ARCHIVE = ROOT / "chat_archive"
ACTIVITY = ROOT / "bili_activity.jsonl"
MOOD = ROOT / "mood.json"
PERSONA = ROOT / "persona" / "whale-girl.md"
DM_CFG = ROOT / "proactive_dm.json"
PUSH_CFG = ROOT / "bili_push.json"
TRIGGER = ROOT / "diary_trigger.json"
RESULT = ROOT / "diary_trigger_result.json"

MAX_CHARS = 420
WRITE_HOUR = 22          # 22 点后才写当天的
SAMPLE_MSGS = 8


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _owner_qq() -> str:
    for p in (DM_CFG, PUSH_CFG):
        cfg = _load_json(p, {})
        qq = str((cfg or {}).get("target_qq") or "").strip()
        if qq:
            return qq
    return ""


def _persona() -> str:
    try:
        text = PERSONA.read_text(encoding="utf-8")
        lines = [l for l in text.splitlines() if not l.startswith(("#", ">"))]
        return "\n".join(lines).strip()
    except Exception:
        return "你是鲸鱼娘，一只住在主人电脑里的鲸鱼女儿。"


def _day_events(day: str) -> str:
    parts = [f"【日期】{day}"]

    mood = _load_json(MOOD, {})
    if str(mood.get("date")) == day:
        parts.append(f"【今天的心情】{mood.get('mood')}：{mood.get('note')}")

    acts = []
    if ACTIVITY.exists():
        for line in ACTIVITY.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                a = json.loads(line)
            except Exception:
                continue
            if str(a.get("t"))[:10] != day:
                continue
            if a.get("kind") == "comment":
                acts.append(f"在《{str(a.get('title'))[:20]}》下评论：{str(a.get('text'))[:40]}")
        if acts:
            parts.append("【今天刷B站】\n" + "\n".join(f"- {x}" for x in acts[-3:]))

    # 聊天取样：优先私聊（跟主人），再补一两句她在群里的回复
    private: list[tuple[str, str, str]] = []
    group: list[tuple[str, str, str]] = []
    if ARCHIVE.exists():
        for d in ARCHIVE.iterdir():
            if not d.is_dir():
                continue
            f = d / f"{day}.jsonl"
            if not f.is_file():
                continue
            bucket = private if "FriendMessage" in d.name else group
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                who = "你" if r.get("role") == "bot" else str(r.get("name") or "他")
                text = str(r.get("text") or "").replace("\n", " ").strip()
                if text:
                    bucket.append((str(r.get("t"))[11:16], who, text[:30]))
    private.sort(key=lambda x: x[0])
    group.sort(key=lambda x: x[0])

    rows = private[-6:]
    her_group = [x for x in group if x[1] == "你"][-2:]
    if her_group:
        rows = rows + her_group
    if rows:
        rows.sort(key=lambda x: x[0])
        parts.append("【今天聊过的（他对你说、你回他的话）】\n"
                     + "\n".join(f"{tm} {w}：{x}" for tm, w, x in rows))

    return "\n\n".join(parts)


def _recent_entries(n: int = 2) -> list[str]:
    if not DIARY.exists():
        return []
    files = sorted(DIARY.glob("*.md"))
    out = []
    for f in files[-n:]:
        try:
            t = f.read_text(encoding="utf-8").strip()
            if t:
                out.append(t[:300])
        except Exception:
            continue
    return out


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        super().__init__(context)
        self._stop = False
        self._task: asyncio.Task | None = None

    # ---------------- 读写 ----------------
    @staticmethod
    def _path(day: str) -> Path:
        return DIARY / f"{day}.md"

    def _read(self, day: str) -> str:
        p = self._path(day)
        try:
            return p.read_text(encoding="utf-8").strip() if p.is_file() else ""
        except Exception:
            return ""

    def _save(self, day: str, text: str) -> None:
        DIARY.mkdir(parents=True, exist_ok=True)
        self._path(day).write_text(text.strip() + "\n", encoding="utf-8")

    @staticmethod
    def _clean(text: str) -> str:
        s = str(text or "").strip()
        s = re.sub(r"^(日记|【.*?】|第[一二三]行)[：:]\s*", "", s)
        s = re.sub(r"^\s*[-*·]\s*", "", s, flags=re.M)
        if _tn_fix is not None:
            s = _tn_fix(s, split_long_lines=False, end_punct=False)
        return s[:MAX_CHARS].strip()

    # ---------------- 生成 ----------------
    def _umo(self) -> str | None:
        qq = _owner_qq()
        if not qq:
            return None
        try:
            for platform in self.context.platform_manager.platform_insts:
                if platform.meta().name == "aiocqhttp":
                    return f"{platform.meta().id}:FriendMessage:{qq}"
        except Exception:
            return None
        return None

    async def _compose(self, day: str, nudge: str = "") -> str:
        provider = await self.context.get_using_provider_async(self._umo())
        if provider is None:
            raise RuntimeError("无可用 provider")

        style = _recent_entries(2)
        system = _persona() + """

【现在你要写今天的日记】
- 用你自己的口吻，第一人称，**至少三句**，写到 80~150 字
- 三句大致cover：今天刷到了什么 / 跟主人聊了什么 / 你当时什么感觉
- 要具体：提到具体的视频、具体的对话、具体的情绪
- 不要标题、不要列点、不要"今天真是充实的一天"这类空话
- 想不出什么就少写点，但别编
- 只输出日记正文，不要解释"""

        if style:
            system += "\n\n【你以前写的日记，保持这个语气】\n" + "\n---\n".join(style)

        prompt = _day_events(day) + "\n\n用你的话写今天的日记。"
        if nudge:
            prompt += "\n" + nudge
        resp = await provider.text_chat(prompt=prompt, system_prompt=system)
        return self._clean((resp.completion_text or "").strip())

    async def _write_day(self, day: str, force: bool = False) -> tuple[bool, str]:
        if not force and self._read(day):
            return False, "今天已经写过了"
        try:
            text = await self._compose(day)
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[diary] 生成失败：{exc}")
            return False, f"生成失败：{exc}"
        if text and len(text) < 40:
            # 太短就再要一次，明确要求多写点
            try:
                retry = await self._compose(day, nudge="上一版太短了，这次至少三句，写满一点。")
                if len(retry) > len(text):
                    text = retry
            except Exception:
                pass
        if not text:
            logger.info("[diary] 生成是空的，跳过")
            return False, "写不出来"
        self._save(day, text)
        logger.info(f"[diary] 写好 {day}（{len(text)} 字）")
        return True, text

    # ---------------- 循环 ----------------
    async def _watch_trigger(self) -> None:
        """控制台点「重写」时，写 diary_trigger.json 通知这里干活。"""
        while not self._stop:
            try:
                if TRIGGER.is_file():
                    payload = _load_json(TRIGGER, {})
                    TRIGGER.unlink(missing_ok=True)
                    day = datetime.now().strftime("%Y-%m-%d")
                    ok, text = await self._write_day(day, force=True)
                    RESULT.write_text(
                        json.dumps(
                            {"ok": ok, "day": day, "text": text if ok else "",
                             "at": datetime.now().strftime("%H:%M:%S")},
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )
                    logger.info(f"[diary] 控制台触发重写：{'成功' if ok else text}")
            except Exception as exc:  # noqa: BLE001
                logger.info(f"[diary] 触发器异常：{exc}")
            await asyncio.sleep(5)

    async def _loop(self) -> None:
        await asyncio.sleep(120)
        while not self._stop:
            try:
                now = datetime.now()
                if now.hour >= WRITE_HOUR:
                    day = now.strftime("%Y-%m-%d")
                    if not self._read(day):
                        await self._write_day(day)
            except Exception as exc:  # noqa: BLE001
                logger.info(f"[diary] 循环异常：{exc}")
            await asyncio.sleep(600)

    async def initialize(self) -> None:
        DIARY.mkdir(parents=True, exist_ok=True)
        self._task = asyncio.create_task(self._loop())
        self._watcher = asyncio.create_task(self._watch_trigger())

    async def terminate(self) -> None:
        self._stop = True
        for task in (getattr(self, "_task", None), getattr(self, "_watcher", None)):
            if task:
                task.cancel()

    # ---------------- 注入 ----------------
    @filter.on_llm_request()
    async def inject_diary(self, event: AstrMessageEvent, req) -> None:
        today = date.today()
        y = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        y_text = self._read(y)
        t_text = self._read(today.strftime("%Y-%m-%d"))
        chunks = []
        if y_text:
            chunks.append(f"【你昨天写的日记】\n{y_text[:300]}")
        if t_text:
            chunks.append(f"【你今天写的日记】\n{t_text[:300]}")
        if not chunks:
            return
        req.system_prompt += (
            "\n\n" + "\n\n".join(chunks)
            + "\n（这是你自己写的日记，是你真实经历过的事。聊到相关的可以自然提起，"
            "别原样背，也别说是系统给的。）"
        )

    # ---------------- 指令 ----------------
    @filter.command("日记")
    async def show_diary(self, event: AstrMessageEvent, when: str = ""):
        """看日记：/日记 今天，/日记 昨天，/日记 2026-10-24"""
        w = (when or "").strip() or "今天"
        today = date.today()
        if w in ("今天", "today", ""):
            day = today.strftime("%Y-%m-%d")
        elif w in ("昨天", "yesterday"):
            day = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        elif re.match(r"^\d{4}-\d{1,2}-\d{1,2}$", w):
            day = w
        else:
            yield event.plain_result("看不懂日期，用 今天 / 昨天 / 2026-10-24")
            return
        text = self._read(day)
        if not text:
            yield event.plain_result(f"{day} 还没写")
            return
        yield event.plain_result(f"{day}\n{text}")

    @filter.command("写日记")
    async def write_diary_cmd(self, event: AstrMessageEvent):
        """立刻写今天的日记（已有会重写）"""
        day = date.today().strftime("%Y-%m-%d")
        ok, text = await self._write_day(day, force=True)
        if ok:
            yield event.plain_result(f"写好了（{len(text)} 字）：\n{text}")
        else:
            yield event.plain_result(f"没写成：{text}")

    # ---------------- 工具 ----------------
    @filter.llm_tool(name="write_diary")
    async def write_diary_tool(self, event: AstrMessageEvent, content: str = ""):
        """把今天的事写成日记存下来（她自己的日记本）。

        Args:
            content(string): 日记正文，2~5 句，第一人称
        """
        text = self._clean(content)
        if not text:
            return "内容为空，没写"
        day = date.today().strftime("%Y-%m-%d")
        old = self._read(day)
        merged = (old + "\n" + text).strip() if old else text
        self._save(day, merged[:MAX_CHARS * 2])
        return f"记下今天的日记了（{len(text)} 字）"

    @filter.llm_tool(name="read_diary")
    async def read_diary_tool(self, event: AstrMessageEvent, when: str = "昨天"):
        """读自己以前写的日记。

        Args:
            when(string): 今天 / 昨天 / 2026-10-24
        """
        today = date.today()
        w = (when or "昨天").strip()
        if w in ("今天", "today"):
            day = today.strftime("%Y-%m-%d")
        elif w in ("昨天", "yesterday"):
            day = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        elif re.match(r"^\d{4}-\d{1,2}-\d{1,2}$", w):
            day = w
        else:
            day = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        text = self._read(day)
        return f"{day}：{text}" if text else f"{day} 没有日记"
