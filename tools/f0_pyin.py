"""对比：源人声 vs 换声后人声的基频范围（半音）。"""

import sys
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf


def stats(path: Path) -> str:
    data, sr = sf.read(str(path), always_2d=True)
    mono = np.ascontiguousarray(data.mean(axis=1).astype(np.float32))
    if sr != 16000:
        mono = librosa.resample(mono, orig_sr=sr, target_sr=16000)
    f0, vflag, vprob = librosa.pyin(mono, fmin=80, fmax=800, sr=16000,
                                    frame_length=1024, fill_na=np.nan)
    voiced = f0[~np.isnan(f0)]
    if voiced.size < 50:
        return "有效音高太少"
    semi = 12 * np.log2(voiced / 440.0)
    p10, p90 = np.percentile(semi, [10, 90])
    return (f"P10-P90 {p90 - p10:5.1f} 半音 | 标准差 {semi.std():4.2f} | "
            f"中位 {np.median(voiced):6.1f} Hz | 有声帧 {voiced.size}")


for f in sys.argv[1:]:
    p = Path(f)
    print(f"{p.stem[:28]:30} {stats(p) if p.exists() else '不存在'}")
