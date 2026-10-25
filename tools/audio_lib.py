"""唱歌素材库操作：加歌（B站 / 本地文件）、列表、删除。

控制台和命令行共用这套逻辑。

    from audio_lib import add_from_bvid, add_from_file, list_songs, delete_song
"""

from __future__ import annotations

import json
import re
import pathlib
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
import wave

import numpy as np

ROOT = pathlib.Path(r"C:\QQBot")
LIB = ROOT / "voice_clips"
FULL = LIB / "full"
CLIPS = LIB / "clips"
INDEX = LIB / "index.json"
DOWNLOADS = ROOT / "downloads" / "songs"
FF = r"C:\Users\venti\miniforge3\Library\bin\ffmpeg.exe"
FFP = r"C:\Users\venti\miniforge3\Library\bin\ffprobe.exe"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
CLIP_LEN = 12
N_CLIPS = 3
CREDIT = "AI 翻唱/原创（音色：大肥鱼）；仅供本机私聊自用，请勿公开传播"


def _load_index() -> dict:
    try:
        return json.loads(INDEX.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 2, "clips": [], "fulls": []}


def _save_index(idx: dict) -> None:
    INDEX.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")


def _cookie() -> str:
    try:
        acc = json.loads((ROOT / "bili_account.json").read_text(encoding="utf-8"))
        return f"SESSDATA={acc.get('sessdata','')}; bili_jct={acc.get('bili_jct','')}; DedeUserID={acc.get('dedeuserid','')}"
    except Exception:
        return ""


def _duration(path: pathlib.Path) -> float:
    try:
        out = subprocess.run(
            [FFP, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        return float(out)
    except Exception:
        return 0.0


def pick_segments(path: pathlib.Path, n: int = N_CLIPS) -> list[int]:
    """按人声能量挑 n 段（间隔 ≥18 秒），返回起始秒。"""
    hp = pathlib.Path(tempfile.gettempdir()) / f"pick_{path.stem}.wav"
    subprocess.run(
        [FF, "-y", "-hide_banner", "-loglevel", "error", "-i", str(path),
         "-af", "highpass=f=120", "-ac", "1", "-ar", "16000", str(hp)],
        check=True, timeout=180,
    )
    with wave.open(str(hp)) as w:
        sr = w.getframerate()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    hp.unlink(missing_ok=True)
    total = len(data) / sr
    rms = np.array(
        [float(np.sqrt(np.mean(data[i * sr:(i + 1) * sr] ** 2))) for i in range(int(total))]
    )
    cand = sorted(
        ((float(rms[i:i + CLIP_LEN].mean()), i) for i in range(max(1, len(rms) - CLIP_LEN))),
        reverse=True,
    )
    picks: list[int] = []
    for _, i in cand:
        if i < 3 or i + CLIP_LEN > total - 2:
            continue
        if all(abs(i - j) > 18 for j in picks):
            picks.append(i)
        if len(picks) >= n:
            break
    return sorted(picks)


def _encode_full(src: pathlib.Path, bvid: str) -> pathlib.Path:
    FULL.mkdir(parents=True, exist_ok=True)
    out = FULL / f"{bvid}.mp3"
    subprocess.run(
        [FF, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
         "-ac", "1", "-ar", "32000", "-b:a", "96k", str(out)],
        check=True, timeout=300,
    )
    return out


def _encode_clip(src: pathlib.Path, start: int, bvid: str) -> pathlib.Path:
    CLIPS.mkdir(parents=True, exist_ok=True)
    out = CLIPS / f"{bvid}_{start:03d}.mp3"
    subprocess.run(
        [FF, "-y", "-hide_banner", "-loglevel", "error",
         "-ss", str(start), "-t", str(CLIP_LEN), "-i", str(src),
         "-af", "highpass=f=80,loudnorm=I=-16:TP=-1.5:LRA=11,"
                f"afade=t=in:st=0:d=0.35,afade=t=out:st={CLIP_LEN-0.5}:d=0.5",
         "-ac", "1", "-ar", "24000", "-b:a", "64k", str(out)],
        check=True, timeout=180,
    )
    return out


def add_audio(src: pathlib.Path, name: str, bvid: str = "", log=print) -> dict:
    """把一个音频文件切成整曲 + 3 段节选，写进索引。"""
    if not src.exists():
        raise FileNotFoundError(src)
    bvid = bvid or f"local{int(src.stat().st_mtime)}"
    log(f"处理《{name}》 时长 {_duration(src):.0f}s")
    idx = _load_index()
    idx["clips"] = [c for c in idx.get("clips", []) if c.get("bvid") != bvid]
    idx["fulls"] = [f for f in idx.get("fulls", []) if f.get("bvid") != bvid]

    full = _encode_full(src, bvid)
    dur = _duration(src)
    log(f"  整曲 {full.name} {full.stat().st_size/1024/1024:.1f} MB")
    idx["fulls"].append({
        "id": f"full_{bvid}", "type": "full", "file": f"full/{full.name}",
        "song": name, "bvid": bvid, "title": name, "up": "",
        "duration": round(dur), "size_kb": round(full.stat().st_size / 1024), "credit": CREDIT,
    })

    picks = pick_segments(src)
    for start in picks:
        out = _encode_clip(src, start, bvid)
        idx["clips"].append({
            "id": f"clip_{bvid}_{start}", "type": "clip", "file": f"clips/{out.name}",
            "song": name, "bvid": bvid, "title": name, "up": "",
            "start": start, "duration": CLIP_LEN,
            "size_kb": round(out.stat().st_size / 1024), "credit": CREDIT,
        })
        log(f"  节选 @{start}s {out.stat().st_size/1024:.0f} KB")

    _save_index(idx)
    log(f"完成：库内 {len(idx['clips'])} 段节选 / {len(idx['fulls'])} 首整曲")
    return {"clips": len(picks), "full": str(full), "seconds": round(dur)}


BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
AUDIO_EXT = {".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg", ".opus", ".mp4", ".webm"}


def resolve_url(url: str, log=print) -> tuple[str, str]:
    """把各种网址归一成 (kind, value)。kind = bvid / audio。"""
    url = (url or "").strip()
    if not url:
        raise ValueError("空网址")
    if url.startswith(("BV", "bv")):
        m = BV_RE.search(url)
        if m:
            return "bvid", m.group(1)
        raise ValueError("不像 BV 号")

    # b23.tv 短链：跟一次跳转
    if "b23.tv" in url or "bili2233.cn" in url:
        log("解析短链…")
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                final = r.geturl()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"短链解析失败：{exc}") from exc
        log(f"跳转到 {final[:70]}")
        url = final

    m = BV_RE.search(url)
    if m:
        return "bvid", m.group(1)

    am = re.search(r"/av(\d+)", url) or re.search(r"^av(\d+)$", url, re.I)
    if am:
        return "aid", am.group(1)

    path = url.split("?")[0].split("#")[0].lower()
    if any(path.endswith(ext) for ext in AUDIO_EXT):
        return "audio", url

    raise ValueError("看不懂这个网址（要 B站视频链接或音频直链）")


def guess_name_from_url(url: str) -> str:
    tail = url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
    stem = tail.rsplit(".", 1)[0]
    return urllib.parse.unquote(stem)[:40] or "未命名音频"


def add_from_aid(aid: str, name: str = "", log=print) -> dict:
    """av 号：先查 aid 拿到 bvid，再走正常流程。"""
    hdr = {"User-Agent": UA, "Referer": "https://www.bilibili.com/", "Cookie": _cookie()}
    req = urllib.request.Request(f"https://api.bilibili.com/x/web-interface/view?aid={aid}", headers=hdr)
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read())
    if d.get("code") != 0:
        raise RuntimeError(f"取视频信息失败：{d.get('message')}")
    bvid = d["data"].get("bvid")
    log(f"av{aid} -> {bvid}")
    return add_from_bvid(bvid, name or d["data"].get("title") or "", log=log)


def add_from_audio_url(url: str, name: str = "", log=print) -> dict:
    """下载一个音频直链再加库。"""
    name = name.strip() or guess_name_from_url(url)
    ext = "." + pathlib.Path(url.split("?")[0]).suffix.lstrip(".")
    if ext.lower() not in AUDIO_EXT:
        ext = ".m4a"
    tmp = pathlib.Path(tempfile.gettempdir()) / f"dl_{abs(hash(url)) % 10**8}{ext}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=180) as r, tmp.open("wb") as f:
        total = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            total += len(chunk)
            if total > 300 * 1024 * 1024:
                raise RuntimeError("文件太大（>300MB）")
    log(f"下载 {tmp.name} {total/1024/1024:.1f} MB")
    res = add_audio(tmp, name, log=log)
    tmp.unlink(missing_ok=True)
    res["title"] = name
    return res


def add_from_url(url: str, name: str = "", log=print) -> dict:
    kind, value = resolve_url(url, log=log)
    if kind == "bvid":
        return add_from_bvid(value, name, log=log)
    if kind == "aid":
        return add_from_aid(value, name, log=log)
    return add_from_audio_url(value, name, log=log)


def add_from_bvid(bvid: str, name: str = "", log=print) -> dict:
    """从 B站 拉音频再加库。"""
    bvid = bvid.strip()
    hdr = {"User-Agent": UA, "Referer": "https://www.bilibili.com/", "Cookie": _cookie()}
    req = urllib.request.Request(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", headers=hdr)
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read())
    if d.get("code") != 0:
        raise RuntimeError(f"取视频信息失败：code={d.get('code')} {d.get('message')}")
    data = d["data"]
    cid = (data.get("pages") or [{}])[0].get("cid")
    title = name.strip()
    if not title:
        # 简介里常有「歌名：xxx」，比视频标题更准
        m = re.search(r"歌名\s*[:：]\s*([^\n\r]{1,40})", data.get("desc") or "")
        if m:
            title = m.group(1).strip()
            log(f"从简介取歌名：{title}")
    if not title:
        title = data.get("title") or bvid
    log(f"《{title}》 {data.get('duration')}s")

    p = pathlib.Path(tempfile.gettempdir()) / f"{bvid}.m4a"
    purl = (f"https://api.bilibili.com/x/player/playurl?avid={data['aid']}&cid={cid}"
            "&fnval=16&fourk=0")
    req = urllib.request.Request(purl, headers=hdr)
    with urllib.request.urlopen(req, timeout=30) as r:
        pd = json.loads(r.read())
    audios = ((pd.get("data") or {}).get("dash") or {}).get("audio") or []
    if not audios:
        raise RuntimeError("拿不到音频流（可能需要登录）")
    audios.sort(key=lambda a: a.get("bandwidth") or 0)
    url = audios[-1].get("baseUrl") or audios[-1].get("base_url")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"})
    with urllib.request.urlopen(req, timeout=180) as r, p.open("wb") as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
    log(f"下载 {p.name} {p.stat().st_size/1024/1024:.1f} MB")

    # 留一份原始音频，方便以后重建
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    keep = DOWNLOADS / f"{bvid}.m4a"
    shutil.copy2(p, keep)
    res = add_audio(p, title, bvid=bvid, log=log)
    p.unlink(missing_ok=True)
    res["title"] = title
    return res


def list_songs() -> list[dict]:
    idx = _load_index()
    songs: dict[str, dict] = {}
    for c in idx.get("clips", []):
        k = c.get("song") or "?"
        songs.setdefault(k, {"song": k, "clips": [], "full": None})
        songs[k]["clips"].append({"file": c.get("file"), "start": c.get("start"),
                                  "size_kb": c.get("size_kb")})
    for f in idx.get("fulls", []):
        k = f.get("song") or "?"
        songs.setdefault(k, {"song": k, "clips": [], "full": None})
        songs[k]["full"] = {"file": f.get("file"), "duration": f.get("duration"),
                            "size_kb": f.get("size_kb")}
    for v in songs.values():
        v["clips"].sort(key=lambda x: x.get("start") or 0)
    return sorted(songs.values(), key=lambda x: x["song"])


def delete_song(song: str) -> int:
    idx = _load_index()
    removed = 0
    for key in ("clips", "fulls"):
        keep = []
        for e in idx.get(key, []):
            if e.get("song") == song:
                p = LIB / str(e.get("file") or "")
                if p.is_file():
                    p.unlink(missing_ok=True)
                removed += 1
            else:
                keep.append(e)
        idx[key] = keep
    _save_index(idx)
    return removed


def play_path(rel: str) -> pathlib.Path | None:
    """只允许读 voice_clips 下的文件。"""
    p = (LIB / rel).resolve()
    try:
        p.relative_to(LIB.resolve())
    except ValueError:
        return None
    return p if p.is_file() else None
