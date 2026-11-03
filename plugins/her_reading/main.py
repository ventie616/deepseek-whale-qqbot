"""她自己读点东西：古文 / 短篇 / 书评，读完写一段，进她的房间。

读的东西会成为「自省」的素材——她可以拿它当参照，慢慢改自己。
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
import sys as _sys
from pathlib import Path as _Path
# offpeak 在仓库 tools/ 下；插件目录向上找到项目根
for _cand in (_Path(__file__).resolve().parents[3] / "tools",
              _Path(__file__).resolve().parents[2] / "tools",
              _Path(r"D:\\QQrobot") / "tools"):
    if (_cand / "offpeak.py").exists():
        _sys.path.insert(0, str(_cand))
        break
try:
    from offpeak import is_offpeak as _is_offpeak
except Exception:  # 没配工具目录时不做限制
    def _is_offpeak(now=None):
        return True, "未配置"

ROOT = Path(r"D:\QQrobot")
CFG = ROOT / "her_reading.json"
STATE = ROOT / "her_reading_state.json"
LOG = ROOT / "her_reading.jsonl"
ROOM_TEXT = ROOT / "her_room" / "text"
ANYSEARCH = "https://api.anysearch.com"
KNOW_DIR = ROOT / "her_room" / "knowledge"
KB_INDEX = KNOW_DIR / "index.jsonl"
EMBED_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
EMBED_MODEL = "text-embedding-v3"
EMBED_DIM = 512
KB_NAME = "她读过的书"
KB_EMBED_PROVIDER = "dashscope-emb/text-embedding-v4"

DEFAULTS = {
    "enabled": True,
    "min_hours": 8,
    "max_hours": 14,
    "active_hours": [9, 23],
    "max_per_day": 2,
    "notify_qq": "3328480063",
    "sources": ["gushiwen", "story"],
    "kb_enable": True,
    "kb_top_k": 2,
    "kb_min_sim": 0.30,
}

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36",
      "Referer": "https://www.gushiwen.cn/"}

STORY_QUERIES = [
    "聊斋志异 名篇 全文",
    "世说新语 有趣 故事 原文",
    "古文观止 名篇 原文 译文",
    "唐宋传奇 全文",
    "汪曾祺 短篇 全文",
    "老舍 短篇小说 全文",
]


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


def _key() -> str:
    try:
        return json.loads((ROOT / "secrets" / "anysearch.json").read_text(encoding="utf-8"))["api_key"]
    except Exception:
        return ""



def _short_title(title: str, text: str = "") -> str:
    """从网页标题里抠出一个干净的篇名。"""
    import re as _re

    SITE = ("古诗文网", "QQ阅读", "百度百科", "维基百科", "豆瓣读书", "豆瓣", "知乎",
            "中国哲学书电子化计划", "在线阅读", "小说")
    NOISE = ("最新章节", "在线阅读", "全文免费", "全文阅读", "全文", "免费", "下载",
             "TXT", "txt", "无弹窗", "章节列表", "免费阅读", "翻译", "原文", "赏析",
             "注释", "译文", "解读", "全文翻译")

    def tidy(s: str) -> str:
        s = (s or "").strip()
        for w in NOISE:
            s = s.replace(w, "")
        s = _re.sub(r"[（(][^）)]*[）)]", "", s)
        s = _re.sub(r"[()（）\[\]【】]", "", s)   # 去掉所有残留括号
        s = s.strip(" -_|丨/·—，。：:；;「」\"'()（）[]【】")
        return s

    def is_site(s: str) -> bool:
        return (not s) or any(s == x or (len(s) <= 8 and x.startswith(s)) for x in SITE)

    # 1) 先看有没有《》书名
    cands = []
    for blob in (title or "", (text or "")[:600]):
        for m in _re.findall(r"《([^》]{2,20})》", blob):
            m = tidy(m)
            if not is_site(m):
                cands.append(m)
    if cands:
        cands.sort(key=len)
        return cands[0]

    # 2) 按分隔符切开，丢掉站点名，留最长的一段
    parts = [_re.split(r"[，。！？；：|丨\-—（(]", title or "")]
    segs: list[str] = []
    for chunk in parts[0]:
        c = tidy(chunk)
        if c and not is_site(c):
            segs.append(c)
    if segs:
        segs.sort(key=len, reverse=True)
        return segs[0][:20]

    # 3) 兜底：原文里抓前几个汉字（跳过站点名）
    for blob in (tidy(title or ""), (text or "")[:80]):
        for m in _re.finditer(r"[\u4e00-\u9fffA-Za-z0-9]{2,20}", blob or ""):
            cand = m.group(0)[:16]
            if not is_site(cand):
                return cand
    return "一篇东西"


def _clean(text: str) -> str:
    """去掉网页导航、图片 markdown，只留中文正文。"""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[[^\]]*\]\([^)]*\)", "", text)
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if len(line) < 14:
            continue
        cn = len(re.findall(r"[\u4e00-\u9fff]", line))
        if cn < len(line) * 0.5:
            continue
        if re.search(r"(首页|登录|注册|下载|版权|举报|上一章|下一章|目录|书架|广告)", line):
            continue
        lines.append(line)
        if sum(len(x) for x in lines) > 2400:
            break
    return "\n".join(lines)


class HerReading(star.Star):
    def __init__(self, context: star.Context) -> None:
        super().__init__(context)
        self._task = None

    async def initialize(self) -> None:
        self._task = asyncio.create_task(self._loop())

        async def _late_sync() -> None:
            await asyncio.sleep(20)
            try:
                await self._kb_backfill()
            except Exception as exc:  # noqa: BLE001
                logger.info(f"[her_reading] 启动补同步失败：{exc}")

        asyncio.create_task(_late_sync())
        logger.info("[her_reading] 已启动：会自己找东西读")

    async def terminate(self) -> None:
        if self._task:
            self._task.cancel()

    # ---------------- 配置 ----------------
    def _cfg(self) -> dict:
        cfg = dict(DEFAULTS)
        cfg.update(_load(CFG, {}) or {})
        return cfg

    def _state(self) -> dict:
        st = _load(STATE, {}) or {}
        today = date.today().isoformat()
        if st.get("date") != today:
            st = {"date": today, "count": 0}
        return st

    def _in_window(self, cfg: dict) -> bool:
        lo, hi = cfg.get("active_hours", [9, 23])
        now = datetime.now()
        return float(lo) <= now.hour + now.minute / 60 <= float(hi)

    # ---------------- 抓 ----------------
    def _get(self, url: str, timeout: int = 25) -> str:
        r = httpx.get(url, headers=UA, timeout=timeout, follow_redirects=True)
        r.raise_for_status()
        return r.text

    def _from_gushiwen(self) -> dict:
        """古诗文网：抓首页推荐的一篇古文/诗词。"""
        home = self._get("https://www.gushiwen.cn/")
        links = re.findall(r'href="(/shiwenv_[0-9a-f]+\.aspx)"', home)
        if not links:
            raise RuntimeError("古诗文网没找到篇目")
        url = "https://www.gushiwen.cn" + random.choice(list(dict.fromkeys(links)))
        html = self._get(url)
        title = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
        body = re.search(r'<div class="contson"[^>]*>(.*?)</div>', html, re.S)
        if not body:
            raise RuntimeError("没抓到正文")
        text = re.sub(r"<[^>]+>", "\n", body.group(1))
        text = "\n".join(x.strip() for x in text.splitlines() if x.strip())
        if len(text) < 40:
            raise RuntimeError("正文太短")
        raw_title = re.sub(r"<[^>]+>", "", title.group(1)).strip() if title else "古文"
        return {"source": "古诗文网", "url": url,
                "title": _short_title(raw_title, text), "text": text[:1800]}

    def _from_story(self) -> dict:
        """按名篇搜一篇公版短篇/古文，读正文。"""
        query = random.choice(STORY_QUERIES)
        headers = {"Content-Type": "application/json"}
        key = _key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        with httpx.Client(timeout=35) as c:
            r = c.post(f"{ANYSEARCH}/v1/search", headers=headers,
                       json={"query": query, "max_results": 5})
            results = ((r.json().get("data") or {}).get("results") or [])
            random.shuffle(results)
            for it in results[:3]:
                url = it.get("url") or ""
                if not url.startswith("http"):
                    continue
                try:
                    r2 = c.post(f"{ANYSEARCH}/v1/extract", headers=headers, json={"url": url})
                    if r2.status_code != 200:
                        continue
                    body = ((r2.json().get("data") or {}).get("content") or "")
                    text = _clean(body)
                    # 书页/介绍页不算，要真的正文
                    if len(text) < 400:
                        continue
                    if re.search(r"(出版社|ISBN|定价|丛书|图书在版编目|开本|印张)", text[:400]):
                        continue
                    return {"source": "网络", "url": url,
                            "title": _short_title(it.get("title") or "", text),
                            "text": text[:1800]}
                except Exception:
                    continue
        raise RuntimeError("没找到能读的")

    def _fetch_one(self, cfg: dict) -> dict:
        srcs = cfg.get("sources") or ["gushiwen", "story"]
        random.shuffle(srcs)
        last = None
        for s in srcs:
            try:
                if s == "gushiwen":
                    return self._from_gushiwen()
                if s == "story":
                    return self._from_story()
            except Exception as exc:  # noqa: BLE001
                last = exc
                logger.info(f"[her_reading] 源 {s} 失败：{exc}")
        raise RuntimeError(str(last or "所有源都没成"))

    # ---------------- 读 + 写 ----------------
    async def _read_once(self, force: bool = False) -> tuple[bool, str]:
        cfg = self._cfg()
        if not cfg.get("enabled") and not force:
            return False, "已关闭"
        if not force:
            ok, why = _is_offpeak()
            if not ok:
                return False, f"{why}（DeepSeek 谷值才读书）"
        if not force and not self._in_window(cfg):
            return False, "不在时段"
        st = self._state()
        if not force and int(st.get("count", 0)) >= int(cfg.get("max_per_day", 2)):
            return False, "今天读够了"

        try:
            piece = await asyncio.to_thread(self._fetch_one, cfg)
        except Exception as exc:  # noqa: BLE001
            return False, f"没找到能读的：{exc}"

        prompt = (
            f"你刚读了一段东西：\n标题：{piece['title']}\n正文节选：\n{piece['text'][:1400]}\n\n"
            "用你自己的口吻写三行（每行单独一行）：\n"
            "第一行：摘一句原文里打动你的话（必须是原文里的，加引号）。\n"
            "第二行：你读到这儿想到什么（具体，可以吐槽）。\n"
            "第三行：这跟你自己有没有关系（比如像不像你、你会不会这样）。\n"
            "不要客套，别提 AI，总共不超过 110 字。"
        )
        text = await self._llm(prompt)
        if not text:
            text = f"读了《{piece['title']}》。\n有点意思，但说不上来。\n大概跟我没什么关系。"

        lines = [x.strip() for x in text.splitlines() if x.strip()]
        while len(lines) < 3:
            lines.append("")
        quote, feel, self_ref = lines[0], lines[1], lines[2]

        ROOM_TEXT.mkdir(parents=True, exist_ok=True)
        name = f"{datetime.now():%Y%m%d_%H%M%S}_读《{re.sub(r'[^\w\u4e00-\u9fff]+', '', piece['title'])[:12]}》.md"
        body = (f"# 读《{piece['title']}》\n\n"
                f"- 来源：{piece['source']}（{piece['url']}）\n"
                f"- 原文：{quote}\n\n{feel}\n\n**和我自己有关的：**{self_ref}\n\n"
                f"---\n\n原文节选：\n\n{piece['text'][:1200]}\n")
        (ROOM_TEXT / name).write_text(body, encoding="utf-8")

        # 入库：本地向量索引 + AstrBot 正式知识库
        self._kb_save(piece, quote, feel, self_ref)
        try:
            await self._kb_index_embed(piece["title"])
        except Exception:
            pass
        try:
            latest = sorted(KNOW_DIR.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True)[:1]
            if latest:
                doc_id = await self._kb_upload(latest[0])
                if doc_id:
                    self._kb_mark(latest[0].name, doc_id)
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[her_reading] 知识库同步失败：{exc}")

        ok = True
        if cfg.get("notify_qq", "3328480063"):
            ok = await self._send(f"读了篇《{piece['title']}》\n{quote}\n{feel}")

        rec = {"t": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
               "title": piece["title"], "source": piece["source"], "url": piece["url"],
               "quote": quote, "feel": feel, "self": self_ref, "file": name, "ok": ok}
        try:
            with LOG.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass
        st["count"] = int(st.get("count", 0)) + 1
        st["last"] = time.time()
        _save(STATE, st)
        logger.info(f"[her_reading] 读完了：{piece['title'][:30]}")
        return ok, piece["title"][:40]

    async def _llm(self, prompt: str) -> str:
        try:
            pm = self.context.provider_manager
            prov = None
            for pid in ("deepseek/deepseek-flash", "deepseek-flash"):
                try:
                    prov = await pm.get_provider_by_id(pid)
                    if prov is not None:
                        break
                except Exception:
                    continue
            if prov is None:
                return ""
            r = await prov.text_chat(prompt=prompt,
                                     system_prompt="你是大肥鱼，一只懒散但会认真读东西的鲸鱼娘。")
            return (r.completion_text or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[her_reading] LLM 失败：{exc}")
            return ""

    async def _send(self, text: str) -> bool:
        try:
            tok_file = ROOT / "secrets" / "onebot_token.txt"
            token = tok_file.read_text(encoding="utf-8").strip() if tok_file.exists() else ""
            if not token:
                return False
            qq = int(self._cfg().get("notify_qq", 0) or 0)
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.post("http://127.0.0.1:3000/send_private_msg",
                                 headers={"Authorization": f"Bearer {token}"},
                                 json={"user_id": qq, "message": text})
            return r.status_code == 200
        except Exception:
            return False


    # ---------------- 知识库 ----------------
    def _qwen_key(self) -> str:
        try:
            return (ROOT / "secrets" / "qwen_key.txt").read_text(encoding="utf-8").strip()
        except Exception:
            return ""

    async def _kb_embed(self, text: str) -> list[float] | None:
        """拿 DashScope 的 embedding（512 维）。失败就返回 None。"""
        key = self._qwen_key()
        if not key or not (text or "").strip():
            return None
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.post(EMBED_URL,
                                 headers={"Authorization": f"Bearer {key}"},
                                 json={"model": EMBED_MODEL, "input": [text[:2000]],
                                       "dimensions": EMBED_DIM})
                if r.status_code != 200:
                    logger.info(f"[her_reading] 嵌入失败 {r.status_code}: {r.text[:80]}")
                    return None
                return list(r.json()["data"][0]["embedding"])
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[her_reading] 嵌入异常：{exc}")
            return None

    def _kb_save(self, piece: dict, quote: str, feel: str, self_ref: str) -> None:
        """把读过的东西存成知识条目（md），并写进索引。"""
        try:
            KNOW_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now()
            name = f"{ts:%Y%m%d_%H%M%S}_{re.sub(r'[^\w\u4e00-\u9fff]+', '', piece['title'])[:16]}.md"
            path = KNOW_DIR / name
            body = (f"# {piece['title']}\n\n"
                    f"- 来源：{piece['source']}（{piece['url']}）\n"
                    f"- 读于：{ts:%Y-%m-%d %H:%M}\n\n"
                    f"## 原文摘录\n\n{piece['text'][:1500]}\n\n"
                    f"## 我当时想的\n\n{quote}\n\n{feel}\n\n{self_ref}\n")
            path.write_text(body, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[her_reading] 知识条目写盘失败：{exc}")
            return
        # 索引（向量在异步里算，这里只登记正文摘要，等下补 vec）
        try:
            with KB_INDEX.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "id": path.stem, "file": str(path.name), "title": piece["title"],
                    "t": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                    "summary": (quote + " " + feel)[:300],
                    "vec": None,
                }, ensure_ascii=False) + "\n")
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[her_reading] 知识索引写入失败：{exc}")

    async def _kb_index_embed(self, title: str) -> None:
        """给最新一条补上向量。"""
        try:
            rows = [json.loads(x) for x in KB_INDEX.read_text(encoding="utf-8").splitlines() if x.strip()]
        except Exception:
            return
        pending = [r for r in rows if r.get("vec") is None]
        if not pending:
            return
        for r in pending[-3:]:
            text = f"{r.get('title','')} {r.get('summary','')}"
            vec = await self._kb_embed(text)
            if vec:
                r["vec"] = vec
        try:
            with KB_INDEX.open("w", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        except Exception:
            pass

    @staticmethod
    def _cos(a: list[float], b: list[float]) -> float:
        try:
            import math

            num = sum(x * y for x, y in zip(a, b))
            na = math.sqrt(sum(x * x for x in a))
            nb = math.sqrt(sum(y * y for y in b))
            return num / (na * nb) if na and nb else 0.0
        except Exception:
            return 0.0

    @filter.on_llm_request()
    async def kb_recall(self, event: AstrMessageEvent, req):
        """聊天时，如果提到相关的，就想起来自己读过什么。"""
        cfg = self._cfg()
        if not cfg.get("kb_enable", True):
            return
        msg = (getattr(event, "message_str", "") or "").strip()
        if len(msg) < 6 or not KB_INDEX.exists():
            return
        try:
            vec = await self._kb_embed(msg)
            if not vec:
                return
            rows = [json.loads(x) for x in KB_INDEX.read_text(encoding="utf-8").splitlines() if x.strip()]
            scored = []
            for r in rows:
                if not r.get("vec"):
                    continue
                sim = self._cos(vec, r["vec"])
                if sim >= float(cfg.get("kb_min_sim", 0.30)):
                    scored.append((sim, r))
            if not scored:
                return
            scored.sort(key=lambda x: x[0], reverse=True)
            top = scored[: int(cfg.get("kb_top_k", 2))]
            block = ["\n\n【我以前读过、可能想得起来的东西】"]
            for sim, r in top:
                block.append(f"·《{r.get('title','')}》{(r.get('summary') or '')[:120]}")
            block.append("（这些是我自己看过的，可以说出来，但别背原文，也别硬提。）")
            req.system_prompt = (req.system_prompt or "") + "\n".join(block)
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[her_reading] 知识回想失败：{exc}")

    @filter.command("知识库")
    async def cmd_kb(self, event: AstrMessageEvent):
        """看看她读过多少、最近读了什么"""
        try:
            rows = [json.loads(x) for x in KB_INDEX.read_text(encoding="utf-8").splitlines() if x.strip()]
        except Exception:
            rows = []
        if not rows:
            yield event.plain_result("还没读过什么")
            return
        with_vec = sum(1 for r in rows if r.get("vec"))
        lines = [f"读过 {len(rows)} 篇（其中 {with_vec} 篇可被想起来）"]
        for r in rows[-5:][::-1]:
            lines.append(f"·《{r.get('title','')[:28]}》{str(r.get('t',''))[5:16]}")
        yield event.plain_result("\n".join(lines))


    # ---------------- AstrBot 正式知识库 ----------------
    async def _kb_astrbot(self):
        """拿到（或创建）她的知识库。"""
        try:
            mgr = getattr(self.context, "kb_manager", None)
            if mgr is None:
                return None
            kb = await mgr.get_kb_by_name(KB_NAME)
            if kb is not None:
                return kb
            kb = await mgr.create_kb(
                kb_name=KB_NAME,
                description="她自己读书看来的东西：原文摘录、当时的想法、和她的关系",
                emoji="📚",
                embedding_provider_id=KB_EMBED_PROVIDER,
            )
            logger.info(f"[her_reading] 新建知识库：{KB_NAME}")
            return kb
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[her_reading] 知识库不可用：{exc}")
            return None

    async def _kb_upload(self, path: pathlib.Path) -> str:
        """把一篇 md 上传进知识库，返回文档 id。"""
        kb = await self._kb_astrbot()
        if kb is None:
            return ""
        try:
            doc = await kb.upload_document(
                file_name=path.name,
                file_content=path.read_bytes(),
                file_type="md",
            )
            doc_id = str(getattr(doc, "doc_id", "") or getattr(doc, "id", "") or "")
            logger.info(f"[her_reading] 已进知识库：{path.name} doc={doc_id[:10]}")
            return doc_id
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[her_reading] 上传知识库失败：{exc}")
            return ""

    def _kb_mark(self, file_name: str, doc_id: str) -> None:
        try:
            rows = [json.loads(x) for x in KB_INDEX.read_text(encoding="utf-8").splitlines() if x.strip()]
        except Exception:
            return
        for r in rows:
            if r.get("file") == file_name:
                r["kb_doc"] = doc_id
        try:
            with KB_INDEX.open("w", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        except Exception:
            pass

    async def _kb_backfill(self) -> None:
        """把还没进正式知识库的条目补进去。"""
        try:
            rows = [json.loads(x) for x in KB_INDEX.read_text(encoding="utf-8").splitlines() if x.strip()]
        except Exception:
            return
        pending = [r for r in rows if not r.get("kb_doc")]
        for r in pending[-20:]:
            p = KNOW_DIR / str(r.get("file") or "")
            if not p.exists():
                continue
            doc_id = await self._kb_upload(p)
            if doc_id:
                self._kb_mark(p.name, doc_id)

    @filter.command("同步知识库")
    async def cmd_sync_kb(self, event: AstrMessageEvent):
        """把读过的东西都推进 AstrBot 知识库"""
        yield event.plain_result("同步中…")
        await self._kb_backfill()
        kb = await self._kb_astrbot()
        n = 0
        try:
            n = await kb.count_documents()
        except Exception:
            pass
        yield event.plain_result(f"知识库《{KB_NAME}》现在有 {n} 篇")

    # ---------------- 循环 / 命令 ----------------
    async def _loop(self) -> None:
        await asyncio.sleep(180)
        while True:
            cfg = self._cfg()
            await asyncio.sleep(random.randint(int(cfg.get("min_hours", 8)),
                                               int(cfg.get("max_hours", 14))) * 3600)
            try:
                ok, why = await self._read_once()
                if not ok:
                    logger.info(f"[her_reading] 这轮没读：{why}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.info(f"[her_reading] 出错：{exc}")

    @filter.command("读书")
    async def cmd_read(self, event: AstrMessageEvent, arg: str = ""):
        """让她现在读一篇"""
        yield event.plain_result("我去找点东西读…")
        ok, why = await self._read_once(force=True)
        if not ok:
            yield event.plain_result(f"没读成：{why}")

    @filter.command("读了什么")
    async def cmd_log(self, event: AstrMessageEvent):
        """看她最近读了什么"""
        rows = []
        try:
            for line in LOG.read_text(encoding="utf-8").splitlines()[-5:]:
                d = json.loads(line)
                rows.append(f"· 《{d.get('title', '')[:26]}》{d.get('feel', '')[:40]}")
        except Exception:
            pass
        yield event.plain_result("\n".join(rows) if rows else "还没读过什么")
