"""耳コピの書き起こしが、どれだけ当たるかを数える（Basic Pitch と、ピアノ専用の Transkun を比べる）。

答えの分かっている amane 自身の曲を弾き、MP3 にしてから書き起こし、弾いた音と比べる。
  - 音の当たり方：同じ音高で、出だしが 50ms 以内の音を当たりとして、適合率・再現率・F 値
  - 強さの誤差：当たった音の強さ（velocity）の差の平均
  - かかった時間（音の長さに対する倍率）

  .venv/bin/python tools/eval_cover.py -n 6 --seed 1
"""
from __future__ import annotations

import argparse
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pianoimprov.audio import SR, Piano, write_wav  # noqa: E402
from pianoimprov.composer import Composer  # noqa: E402
from pianoimprov.cover import has_transkun, transcribe_piano  # noqa: E402
from pianoimprov.engine import DEFAULT_SF2, cover_notes  # noqa: E402
from pianoimprov.listen import to_wav, transcribe  # noqa: E402
from pianoimprov.style import NAMES  # noqa: E402


def score(est: list, truth: list) -> tuple[float, float, float, float]:
    used, hit, dv = set(), 0, []
    by_p: dict[int, list] = {}
    for i, (s, _, p, v) in enumerate(truth):
        by_p.setdefault(p, []).append((s, i, v))
    for s, _, p, v in est:
        best = None
        for ts, i, tv in by_p.get(p, []):
            if i not in used and abs(ts - s) <= 0.05 and (best is None or abs(ts - s) < abs(best[0] - s)):
                best = (ts, i, tv)
        if best:
            used.add(best[1])
            hit += 1
            dv.append(abs(best[2] - v))
    prec, rec = hit / max(1, len(est)), hit / max(1, len(truth))
    f = 2 * prec * rec / max(1e-9, prec + rec)
    return prec, rec, f, float(np.mean(dv)) if dv else float("nan")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=6)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    out = Path(tempfile.mkdtemp(prefix="eval_cover-"))
    rng = random.Random(a.seed)
    thetas = [{k: rng.random() for k in NAMES} for _ in range(a.n)]
    it = iter(thetas + [dict(thetas[0])])
    comp = Composer(lambda: dict(next(it)), lambda piece: dict(piece), a.seed)
    names = ["Basic Pitch"] + (["Transkun"] if has_transkun() else [])
    res = {k: [] for k in names}
    took = {k: 0.0 for k in names}
    total_sec = 0.0
    sec = comp.next_section()
    for i in range(a.n):
        piano, t, truth = Piano(DEFAULT_SF2), 0.3, []
        no = sec.piece
        while sec.piece == no:
            for n in sec.notes:
                piano.schedule(int((t + n.t) * SR), n.pitch, n.vel, int(n.dur * SR))
                truth.append((t + n.t, n.dur, n.pitch, n.vel))
            t += sec.duration
            sec = comp.next_section()
        total = int((t + 2) * SR)
        audio = np.concatenate([piano.render(min(SR, total - j)) for j in range(0, total, SR)])
        wav, mp3 = out / "p.wav", out / "p.mp3"
        write_wav(wav, audio)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-c:a", "libmp3lame", "-b:a", "128k", str(mp3)], check=True)
        w = to_wav(mp3, out / "x.wav")
        total_sec += total / SR
        for k in names:
            t0 = time.time()
            est = cover_notes(transcribe(w)) if k == "Basic Pitch" else transcribe_piano(w)
            took[k] += time.time() - t0
            res[k].append(score(est, truth))
        print(f"曲 {i + 1}/{a.n}: 本当の音 {len(truth)}・" + "  ".join(f"{k} F {res[k][-1][2]:.2f}" for k in names), flush=True)
    print(f"\n{'':<12} {'適合率':>6} {'再現率':>6} {'F 値':>6} {'強さの誤差':>8} {'時間（音の長さの倍率）':>12}")
    for k in names:
        p, r, f, dv = np.mean(res[k], axis=0)
        print(f"{k:<12} {p:6.2f} {r:6.2f} {f:6.2f} {dv:8.1f} {took[k] / total_sec:12.2f}")


if __name__ == "__main__":
    main()
