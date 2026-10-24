"""重建唱歌素材库：每首歌 1 份完整音频 + 3 段节选。

结构：
  voice_clips/full/<bvid>.mp3      整首（96k 单声道，体积小）
  voice_clips/clips/<bvid>_<start>.mp3   12 秒节选
  voice_clips/index.json           {clips:[...], fulls:[...]}
"""

import json
import pathlib
import subprocess

ROOT = pathlib.Path(r"C:\QQBot")
SONGS = ROOT / "downloads" / "songs"
LIB = ROOT / "voice_clips"
FULL = LIB / "full"
CLIPS = LIB / "clips"
FF = r"C:\Users\venti\miniforge3\Library\bin\ffmpeg.EXE"

FULL.mkdir(parents=True, exist_ok=True)
CLIPS.mkdir(parents=True, exist_ok=True)
# 清掉旧结构与旧片段
for p in LIB.glob("*.mp3"):
    p.unlink()
for p in CLIPS.glob("*.mp3"):
    p.unlink()

meta = json.loads((SONGS / "meta.json").read_text(encoding="utf-8"))
clips, fulls = [], []
for bvid, info in meta.items():
    src = pathlib.Path(info["file"])
    if not src.exists():
        print(f"跳过（源文件没了）{bvid}")
        continue

    # 1) 整首 -> mp3 96k
    full_out = FULL / f"{bvid}.mp3"
    subprocess.run(
        [FF, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
         "-ac", "1", "-ar", "32000", "-b:a", "96k", str(full_out)],
        check=True,
    )
    fulls.append(
        {
            "id": f"full_{bvid}",
            "type": "full",
            "file": f"full/{full_out.name}",
            "song": info["name"],
            "bvid": bvid,
            "title": info["title"],
            "up": info["up"],
            "duration": info.get("total_sec") or 0,
            "size_kb": round(full_out.stat().st_size / 1024),
            "credit": info.get("credit"),
        }
    )
    print(f"  整首 {full_out.name}  {full_out.stat().st_size/1024/1024:.1f} MB  《{info['name']}》")

    # 2) 三段节选
    picks = (info.get("picks") or [])[:3]
    for pick in picks:
        start, length = pick["start"], 12
        out = CLIPS / f"{bvid}_{start:03d}.mp3"
        subprocess.run(
            [FF, "-y", "-hide_banner", "-loglevel", "error",
             "-ss", str(start), "-t", str(length), "-i", str(src),
             "-af", "highpass=f=80,loudnorm=I=-16:TP=-1.5:LRA=11,"
                    f"afade=t=in:st=0:d=0.35,afade=t=out:st={length-0.5}:d=0.5",
             "-ac", "1", "-ar", "24000", "-b:a", "64k", str(out)],
            check=True,
        )
        clips.append(
            {
                "id": f"clip_{bvid}_{start}",
                "type": "clip",
                "file": f"clips/{out.name}",
                "song": info["name"],
                "bvid": bvid,
                "title": info["title"],
                "up": info["up"],
                "start": start,
                "duration": length,
                "size_kb": round(out.stat().st_size / 1024),
                "credit": info.get("credit"),
            }
        )
        print(f"    节选 {out.name}  @{start}s  {out.stat().st_size/1024:.0f} KB")

index = {"version": 2, "clips": clips, "fulls": fulls}
(LIB / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

print(f"\n节选 {len(clips)} 段（{sum(c['size_kb'] for c in clips)/1024:.1f} MB）"
      f"｜整首 {len(fulls)} 首（{sum(f['size_kb'] for f in fulls)/1024:.1f} MB）")
