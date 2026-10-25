"""唱歌素材库操作：加歌（B站 / 本地文件）、列表、删除。

控制台和命令行共用这套逻辑。

    from audio_lib import add_from_bvid, add_from_file, list_songs, delete_song
"""

from __future__ import annotations

import json
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
    title = name.strip() or data.get("title") or bvid
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
