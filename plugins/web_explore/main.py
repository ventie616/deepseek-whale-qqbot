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

DEFAULTS = {
    "enabled": True,
    "target_qq": "3328480063",
    "min_minutes": 150,
    "max_minutes": 260,
    "active_hours": [[9, 23.5]],
    "notify": True,
    "max_per_day": 4,
    "seeds": ["AI 新模型", "独立游戏", "冷知识", "机械键盘", "鲸鱼", "合成器音乐"],
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
        if not force and not self._in_window(cfg):
            return False, "不在时段"
        st = self._state()
        if not force and int(st.get("count", 0)) >= int(cfg.get("max_per_day", 4)):
            return False, "今天够了"

        topics = self._topics(cfg)
        results: list[dict] = []
        used_topic = ""
        used_dir = ""
        for used_dir, topic in topics:
            try:
                rs = await asyncio.to_thread(self._search, topic, 5)
            except Exception as exc:  # noqa: BLE001
                logger.info(f"[web_explore] 搜索失败 {topic}: {exc}")
                continue
            rs = [r for r in rs
                  if r.get("url") and not any(bad in r["url"] for bad in BAD_SITES)
                  and (r.get("title") or "").strip()]
            if rs:
                used_topic = topic
                results = rs
                break
            logger.info(f"[web_explore] 「{topic}」没搜到能看的，换下一个")
        if not results:
            return False, "没搜到（或都是不想看的站）"

        # 让她自己挑
        listing = "\n".join(f"{i+1}. {r['title']}｜{r['snippet'][:90]}" for i, r in enumerate(results))
        pick_prompt = (
            f"你在网上看到这些关于「{used_topic}」的东西：\n{listing}\n\n"
            f"你是大肥鱼，一只嘴硬但其实好奇的鲸鱼娘。这些是在搜「{used_topic}」时出来的，"
            "挑一条**真的和这个话题有关、而且你愿意点开看**的，只回一个数字；"
            "如果跟「" + used_topic + "」无关，或者是法律文书/判决书/公告、商品页、广告、纯列表、标题看不懂，就回 0。"
        )
        raw = await self._llm(pick_prompt, "你是大肥鱼，回答只给数字。")
        idx = 0
        m = re.search(r"\d+", raw or "")
        if m:
            idx = int(m.group(0))
        if idx <= 0 or idx > len(results):
            return False, f"这次没看得上的（{used_topic}）"

        chosen = results[idx - 1]
        # 硬检查：标题/摘要里至少出现一个方向关键词，否则这轮放弃
        words = DIRECTION_WORDS.get(used_dir, ())
        blob = ((chosen.get("title") or "") + " " + (chosen.get("snippet") or "")).lower()
        if words and not any(w.lower() in blob for w in words):
            return False, f"跟「{used_dir}」不搭（{chosen.get('title','')[:20]}）"
        body = ""
        try:
            body = await asyncio.to_thread(self._extract, chosen["url"])
        except Exception:
            body = chosen.get("snippet", "")

        write_prompt = (
            f"你刚在网上看了这条内容：\n标题：{chosen['title']}\n内容：{body[:1200]}\n\n"
            "用你自己的口吻写两句话，必须写成两行（中间用换行分隔）：\n"
            "第一行：今天在网上看到什么（概括内容，别照抄标题）。\n"
            "第二行：你觉得有意思在哪（要具体，可以是吐槽）。\n"
            "不要链接，不要客套，总计不超过 80 字。"
        )
        text = await self._llm(write_prompt, "你是大肥鱼，说话懒散、嘴硬、偶尔吐槽。")
        if not text:
            text = f"今天在网上看到《{chosen['title']}》，还行。"
        lines = [x.strip() for x in text.splitlines() if x.strip()]
        if len(lines) < 2:
            parts = [x.strip() for x in re.split(r"(?<=[。！？])", text) if x.strip()]
            lines = parts[:2] if len(parts) >= 2 else lines
        line1 = lines[0] if lines else text
        line2 = lines[1] if len(lines) > 1 else ""

        if cfg.get("notify", True):
            ok = await self._send_qq(line1, line2, chosen)
        else:
            ok = True

        rec = {
            "t": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "topic": used_topic,
            "title": chosen["title"],
            "url": chosen["url"],
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
        logger.info(f"[web_explore] 看完了：{chosen['title'][:40]}（话题 {used_topic}）")
        return ok, f"{used_topic} / {chosen['title'][:40]}"

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
            f"时段 {cfg.get('active_hours')}"
        )
