"""B 站浏览工具：搜索、视频详情、热门、排行、UP 主、推荐流、观看历史、我的账号。

- 匿名可用：搜索 / 详情 / 热门 / 排行 / UP 主 / 推荐流
- 需要登录：观看历史、我的账号（在 C:\\QQBot\\bili_account.json 里配 cookie）
- 搜索和 WBI 接口需要 buvid3，启动后自动访问首页预热
"""

import asyncio
import hashlib
import http.cookiejar
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, filter

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
TIMEOUT = 12
BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
COOKIE_FILE = Path(r"C:\QQBot\bili_account.json")

# WBI 混淆表
MIXIN_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]

_jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_jar))
_warmed = False
_login_loaded = False
_wbi_cache: tuple[float, str] | None = None

_cache: dict[str, tuple[float, str]] = {}
CACHE_TTL = 300


# ---------------- cookie / 登录态 ----------------
def _add_cookie(name: str, value: str) -> None:
    _jar.set_cookie(
        http.cookiejar.Cookie(
            0, name, value, None, False, ".bilibili.com", True, True,
            "/", True, False, None, False, None, None, {}, False,
        )
    )


def _load_login() -> bool:
    """把 bili_account.json 里的 cookie 装进 jar，返回是否处于登录态。"""
    global _login_loaded
    if _login_loaded:
        return bool(_jar._cookies)  # noqa: SLF001
    _login_loaded = True
    try:
        data = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return False
    for cookie_name, key in (
        ("SESSDATA", "sessdata"),
        ("bili_jct", "bili_jct"),
        ("DedeUserID", "dedeuserid"),
        ("buvid3", "buvid3"),
    ):
        value = str(data.get(key) or "").strip()
        if value:
            _add_cookie(cookie_name, value)
    return bool(str(data.get("sessdata") or "").strip())


def _warm(force: bool = False) -> None:
    global _warmed
    _load_login()
    if _warmed and not force:
        return
    try:
        req = urllib.request.Request(
            "https://www.bilibili.com/", headers={"User-Agent": UA}
        )
        with _opener.open(req, timeout=TIMEOUT) as resp:
            resp.read(2048)
    except Exception:
        pass
    _warmed = True


def _get(url: str, referer: str = "https://www.bilibili.com/") -> dict:
    _warm()
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Referer": referer,
            "Accept": "application/json, text/plain, */*",
        },
    )
    try:
        with _opener.open(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code in (412, 403):
            _warm(force=True)
            with _opener.open(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read())
        raise


def _mixin_key() -> str:
    global _wbi_cache
    if _wbi_cache and time.time() - _wbi_cache[0] < 3600:
        return _wbi_cache[1]
    nav = _get("https://api.bilibili.com/x/web-interface/nav")
    wbi = (nav.get("data") or {}).get("wbi_img") or {}
    img = str(wbi.get("img_url", "")).rsplit("/", 1)[-1].split(".")[0]
    sub = str(wbi.get("sub_url", "")).rsplit("/", 1)[-1].split(".")[0]
    key = "".join((img + sub)[i] for i in MIXIN_TAB)[:32] if img and sub else ""
    _wbi_cache = (time.time(), key)
    return key


def _signed(base: str, params: dict) -> str:
    p = dict(params)
    p["wts"] = int(time.time())
    query = urllib.parse.urlencode(sorted(p.items()))
    key = _mixin_key()
    if key:
        p["w_rid"] = hashlib.md5((query + key).encode()).hexdigest()
    return base + "?" + urllib.parse.urlencode(p)


async def _run(work) -> str:
    try:
        return await asyncio.to_thread(work)
    except urllib.error.HTTPError as exc:
        return f"B站接口返回 {exc.code}（可能被风控），过一会儿再试"
    except Exception as exc:  # noqa: BLE001
        return f"B站查询失败：{type(exc).__name__}"


# ---------------- 格式化 ----------------
def _fmt_num(n) -> str:
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return "0"
    if n >= 100_000_000:
        return f"{n / 1e8:.1f}亿"
    if n >= 10_000:
        return f"{n / 1e4:.1f}万"
    return str(n)


def _fmt_dur(sec) -> str:
    if isinstance(sec, str):
        s = sec.strip()
        if ":" in s:
            try:
                parts = [int(p) for p in s.split(":")]
            except ValueError:
                return s
            if len(parts) == 2:
                return f"{parts[0]}:{parts[1]:02d}"
            if len(parts) == 3:
                return f"{parts[0]}:{parts[1]:02d}:{parts[2]:02d}"
            return s
        try:
            sec = int(s)
        except ValueError:
            return s or "?"
    sec = int(sec or 0)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _clean(text: str) -> str:
    return (
        re.sub(r"<[^>]+>", "", text or "")
        .replace("&quot;", '"')
        .replace("&amp;", "&")
        .strip()
    )


def _video_line(v: dict, i: int) -> str:
    title = _clean(v.get("title"))
    up = (v.get("owner") or {}).get("name") or v.get("author") or v.get("author_name") or "?"
    bvid = v.get("bvid") or ""
    stat = v.get("stat") if isinstance(v.get("stat"), dict) else {}
    play = _fmt_num(stat.get("view") if stat else v.get("play") or v.get("view"))
    dur = _fmt_dur(v.get("duration"))
    return f"{i}. {title} | UP:{up} | 播放:{play} | 时长:{dur} | {bvid}"


class Main(star.Star):
    def __init__(self, context: star.Context) -> None:
        super().__init__(context)

    def _cached(self, key: str) -> str | None:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return hit[1]
        return None

    # ---------------- 匿名可用 ----------------
    @filter.llm_tool(name="bili_search")
    async def bili_search(self, event: AstrMessageEvent, keyword: str, limit: int = 5):
        """在 B 站搜索视频。用户想找某个主题的视频、想看某类内容时使用。

        Args:
            keyword(string): 搜索关键词
            limit(number): 返回几条结果，1-10，默认 5
        """

        n = max(1, min(10, int(limit or 5)))

        def work() -> str:
            q = urllib.parse.quote(keyword)
            d = _get(
                "https://api.bilibili.com/x/web-interface/search/type"
                f"?search_type=video&keyword={q}&page=1"
            )
            if d.get("code") != 0:
                return f"搜索失败：{d.get('message')}"
            items = ((d.get("data") or {}).get("result") or [])[:n]
            if not items:
                return f"没有搜到「{keyword}」相关的视频"
            return "\n".join(
                [f"B站搜索「{keyword}」结果："]
                + [_video_line(v, i + 1) for i, v in enumerate(items)]
            )

        return await _run(work)

    @filter.llm_tool(name="bili_video")
    async def bili_video(self, event: AstrMessageEvent, bvid: str):
        """查询 B 站单个视频的详细信息（标题、UP 主、播放/点赞、时长、简介）。用户发了 BV 号或视频链接时使用。

        Args:
            bvid(string): BV 号（如 BV1GJ411x7h7）或视频链接
        """

        def work() -> str:
            m = BV_RE.search(bvid)
            if not m:
                return "没识别出 BV 号，请提供 BV 号或完整链接"
            d = _get(f"https://api.bilibili.com/x/web-interface/view?bvid={m.group(1)}")
            if d.get("code") != 0:
                return f"查不到这个视频：{d.get('message')}"
            v = d["data"]
            st = v.get("stat") or {}
            return (
                f"标题：{v.get('title')}\n"
                f"UP主：{(v.get('owner') or {}).get('name')}\n"
                f"播放 {_fmt_num(st.get('view'))} / 点赞 {_fmt_num(st.get('like'))} / "
                f"投币 {_fmt_num(st.get('coin'))} / 收藏 {_fmt_num(st.get('favorite'))}\n"
                f"弹幕 {_fmt_num(st.get('danmaku'))} / 评论 {_fmt_num(st.get('reply'))}\n"
                f"时长：{_fmt_dur(v.get('duration'))}｜分P：{v.get('videos')}\n"
                f"简介：{_clean(v.get('desc'))[:120] or '（无）'}"
            )

        return await _run(work)

    @filter.llm_tool(name="bili_hot")
    async def bili_hot(self, event: AstrMessageEvent, limit: int = 5):
        """查看 B 站当前热门视频。用户问「最近有什么热门」「B站在火什么」时使用。

        Args:
            limit(number): 返回几条，1-10，默认 5
        """

        n = max(1, min(10, int(limit or 5)))

        def work() -> str:
            cached = self._cached("hot")
            if cached:
                return "\n".join(cached.splitlines()[: n + 1])
            d = _get("https://api.bilibili.com/x/web-interface/popular?ps=10&pn=1")
            if d.get("code") != 0:
                return f"获取热门失败：{d.get('message')}"
            items = (d.get("data") or {}).get("list") or []
            lines = ["B站当前热门："] + [_video_line(v, i + 1) for i, v in enumerate(items)]
            _cache["hot"] = (time.time(), "\n".join(lines))
            return "\n".join(lines[: n + 1])

        return await _run(work)

    @filter.llm_tool(name="bili_ranking")
    async def bili_ranking(self, event: AstrMessageEvent, limit: int = 5):
        """查看 B 站全站排行榜。用户问「排行榜」「榜上有什么」时使用。

        Args:
            limit(number): 返回几条，1-10，默认 5
        """

        n = max(1, min(10, int(limit or 5)))

        def work() -> str:
            cached = self._cached("rank")
            if cached:
                return "\n".join(cached.splitlines()[: n + 1])
            d = _get("https://api.bilibili.com/x/web-interface/ranking/v2?rid=0&type=all")
            if d.get("code") != 0:
                return f"获取排行榜失败：{d.get('message')}"
            items = (d.get("data") or {}).get("list") or []
            lines = ["B站全站排行榜："] + [_video_line(v, i + 1) for i, v in enumerate(items)]
            _cache["rank"] = (time.time(), "\n".join(lines))
            return "\n".join(lines[: n + 1])

        return await _run(work)

    @filter.llm_tool(name="bili_up")
    async def bili_up(self, event: AstrMessageEvent, mid: int):
        """查询 B 站 UP 主信息（昵称、粉丝数、简介）。用户问某个 UP 主情况时使用。

        Args:
            mid(number): UP 主的 UID（数字）
        """

        def work() -> str:
            d = _get(f"https://api.bilibili.com/x/web-interface/card?mid={int(mid)}")
            if d.get("code") != 0:
                return f"查不到这个 UP 主：{d.get('message')}"
            card = (d.get("data") or {}).get("card") or {}
            return (
                f"UP主：{card.get('name')}\n"
                f"UID：{mid}｜粉丝：{_fmt_num(card.get('fans'))}｜"
                f"投稿：{_fmt_num(card.get('archive_count'))}\n"
                f"简介：{_clean(card.get('sign'))[:100] or '（无）'}"
            )

        return await _run(work)

    # ---------------- 刷 B 站 ----------------
    @filter.llm_tool(name="bili_feed")
    async def bili_feed(self, event: AstrMessageEvent, limit: int = 6):
        """刷 B 站推荐流，拿首页推荐给「我」的视频。用户说「刷B站」「看看推荐」「有什么好玩的」时使用。

        Args:
            limit(number): 返回几条，1-10，默认 6
        """

        n = max(1, min(10, int(limit or 6)))

        def work() -> str:
            url = _signed(
                "https://api.bilibili.com/x/web-interface/wbi/index/top/feed/rcmd",
                {"ps": n, "fresh_type": 4, "feed_version": "V8"},
            )
            d = _get(url)
            if d.get("code") != 0:
                return f"刷推荐流失败：{d.get('message')}"
            items = ((d.get("data") or {}).get("item") or [])
            items = [it for it in items if str(it.get("title") or "").strip()][:n]
            if not items:
                return "推荐流是空的，等会儿再刷"
            head = "B站推荐流"
            if not _load_login():
                head += "（未登录，是通用推荐）"
            return "\n".join(
                [head + "："] + [_video_line(v, i + 1) for i, v in enumerate(items)]
            )

        return await _run(work)

    # ---------------- 需要登录 ----------------
    @filter.llm_tool(name="bili_history")
    async def bili_history(self, event: AstrMessageEvent, limit: int = 5):
        """查看「我」的 B 站观看历史。需要已配置 B 站账号 cookie。用户问「我最近看什么」「上次那个视频」时使用。

        Args:
            limit(number): 返回几条，1-10，默认 5
        """

        n = max(1, min(10, int(limit or 5)))

        def work() -> str:
            if not _load_login():
                return "还没接 B 站账号，看不到观看历史（需要在 bili_account.json 里配 cookie）"
            d = _get(f"https://api.bilibili.com/x/web-interface/history/cursor?ps={n}")
            if d.get("code") != 0:
                return f"获取历史失败：{d.get('message')}"
            items = ((d.get("data") or {}).get("list") or [])[:n]
            if not items:
                return "历史记录是空的"
            lines = ["最近看过："]
            for i, v in enumerate(items, 1):
                prog = v.get("progress", -1)
                total = v.get("duration") or 0
                if prog >= 0 and total:
                    pct = f"{min(100, int(prog / total * 100))}%"
                else:
                    pct = "已看完"
                bvid = v.get("bvid") or (v.get("history") or {}).get("bvid") or ""
                lines.append(
                    f"{i}. {_clean(v.get('title'))} | UP:{v.get('author_name')} | "
                    f"进度:{pct} | {bvid}"
                )
            return "\n".join(lines)

        return await _run(work)

    @filter.llm_tool(name="bili_me")
    async def bili_me(self, event: AstrMessageEvent):
        """查看「我」的 B 站账号信息（昵称、等级、硬币、粉丝、关注数）。需要已配置 B 站账号 cookie。"""

        def work() -> str:
            if not _load_login():
                return "还没接 B 站账号（需要在 bili_account.json 里配 cookie）"
            d = _get("https://api.bilibili.com/x/space/myinfo")
            if d.get("code") != 0:
                return f"获取账号信息失败：{d.get('message')}（cookie 可能已过期）"
            u = d.get("data") or {}
            return (
                f"昵称：{u.get('name')}｜UID：{u.get('mid')}\n"
                f"等级：Lv{u.get('level')}｜硬币：{u.get('coins')}｜"
                f"粉丝：{_fmt_num(u.get('follower'))}｜关注：{_fmt_num(u.get('following'))}\n"
                f"签名：{_clean(u.get('sign'))[:100] or '（无）'}"
            )

        return await _run(work)
