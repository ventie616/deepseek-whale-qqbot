"""把歌里的人生换成她的声音：demucs 分离 → 常驻 RVC 换声 → 与原伴奏混音。

用法：python cover_her.py /path/song.mp3 [--out /path/out.wav]
"""

import argparse
import json
import socket
import subprocess
import sys
from pathlib import Path

PY = "/root/.local/share/feiyu-voice/cuda/rvc/venv/bin/python"
RVC_SOCK = "/run/feiyu-rvc.sock"
WORK = Path("/root/cover_work")


def sh(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)


def separate(src: Path) -> tuple[Path, Path]:
    out = WORK / "sep"
    out.mkdir(parents=True, exist_ok=True)
    r = sh([PY, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs", "-o", str(out), str(src)])
    if r.returncode != 0:
        raise RuntimeError("分离失败：" + (r.stderr or "")[-400:])
    stem = out / "htdemucs" / src.stem
    vocals, inst = stem / "vocals.wav", stem / "no_vocals.wav"
    if not vocals.exists() or not inst.exists():
        raise RuntimeError("没找到分离结果")
    return vocals, inst


def convert(vocals: Path) -> Path:
    """通过常驻 daemon 换声。"""
    out_dir = WORK / "rvc"
    out_dir.mkdir(parents=True, exist_ok=True)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(1800)
    s.connect(RVC_SOCK)
    s.sendall((json.dumps({"input": str(vocals), "output_dir": str(out_dir)}) + "\n").encode())
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
    s.close()
    reply = json.loads(buf.decode() or "{}")
    if not reply.get("ok"):
        raise RuntimeError("换声失败：" + str(reply.get("error")))
    return out_dir / "rvc_native.wav"


def _rms_db(path: Path) -> float:
    """读音频算 RMS（dBFS）。"""
    import numpy as np
    import soundfile as sf

    data, rate = sf.read(str(path), always_2d=True)
    mono = data.mean(axis=1) if data.ndim > 1 else data
    rms = float(np.sqrt(np.mean(np.square(mono)))) if mono.size else 0.0
    return 20.0 * np.log10(max(rms, 1e-9))


def mix(inst: Path, her: Path, out: Path) -> Path:
    """把人声抬到比伴奏高 5 dB，再整体响度归一到 -14 LUFS。"""
    inst_db, her_db = _rms_db(inst), _rms_db(her)
    gain_db = max(-18.0, min(18.0, (inst_db + 5.0) - her_db))
    print(f"   伴奏 {inst_db:.1f} dB ／ 人声 {her_db:.1f} dB → 人声增益 {gain_db:+.1f} dB", flush=True)
    r = sh(["ffmpeg", "-y", "-i", str(inst), "-i", str(her),
            "-filter_complex",
            f"[1:a]volume={gain_db:.2f}dB[her];"
            "[0:a][her]amix=inputs=2:duration=longest:normalize=0,"
            "loudnorm=I=-14:TP=-1.5:LRA=11",
            "-ar", "44100", "-ac", "2", str(out)])
    if r.returncode != 0:
        raise RuntimeError("混音失败：" + (r.stderr or "")[-400:])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    src = Path(args.src).resolve()
    out = Path(args.out) if args.out else (Path("/mnt/d/QQrobot/voice_out") / f"cover_{src.stem}.wav")
    out.parent.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)

    print("① 分离人声…", flush=True)
    vocals, inst = separate(src)
    print("   人声：", vocals, flush=True)

    print("② RVC 换成她的声音…", flush=True)
    her = convert(vocals)
    print("   换声完成：", her, flush=True)

    print("③ 混回伴奏…", flush=True)
    mix(inst, her, out)
    print(f"OK {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
