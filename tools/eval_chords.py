"""和音の進行の読み取り（pianoimprov/harmony.py）が、どれだけ当たるかを数える。

答えの分かっている amane 自身の曲を弾き、MP3 にしてから書き起こして和音を読み、弾いた和音と比べる。
  - 半拍ごとの根音・根音と長短（maj / min / sus。7th などのテンションは長短にまとめる）
  - 調（主音と長短）
  - 進行の型 A：読んだ 4 つ組と、本当の A（主音からの半音と長短）が、ずらし方を選んで何個合うか。
    比べる相手として、同じ長短の amane の型から 1 つ選んだときの合い方（当て推量）も出す

  .venv/bin/python tools/eval_chords.py -n 12 --seed 1 [--switch 0.25]
"""
from __future__ import annotations

import argparse
import random
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pianoimprov import harmony  # noqa: E402
from pianoimprov.audio import SR, Piano, write_wav  # noqa: E402
from pianoimprov.composer import Composer  # noqa: E402
from pianoimprov.engine import DEFAULT_SF2  # noqa: E402
from pianoimprov.listen import tempo, to_wav, transcribe  # noqa: E402
from pianoimprov.style import NAMES  # noqa: E402
from pianoimprov.theory import (FILM_MAJOR, FILM_MINOR, IMPRESSIONIST_MAJOR, IMPRESSIONIST_MINOR,  # noqa: E402
                                LABELS, NOTE_NAMES)

CLASS = {"maj": "maj", "maj7": "maj", "dom": "maj", "min": "min", "sus": "sus"}


def qclass(q: str) -> str:
    if "sus" in q:
        return "sus"
    if q.startswith("m") and not q.startswith("maj"):
        return "min"
    return "maj"


def parse(label: str) -> tuple[int, str]:
    """"B♭m11/F" → (10, "min")"""
    name = label.split("/")[0]
    root = max((i for i, n in enumerate(NOTE_NAMES) if name.startswith(n)), key=lambda i: len(NOTE_NAMES[i]))
    suf = name[len(NOTE_NAMES[root]):]
    q = {v: k for k, v in LABELS.items()}.get(suf, suf or "maj")
    return root, qclass(q)


def best_match(true: list[tuple], est: list[tuple]) -> float:
    """4 つ組どうし、ずらし方を選んで合う割合。"""
    n = len(true)
    return max(sum(true[i] == est[(i + k) % len(est)] for i in range(n)) / n for k in range(len(est)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=12)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--switch", type=float, nargs="*", default=[harmony.SWITCH])
    a = ap.parse_args()
    out = Path(tempfile.mkdtemp(prefix="eval_chords-"))
    rng = random.Random(a.seed)
    thetas = [{k: rng.random() for k in NAMES} for _ in range(a.n)]
    it = iter(thetas + [dict(thetas[0])])
    comp = Composer(lambda: dict(next(it)), lambda piece: dict(piece), a.seed)
    piano = Piano(DEFAULT_SF2)
    clock, pieces = 0, []
    sec = comp.next_section()
    for i in range(a.n):
        p = comp.piece
        base, t, bars = clock, int(SR * 0.3), []
        no = sec.piece
        while sec.piece == no:
            for n in sec.notes:
                piano.schedule(base + t + int(n.t * SR), n.pitch, n.vel, int(n.dur * SR))
            bar = sec.meter * 60 / sec.bpm
            for j, lab in enumerate(sec.chords):
                bars.append((t / SR + j * bar, t / SR + (j + 1) * bar, *parse(lab)))
            t += int(sec.duration * SR)
            sec = comp.next_section()
        total = t + SR * 2
        audio = np.concatenate([piano.render(min(SR, total - j)) for j in range(0, total, SR)])
        clock += total
        wav, mp3 = out / f"p{i:02d}.wav", out / f"p{i:02d}.mp3"
        write_wav(wav, audio)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-c:a", "libmp3lame", "-b:a", "128k", str(mp3)], check=True)
        w = to_wav(mp3, out / "x.wav")
        ev, bpm = transcribe(w), tempo(w)
        true_a = [((off) % 12, CLASS[fam]) for off, fam, _ in p.templates["A"]]
        pool = (FILM_MINOR + IMPRESSIONIST_MINOR) if p.minor else (FILM_MAJOR + IMPRESSIONIST_MAJOR)
        guess = np.mean([best_match(true_a, [(o % 12, CLASS[f]) for o, f, _ in g]) for g in pool])
        pieces.append({"bars": bars, "ev": ev, "bpm": bpm, "tonic": p.tonic, "minor": p.minor,
                       "true_a": true_a, "guess": guess, "bpm_true": sec.bpm})
        print(f"曲 {i + 1}/{a.n}: {total / SR:.0f} 秒・{len(ev)} 音・♩ {bpm:.0f}", flush=True)

    for sw in a.switch:
        root = cls = frames = 0
        key_ok, mode_ok, a_root, a_cls, guess = 0, 0, [], [], []
        for pc in pieces:
            segs = harmony.chords(pc["ev"], pc["bpm"], switch=sw)
            for s, e, r, c in pc["bars"]:
                for tt in np.arange(s + 0.1, e, 0.25):
                    g = next((x for x in segs if x[0] <= tt < x[1]), None)
                    if g is None:
                        continue
                    frames += 1
                    root += g[2] == r
                    cls += g[2] == r and qclass(g[3]) == c
            tonic, minor = harmony.key_of(pc["ev"], [x for x in harmony.chords(pc["ev"], pc["bpm"], switch=sw) if x[1] - x[0] >= 0.75 * 60 / pc["bpm"]])
            key_ok += tonic == pc["tonic"] and minor == pc["minor"]
            mode_ok += minor == pc["minor"]
            old = harmony.SWITCH
            harmony.SWITCH = sw
            prog = harmony.progression(pc["ev"], pc["bpm"])
            harmony.SWITCH = old
            if prog:
                # 主音を取り違えても、本当の主音から数え直して比べる（進行の形が合っているか）
                shift = (prog["tonic"] - pc["tonic"]) % 12
                est_r = [((o + shift) % 12, None) for o, f, _ in prog["A"]]
                est_c = [((o + shift) % 12, CLASS[f]) for o, f, _ in prog["A"]]
                a_root.append(best_match([(o, None) for o, _ in pc["true_a"]], est_r))
                a_cls.append(best_match(pc["true_a"], est_c))
            guess.append(pc["guess"])
        n = len(pieces)
        print(f"\nswitch {sw}: 半拍ごと 根音 {root / frames:.0%}・根音と長短 {cls / frames:.0%}（{frames} 点）"
              f"  調 {key_ok}/{n}・長短 {mode_ok}/{n}"
              f"  型 A 根音 {np.mean(a_root):.0%}・根音と長短 {np.mean(a_cls):.0%}（当て推量 {np.mean(guess):.0%}）")


if __name__ == "__main__":
    main()
