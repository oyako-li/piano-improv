"""1 曲としての形（盛り上がりの弧・リフレイン）を数える。作曲を直す前と後、参考の録音を同じ物差しで比べる。

  .venv/bin/python tools/form_stats.py -n 8                 # 今の作曲で 8 曲（初期の好み、seed 1..8）
  .venv/bin/python tools/form_stats.py -n 8 --set drama=1   # 好みの値を固定して
  .venv/bin/python tools/form_stats.py --audio ref.wav ...  # 録音を数える（参考の曲など）

数えるもの（どれも音声から。自分の曲も一度音にして、録音と同じ手順で数える）
  長さ        秒
  音量の幅    4 秒ごとの音量（dB）の上位 10% − 下位 10%。最後の 8 秒（消えていくところ）は除く
  盛り上がり  音量の山（3 窓の移動平均の最大）− 最初の 12 秒の平均（dB）
  頂点        音量の山がある位置（曲の長さに対する割合）
  音の多さ比  8 秒ごとの打鍵数の最大 ÷ 最初の 12 秒の打鍵数
  主題の戻り  後半に、最初の 1/4 と和音・旋律の響き（クロマ）がよく似た 4 秒があるか
"""
from __future__ import annotations

import argparse
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import warnings
from pathlib import Path

import numpy as np

warnings.simplefilter("ignore")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pianoimprov.listen import loudness_arc, transcribe  # noqa: E402

SR = 22050


def stats(wav: Path) -> dict:
    import librosa
    y, sr = librosa.load(str(wav), sr=SR)
    dur = len(y) / sr
    width, build = loudness_arc(y, sr)
    W = 4.0
    rms = np.array([20 * np.log10(np.sqrt(np.mean(y[int(t * sr):int((t + W) * sr)] ** 2)) + 1e-9)
                    for t in np.arange(0, dur - W / 2, W)])
    smooth = np.convolve(rms, np.ones(3) / 3, mode="same")
    ev = transcribe(wav)
    on = np.array(sorted(e[0] for e in ev))
    first = np.sum(on < 12) / 12
    rates = [np.sum((on >= t) & (on < t + 8)) / 8 for t in np.arange(0, max(1, dur - 8), 2)]
    hop = 512
    ch = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop)
    B = int(4 * sr / hop)
    segs = np.array([ch[:, i:i + B].mean(axis=1) for i in range(0, ch.shape[1] - B, B)])
    segs = segs / (np.linalg.norm(segs, axis=1, keepdims=True) + 1e-9)
    n = len(segs)
    head = segs[: max(1, n // 4)]
    ret = [i * 4 for i in range(n // 2, n - 1) if (head @ segs[i]).max() > 0.95]
    return {
        "長さ": dur,
        "音量の幅": width,
        "盛り上がり": build,
        "頂点": float((np.argmax(smooth[1:-1]) + 1) * W / dur) if len(rms) > 3 else 0.0,
        "音の多さ比": float(max(rates) / first) if first > 0 else float("nan"),
        "主題の戻り": ret[0] if ret else None,
    }


def render(seed: int, out: Path, fixed: dict | None = None) -> Path:
    data = Path(tempfile.mkdtemp(prefix="form-"))        # 初期の好みで（--set の値だけ変える）
    if fixed:
        from pianoimprov.taste import Taste
        t = Taste(data)
        t.mu.update(fixed)
        t.save()
    env = dict(os.environ, PIANO_DATA=str(data))
    subprocess.run([sys.executable, "-m", "pianoimprov", "--seed", str(seed), "render", "-n", "1", str(out)],
                   cwd=ROOT, env=env, check=True, capture_output=True)
    return out


def show(name: str, s: dict) -> None:
    ret = f"{s['主題の戻り']:.0f}秒" if s["主題の戻り"] is not None else "なし"
    print(f"{name:<22} {s['長さ']:5.0f}秒  幅 {s['音量の幅']:4.1f}dB  盛り上がり {s['盛り上がり']:+5.1f}dB"
          f"  頂点 {s['頂点']:.2f}  音の多さ比 {s['音の多さ比']:4.2f}  主題の戻り {ret}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=8)
    ap.add_argument("--audio", nargs="*", default=[])
    ap.add_argument("--set", nargs="*", default=[], help="好みの値を固定する（例: drama=1.0）")
    a = ap.parse_args()
    for f in a.audio:
        with contextlib.redirect_stdout(io.StringIO()):
            s = stats(Path(f))
        show(Path(f).stem, s)
    if a.audio:
        return
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for seed in range(1, a.n + 1):
            fixed = {k: float(v) for k, v in (x.split("=") for x in a.set)}
            s = stats(render(seed, Path(tmp) / f"s{seed}.wav", fixed))
            rows.append(s)
            show(f"seed {seed}", s)
    keys = ["長さ", "音量の幅", "盛り上がり", "頂点", "音の多さ比"]
    print("中央値".ljust(22), "  ".join(f"{k} {np.nanmedian([r[k] for r in rows]):.2f}" for k in keys),
          f" 主題の戻り {sum(r['主題の戻り'] is not None for r in rows)}/{len(rows)} 曲")


if __name__ == "__main__":
    main()
