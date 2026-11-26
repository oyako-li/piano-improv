"""旋律の癖の読み取り（pianoimprov/habits.py）が、どれだけ当たるかを数える。

答えの分かっている amane 自身の曲を弾き、MP3 にしてから書き起こして旋律の癖を数え、作曲した旋律の癖と比べる。
違いは全変動距離（0 = 同じ分布、1 = まったく重ならない）。比べる相手は「ほかの曲の本当の癖の平均」を言うだけの当て推量。

  .venv/bin/python tools/eval_habits.py -n 12 --seed 1
"""
from __future__ import annotations

import argparse
import pickle
import random
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pianoimprov.composer as C  # noqa: E402
from pianoimprov import harmony  # noqa: E402
from pianoimprov.audio import SR, Piano, write_wav  # noqa: E402
from pianoimprov.engine import DEFAULT_SF2  # noqa: E402
from pianoimprov.habits import CELLS, MAX_STEP, habits, melody_line, to_beats, tvd  # noqa: E402
from pianoimprov.listen import beat_times, tempo, to_wav, transcribe  # noqa: E402
from pianoimprov.style import NAMES  # noqa: E402

_mel: list = []
_orig = C.Composer._melody


def _spy(self, p, part, chords, B, *a, **k):
    out = _orig(self, p, part, chords, B, *a, **k)
    _mel.append([(e.beat, e.pitch) for e in out if e.part == "mel"])
    return out


C.Composer._melody = _spy


def mean_dist(hs: list[dict]) -> dict:
    return {"cells": {c: float(np.mean([h["cells"].get(c, 0) for h in hs])) for c in CELLS},
            "steps": [float(np.mean([h["steps"][k] for h in hs])) for k in range(MAX_STEP + 1)]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=12)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--keep", help="途中の結果を pickle で残す場所（読み取りを直して数え直すとき用）")
    a = ap.parse_args()
    out = Path(tempfile.mkdtemp(prefix="eval_habits-"))
    rng = random.Random(a.seed)
    thetas = [{k: rng.random() for k in NAMES} for _ in range(a.n)]
    it = iter(thetas + [dict(thetas[0])])
    comp = C.Composer(lambda: dict(next(it)), lambda piece: dict(piece), a.seed)
    piano = Piano(DEFAULT_SF2)
    clock, rows = 0, []
    _mel.clear()
    sec = comp.next_section()
    for i in range(a.n):
        p = comp.piece
        base, t, beat0, truth = clock, int(SR * 0.3), 0.0, []
        no = sec.piece
        while sec.piece == no:
            mel = _mel.pop(0) if _mel else []
            if sec.part != "intro" and mel:
                truth += [(beat0 + b, q) for b, q in mel]
            for n in sec.notes:
                piano.schedule(base + t + int(n.t * SR), n.pitch, n.vel, int(n.dur * SR))
            beat0 += len(sec.chords) * sec.meter
            t += int(sec.duration * SR)
            sec = comp.next_section()
        _mel.clear()
        total = t + SR * 2
        audio = np.concatenate([piano.render(min(SR, total - j)) for j in range(0, total, SR)])
        clock += total
        wav, mp3 = out / f"p{i:02d}.wav", out / f"p{i:02d}.mp3"
        write_wav(wav, audio)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-c:a", "libmp3lame", "-b:a", "128k", str(mp3)], check=True)
        w = to_wav(mp3, out / "x.wav")
        ev, bpm = transcribe(w), tempo(w)
        bt = beat_times(w, bpm)
        rows.append({"ev": ev, "bpm": bpm, "beats": bt, "truth": habits(truth, p.tonic, p.minor),
                     "tonic": p.tonic, "minor": p.minor})
        print(f"曲 {i + 1}/{a.n}: {len(ev)} 音・♩ {bpm:.0f}・拍 {len(bt)}", flush=True)
    if a.keep:
        Path(a.keep).write_bytes(pickle.dumps(rows))
    report(rows)


def estimate(r: dict) -> dict | None:
    segs = [x for x in harmony.chords(r["ev"], r["bpm"]) if x[1] - x[0] >= 0.75 * 60 / r["bpm"]]
    tonic, minor = harmony.key_of(r["ev"], segs)
    return habits(to_beats(melody_line(r["ev"]), r["beats"]), tonic, minor)


def report(rows: list[dict]) -> None:
    e_c, e_s, g_c, g_s = [], [], [], []
    for i, r in enumerate(rows):
        est = estimate(r)
        if not est or not r["truth"]:
            continue
        others = mean_dist([x["truth"] for j, x in enumerate(rows) if j != i and x["truth"]])
        e_c.append(tvd(est["cells"], r["truth"]["cells"]))
        e_s.append(tvd(est["steps"], r["truth"]["steps"]))
        g_c.append(tvd(others["cells"], r["truth"]["cells"]))
        g_s.append(tvd(others["steps"], r["truth"]["steps"]))
    print(f"\n{len(e_c)} 曲  リズムの違い 読み取り {np.mean(e_c):.2f}（当て推量 {np.mean(g_c):.2f}）"
          f"  音の動きの違い 読み取り {np.mean(e_s):.2f}（当て推量 {np.mean(g_s):.2f}）")


if __name__ == "__main__":
    main()
