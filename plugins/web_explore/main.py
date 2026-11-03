"""她自己上网溜达：按兴趣找东西看，觉得有意思就告诉主人。

和 B站 刷视频是同一套思路，只是把来源换成了公开网络。
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
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Plain
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
                       json={"query": query, "max_results": limit})
            r.raise_for_status()
            d = r.json()
        out = []
        for it in ((d.get("data") or {}).get("results") or [])[:limit]:
            out.append({"title": it.get("title") or "", "url": it.get("url") or "",
                        "snippet": it.get("snippet") or ""})
        return out

    def _extract(self, url: str, limit: int = 1500) -> str:
        headers = {"Content-Type": "application/json"}
        key = _key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        with httpx.Client(timeout=30) as c:
            r = c.post(f"{ANYSEARCH}/v1/extract", headers=headers, json={"url": url})
            r.raise_for_status()
            d = r.json()
        return ((d.get("data") or {}).get("content") or "")[:limit]


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
        if not ok:
            yield event.plain_result(f"没看成：{why}")

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
