"""她会唱歌：曲库直接唱；曲库没有的，去酷狗搜一首，下下来换成她的声音。

- /翻唱            随机一首（曲库）
- /翻唱 3          第 3 首
- /翻唱 稻香       曲库没有就去酷狗找
- 自己唱：每天活跃时段随机一次
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import astrbot.api.message_components as Comp
import httpx
from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.core.star.filter.command import GreedyStr

ROOT = Path(r"D:\QQrobot")
CLIPS = ROOT / "voice_clips"
OUT = ROOT / "voice_out"
COVERS = ROOT / "covers"   # 翻唱成品单独放
CFG = ROOT / "cover_sing.json"
LOG = ROOT / "cover_sing.jsonl"
STATE = ROOT / "cover_state.json"
COOKIE_FILE = ROOT / "secrets" / "kugou_cookie.txt"
BILI_COOKIES = ROOT / "secrets" / "bili_cookies.txt"
PY = r"D:\QQrobot\venv\Scripts\python.exe"
FFMPEG = r"C:\Users\venti\miniforge3\Library\bin\ffmpeg.EXE"
WSL_DISTRO = "Ubuntu-22.04"
WSL_PY = "/root/.local/share/feiyu-voice/cuda/rvc/venv/bin/python"
ONEBOX = "http://127.0.0.1:3000"
TOKEN = ""
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36",
      "Referer": "https://www.kugou.com/"}
# B站 必须用 bilibili 的 Referer，否则 playurl 直接 403
BILI_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36",
           "Referer": "https://www.bilibili.com/"}

DEFAULT = {
    "enabled": True,
    "auto_daily": 1,
    "active_hours": [11, 23],
    "notify_qq": 3328480063,
    "online_search": True,
    "bili_require_hires": True,
    "max_duration": 420,
    "bili_allow_188k": False,
}


def _cfg() -> dict:
    out = dict(DEFAULT)
    try:
        out.update(json.loads(CFG.read_text(encoding="utf-8")))
    except Exception:
        pass
    return out


def _state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _songs() -> list[dict]:
    try:
        idx = json.loads((CLIPS / "index.json").read_text(encoding="utf-8"))
        return [f for f in idx.get("fulls", []) if (CLIPS / f["file"]).exists()]
    except Exception:
        return []


def _clean_query(raw: str) -> str:
    q = str(raw or "").strip()
    q = re.sub(r"^[\s/\\:：,，。]+", "", q)
    for ch in "《》「」“”\"'":
        q = q.replace(ch, "")
    return q.strip(" 　.。,，")


def _norm(s: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", str(s or "")).lower()


def _cookie() -> str:
    try:
        return COOKIE_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        mid = hashlib.md5(str(random.random()).encode()).hexdigest()
        dfid = "".join(random.choice("0123456789abcdef") for _ in range(10))
        return f"kg_mid={mid}; kg_dfid={dfid}"


def _get(url: str, timeout: float = 30) -> bytes:
    req = urllib.request.Request(url, headers={**UA, "Cookie": _cookie()})
    return urllib.request.urlopen(req, timeout=timeout).read()


def _send_qq(user_id: int, text: str, audio: Path | None = None) -> None:
    message = []
    if text:
        message.append({"type": "text", "data": {"text": text}})
    if audio is not None:
        message.append({"type": "record",
                        "data": {"file": "file:///" + str(audio).replace("\\", "/")}})
    httpx.post(f"{ONEBOX}/send_private_msg",
               headers={"Authorization": f"Bearer {TOKEN or _onebot_token()}"},
               json={"user_id": user_id, "message": message}, timeout=120)


class CoverSing(star.Star):
    def __init__(self, context: star.Context) -> None:
        super().__init__(context)
        self._busy = False

    # ---------------- 酷狗 ----------------
    def _kugou_search(self, name: str) -> list[dict]:
        q = urllib.parse.quote(name)
        url = ("https://songsearch.kugou.com/song_search_v2?"
               f"keyword={q}&page=1&pagesize=10&platform=WebFilter&tag=em&filter=2&iscorrection=1")
        try:
            d = json.loads(_get(url))
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[cover] 酷狗搜索失败：{exc}")
            return []
        rows = ((d.get("data") or {}).get("lists") or [])
        out = []
        for r in rows:
            dur = int(r.get("Duration") or 0)
            if dur < 45 or dur > int(_cfg().get("max_duration", 420)):
                continue
            out.append({
                "hash": r.get("FileHash") or "",
                "song": re.sub(r"<[^>]+>", "", str(r.get("SongName") or "")),
                "singer": re.sub(r"<[^>]+>", "", str(r.get("SingerName") or "")),
                "duration": dur,
            })
        nk = _norm(name)
        out.sort(key=lambda x: 0 if nk and nk in _norm(x["song"]) else 1)
        return out

    def _kugou_fetch(self, cand: dict, name: str) -> dict:
        """拿直链、下成 mp3、登记进曲库。"""
        api = f"https://m.kugou.com/app/i/getSongInfo.php?cmd=playInfo&hash={cand['hash']}"
        d = json.loads(_get(api))
        url = d.get("url") or ""
        if not url:
            raise RuntimeError("酷狗这首要会员或者没版权")
        data = _get(url, timeout=120)
        stem = f"kg_{cand['hash'][:12]}"
        dest = CLIPS / "full" / f"{stem}.mp3"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

        idx_path = CLIPS / "index.json"
        try:
            idx = json.loads(idx_path.read_text(encoding="utf-8"))
            if not any(f.get("file") == f"full/{stem}.mp3" for f in idx.get("fulls", [])):
                idx.setdefault("fulls", []).append({
                    "id": f"full_{stem}", "type": "full", "file": f"full/{stem}.mp3",
                    "song": name, "bvid": "", "title": f"{cand['song']} - {cand['singer']}",
                    "up": cand.get("singer", ""), "duration": cand.get("duration", 0),
                    "source": "kugou",
                })
                idx_path.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[cover] 登记曲库失败：{exc}")
        return {"file": f"full/{stem}.mp3", "song": name,
                "title": f"{cand['song']} - {cand['singer']}", "duration": cand.get("duration", 0)}


    # ---------------- B站（只要杜比 / Hi-Res 高品质音频） ----------------
    BAD_TITLE = ("mv", "官方", "自制", "音乐视频", "现场", "live", "伴奏", "纯音乐",
                 "dj", "片段", "合集", "翻唱", "cover", "remix", "反应", "reaction")


    def _bili_json(self, url: str, timeout: float = 25):
        """带重试的 B站 JSON 请求（403/412 时退避重试）。"""
        import time as _time

        last = None
        for attempt in range(3):
            try:
                raw = urllib.request.urlopen(
                    urllib.request.Request(url, headers={**BILI_UA, "Cookie": self._bili_cookie()}),
                    timeout=timeout).read()
                return json.loads(raw)
            except Exception as exc:  # noqa: BLE001
                last = exc
                _time.sleep(1.2 * (attempt + 1))
        raise last  # type: ignore[misc]

    def _bili_view(self, aid_or_bvid: str) -> dict:
        url = ("https://api.bilibili.com/x/web-interface/view?" +
               (f"bvid={aid_or_bvid}" if str(aid_or_bvid).startswith("BV")
                else f"aid={aid_or_bvid}"))
        return (self._bili_json(url, timeout=20) or {}).get("data") or {}

    def _bili_cookie(self) -> str:
        try:
            acc = json.loads((ROOT / "bili_account.json").read_text(encoding="utf-8"))
            return (f"SESSDATA={acc.get('sessdata','')}; bili_jct={acc.get('bili_jct','')}; "
                    f"DedeUserID={acc.get('dedeuserid','')}")
        except Exception:
            return ""

    def _bili_audio_streams(self, bvid: str, cid: int) -> dict:
        """查这个视频有哪些音频流：hires(flac) / dolby / 188k。"""
        url = (f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}"
               "&fnval=4048&fourk=1")
        dash = (((self._bili_json(url, timeout=25) or {}).get("data") or {}).get("dash") or {})
        flac = ((dash.get("flac") or {}).get("audio") or [])
        dolby = ((dash.get("dolby") or {}).get("audio") or [])
        normal = sorted((a.get("id"), a.get("baseUrl") or a.get("base_url"))
                        for a in (dash.get("audio") or []))
        best_normal = normal[-1] if normal else (None, None)
        return {
            "hires_url": flac[0].get("baseUrl") or flac[0].get("base_url") if flac else None,
            "dolby_url": dolby[0].get("baseUrl") or dolby[0].get("base_url") if dolby else None,
            "normal_id": best_normal[0],
            "normal_url": best_normal[1],
        }

    def _bili_search(self, name: str) -> list[dict]:
        """B站搜歌，只留杜比 / Hi-Res 的（非大会员会返回空并说明）。"""
        cfg = _cfg()
        require = bool(cfg.get("bili_require_hires", True))
        allow_188 = bool(cfg.get("bili_allow_188k", False))
        queries = [f"{name} Hi-Res", f"{name} 无损", name]
        seen, cands = set(), []
        for q in queries:
            try:
                r = subprocess.run(
                    [PY, "-m", "yt_dlp", f"bilisearch6:{q}", "--flat-playlist",
                     "--cookies", str(BILI_COOKIES), "--print", "%(id)s", "--no-warnings"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
                )
                ids = [x.strip() for x in (r.stdout or "").splitlines() if x.strip()]
            except Exception as exc:  # noqa: BLE001
                logger.info(f"[cover] B站搜索失败：{exc}")
                continue
            for item in ids:
                if item in seen:
                    continue
                seen.add(item)
                time.sleep(0.25)
                try:
                    v = self._bili_view(item)
                    title = str(v.get("title") or "")
                    dur = int(v.get("duration") or 0)
                    bvid = v.get("bvid")
                    if not bvid or dur < 45 or dur > int(cfg.get("max_duration", 420)):
                        continue
                    low = title.lower()
                    if any(b in low for b in self.BAD_TITLE):
                        continue
                    if _norm(name) not in _norm(title):
                        continue
                    cid = (v.get("pages") or [{}])[0].get("cid")
                    st = self._bili_audio_streams(bvid, cid)
                    has_hi = bool(st["hires_url"] or st["dolby_url"])
                    if require and not has_hi and not allow_188:
                        continue
                    cands.append({"bvid": bvid, "title": title, "duration": dur,
                                  "hires": st["hires_url"], "dolby": st["dolby_url"],
                                  "normal": st["normal_url"], "normal_id": st["normal_id"]})
                except Exception:
                    continue
            if cands:
                break
        cands.sort(key=lambda x: 0 if (x["hires"] or x["dolby"]) else 1)
        return cands

    def _bili_fetch(self, cand: dict, name: str) -> dict:
        """下载选中的音频流（杜比/Hi-Res 优先），转 320k mp3，登记曲库。"""
        url = cand.get("hires") or cand.get("dolby") or cand.get("normal")
        if not url:
            raise RuntimeError("这个视频没有可用音频流")
        kind = "hires" if cand.get("hires") else ("dolby" if cand.get("dolby") else "normal")
        tmp = OUT / "covers_src"
        tmp.mkdir(parents=True, exist_ok=True)
        raw = tmp / f"bili_{cand['bvid']}.src"
        import httpx

        with httpx.Client(timeout=180, follow_redirects=True) as c:
            r = c.get(url, headers={**BILI_UA, "Cookie": self._bili_cookie()})
            r.raise_for_status()
            raw.write_bytes(r.content)

        stem = f"bili_{cand['bvid']}"
        dest = CLIPS / "full" / f"{stem}.mp3"
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(raw), "-b:a", "320k", str(dest)],
                       capture_output=True, timeout=600)
        if not dest.exists():
            raise RuntimeError("B站音频转 mp3 失败")

        idx_path = CLIPS / "index.json"
        try:
            idx = json.loads(idx_path.read_text(encoding="utf-8"))
            if not any(f.get("file") == f"full/{stem}.mp3" for f in idx.get("fulls", [])):
                idx.setdefault("fulls", []).append({
                    "id": f"full_{stem}", "type": "full", "file": f"full/{stem}.mp3",
                    "song": name, "bvid": cand["bvid"], "title": cand["title"],
                    "up": "", "duration": cand.get("duration", 0),
                    "source": f"bilibili-{kind}",
                })
                idx_path.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[cover] 登记失败：{exc}")
        return {"file": f"full/{stem}.mp3", "song": name, "title": cand["title"],
                "duration": cand.get("duration", 0)}

    # ---------------- 选歌 ----------------
    def _pick(self, key: str = "") -> dict | None:
        songs = _songs()
        if not songs:
            return None
        key = _clean_query(key)
        if not key:
            return random.choice(songs)
        if key.isdigit():
            i = int(key)
            return songs[i - 1] if 1 <= i <= len(songs) else None
        nk = _norm(key)
        if nk:
            for s in songs:
                if nk in _norm(s.get("song")) or nk in _norm(s.get("title")):
                    return s
        for s in songs:
            if key in str(s.get("song", "")) or key in str(s.get("title", "")):
                return s
        return None

    def _menu(self) -> str:
        return "、".join(f"{i+1}.{s.get('song', '?')}" for i, s in enumerate(_songs()))

    # ---------------- 生成 ----------------
    def _run(self, mp3: Path) -> Path:
        OUT.mkdir(parents=True, exist_ok=True)
        wav = OUT / f"cover_{mp3.stem}.wav"
        src = f"/mnt/d/QQrobot/voice_clips/{mp3.parent.name}/{mp3.name}"
        dst = f"/mnt/d/QQrobot/voice_out/{wav.name}"
        proc = subprocess.run(
            ["wsl", "-d", WSL_DISTRO, "--", "bash", "-lc",
             f"cd /root && {WSL_PY} cover_a.py '{src}' --out '{dst}'"],
            capture_output=True, text=True, timeout=1800, encoding="utf-8", errors="replace",
        )
        if not wav.exists():
            tail = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()[-3:]
            raise RuntimeError("生成失败：" + " / ".join(tail)[:300])
        COVERS.mkdir(parents=True, exist_ok=True)
        mp3out = COVERS / f"cover_{mp3.stem}.mp3"
        subprocess.run(
            ["wsl", "-d", WSL_DISTRO, "--", "bash", "-lc",
             f"ffmpeg -y -v error -i '/mnt/d/QQrobot/voice_out/{wav.name}' "
             f"-b:a 192k '/mnt/d/QQrobot/covers/{mp3out.name}'"],
            capture_output=True, timeout=600,
        )
        if mp3out.exists():
            try:
                wav.unlink()
            except OSError:
                pass
            return mp3out
        return wav

    def _log(self, rec: dict) -> None:
        try:
            with LOG.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"t": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), **rec},
                                    ensure_ascii=False) + "\n")
        except OSError:
            pass

    # ---------------- 自动唱 ----------------
    def _due(self) -> bool:
        cfg = _cfg()
        if not cfg.get("enabled", True) or int(cfg.get("auto_daily", 1)) <= 0:
            return False
        now = datetime.now()
        lo, hi = cfg.get("active_hours", [11, 23])
        if not (float(lo) <= now.hour + now.minute / 60 <= float(hi)):
            return False
        st = _state()
        today = now.strftime("%Y-%m-%d")
        if st.get("date") == today and int(st.get("count", 0)) >= int(cfg.get("auto_daily", 1)):
            return False
        random.seed(f"{today}-sing")
        target = random.uniform(float(lo), float(hi))
        random.seed()
        return (now.hour + now.minute / 60) >= target

    async def _auto(self) -> None:
        if self._busy or not self._due():
            return
        self._busy = True
        try:
            song = self._pick()
            if not song:
                return
            mp3 = await asyncio.to_thread(self._run, CLIPS / song["file"])
            target = int(_cfg().get("notify_qq") or 0)
            if target:
                await asyncio.to_thread(
                    _send_qq, target, f"我刚才自己唱了首《{song.get('song', '')}》，你听听", mp3)
            st = _state()
            today = datetime.now().strftime("%Y-%m-%d")
            if st.get("date") != today:
                st = {"date": today, "count": 0}
            st["count"] = int(st.get("count", 0)) + 1
            STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
            self._log({"auto": True, "song": song.get("song"), "ok": True})
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[cover] 自动唱失败：{exc}")
            self._log({"auto": True, "error": str(exc)[:200]})
        finally:
            self._busy = False

    @filter.on_astrbot_loaded()
    async def on_loaded(self) -> None:
        async def loop() -> None:
            while True:
                await asyncio.sleep(90)
                try:
                    await self._auto()
                except Exception as exc:  # noqa: BLE001
                    logger.info(f"[cover] tick 出错：{exc}")

        asyncio.create_task(loop())

    # ---------------- 入口 ----------------
    @filter.regex(r"^[/\\]?\s*(?:翻唱|唱)\s*[:：]?\s*(?P<q>.+)$")
    async def cmd_cover_loose(self, event: AstrMessageEvent):
        m = re.search(r"^[/\\]?\s*(?:翻唱|唱)\s*[:：]?\s*(?P<q>.+)$",
                      event.get_message_str().strip())
        async for r in self._sing(event, m.group("q") if m else ""):
            yield r
        event.stop_event()

    @filter.command("翻唱", alias={"唱一首", "唱个歌"})
    async def cmd_cover(self, event: AstrMessageEvent, which: GreedyStr = ""):
        async for r in self._sing(event, str(which or "")):
            yield r

    async def _sing(self, event: AstrMessageEvent, raw: str):
        if self._busy:
            yield event.plain_result("正在唱上一首，等我唱完")
            return
        key = _clean_query(raw)
        song = self._pick(key)
        if song is None and key and _cfg().get("online_search", True):
            # 先 B站（188k，音质更好）
            yield event.plain_result(f"曲库里没有《{key}》，去 B站 找…")
            try:
                bcands = await asyncio.to_thread(self._bili_search, key)
            except Exception as exc:  # noqa: BLE001
                bcands = []
                logger.info(f"[cover] B站搜索异常：{exc}")
            if bcands:
                yield event.plain_result(f"B站找到：{bcands[0]['title'][:40]}，下载中…")
                try:
                    song = await asyncio.to_thread(self._bili_fetch, bcands[0], key)
                except Exception as exc:  # noqa: BLE001
                    yield event.plain_result(f"B站没下来（{exc}），去酷狗再试…")

            # B站 没有 → 酷狗 兜底
            if song is None:
                yield event.plain_result("去酷狗找…")
                try:
                    cands = await asyncio.to_thread(self._kugou_search, key)
                except Exception as exc:  # noqa: BLE001
                    cands = []
                    logger.info(f"[cover] 酷狗搜索异常：{exc}")
                if cands:
                    yield event.plain_result(f"酷狗找到：{cands[0]['song']} - {cands[0]['singer']}，下载中…")
                    try:
                        song = await asyncio.to_thread(self._kugou_fetch, cands[0], key)
                    except Exception as exc:  # noqa: BLE001
                        yield event.plain_result(f"酷狗也没下来：{exc}")

            if song is None:
                yield event.plain_result(f"B站和酷狗都没找到《{key}》。\n曲库现有：{self._menu()}")

        if song is None:
            return

        self._busy = True
        try:
            title = song.get("song", "")
            yield event.plain_result(f"唱《{title}》…大概 30 秒")
            mp3 = await asyncio.to_thread(self._run, CLIPS / song["file"])
            yield event.chain_result([Comp.Record.fromFileSystem(str(mp3))])
            self._log({"song": title, "by": str(event.get_sender_id())})
        except Exception as exc:  # noqa: BLE001
            logger.info(f"[cover] 翻唱失败：{exc}")
            yield event.plain_result(f"没唱成：{exc}")
        finally:
            self._busy = False

    @filter.llm_tool(name="cover_sing")
    async def tool_cover(self, event: AstrMessageEvent, which: str = "") -> str:
        """唱一首歌：曲库有就直接唱，没有就去酷狗找。

        Args:
            which(string): 歌名或序号，可留空（随机）。
        """
        try:
            key = _clean_query(which)
            song = self._pick(key)
            if song is None and key and _cfg().get("online_search", True):
                bcands = await asyncio.to_thread(self._bili_search, key)
                if bcands:
                    song = await asyncio.to_thread(self._bili_fetch, bcands[0], key)
                if song is None:
                    cands = await asyncio.to_thread(self._kugou_search, key)
                    if cands:
                        song = await asyncio.to_thread(self._kugou_fetch, cands[0], key)
            if song is None:
                return f"曲库没有，酷狗也没搜到。现有：{self._menu()}"
            mp3 = await asyncio.to_thread(self._run, CLIPS / song["file"])
            await self.context.send_message(
                event.unified_msg_origin,
                MessageChain([Comp.Record.fromFileSystem(str(mp3))]),
            )
            return f"（唱了《{song.get('song', '')}》）"
        except Exception as exc:  # noqa: BLE001
            return f"没唱成：{exc}"
