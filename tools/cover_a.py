"""翻唱主流程（A 方案参数）：旧模型 + pitch -2 + 温暖后期。

在 WSL 里跑：
  python cover_a.py <歌曲> --out out.wav
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

SRC = Path("/root/.local/share/feiyu-voice/cuda/rvc/source")
WORK = Path("/root/cover_a")
PY = "/root/.local/share/feiyu-voice/cuda/rvc/venv/bin/python"

MODEL = "/root/.local/share/feiyu-voice/cuda/rvc/source/logs/feiyu_hq/feiyu_hq_120e_10080s.pth"
INDEX = "/root/.local/share/feiyu-voice/cuda/rvc/source/logs/feiyu_hq/feiyu_hq.index"

PITCH = -2
INDEX_RATE = 0.55
PROTECT = 0.33


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def separate(src: Path) -> tuple[Path, Path]:
    out = WORK / "sep" / src.stem
    if (out / "vocals.wav").exists():
        return out / "vocals.wav", out / "no_vocals.wav"
    out.mkdir(parents=True, exist_ok=True)
    r = sh([PY, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs", "-o", str(out), str(src)])
    if r.returncode != 0:
        raise RuntimeError("分离失败：" + (r.stderr or "")[-300:])
    stem = out / "htdemucs" / src.stem
    return stem / "vocals.wav", stem / "no_vocals.wav"


def rms_db(path: Path) -> float:
    import numpy as np
    import soundfile as sf

    data, _ = sf.read(str(path), always_2d=True)
    mono = data.mean(axis=1)
    return 20.0 * float(np.log10(max(float(np.sqrt(np.mean(np.square(mono)))), 1e-9)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--out", required=True)
    ap.add_argument("--pitch", type=int, default=0)
    ap.add_argument("--index-rate", type=float, default=0.3)
    ap.add_argument("--reverb", type=float, default=0.0)
    ap.add_argument("--protect", type=float, default=0.5)
    ap.add_argument("--compress", type=float, default=0)
    ap.add_argument("--loudnorm", type=int, default=1)
    ap.add_argument("--f0-method", default="crepe")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--index", default=INDEX)
    ap.add_argument("--volume-envelope", type=float, default=1.0)
    args = ap.parse_args()

    WORK.mkdir(parents=True, exist_ok=True)
    src = Path(args.src).resolve()
    print("① 分离人声…", flush=True)
    vocals, inst = separate(src)

    print(f"② 换声（pitch {args.pitch} / index {args.index_rate}）…", flush=True)
    os.environ.update(
        APPLIO_CONFIG_FILE=str(SRC / "assets" / "config.json"),
        APPLIO_LOGS_DIR=str(WORK / "logs"),
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        OMP_NUM_THREADS="4",
        PYTHONDONTWRITEBYTECODE="1",
    )
    os.chdir(SRC)
    sys.path.insert(0, str(SRC))
    import torch
    from rvc.infer.infer import VoiceConverter

    torch.set_num_threads(4)
    native = WORK / "native.wav"
    vc = VoiceConverter()
    vc.convert_audio(
        audio_input_path=str(vocals),
        audio_output_path=str(native),
        model_path=args.model,
        index_path=args.index,
        pitch=args.pitch,
        f0_method=args.f0_method,
        index_rate=args.index_rate,
        volume_envelope=args.volume_envelope,
        protect=args.protect,
        sid=0,
        export_format="WAV",
    )

    import shutil as _sh

    _sh.copy2(native, Path(args.out).with_name(Path(args.out).stem + "_native.wav"))
    print("③ 温暖后期 + 混音…", flush=True)
    inst_db, her_db = rms_db(inst), rms_db(native)
    gain = max(-18.0, min(18.0, (inst_db + 5.0) - her_db))
    print(f"   伴奏 {inst_db:.1f} dB ／ 人声 {her_db:.1f} dB → 人声 {gain:+.1f} dB", flush=True)
    # 包裹感：低频托底、3k 稍收、6.5k 以上收、轻压缩
    comp = (f"acompressor=threshold=-18dB:ratio={args.compress}:attack=15:release=220"
            if args.compress > 1 else "")
    norm = "loudnorm=I=-14:TP=-1.5:LRA=11" if args.loudnorm else "alimiter=limit=0.95"
    chain = (
        f"[1:a]volume={gain:.2f}dB,"
        "highpass=f=70,"
        "bass=g=4:f=120,"
        "equalizer=f=3200:t=q:w=1.2:g=-1.5,"
        "treble=g=-2:f=6500"
        + ("," + comp if comp else "")
        + (f",aecho=0.8:0.7:22|34:{args.reverb:.2f}|{args.reverb*0.6:.2f}" if args.reverb > 0 else "")
        + "[her];[0:a][her]amix=inputs=2:duration=longest:normalize=0,"
        + norm
    )
    out = Path(args.out)
    r = sh(["ffmpeg", "-y", "-i", str(inst), "-i", str(native),
            "-filter_complex", chain, "-ar", "44100", "-ac", "2", str(out)])
    if r.returncode != 0:
        raise RuntimeError("混音失败：" + (r.stderr or "")[-300:])
    print(f"OK {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
