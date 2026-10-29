"""上网学网络用语与说话方式。

思路：
- 定期搜「最近的网络热词/流行语」，让 flash 抽成结构化词条（词、意思、用法、语气）
- 存进 slang.json，同类词只累加权重，不重复
- 聊天时把最新的几条以极短提示注入 system prompt（不写进历史）
- 支持 /学词 手动学、/热词 看学了什么、/学词 off 关掉
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from datetime import date, datetime
from pathlib import Path

import httpx
from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter

ROOT = Path(r"D:\QQrobot")
STORE = ROOT / "slang.json"
CFG = ROOT / "slang_config.json"
ANYSEARCH = "https://api.anysearch.com"

DEFAULTS = {
    "enabled": True,
    "interval_hours": 12,
    "inject_count": 5,
    "max_terms": 120,
}

QUERIES = [
    "2026年 网络流行语 热词 盘点",
    "最近的网络梗 出处 意思",
    "年轻人 常用 网络用语 2026",
]

SAFE_RULE = (
    "只收中文互联网上真实流通的、用来表达情绪或态度的词；"
    "不要收录辱骂、地域/性别歧视、低俗、政治敏感、或者针对具体真人的贬称。"
)


def _key() -> str:
    try:
        return json.loads((ROOT / "secrets" / "anysearch.json").read_text(encoding="utf-8"))["api_key"]
    except Exception:
        return ""


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


class SlangLearn(star.Star):
    def __init__(self, context: star.Context) -> None:
        super().__init__(context)
        self._task: asyncio.Task | None = None

    async def initialize(self) -> None:
        self._task = asyncio.create_task(self._loop())
        logger.info("[slang] 启动：会自己上网学新词")

    async def terminate(self) -> None:
        if self._task:
            self._task.cancel()

    # ---------------- 配置 ----------------
    def _cfg(self) -> dict:
        cfg = dict(DEFAULTS)
        cfg.update(_load(CFG, {}) or {})
        return cfg

    def _store(self) -> dict:
        return _load(STORE, {"terms": [], "updated": ""}) or {"terms": [], "updated": ""}

    # ---------------- 检索 ----------------
    def _search(self, q: str) -> list[dict]:
        h = {"Content-Type": "application/json"}
        k = _key()
        if k:
            h["Authorization"] = f"Bearer {k}"
        with httpx.Client(timeout=25) as c:
            r = c.post(f"{ANYSEARCH}/v1/search", headers=h, json={"query": q, "max_results": 5})
            r.raise_for_status()
            d = r.json()
        return [{"title": x.get("title") or "", "url": x.get("url") or "", "snippet": x.get("snippet") or ""}
                for x in ((d.get("data") or {}).get("results") or [])]

    def _extract(self, url: str, limit: int = 1600) -> str:
        h = {"Content-Type": "application/json"}
        k = _key()
        if k:
            h["Authorization"] = f"Bearer {k}"
        with httpx.Client(timeout=30) as c:
            r = c.post(f"{ANYSEARCH}/v1/extract", headers=h, json={"url": url})
            r.raise_for_status()
            d = r.json()
        return ((d.get("data") or {}).get("content") or "")[:limit]

    # ---------------- 模型 ----------------
    async def _flash(self):
        try:
            pm = getattr(self.context, "provider_manager", None)
            if pm is not None:
                for pid in ("deepseek/deepseek-flash", "deepseek-flash"):
                    try:
                        p = await pm.get_provider_by_id(pid)
                        if p is not None:
                            return p
                    except Exception:
                        continue
        except Exception:
            pass
        return None

    async def _llm(self, prompt: str, system: str = "") -> str:
        prov = await self._flash()
        if prov is None:
            return ""
        try:
            resp = await prov.text_chat(prompt=prompt, system_prompt=system or None)
            return str(getattr(resp, "completion_text", "") or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[slang] LLM 失败：{exc}")
            return ""

    # ---------------- 学习 ----------------
    async def _learn_once(self) -> tuple[bool, str]:
        cfg = self._cfg()
        if not cfg.get("enabled"):
            return False, "已关闭"

        materials: list[str] = []
        for q in random.sample(QUERIES, k=2):
            try:
                rs = await asyncio.to_thread(self._search, q)
            except Exception as exc:  # noqa: BLE001
                logger.info(f"[slang] 搜索失败：{exc}")
                continue
            for r in rs[:2]:
                if not r["url"]:
                    continue
                try:
                    body = await asyncio.to_thread(self._extract, r["url"])
                except Exception:
                    body = r.get("snippet", "")
                materials.append(f"《{r['title']}》\n{body[:1200]}")
            if len(materials) >= 3:
                break

        if not materials:
            return False, "没搜到素材"

        prompt = (
            "下面是网上关于「网络流行语/热词」的资料。请从中抽出最多 8 个**当下真实在用**的网络用语，"
            "输出 JSON 数组，每项字段：\n"
            '  "term": 词本身（2-8 字）\n'
            '  "meaning": 一句话解释（不超过 25 字）\n'
            '  "usage": 一个极短例句（不超过 20 字，用它本来的语气）\n'
            '  "tone": 语气，从 吐槽/调侃/惊讶/认同/敷衍/卖萌 里选一个\n'
            f"{SAFE_RULE}\n"
            "只输出 JSON，不要解释、不要 markdown 代码块。\n\n"
            + "\n\n".join(materials[:3])
        )
        raw = await self._llm(prompt, "你是网络用语整理器，只输出 JSON 数组。")
        if not raw:
            return False, "模型没返回"
        raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
        m = re.search(r"\[.*\]", raw, re.S)
        if not m:
            return False, "返回格式不对"
        try:
            items = json.loads(m.group(0))
        except Exception:
            return False, "JSON 解析失败"

        # 素材里没出现过的词一律丢掉（防模型编词）
        materials_blob = "\n".join(materials).lower()
        items = [it for it in items
                 if isinstance(it, dict)
                 and str(it.get("term") or "").strip().lower() in materials_blob]
        if not items:
            return False, "模型给的都是素材里没有的词"

        store = self._store()
        terms = store.get("terms") or []
        by_term = {t.get("term"): t for t in terms}
        added = 0
        for it in items:
            if not isinstance(it, dict):
                continue
            term = str(it.get("term") or "").strip()
            if not term or len(term) > 10:
                continue
            if re.search(r"[A-Za-z]{1,}", term) and len(term) > 12:
                continue
            if term in by_term:
                old = by_term[term]
                old["weight"] = float(old.get("weight", 1)) + 0.5
                old["seen"] = date.today().isoformat()
                continue
            entry = {
                "term": term,
                "meaning": str(it.get("meaning") or "")[:40],
                "usage": str(it.get("usage") or "")[:40],
                "tone": str(it.get("tone") or "")[:6],
                "weight": 1.0,
                "seen": date.today().isoformat(),
            }
            terms.append(entry)
            by_term[term] = entry
            added += 1

        terms.sort(key=lambda t: (-float(t.get("weight", 1)), t.get("seen", "")))
        store["terms"] = terms[: int(cfg.get("max_terms", 120))]
        store["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        _save(STORE, store)
        logger.info(f"[slang] 学到 {added} 个新词，库里共 {len(store['terms'])} 个")
        return True, f"新增 {added} 个，库里 {len(store['terms'])} 个"

    # ---------------- 注入 ----------------
    @filter.on_llm_request()
    async def inject(self, event: AstrMessageEvent, req) -> None:
        cfg = self._cfg()
        if not cfg.get("enabled"):
            return
        store = self._store()
        terms = (store.get("terms") or [])[: int(cfg.get("inject_count", 5))]
        if not terms:
            return
        picks = [f"{t['term']}（{t.get('meaning','')}）" for t in terms if t.get("term")]
        if not picks:
            return
        block = (
            "【你最近在网上学的词】"
            + "、".join(picks)
            + "\n用法：真想用再用，一条回复最多一个，别硬塞；不合适的场合就别用，装懂更难看。"
        )
        base = str(getattr(req, "system_prompt", "") or "")
        req.system_prompt = (base + "\n\n" + block).strip()

    # ---------------- 循环 ----------------
    async def _loop(self) -> None:
        await asyncio.sleep(120)
        while True:
            cfg = self._cfg()
            await asyncio.sleep(max(1.0, float(cfg.get("interval_hours", 12))) * 3600)
            try:
                ok, why = await self._learn_once()
                logger.info(f"[slang] 这轮学词：{why}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.info(f"[slang] 异常：{exc}")

    # ---------------- 指令 ----------------
    @filter.command("学词")
    async def cmd_learn(self, event: AstrMessageEvent, arg: str = ""):
        """立刻上网学一轮网络用语：/学词"""
        a = (arg or "").strip().lower()
        cfg = self._cfg()
        if a in ("off", "关"):
            cfg["enabled"] = False
            _save(CFG, cfg)
            yield event.plain_result("自动学词关了")
            return
        if a in ("on", "开"):
            cfg["enabled"] = True
            _save(CFG, cfg)
            yield event.plain_result("自动学词开了")
            return
        yield event.plain_result("我去网上看看最近流行什么…")
        ok, why = await self._learn_once()
        yield event.plain_result(("学完了：" if ok else "没学成：") + why)

    @filter.command("热词")
    async def cmd_list(self, event: AstrMessageEvent):
        """看看她学了哪些词"""
        store = self._store()
        terms = (store.get("terms") or [])[:12]
        if not terms:
            yield event.plain_result("还没学，发 /学词 试试")
            return
        lines = [f"{i}. {t['term']}｜{t.get('meaning','')}" for i, t in enumerate(terms, 1)]
        yield event.plain_result("最近学的词：\n" + "\n".join(lines))
