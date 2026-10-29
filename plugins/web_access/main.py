"""联网：让她能搜东西、能读网页。

主引擎 AnySearch（匿名可用，有每日免费额度），失败时退回 Bing / 本地抓取。

两个工具：
- web_search(query, limit)：AnySearch /v1/search → Bing 兜底
- web_read(url)：AnySearch /v1/extract → 本地抓正文兜底

设计取舍：
- 只用 http/https，挡掉 localhost 和内网地址（防止被利用去访问本机服务）
- 单页最多 2MB、正文最多 4000 字，避免把上下文撑爆
- 结果里带链接，方便她回答时引用；读不到就如实说读不到
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import socket
import urllib.parse
from pathlib import Path

import httpx
from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6"}
ANYSEARCH = "https://api.anysearch.com"
MAX_BYTES = 2 * 1024 * 1024
MAX_TEXT = 4000
MAX_SNIPPET = 200


def _api_key() -> str:
    key = (os.environ.get("ANYSEARCH_API_KEY") or "").strip()
    if key:
        return key
    for path in (r"D:\QQrobot\secrets\anysearch.json",
                 r"D:\QQrobot\astrbot\data\skills\anysearch\.env"):
        try:
            if path.endswith(".json"):
                return json.loads(Path(path).read_text(encoding="utf-8")).get("api_key", "")
            for line in Path(path).read_text(encoding="utf-8").splitlines():
                if line.startswith("ANYSEARCH_API_KEY="):
                    return line.split("=", 1)[1].strip()
        except Exception:
            continue
    return ""


def _as_headers() -> dict:
    h = {"Content-Type": "application/json"}
    key = _api_key()
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def _is_public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False
    return True


def _clean(text: str, limit: int = MAX_SNIPPET) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:limit]


class WebAccess(star.Star):
    def __init__(self, context: star.Context) -> None:
        super().__init__(context)

    # ---------------- AnySearch ----------------
    def _anysearch(self, query: str, limit: int) -> list[dict]:
        with httpx.Client(timeout=25) as c:
            r = c.post(
                f"{ANYSEARCH}/v1/search",
                headers=_as_headers(),
                json={"query": query, "max_results": limit},
            )
            r.raise_for_status()
            d = r.json()
        if d.get("code") != 0:
            raise RuntimeError(d.get("message") or "AnySearch 返回错误")
        out = []
        for it in ((d.get("data") or {}).get("results") or [])[:limit]:
            out.append(
                {
                    "title": _clean(it.get("title") or "", 120),
                    "url": it.get("url") or "",
                    "snippet": _clean(it.get("snippet") or "", 240),
                }
            )
        return out

    def _anysearch_extract(self, url: str) -> str:
        with httpx.Client(timeout=35, follow_redirects=True) as c:
            r = c.post(f"{ANYSEARCH}/v1/extract", headers=_as_headers(), json={"url": url})
            r.raise_for_status()
            d = r.json()
        if d.get("code") != 0:
            raise RuntimeError(d.get("message") or "AnySearch extract 返回错误")
        data = d.get("data") or {}
        text = data.get("content") or data.get("markdown") or ""
        title = data.get("title") or url
        if not text:
            raise RuntimeError("extract 没有返回正文")
        return f"{title}\n{url}\n\n{text[:MAX_TEXT]}"

    # ---------------- Bing 兜底 ----------------
    def _bing(self, query: str, limit: int) -> list[dict]:
        from bs4 import BeautifulSoup

        with httpx.Client(headers=HEADERS, timeout=15, follow_redirects=True) as c:
            r = c.get("https://cn.bing.com/search", params={"q": query, "ensearch": "0"})
            r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        out: list[dict] = []
        for li in soup.select("li.b_algo"):
            a = li.select_one("h2 a")
            if not a:
                continue
            href = a.get("href") or ""
            if not href.startswith("http"):
                continue
            p = li.select_one(".b_caption p") or li.select_one("p")
            out.append(
                {
                    "title": _clean(a.get_text(), 120),
                    "url": href,
                    "snippet": _clean(p.get_text() if p else "", 240),
                }
            )
            if len(out) >= limit:
                break
        return out

    # ---------------- 拼音/本地抓取兜底 ----------------
    def _fetch_local(self, url: str) -> str:
        from bs4 import BeautifulSoup

        with httpx.Client(headers=HEADERS, timeout=20, follow_redirects=True) as c:
            with c.stream("GET", url) as r:
                r.raise_for_status()
                ctype = r.headers.get("content-type", "")
                if "html" not in ctype and "text" not in ctype:
                    raise RuntimeError(f"不是网页（{ctype}）")
                buf, total = [], 0
                for chunk in r.iter_bytes():
                    total += len(chunk)
                    buf.append(chunk)
                    if total >= MAX_BYTES:
                        break
                raw = b"".join(buf)
                encoding = r.charset_encoding or "utf-8"
        html = raw.decode(encoding, errors="replace")
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside", "form"]):
            tag.decompose()
        title = (soup.title.get_text().strip() if soup.title else "") or url
        main = soup.find("article") or soup.find("main") or soup.body or soup
        text = re.sub(r"\n{3,}", "\n\n", main.get_text("\n"))
        text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        return f"{title}\n{url}\n\n{text[:MAX_TEXT]}"

    # ---------------- 组合 ----------------
    def _search_sync(self, query: str, limit: int) -> str:
        errors = []
        try:
            items = self._anysearch(query, limit)
            if items:
                return self._render(query, items, "AnySearch")
            errors.append("AnySearch 空结果")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"AnySearch: {type(exc).__name__}")
            logger.info(f"[web] AnySearch 失败：{exc}")
        try:
            items = self._bing(query, limit)
            if items:
                return self._render(query, items, "Bing")
            errors.append("Bing 空结果")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Bing: {type(exc).__name__}")
        return f"搜索「{query}」失败（{'；'.join(errors)}）"

    @staticmethod
    def _render(query: str, items: list[dict], source: str) -> str:
        lines = [f"搜索「{query}」（来源：{source}）"]
        for i, it in enumerate(items, 1):
            lines.append(f"{i}. {it['title']}\n   {it['url']}\n   {it['snippet']}")
        return "\n".join(lines)

    def _read_sync(self, url: str) -> str:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return "只支持 http/https 链接"
        if not _is_public_host(parsed.hostname):
            return "这个地址指向内网或本机，不能读"
        try:
            return self._anysearch_extract(url)
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[web] extract 失败，改本地抓取：{exc}")
        try:
            return self._fetch_local(url)
        except Exception as exc:  # noqa: BLE001
            return f"读不了这个页面：{type(exc).__name__}: {exc}"

    # ---------------- 工具 ----------------
    @filter.llm_tool(name="web_search")
    async def web_search(self, event: AstrMessageEvent, query: str, limit: int = 5):
        """快速搜索，只返回几条标题+链接+摘要（不读正文）。仅当只需要链接、或想先看看有什么时才用它；要完整答案请用 web_research。

        Args:
            query(string): 搜索关键词，可空格分隔多个词
            limit(number): 返回几条，1-8，默认 5
        """

        n = max(1, min(8, int(limit or 5)))
        return await asyncio.to_thread(self._search_sync, query, n)

    # ---------------- 大模型辅助检索 ----------------
    async def _flash(self):
        """拿 flash 做拆词和总结（便宜快）。"""
        try:
            pm = getattr(self.context, "provider_manager", None)
            if pm is not None:
                for pid in ("deepseek/deepseek-flash", "deepseek-flash"):
                    try:
                        prov = await pm.get_provider_by_id(pid)
                        if prov is not None:
                            return prov
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
            logger.info(f"[web] LLM 调用失败：{exc}")
            return ""

    @filter.llm_tool(name="web_research")
    async def web_research(self, event: AstrMessageEvent, question: str):
        """联网查资料并给出带出处的答案（多来源 → 读正文 → 总结）。用户说"帮我查""查一下""要来源/出处""最近怎么样""这个是什么"时，优先用这个而不是 web_search。

        Args:
            question(string): 用户的问题，可以是完整一句话
        """

        import asyncio as _aio

        q = (question or "").strip()
        if not q:
            return "要查什么？"

        # 1) 拆成三个检索词
        expand_prompt = (
            "把下面的问题拆成 3 个适合搜索引擎的短查询词，每行一个，不要编号，不要解释。\n"
            f"问题：{q}"
        )
        raw = await self._llm(expand_prompt, "你是一个检索词生成器，只输出查询词。")
        queries = [x.strip(" -•\t") for x in raw.splitlines() if x.strip()][:3] or [q]

        # 2) 并发搜索
        def _one(kw: str):
            try:
                return self._anysearch(kw, 4)
            except Exception:
                return []

        results = await _aio.gather(*[_aio.to_thread(_one, kw) for kw in queries])
        merged: list[dict] = []
        seen_urls = set()
        for group in results:
            for it in group:
                url = it.get("url") or ""
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                merged.append(it)
        if not merged:
            return f"没搜到「{q}」的相关内容"

        # 3) 读前 3 篇正文
        def _read(url: str) -> str:
            try:
                return self._anysearch_extract(url)[:1800]
            except Exception:
                return ""

        picked = merged[:3]
        bodies = await _aio.gather(*[_aio.to_thread(_read, it["url"]) for it in picked])

        # 4) 带出处总结
        sources = []
        for i, (it, body) in enumerate(zip(picked, bodies), 1):
            sources.append(f"[{i}] {it.get('title') or ''}\nURL: {it.get('url')}\n{(body or it.get('snippet') or '')[:1400]}")
        prompt = (
            f"用户的问题：{q}\n\n"
            "下面是从网上查到的资料（可能不完整，也可能互相矛盾）：\n\n"
            + "\n\n".join(sources)
            + "\n\n请用中文回答，要求：\n"
            "1. 先给结论，再给依据；\n"
            "2. 关键事实后面标注来源编号，如 [1][2]；\n"
            "3. 资料里没有的信息不要编，明确说「没查到」；\n"
            "4. 控制在 200 字以内，除非问题需要更长的解释。"
        )
        answer = await self._llm(prompt, "你是严谨的检索助手，只根据给定资料回答。")
        if not answer:
            # 模型不可用时退化成分条结果
            answer = "\n".join(
                f"{i}. {it.get('title') or ''} — {(it.get('snippet') or '')[:80]}\n   {it.get('url')}"
                for i, it in enumerate(picked, 1)
            )
        lines = [answer, "", "出处："]
        for i, it in enumerate(picked, 1):
            lines.append(f"[{i}] {it.get('title') or ''} {it.get('url')}")
        return "\n".join(lines)

    @filter.llm_tool(name="web_read")
    async def web_read(self, event: AstrMessageEvent, url: str):
        """读一个网页的正文（AnySearch extract）。搜索结果里有用的链接用它读全文。

        Args:
            url(string): 完整网址，以 http:// 或 https:// 开头
        """

        return await asyncio.to_thread(self._read_sync, url.strip())

    @filter.command("搜")
    async def search_cmd(self, event: AstrMessageEvent, query: str = ""):
        """手动搜索：/搜 关键词"""
        q = (query or "").strip()
        if not q:
            yield event.plain_result("要搜什么？用法：/搜 关键词")
            return
        yield event.plain_result(await asyncio.to_thread(self._search_sync, q, 5))
