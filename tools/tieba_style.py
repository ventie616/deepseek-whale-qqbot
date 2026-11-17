"""去贴吧学话术：爬若干吧的标题与首帖摘要，作为「人怎么说话」的样本。

不评判内容对错，只学语气：怎么吐槽、怎么接梗、怎么自嘲、怎么反问。
输出 D:\QQrobot\her_room\tieba_style.md
"""

from __future__ import annotations

import gzip
import html
import http.cookiejar
import json
import pathlib
import random
import re
import urllib.parse
import urllib.request

ROOT = pathlib.Path(r"D:\QQrobot")
OUT = ROOT / "her_room" / "tieba_style.md"
CACHE = ROOT / "tieba_style_cache.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

BARS = {
    "弱智吧": "冷幽默 / 脑筋急转弯",
    "笑话吧": "段子 / 接梗",
    "段子吧": "整活 / 反转",
    "情感吧": "感情 / 吐槽",
    "生活吧": "生活 / 抱怨",
    "职场吧": "打工 / 阴阳",
    "动漫吧": "二次元 / 玩梗",
    "游戏吧": "游戏 / 对线",
}

TITLE_RE = re.compile(r'class="j_th_tit[^"]*"[^>]*>(.*?)</a>', re.S)
ABS_RE = re.compile(r'class="threadlist_abs[^"]*"[^>]*>(.*?)</div>', re.S)
TAG_RE = re.compile(r"<[^>]+>")
BAD = re.compile(r"(广告|加微|代练|出售|招聘|点击|http|www\.|【公告】|吧主|吧规|置顶)")

_opener = None


def _open() -> urllib.request.OpenerDirector:
    global _opener
    if _opener is None:
        cj = http.cookiejar.CookieJar()
        _opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        try:  # 预热拿 BAIDUID，否则会被 403
            _get("https://tieba.baidu.com/")
        except Exception:
            pass
    return _opener


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://tieba.baidu.com/",
        "Accept-Encoding": "gzip",
    })
    r = _open().open(req, timeout=20)
    raw = r.read()
    if r.headers.get("Content-Encoding") == "gzip":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", errors="replace")


def _text(fragment: str) -> str:
    t = html.unescape(TAG_RE.sub("", fragment))
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _keep(line: str) -> bool:
    if not (6 <= len(line) <= 60):
        return False
    if BAD.search(line):
        return False
    if line.count("[") >= 2:      # [图片][表情] 这类
        return False
    return True


def fetch_bar(bar: str, limit: int = 18) -> list[str]:
    url = "https://tieba.baidu.com/f?kw=" + urllib.parse.quote(bar) + "&ie=utf-8&pn=0"
    page = _get(url)
    out: list[str] = []
    for m in TITLE_RE.finditer(page):
        t = _text(m.group(1))
        if _keep(t) and t.endswith(("?", "？", "!", "！", "吧", "吗", "呢", "了", "啊", "的", "人")):
            out.append(t)
    for m in ABS_RE.finditer(page):
        t = _text(m.group(1))
        if _keep(t):
            out.append(t)
    # 去重
    seen, uniq = set(), []
    for t in out:
        if t in seen:
            continue
        seen.add(t)
        uniq.append(t)
    return uniq[:limit]


def build(per_bar: int = 12) -> dict:
    result: dict[str, list[str]] = {}
    for bar in BARS:
        try:
            lines = fetch_bar(bar, per_bar)
            if lines:
                result[bar] = lines
        except Exception as exc:  # noqa: BLE001
            print(f"  {bar} 失败: {str(exc)[:60]}")
    if result:
        CACHE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    else:  # 全部失败就用缓存
        try:
            result = json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            result = {}

    body = ["# 贴吧学来的话术（学语气，不学观点）", ""]
    for bar, tag in BARS.items():
        lines = result.get(bar) or []
        if not lines:
            continue
        body.append(f"## {bar} · {tag}")
        body += [f"- {t}" for t in lines]
        body.append("")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(body), encoding="utf-8")
    return result


def pick(n: int = 3) -> list[str]:
    try:
        data = json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:
        return []
    pool = [f"{t}" for lines in data.values() for t in lines]
    if not pool:
        return []
    return random.sample(pool, min(n, len(pool)))


if __name__ == "__main__":
    got = build()
    total = sum(len(v) for v in got.values())
    print(f"学到 {total} 句 / {len(got)} 个吧 → {OUT}")
    for bar, lines in list(got.items())[:4]:
        print(f"\n[{bar}]")
        for t in lines[:3]:
            print("  ", t)
