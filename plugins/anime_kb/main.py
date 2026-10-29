"""二次元圈内参考库：黑话 + 热门作品。

不走向量知识库（DeepSeek 无 embedding 接口），改成：
- 用户消息里命中词条/作品名 => 只注入命中那几条解释（省 token）
- 另提供 anime_lookup 工具，让她主动查词

数据：D:\\QQrobot\\anime_kb\\glossary.json / works.json（改完不用重启，按 mtime 热加载）
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter

KB_DIR = Path(r"D:\QQrobot\anime_kb")
GLOSSARY = KB_DIR / "glossary.json"
WORKS = KB_DIR / "works.json"

MAX_TERMS = 5
MAX_WORKS = 3


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        super().__init__(context)
        self._g_cache: tuple[float, list[dict]] = (0.0, [])
        self._w_cache: tuple[float, list[dict]] = (0.0, [])

    # ---------------- 数据加载 ----------------
    def _glossary(self) -> list[dict]:
        try:
            mtime = GLOSSARY.stat().st_mtime
        except OSError:
            return []
        if mtime != self._g_cache[0]:
            try:
                data = json.loads(GLOSSARY.read_text(encoding="utf-8"))
                self._g_cache = (mtime, data if isinstance(data, list) else [])
            except Exception as exc:  # noqa: BLE001
                logger.info(f"[anime_kb] 词表读取失败：{exc}")
                return self._g_cache[1]
        return self._g_cache[1]

    def _works(self) -> list[dict]:
        try:
            mtime = WORKS.stat().st_mtime
        except OSError:
            return []
        if mtime != self._w_cache[0]:
            try:
                data = json.loads(WORKS.read_text(encoding="utf-8"))
                items = data.get("items") if isinstance(data, dict) else data
                self._w_cache = (mtime, items if isinstance(items, list) else [])
            except Exception as exc:  # noqa: BLE001
                logger.info(f"[anime_kb] 作品表读取失败：{exc}")
                return self._w_cache[1]
        return self._w_cache[1]

    # ---------------- 匹配 ----------------
    @staticmethod
    def _hit(text: str, term: str, aliases: list[str]) -> bool:
        low = text.lower()
        for name in [term, *aliases]:
            if not name:
                continue
            if len(name) <= 1:
                continue
            if name.lower() in low:
                return True
        return False

    def _match_terms(self, text: str) -> list[dict]:
        out = []
        for item in self._glossary():
            if item.get("safety") == "nsfw":
                continue
            if self._hit(text, item.get("term", ""), item.get("aliases") or []):
                out.append(item)
            if len(out) >= MAX_TERMS:
                break
        return out

    def _match_works(self, text: str) -> list[dict]:
        out = []
        for item in self._works():
            if self._hit(text, item.get("title", ""), item.get("aliases") or []):
                out.append(item)
            if len(out) >= MAX_WORKS:
                break
        return out

    # ---------------- 注入 ----------------
    @filter.on_llm_request()
    async def inject_anime_kb(self, event: AstrMessageEvent, req) -> None:
        try:
            text = event.get_message_str() or ""
        except Exception:
            return
        if not text.strip():
            return
        terms = self._match_terms(text)
        works = self._match_works(text)
        if not terms and not works:
            return

        lines = []
        if terms:
            lines.append("【圈内词】")
            for t in terms:
                lines.append(f"- {t['term']}：{t['meaning']}（{t.get('tone') or ''}）")
        if works:
            lines.append("【作品】")
            for w in works:
                tags = "/".join(w.get("tags") or [])
                one = w.get("one_liner") or ""
                lines.append(f"- {w['title']}（{w.get('type') or ''} {w.get('year') or ''}，{tags}）：{one}")

        req.system_prompt += (
            "\n\n【二次元参考·你可以自然用，但别照念解释、别提「参考」这两个字】\n"
            + "\n".join(lines)
        )
        logger.info(f"[anime_kb] 注入 {len(terms)} 个词 / {len(works)} 部作品")

    # ---------------- 工具：查词 ----------------
    @filter.llm_tool(name="anime_lookup")
    async def anime_lookup(self, event: AstrMessageEvent, keyword: str):
        """查二次元黑话或作品的解释。遇到不熟的梗、作品名时用它确认含义。

        Args:
            keyword(string): 要查的词或作品名
        """
        kw = (keyword or "").strip()
        if not kw:
            return "给我个词"
        low = kw.lower()
        for item in self._glossary():
            if item.get("safety") == "nsfw":
                continue
            names = [item.get("term", ""), *(item.get("aliases") or [])]
            if any(n and (n.lower() == low or n.lower() in low) for n in names):
                return (
                    f"{item['term']}：{item['meaning']}"
                    f"（语气：{item.get('tone') or '中性'}｜分类：{item.get('cat') or ''}）"
                )
        for w in self._works():
            names = [w.get("title", ""), *(w.get("aliases") or [])]
            if any(n and (n.lower() == low or n.lower() in low) for n in names):
                tags = "/".join(w.get("tags") or [])
                return (
                    f"{w['title']}（{w.get('type') or ''} {w.get('year') or ''}，{tags}）："
                    f"{w.get('one_liner') or '暂无简介'}"
                )
        return f"「{kw}」这个词库里没有，我不确定，别瞎编"
