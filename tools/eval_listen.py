"""音声からの曲調の推定（pianoimprov/listen.py）が、どれだけ当たるかを数える。

設定 θ を乱数で決めて曲を弾き（1 曲ずつ θ を変える）、音を MP3 にしてから書き起こして推定し、元の θ と比べる。
比べるのは、同じ曲の MIDI から推定した値（書き起こしで失うもの）と、θ の平均を言うだけの当て推量（推定が役に立つか）。

  .venv/bin/python tools/eval_listen.py -n 16 --seed 1 [--out /tmp/eval_listen]
"""
from __future__ import annotations

import argparse
import json
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
from pianoimprov.engine import DEFAULT_SF2, save_midi  # noqa: E402
from pianoimprov.listen import analyze_audio, to_wav  # noqa: E402
from pianoimprov.reference import analyze  # noqa: E402
from pianoimprov.style import NAMES, PARAMS  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=16)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    out = Path(a.out or tempfile.mkdtemp(prefix="eval_listen-"))
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(a.seed)
    thetas = [{k: rng.random() for k in NAMES} for _ in range(a.n)]
    it = iter(thetas + [dict(thetas[0])])   # 最後の曲の後に次の曲を 1 つ作るぶん
    comp = Composer(lambda: dict(next(it)), lambda piece: dict(piece), a.seed)
    rows = []
    piano = Piano(DEFAULT_SF2)
    clock = 0                      # ピアノの時計（サンプル）。曲をまたいで進み続ける
    sec = comp.next_section()
    for i in range(a.n):
        base = clock
        secs, t = [], int(SR * 0.3)
        piece = sec.piece
        while sec.piece == piece:
            for n in sec.notes:
                piano.schedule(base + t + int(n.t * SR), n.pitch, n.vel, int(n.dur * SR))
            secs.append((t, sec))
            t += int(sec.duration * SR)
            sec = comp.next_section()
        total = t + SR * 2
        audio = np.concatenate([piano.render(min(SR, total - j)) for j in range(0, total, SR)])
        clock += total
        wav, mp3, mid = out / f"p{i:02d}.wav", out / f"p{i:02d}.mp3", out / f"p{i:02d}.mid"
        write_wav(wav, audio)
        save_midi(mid, secs, secs[0][1].bpm)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-c:a", "libmp3lame", "-b:a", "128k", str(mp3)], check=True)
        wav.unlink()
        theta = thetas[i]
        t0 = time.time()
        est_a, info = analyze_audio(to_wav(mp3, out / "x.wav"))
        took = time.time() - t0
        est_m = analyze(mid)
        rows.append({"theta": theta, "audio": est_a, "midi": est_m, "bpm_true": secs[0][1].bpm, **info,
                     "seconds": total / SR, "took": took})
        print(f"曲 {i + 1}/{a.n}: {total / SR:.0f} 秒・書き起こし {info['notes']} 音・♩ {info['bpm']}（本当は {secs[0][1].bpm:.0f}）・{took:.0f} 秒", flush=True)
    (out / "result.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    report(rows)
    print(f"\n→ {out / 'result.json'}")


def report(rows: list[dict]) -> None:
    print(f"\n{'値':<18}{'n':>3}  {'MIDI r':>7} {'MIDI 誤差':>9}  {'音声 r':>7} {'音声 誤差':>9}  {'当て推量の誤差':>12}")
    for k in NAMES:
        th = np.array([r["theta"][k] for r in rows])
        line = f"{PARAMS[k][0][:9]:<12}{k[:6]:>6}"
        any_ = False
        cells = []
        for src in ("midi", "audio"):
            idx = [i for i, r in enumerate(rows) if k in r[src]]
            if len(idx) < 4:
                cells.append(f"{'—':>7} {'—':>9}")
                continue
            any_ = True
            e = np.array([rows[i][src][k] for i in idx])
            t = th[idx]
            rr = np.corrcoef(t, e)[0, 1] if e.std() > 1e-9 else float("nan")
            cells.append(f"{rr:7.2f} {np.mean(np.abs(t - e)):9.2f}")
        if any_:
            base = np.mean(np.abs(th - th.mean()))
            print(f"{line} {len(rows):>3}  {cells[0]}  {cells[1]}  {base:12.2f}")


if __name__ == "__main__":
    main()
