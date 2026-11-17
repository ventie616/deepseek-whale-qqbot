"""她自己上网溜达：按兴趣找东西看，觉得有意思就告诉主人。

和 B站 刷视频是同一套思路，只是把来源换成了公开网络。
"""

from __future__ import annotations

import asyncio
import json
import ipaddress
import socket
import random
import re
import time
from datetime import date, datetime
from pathlib import Path

import httpx
from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Plain
import sys as _sys
_sys.path.insert(0, r'D:\QQrobot\tools')
from offpeak import is_offpeak as _is_offpeak
from delivered_shares import DeliveredShares
from persona_learning import (
    MAX_OPEN_TASKS, UNTRUSTED, LearningAgenda, LearningJournal, build_note,
    clean, parse_object, persona_core, public_url, wait_hours,
)

ROOT = Path(r"D:\QQrobot")
CFG = ROOT / "web_explore.json"
STATE = ROOT / "web_explore_state.json"
LOG = ROOT / "web_wander.jsonl"
INTEREST = ROOT / "interest_profile.json"
CHAT_ARCHIVE = ROOT / "chat_archive"
ANYSEARCH = "https://api.anysearch.com"

TOPIC_STOP = {
    "超标", "精彩集锦", "电子榨菜", "其他", "生活", "搞笑", "集锦", "推荐", "单机",
    "电子竞技", "MOBA", "asmr",
}
BAD_SITES = ("court.gov.cn", "wenshu", "chinacourt.org", "pkulaw", "lawinfochina",
             "taobao.com", "jd.com", "pinduoduo", "zhihu.com/question/0",
             "baike.baidu.com/item/0", "so.com", "hao123", "alipay", "1688.com")

# 她的兴趣方向：每个方向配几种"像人会在网上搜"的说法
DIRECTIONS = {
    "动漫": ["新番 推荐 评价", "老番 补番 推荐", "漫画 完结 推荐", "动画 制作 幕后"],
    "二次元": ["同人展 现场记录", "二次元 文化 现象", "cosplay 作品", "vtuber 趣事"],
    "游戏": ["独立游戏 推荐", "游戏 设计 分析", "新游 评测", "玩家 的故事"],
    "风景": ["绝景 摄影 地点", "自然 风光 记录", "徒步 国家公园"],
    "旅游": ["小众 旅行 目的地", "旅行 见闻 故事", "一个人 旅行 攻略"],
    "人文风俗": ["世界各地 奇怪 风俗", "当地 生活 记录", "节日 传统 由来"],
    "文学": ["作家 轶事", "小说 推荐 短评", "诗歌 赏析", "文学 冷知识"],
    "摄影": ["摄影 作品 赏析", "手机 摄影 技巧", "街头 摄影 故事"],
}
DIRECTION_WORDS = {
    "动漫": ("动漫", "动画", "新番", "漫画", "番剧", "acg", "动画"),
    "二次元": ("二次元", "cos", "同人", "vtuber", "虚拟主播", "漫展"),
    "游戏": ("游戏", "玩家", "独立游戏", "steam", "主機", "主机", "手游", "评测"),
    "风景": ("风景", "风光", "绝景", "山川", "湖", "海", "摄影", "景色"),
    "旅游": ("旅行", "旅游", "攻略", "目的地", "游记", "出发"),
    "人文风俗": ("风俗", "人文", "传统", "节日", "文化", "当地", "习俗"),
    "文学": ("文学", "作家", "小说", "诗", "散文", "阅读", "书"),
    "摄影": ("摄影", "镜头", "相机", "拍摄", "照片", "构图"),
}

SOURCES = {
    "weibo": "微博热搜",
    "tieba": "贴吧热榜",
    "juejin": "掘金",
    "bili": "B站热门",
    "search": "按兴趣搜",
}

DEFAULTS = {
    "enabled": True,
    "target_qq": "3328480063",
    "min_minutes": 150,
    "max_minutes": 260,
    "active_hours": [[9, 23.5]],
    "notify": True,
    "max_per_day": 4,
    "seeds": ["AI 新模型", "独立游戏", "冷知识", "机械键盘", "鲸鱼", "合成器音乐"],
    "sources": ["weibo", "tieba", "juejin", "bili", "search"],
    "min_score": 6,
    "batch": 1,
    "learning_enabled": True,
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


def _key() -> str:
    try:
        return json.loads((ROOT / "secrets" / "anysearch.json").read_text(encoding="utf-8"))["api_key"]
    except Exception:
        return ""


class WebExplore(star.Star):
    def __init__(self, context: star.Context) -> None:
        super().__init__(context)
        self._task: asyncio.Task | None = None
        self._learning_lock = asyncio.Lock()
        self._journal = LearningJournal()

    async def initialize(self) -> None:
        self._task = asyncio.create_task(self._loop())
        logger.info("[web_explore] 已启动：自己上网找东西看")

    async def terminate(self) -> None:
        if self._task:
            self._task.cancel()

    # ---------------- 配置 / 状态 ----------------
    def _cfg(self) -> dict:
        cfg = dict(DEFAULTS)
        cfg.update(_load(CFG, {}) or {})
        return cfg

    def _state(self) -> dict:
        st = _load(STATE, {}) or {}
        today = date.today().isoformat()
        if st.get("date") != today:
            st = {"date": today, "count": 0, "last": 0}
        return st

    def _in_window(self, cfg: dict) -> bool:
        now = datetime.now()
        hour = now.hour + now.minute / 60
        for start, end in cfg.get("active_hours") or [[0, 24]]:
            if start <= hour < end:
                return True
        return False

    # ---------------- AnySearch ----------------
    def _search(self, query: str, limit: int = 5) -> list[dict]:
        headers = {"Content-Type": "application/json"}
        key = _key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        with httpx.Client(timeout=25) as c:
            r = c.post(f"{ANYSEARCH}/v1/search", headers=headers,
                       json={"query": query, "max_results": limit, "language": "zh-CN"})
            r.raise_for_status()
            d = r.json()
        if d.get("code") != 0:
            raise RuntimeError("搜索服务返回错误")
        out = []
        for it in ((d.get("data") or {}).get("results") or [])[:limit]:
            out.append({"title": it.get("title") or "", "url": it.get("url") or "",
                        "snippet": it.get("snippet") or it.get("content") or ""})
        return out

    def _extract(self, url: str, limit: int = 1500) -> str:
        headers = {"Content-Type": "application/json"}
        key = _key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        try:
            with httpx.Client(timeout=20) as client:
                response = client.post(
                    f"{ANYSEARCH}/v1/extract", headers=headers, json={"url": url}
                )
                response.raise_for_status()
                result = response.json()
            if result.get("code") != 0:
                raise ValueError("正文服务返回错误")
            data = result.get("data") or {}
            body = data.get("content") or data.get("markdown") or ""
            if len(clean(body, limit)) >= 100:
                return body[:limit]
        except (httpx.HTTPError, ValueError, TypeError):
            logger.info("[web_explore] 正文服务未提取到内容，尝试直接读网页")
        return self._fetch_page(url, limit)

    def _fetch_page(self, url: str, limit: int) -> str:
        from bs4 import BeautifulSoup

        # Validate every redirect before contacting a new destination.
        with httpx.Client(timeout=12, follow_redirects=False, headers=self.UA) as client:
            for _ in range(4):
                url = public_url(url)
                if not url:
                    raise ValueError("正文链接不是公开网页")
                host = httpx.URL(url).host
                addresses = socket.getaddrinfo(host, None)
                if not addresses or any(
                    not ipaddress.ip_address(address[4][0]).is_global for address in addresses
                ):
                    raise ValueError("正文链接指向内网")
                with client.stream("GET", url) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise ValueError("网页跳转没有目标")
                        url = str(response.url.join(location))
                        continue
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").lower()
                    if not any(kind in content_type for kind in ("text/html", "application/xhtml", "text/plain")):
                        raise ValueError("链接不是网页正文")
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > 2 * 1024 * 1024:
                            raise ValueError("网页正文过大")
                        chunks.append(chunk)
                    raw = b"".join(chunks)
                    if "text/plain" in content_type:
                        return raw.decode(response.encoding or "utf-8", errors="replace")[:limit]
                    soup = BeautifulSoup(raw, "lxml")
                    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "form", "aside"]):
                        tag.decompose()
                    main = soup.find("article") or soup.find("main") or soup.body or soup
                    return main.get_text(" ", strip=True)[:limit]
        raise ValueError("网页跳转过多")

    def _learning_sources(self, question: str, known_urls: set) -> tuple[list[dict], str]:
        from ..web_access.main import WebAccess

        sources, visited = [], set()
        errors = {"search": 0, "extract": 0, "short": 0, "filtered": 0}
        searches = (self._search, lambda q, n: WebAccess._bing(self, q, n))
        for search in searches:
            try:
                candidates = search(question, 7)
            except Exception as exc:
                errors["search"] += 1
                logger.info(f"[web_explore] 搜索失败：{type(exc).__name__}")
                continue
            attempts = 0
            for candidate in candidates:
                url = public_url(candidate.get("url", ""))
                parsed = httpx.URL(url) if url else None
                suffix = parsed.path.lower() if parsed else ""
                host = parsed.host if parsed else ""
                video_page = (
                    host in ("youtu.be", "youtube.com", "www.youtube.com", "m.youtube.com")
                    or (host.endswith("bilibili.com") and suffix.startswith("/video/"))
                )
                if (not url or video_page or url in visited or url in known_urls
                        or any(site in url for site in BAD_SITES)
                        or suffix.endswith((".pdf", ".doc", ".docx", ".mp4", ".mp3"))):
                    errors["filtered"] += 1
                    continue
                visited.add(url)
                attempts += 1
                try:
                    body = clean(self._extract(url, 6000), 5500)
                except Exception as exc:
                    errors["extract"] += 1
                    logger.info(f"[web_explore] 正文读取失败：{type(exc).__name__}")
                    body = ""
                if len(body) >= 100:
                    sources.append({
                        "title": clean(candidate.get("title"), 100) or url, "url": url, "body": body
                    })
                elif body:
                    errors["short"] += 1
                if len(sources) == 2:
                    return sources, ""
                if attempts >= 4:
                    break
            if sources:
                return sources, ""
        labels = {"search": "搜索失败", "extract": "正文读取失败", "short": "正文过短", "filtered": "已过滤链接"}
        counts = "、".join(f"{labels[key]} {value}" for key, value in errors.items() if value)
        return [], f"正文尚未取到（{counts or '搜索结果为空'}），留待下次再查"


    # ---------------- 多源抓取 ----------------
    UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"}

    def _get(self, url: str, data: bytes | None = None, headers: dict | None = None, timeout: int = 20) -> bytes:
        import urllib.request

        req = urllib.request.Request(url, data=data, headers={**self.UA, **(headers or {})})
        return urllib.request.urlopen(req, timeout=timeout).read()

    def _weibo_hot(self, limit: int = 15) -> list[dict]:
        """微博热搜（移动端接口，不需要登录）。"""
        try:
            d = json.loads(self._get("https://weibo.com/ajax/side/hotSearch",
                                     headers={"Referer": "https://weibo.com/"}))
            out = []
            for it in ((d.get("data") or {}).get("realtime") or [])[:limit]:
                word = str(it.get("word") or "").strip()
                if not word:
                    continue
                out.append({
                    "source": "weibo", "title": word,
                    "snippet": str(it.get("note") or it.get("label_name") or "")[:80],
                    "url": f"https://s.weibo.com/weibo?q={word}",
                    "extra": f"热度 {it.get('num', '')}",
                })
            return out
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[web_explore] 微博热搜失败：{exc}")
            return []


    def _tieba_hot(self, limit: int = 15) -> list[dict]:
        """百度贴吧热榜（JSON，带话题简介）。"""
        import html as _html

        try:
            d = json.loads(self._get("https://tieba.baidu.com/hottopic/browse/topicList",
                                     headers={"Referer": "https://tieba.baidu.com/"}))
            topics = (((d.get("data") or {}).get("bang_topic") or {}).get("topic_list")) or []
            out = []
            for tp in topics[:limit]:
                name = str(tp.get("topic_name") or "").strip()
                if not name:
                    continue
                url = _html.unescape(str(tp.get("topic_url") or ""))
                out.append({
                    "source": "tieba", "title": name,
                    "snippet": str(tp.get("topic_desc") or tp.get("abstract") or "")[:110],
                    "url": url,
                    "extra": f"讨论 {tp.get('discuss_num', 0)}",
                })
            return out
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[web_explore] 贴吧失败：{exc}")
            return []

    def _juejin(self, limit: int = 12) -> list[dict]:
        """掘金推荐流。"""
        try:
            body = json.dumps({"id_type": 2, "client_type": 2608, "sort_type": 200,
                               "cursor": "0", "limit": limit}).encode()
            d = json.loads(self._get("https://api.juejin.cn/recommend_api/v1/article/recommend_all_feed",
                                     data=body, headers={"Content-Type": "application/json"}))
            out = []
            for it in (d.get("data") or [])[:limit]:
                info = ((it.get("item_info") or {}).get("article_info") or {})
                title = str(info.get("title") or "").strip()
                if not title:
                    continue
                out.append({
                    "source": "juejin", "title": title,
                    "snippet": str(info.get("brief_content") or "")[:90],
                    "url": f"https://juejin.cn/post/{info.get('article_id')}",
                    "extra": f"阅读 {info.get('view_count', 0)}",
                })
            return out
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[web_explore] 掘金失败：{exc}")
            return []

    def _bili_hot(self, limit: int = 15) -> list[dict]:
        """B站热门（综合）。"""
        try:
            acc = json.loads((ROOT / "bili_account.json").read_text(encoding="utf-8"))
            cookie = f"SESSDATA={acc.get('sessdata','')}; bili_jct={acc.get('bili_jct','')}; DedeUserID={acc.get('dedeuserid','')}"
            d = json.loads(self._get("https://api.bilibili.com/x/web-interface/popular?ps=30&pn=1",
                                     headers={"Referer": "https://www.bilibili.com/", "Cookie": cookie}))
            out = []
            for v in ((d.get("data") or {}).get("list") or [])[:limit]:
                out.append({
                    "source": "bili", "title": str(v.get("title") or ""),
                    "snippet": str(v.get("desc") or "")[:90],
                    "url": f"https://www.bilibili.com/video/{v.get('bvid')}",
                    "extra": f"{v.get('tname','')} · 播放 {(v.get('stat') or {}).get('view', 0)}",
                })
            return out
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[web_explore] B站热门失败：{exc}")
            return []

    def _search_pool(self, cfg: dict) -> list[dict]:
        """沿兴趣方向搜（沿用原来的 AnySearch）。"""
        out = []
        for used_dir, topic in self._topics(cfg)[:3]:
            try:
                rs = self._search(topic, 5)
            except Exception:
                continue
            for r in rs:
                if not r.get("url") or any(bad in r["url"] for bad in BAD_SITES):
                    continue
                out.append({
                    "source": "search", "title": r.get("title") or "",
                    "snippet": (r.get("snippet") or "")[:90],
                    "url": r["url"], "extra": topic, "dir": used_dir,
                })
            if out:
                break
        return out

    # ---------------- LLM ----------------
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
            logger.info(f"[web_explore] LLM 失败：{exc}")
            return ""

    # ---------------- 选题 ----------------
    def _topics(self, cfg: dict) -> list[tuple[str, str]]:
        """返回 [(方向, 搜索词)]，优先用主人指定的兴趣方向。"""
        dirs = list(cfg.get("directions") or DIRECTIONS.keys())
        random.shuffle(dirs)
        out: list[tuple[str, str]] = []
        for d in dirs[:4]:
            variants = DIRECTIONS.get(d) or [""]
            v = random.choice(variants)
            out.append((d, f"{d} {v}".strip()))
        for seed in (cfg.get("seeds") or []):
            out.append(("杂项", str(seed)))
        return out or [("动漫", "动漫 新番 推荐")]

    async def _plan_learning(self, cfg, agenda, recent, core) -> tuple[dict | None, str]:
        now = time.time()
        options = [
            {"number": i, "topic": item["topic"], "question": item["question"],
             "why": item["why"], "last_reason": item.get("reason", ""),
             "status": item["status"], "available": item["due"] <= now}
            for i, item in enumerate(agenda.tasks, 1)
        ]
        prompt = (
            "你有一张「未完的事」清单。想一想现在愿意做什么，选一项行动。"
            "follow 继续查一项 available=true 的问题。start 开一个具体的新问题，须有空位。"
            "park 把一项暂时搁置，写下想过几小时再看。drop 收起一项不想再追的事，写下缘由。"
            "rest 这轮先歇着。按兴趣、上次卡住的地方和这次想知道什么来选。"
            "reason 写你自己的理由，question 仅 start 时填，number 仅选择已有事项时填。"
            '只输出 JSON：{"action":"follow|start|park|drop|rest","number":1,'
            '"question":"","reason":"","hours":48}。\n'
            + json.dumps({
                "tasks": options,
                "slots_left": MAX_OPEN_TASKS - len(options),
                "directions": self._topics(cfg),
                "recent": [n["question"] for n in recent[:8]],
            }, ensure_ascii=False)
        )
        if len(options) == MAX_OPEN_TASKS and not agenda.due(now):
            prompt = "现在清单已满，且都还没到续查时间。可以放下一个失去兴趣的事项，或歇一轮。\n" + prompt
        raw = parse_object(await self._llm(prompt, core + "\n" + UNTRUSTED))
        action, number = raw.get("action"), raw.get("number")
        reason = clean(raw.get("reason"), 120)
        task = agenda.tasks[number - 1] if type(number) is int and 1 <= number <= len(options) else None

        if action == "start" and len(options) < MAX_OPEN_TASKS and reason:
            question = clean(raw.get("question"), 95)
            previous = [note["question"] for note in recent] + [item["question"] for item in agenda.tasks]
            if question and question.rstrip("？?") not in {q.rstrip("？?") for q in previous}:
                new_task = agenda.add(question, reason, now)
                agenda.save()
                return new_task, ""
        elif action == "follow" and task is not None and task["due"] <= now and reason:
            task["status"] = "open"
            agenda.record("resume", reason, now, task)
            agenda.save()
            return task, ""
        elif action == "park" and task is not None and reason:
            agenda.park(task, reason, now, wait_hours(raw.get("hours"), 48))
            agenda.save()
            return None, f"先搁一下「{task['topic']}」。{reason}"
        elif action == "drop" and task is not None and reason:
            agenda.finish(task, "drop", reason, now)
            agenda.save()
            return None, f"把「{task['topic']}」收起来了。{reason}"
        elif action == "rest" and reason:
            agenda.record("rest", reason, now)
            agenda.save()
            return None, reason
        return None, "这轮还没挑好要做的事"

    async def _learn_once(self, cfg: dict, state: dict, force: bool) -> tuple[bool, str]:
        core = persona_core()
        recent = self._journal.recent()
        agenda = LearningAgenda()
        agenda.reconcile(recent)
        if agenda.is_new and recent and recent[0].get("next_question"):
            agenda.add(recent[0]["next_question"], "接着上一次没查完的问题", time.time(), recent[0])
            agenda.save()
        task, decision = await self._plan_learning(cfg, agenda, recent, core)
        if task is None:
            return True, decision
        question = task["question"]
        note_id = task.get("last_note")
        parent = self._journal.get(note_id) or ({"id": note_id} if note_id else {})

        def retry(reason: str) -> tuple[bool, str]:
            now = time.time()
            task["failures"] = int(task.get("failures", 0)) + 1
            if task["failures"] >= 2:
                agenda.park(task, reason, now, 48)
            else:
                task.update(due=now + 6 * 3600, reason=reason)
                agenda.record("retry", reason, now, task)
            agenda.save()
            return False, reason
        known_urls = {fact.get("url") for note in recent for fact in note["facts"]}
        sources, failure = await asyncio.to_thread(self._learning_sources, question, known_urls)
        if not sources:
            return retry(failure)

        prompt = (
            "根据正文回答这个问题。每条 claim 必须能从对应正文得到支持，"
            "evidence 必须是正文中连续的原话。多篇不一致就保留差异。"
            "thought 写你自己的简短想法。只有新资料真的改变了之前看法，才写 revision，"
            "说清改了什么和为什么。还有想追的具体问题再填 next_question，否则用空串。"
            "keywords 填聊天时能命中这个主题的具体词组。决策 decision 可选 continue（有后续问题）、"
            "park（暂时放下）或 done（已经查清或想收尾）。reason 写决策理由，hours 写希望过几小时再接着查。"
            '只输出 JSON：{"facts":[{"source":1,"claim":"","evidence":""}],'
            '"thought":"","revision":"","next_question":"","keywords":[""],'
            '"decision":"continue|park|done","reason":"","hours":6}。\n'
            + json.dumps({
                "question": question,
                "previous": {"question": parent.get("question"), "thought": parent.get("thought")},
                "sources": [dict(number=i, **s) for i, s in enumerate(sources, 1)],
            }, ensure_ascii=False)
        )
        raw = parse_object(await self._llm(prompt, core + "\n" + UNTRUSTED))
        note = build_note(question, parent, sources, raw)
        if not note:
            return retry("正文里没有核对到可记录的摘录")
        outcome = raw.get("decision")
        if outcome not in ("park", "done"):
            outcome = "continue" if note["next_question"] else "done"
        reason = clean(raw.get("reason"), 120) or {
            "park": "这个问题过段时间再看",
            "continue": "还有一件事没弄清",
            "done": "这轮没有留下要续查的问题",
        }[outcome]
        if outcome == "done":
            note["next_question"] = ""
        note["agenda"] = {"task_id": task["id"], "status": outcome, "reason": reason,
                          "at": time.time(),
                          "hours": wait_hours(raw.get("hours"), 48 if outcome == "park" else 6)}
        self._journal.save(note)
        agenda.apply_note(note)
        agenda.save()
        state.update(count=int(state.get("count", 0)) + 1, last=time.time())
        _save(STATE, state)

        first = note["facts"][0]
        report = f"这轮查了「{question}」\n{note['thought']}"
        if note["revision"]:
            report += f"\n{note['revision']}"
        if note["next_question"]:
            report += f"\n还想查：{note['next_question']}"
        report += f"\n{reason}"
        sent = True
        if cfg.get("notify", True) and not force:
            sent = await self._send_qq(
                f"刚去查了「{question}」", note["thought"],
                {"title": question, "url": first["url"], "learning_id": note["id"]},
            )
        try:
            row = {"t": note["read_at"], "source": "search", "title": first["title"],
                   "url": first["url"], "line1": question, "line2": note["thought"],
                   "ok": sent, "learning_id": note["id"]}
            with LOG.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.info(f"[web_explore] 浏览日志写入失败：{type(exc).__name__}")
        return True, report

    @filter.on_llm_request()
    async def inject_learning_context(self, event: AstrMessageEvent, req) -> None:
        if not self._cfg().get("learning_enabled", True):
            return
        current = getattr(req, "system_prompt", "") or ""
        if "大肥鱼" not in current or "鲸鱼娘" not in current:
            return
        req.system_prompt = current + "\n\n" + persona_core()
        note = self._journal.recall(event.get_message_str() or "")
        if not note:
            return
        lines = ["【这轮可能有关的旧阅读笔记】", f"当时查的问题：{note['question']}"]
        for fact in note["facts"][:2]:
            lines.append(f"来源《{fact['title']}》的说法：{fact['claim']}（{fact['url']}）")
        lines.append(f"她当时的想法：{note['thought']}")
        if note.get("revision"):
            lines.append(f"后来修正：{note['revision']}")
        lines.append("只在与当前问题直接相关时使用。来源说法和个人想法分开表达。" + UNTRUSTED)
        req.system_prompt += "\n\n" + "\n".join(lines)
    async def _one_wander(self, force: bool = False) -> tuple[bool, str]:
        cfg = self._cfg()
        if not cfg.get("enabled") and not force:
            return False, "已关闭"
        if not force:
            ok, why = _is_offpeak()
            if not ok:
                return False, f"{why}（DeepSeek 谷值才上网）"
        if not force and not self._in_window(cfg):
            return False, "不在时段"
        st = self._state()
        if not force and int(st.get("count", 0)) >= int(cfg.get("max_per_day", 4)):
            return False, "今天够了"

        if cfg.get("learning_enabled", True):
            if self._learning_lock.locked():
                return False, "上一轮还在读"
            async with self._learning_lock:
                try:
                    return await self._learn_once(cfg, st, force)
                except (OSError, ValueError, httpx.HTTPError) as exc:
                    logger.info(f"[web_explore] 学习未完成：{type(exc).__name__}")
                    return False, "这轮查询或保存失败"

        # 从启用的源里随机挑 2~3 个，凑一个候选池
        enabled = [s for s in (cfg.get("sources") or list(SOURCES)) if s in SOURCES]
        random.shuffle(enabled)
        picked_sources = enabled[:random.randint(2, 3)]
        pool: list[dict] = []
        for name in picked_sources:
            try:
                if name == "weibo":
                    pool += await asyncio.to_thread(self._weibo_hot)
                elif name == "tieba":
                    pool += await asyncio.to_thread(self._tieba_hot)
                elif name == "juejin":
                    pool += await asyncio.to_thread(self._juejin)
                elif name == "bili":
                    pool += await asyncio.to_thread(self._bili_hot)
                elif name == "search":
                    pool += await asyncio.to_thread(self._search_pool, cfg)
            except Exception as exc:  # noqa: BLE001
                logger.info(f"[web_explore] 源 {name} 出错：{exc}")
        pool = [x for x in pool if (x.get("title") or "").strip()]
        if not pool:
            return False, "所有源都没东西"

        # 去重 + 限制长度
        seen, uniq = set(), []
        for x in pool:
            k = re.sub(r"\s+", "", x["title"])[:24]
            if k in seen:
                continue
            seen.add(k)
            uniq.append(x)
        pool = uniq[:36]

        listing = "\n".join(
            f"{i+1}. [{SOURCES.get(x['source'], x['source'])}] {x['title'][:46]}｜{x['snippet'][:60]}"
            for i, x in enumerate(pool)
        )
        scoring = (
            f"你在网上逛，看到下面这些东西：\n{listing}\n\n"
            "你是大肥鱼，一只嘴硬但其实很好奇的鲸鱼娘。平时爱看：动漫、二次元、游戏、风景、旅行、"
            "人文风俗、文学、摄影，偶尔也刷科技和怪新闻。\n"
            "选一条**你自己真的会点进去看**的，输出两行：\n"
            "第一行：编号（只写数字，都不想看就写 0）\n"
            "第二行：0-10 的兴趣分（10 表示非常想看）"
        )
        raw = await self._llm(scoring, "你是大肥鱼，严格按格式输出，只给编号和分数。")
        idx, score = 0, 0
        nums = re.findall(r"\d+", raw or "")
        if len(nums) >= 1:
            idx = int(nums[0])
        if len(nums) >= 2:
            score = int(nums[1])
        elif nums:
            score = 7
        if idx <= 0 or idx > len(pool):
            return False, "这次都不太想看"
        chosen = pool[idx - 1]
        if score < int(cfg.get("min_score", 6)) and not force:
            return False, f"兴趣分不够（{score}/10）：{chosen['title'][:24]}"

        body = ""
        # 微博热搜只有词条，先搜一条有正文的报道/帖子
        if chosen.get("source") == "weibo":
            try:
                rs = await asyncio.to_thread(self._search, chosen["title"], 4)
                rs = [r for r in rs if r.get("url") and not any(b in r["url"] for b in BAD_SITES)]
                if rs:
                    chosen["url"] = rs[0]["url"]
                    chosen["extra"] = f"热搜 · {chosen.get('extra', '')}".strip(" ·")
                    # 先把几条摘要拼起来（微博热点常常没有可读正文）
                    body = "\n".join(
                        f"· {r['title'][:40]}：{(r.get('snippet') or '')[:120]}" for r in rs[:3]
                    ) + "\n"
            except Exception as exc:  # noqa: BLE001
                logger.info(f"[web_explore] 微博内容补抓失败：{exc}")
        try:
            more = await asyncio.to_thread(self._extract, chosen["url"], 900)
            if more and len(more) > 80:
                body = (body + "\n" + more)[:1400]
        except Exception:
            pass
        if not body.strip():
            body = chosen.get("snippet", "")

        write_prompt = (
            f"你刚在网上刷到这条：\n来源：{SOURCES.get(chosen['source'], chosen['source'])}\n"
            f"标题：{chosen['title']}\n补充：{chosen.get('extra','')}\n内容：{body[:900]}\n\n"
            "用你自己的口吻写两行（中间换行）：\n"
            "第一行：你看到了什么（具体，别照抄标题）。\n"
            "第二行：你怎么想（可以吐槽、可以联想，别客套）。\n"
            "不要链接，不要「作为一个AI」，总共不超过 80 字。"
        )
        text = await self._llm(write_prompt, "你是大肥鱼，说话懒散、嘴硬、偶尔吐槽，但真心好奇。")
        if not text:
            text = f"刚刷到《{chosen['title']}》。\n有点意思，说不上来哪有意思。"
        lines = [x.strip() for x in text.splitlines() if x.strip()]
        if len(lines) < 2:
            parts = [x.strip() for x in re.split(r"(?<=[。！？])", text) if x.strip()]
            lines = parts[:2] if len(parts) >= 2 else lines
        line1 = lines[0] if lines else text
        line2 = lines[1] if len(lines) > 1 else ""

        ok = True
        if cfg.get("notify", True):
            ok = await self._send_qq(line1, line2, chosen)

        # 特别喜欢的（8 分以上）顺手存进她的房间
        if score >= 8:
            try:
                self._keep_in_room(chosen, score, line1, line2)
            except Exception as exc:  # noqa: BLE001
                logger.info(f"[web_explore] 存房间失败：{exc}")

        rec = {
            "t": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "source": chosen["source"],
            "title": chosen["title"],
            "url": chosen["url"],
            "score": score,
            "line1": line1,
            "line2": line2,
            "ok": ok,
        }
        try:
            with LOG.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass

        st["count"] = int(st.get("count", 0)) + 1
        st["last"] = time.time()
        _save(STATE, st)
        logger.info(f"[web_explore] 看完了：{chosen['title'][:40]}（{chosen['source']} {score}/10）")
        return ok, f"{SOURCES.get(chosen['source'], chosen['source'])} / {chosen['title'][:36]}"


    def _keep_in_room(self, item: dict, score: int, line1: str, line2: str) -> None:
        """把 8 分以上的内容记进 her_room/links.jsonl。"""
        room = ROOT / "her_room"
        room.mkdir(exist_ok=True)
        rec = {
            "t": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "url": item.get("url", ""),
            "title": item.get("title", ""),
            "note": f"{line1} / {line2}".strip(" /"),
            "score": score,
            "source": item.get("source", ""),
        }
        try:
            with (room / "links.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass

    # ---------------- 发送 ----------------
    def _umo(self, qq: str) -> str | None:
        try:
            for platform in self.context.platform_manager.platform_insts:
                meta = platform.meta()
                if meta.name == "aiocqhttp":
                    return f"{meta.id}:FriendMessage:{qq}"
        except Exception:
            return None
        return None

    async def _send_qq(self, line1: str, line2: str, item: dict) -> bool:
        cfg = self._cfg()
        qq = str(cfg.get("target_qq") or "").strip()
        umo = self._umo(qq) if qq else None
        if not umo:
            return False
        text = line1 if not line2 else f"{line1}\n{line2}"
        chain = MessageChain([Plain(text)])
        try:
            ok = bool(await self.context.send_message(umo, chain))
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[web_explore] 发送失败：{exc}")
            return False
        if ok:
            self._archive(text)
            try:
                DeliveredShares().record(umo, text, "网上", item)
            except (OSError, ValueError) as exc:
                logger.warning(f"[web_explore] 已发送分享记忆写入失败：{type(exc).__name__}")
        return ok

    def _archive(self, text: str) -> None:
        """写进聊天存档，免得她以后忘了自己看过。"""
        try:
            qq = str(self._cfg().get("target_qq") or "")
            umo = self._umo(qq) or f"unknown_FriendMessage_{qq}"
            d = CHAT_ARCHIVE / re.sub(r"[^\w\-]+", "_", umo).strip("_")[:80]
            d.mkdir(parents=True, exist_ok=True)
            rec = {"t": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "role": "bot",
                   "uid": "self", "name": "本鲸", "text": f"[网上] {text}".strip()}
            with (d / f"{datetime.now():%Y-%m-%d}.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass

    # ---------------- 循环 ----------------
    async def _loop(self) -> None:
        await asyncio.sleep(90)
        while True:
            cfg = self._cfg()
            delay = random.randint(int(cfg.get("min_minutes", 150)),
                                   int(cfg.get("max_minutes", 260))) * 60
            await asyncio.sleep(delay)
            try:
                ok, why = await self._one_wander()
                if not ok:
                    logger.info(f"[web_explore] 这轮没去：{why}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.info(f"[web_explore] 异常：{exc}")

    # ---------------- 指令 ----------------
    @filter.command("上网看看")
    async def cmd_wander(self, event: AstrMessageEvent):
        """立刻自己上网看一眼"""
        yield event.plain_result("我去翻翻…")
        ok, why = await self._one_wander(force=True)
        if ok and self._cfg().get("learning_enabled", True):
            yield event.plain_result(why)
        elif not ok:
            yield event.plain_result(f"没看成：{why}")

    @filter.command("上网计划")
    async def cmd_agenda(self, event: AstrMessageEvent):
        """查看她自己选的事情和最近的决定。"""
        try:
            agenda = LearningAgenda()
        except (OSError, ValueError):
            yield event.plain_result("学习清单暂时读不了")
            return
        labels = {"start": "开始", "resume": "续查", "follow": "等下次", "park": "搁置",
                  "retry": "待重试", "done": "收尾", "drop": "放下", "rest": "休息"}
        lines = ["未完的事"]
        for item in agenda.tasks:
            due = datetime.fromtimestamp(item["due"]).strftime("%m-%d %H:%M")
            status = "搁置" if item["status"] == "parked" else "待查"
            lines.append(f"{status} · {item['question']}（{due} 后可查）")
        if not agenda.tasks:
            lines.append("暂时没有")
        if agenda.history:
            lines.append("最近的决定")
            for entry in agenda.history[-5:][::-1]:
                topic = f"《{entry['topic']}》" if entry["topic"] else ""
                lines.append(f"{labels.get(entry['action'], entry['action'])}{topic} · {entry['reason']}")
        yield event.plain_result("\n".join(lines))
    @filter.command("上网")
    async def cmd_status(self, event: AstrMessageEvent, arg: str = ""):
        """上网状态：/上网 [开|关|状态]"""
        cfg = self._cfg()
        a = (arg or "").strip().lower()
        if a in ("off", "关", "关闭"):
            cfg["enabled"] = False
            _save(CFG, cfg)
            yield event.plain_result("上网关了")
            return
        if a in ("on", "开", "开启"):
            cfg["enabled"] = True
            _save(CFG, cfg)
            yield event.plain_result("上网开了")
            return
        st = self._state()
        yield event.plain_result(
            f"上网：{'开' if cfg.get('enabled') else '关'}\n"
            f"今天看了 {st.get('count', 0)}/{cfg.get('max_per_day')} 次\n"
            f"间隔 {cfg.get('min_minutes')}–{cfg.get('max_minutes')} 分钟\n"
            f"时段 {cfg.get('active_hours')}\n"
            f"来源 {'、'.join(SOURCES.get(s, s) for s in (cfg.get('sources') or []))}"
        )
