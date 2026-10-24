"""把一条 B 站音频加进唱歌素材库（整首 + 3 段节选）。

用法：
    python tools/add_song.py BV1NXer6mEtA
    python tools/add_song.py BV1NXer6mEtA --name "At Last I See You"
"""

import argparse
import json
import pathlib
import subprocess
import urllib.request
import wave

import numpy as np

ROOT = pathlib.Path(r"C:\QQBot")
ACC = json.loads((ROOT / "bili_account.json").read_text(encoding="utf-8"))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
HDR = {
    "User-Agent": UA,
    "Referer": "https://www.bilibili.com/",
    "Cookie": f"SESSDATA={ACC['sessdata']}; bili_jct={ACC['bili_jct']}; DedeUserID={ACC.get('dedeuserid','')}",
}
FF = r"C:\Users\venti\miniforge3\Library\bin\ffmpeg.EXE"
SONGS = ROOT / "downloads" / "songs"
LIB = ROOT / "voice_clips"
CLIP_LEN = 12
N_CLIPS = 3


def api_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def download(url: str, dst: pathlib.Path) -> None:
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=180) as r, dst.open("wb") as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)


def pick_segments(src: pathlib.Path, n: int = N_CLIPS) -> list[int]:
    hp = src.with_suffix(".hp.wav")
    subprocess.run(
        [FF, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
         "-af", "highpass=f=120,highpass=f=120", "-ac", "1", "-ar", "16000", str(hp)],
        check=True,
    )
    with wave.open(str(hp)) as w:
        sr = w.getframerate()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    total = len(data) / sr
    sec = sr
    rms = np.array(
        [float(np.sqrt(np.mean(data[i * sec:(i + 1) * sec] ** 2))) for i in range(len(data) // sec)]
    )
    cand = sorted(((float(rms[i:i + CLIP_LEN].mean()), i) for i in range(max(1, len(rms) - CLIP_LEN))), reverse=True)
    picks: list[int] = []
    for _, i in cand:
        if i < 3 or i + CLIP_LEN > total - 2:
            continue
        if all(abs(i - j) > 18 for j in picks):
            picks.append(i)
        if len(picks) >= n:
            break
    hp.unlink(missing_ok=True)
    return sorted(picks)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bvid")
    ap.add_argument("--name", default="")
    args = ap.parse_args()

    d = api_json(f"https://api.bilibili.com/x/web-interface/view?bvid={args.bvid}")["data"]
    cid = (d.get("pages") or [{}])[0]["cid"]
    name = args.name or d.get("title", args.bvid)
    up = (d.get("owner") or {}).get("name", "")
    print(f"《{name}》 {d.get('title')} | UP {up} | {d.get('duration')}s")

    p = api_json(f"https://api.bilibili.com/x/player/playurl?avid={d['aid']}&cid={cid}&fnval=16&fourk=0")
    audios = ((p.get("data") or {}).get("dash") or {}).get("audio") or []
    if not audios:
        print("拿不到音频流")
        return 1
    audios.sort(key=lambda a: a.get("bandwidth") or 0)
    src = SONGS / f"{args.bvid}.m4a"
    SONGS.mkdir(parents=True, exist_ok=True)
    download(audios[-1].get("baseUrl") or audios[-1].get("base_url"), src)
    print(f"下载 {src.name} {src.stat().st_size/1024/1024:.1f} MB")

    (LIB / "full").mkdir(parents=True, exist_ok=True)
    (LIB / "clips").mkdir(parents=True, exist_ok=True)
    idx_path = LIB / "index.json"
    index = json.loads(idx_path.read_text(encoding="utf-8")) if idx_path.exists() else {"version": 2, "clips": [], "fulls": []}
    index.setdefault("clips", [])
    index.setdefault("fulls", [])
    index["clips"] = [c for c in index["clips"] if c.get("bvid") != args.bvid]
    index["fulls"] = [c for c in index["fulls"] if c.get("bvid") != args.bvid]

    credit = "AI 翻唱（音色：大肥鱼 / 参考 BV1PPYM6EEUR）；仅供本机私聊自用，请勿公开传播"

    # 整首
    full_out = LIB / "full" / f"{args.bvid}.mp3"
    subprocess.run(
        [FF, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
         "-ac", "1", "-ar", "32000", "-b:a", "96k", str(full_out)], check=True
    )
    index["fulls"].append({
        "id": f"full_{args.bvid}", "type": "full", "file": f"full/{full_out.name}",
        "song": name, "bvid": args.bvid, "title": d.get("title"), "up": up,
        "duration": round(d.get("duration") or 0), "size_kb": round(full_out.stat().st_size / 1024),
        "credit": credit,
    })
    print(f"  整首 {full_out.name}  {full_out.stat().st_size/1024/1024:.1f} MB")

    # 节选
    for start in pick_segments(src):
        out = LIB / "clips" / f"{args.bvid}_{start:03d}.mp3"
        subprocess.run(
            [FF, "-y", "-hide_banner", "-loglevel", "error",
             "-ss", str(start), "-t", str(CLIP_LEN), "-i", str(src),
             "-af", "highpass=f=80,loudnorm=I=-16:TP=-1.5:LRA=11,"
                    f"afade=t=in:st=0:d=0.35,afade=t=out:st={CLIP_LEN-0.5}:d=0.5",
             "-ac", "1", "-ar", "24000", "-b:a", "64k", str(out)], check=True
        )
        index["clips"].append({
            "id": f"clip_{args.bvid}_{start}", "type": "clip", "file": f"clips/{out.name}",
            "song": name, "bvid": args.bvid, "title": d.get("title"), "up": up,
            "start": start, "duration": CLIP_LEN, "size_kb": round(out.stat().st_size / 1024),
            "credit": credit,
        })
        print(f"    节选 {out.name} @{start}s {out.stat().st_size/1024:.0f} KB")

    idx_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    # 同步 meta.json，保证 build_voice_lib.py 重建时不会丢这首歌
    meta_path = SONGS / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    meta[args.bvid] = {
        "name": name, "title": d.get("title"), "up": up,
        "duration": round(d.get("duration") or 0), "file": str(src),
        "total_sec": round(index["fulls"][-1].get("duration") or 0),
        "picks": [{"start": c["start"], "end": c["start"] + CLIP_LEN, "energy": 0}
                  for c in index["clips"] if c.get("bvid") == args.bvid],
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n库内：节选 {len(index['clips'])} 段 / 整曲 {len(index['fulls'])} 首")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
