"""コマンド。

  piano play               ずっと即興で弾く（演奏中に + / - / 言葉で好みを教える）
  piano serve              ずっと即興で弾き、ブラウザの UI から操作する（http://127.0.0.1:8765）
  piano render -n 2 out.wav 曲を WAV に書き出す（試聴・確認用）
  piano learn a.mid b.mid  好きな曲の MIDI から好みを推定して取り込む
  piano show               いまの好みを表示
  piano reset              好みを初期値に戻す
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

import numpy as np

from .audio import SR, Piano, write_wav
from .composer import Composer
from .engine import DATA, DEFAULT_SF2, Engine, save_midi
from .style import NAMES, PARAMS
from .taste import Taste

HELP = """\
  +  / ++   いい / すごくいい        -  / --   微妙 / かなり微妙
  s         この曲を MIDI に保存       n         次の曲へ
  p         いまの好みを表示           q         終わる
  それ以外   言葉で指示（例: もっと静かに / 雨の夜っぽく / 3拍子で）"""


def bar(v: float, w: int = 12) -> str:
    k = int(round(v * w))
    return "█" * k + "·" * (w - k)


def show_taste(t: Taste) -> None:
    print(f"評価 {t.n} 回 / 探索の幅 {t.spread:.2f}")
    for k in NAMES:
        d, a, b, _ = PARAMS[k]
        print(f"  {d:<14}{a:>12} {bar(t.mu[k])} {b}  {t.mu[k]:.2f}")


def announce(sec) -> None:
    mark = "♪" if sec.part != "intro" else "\n♪"
    print(f"{mark} 曲{sec.piece} {sec.part:<5} {sec.key:<9} {sec.meter}/4 ♩={sec.bpm:.0f}  "
          f"{' '.join(sec.chords[::max(1, len(sec.chords) // 4)])}", flush=True)


# ---------------------------------------------------------------------------- commands
def cmd_play(a) -> None:
    print("ピアノを読み込み中…", flush=True)
    eng = Engine(Path(a.sf2), a.seed, on_message=lambda m: print("  " + m, flush=True), on_section=announce)
    print(HELP, flush=True)
    try:
        for line in sys.stdin:
            cmd = line.strip()
            if not cmd:
                continue
            if cmd in ("q", "quit", "exit"):
                break
            if cmd in ("+", "++", "-", "--", "g", "b"):
                eng.rate({"+": 1, "g": 1, "++": 2, "-": -1, "b": -1, "--": -2}[cmd])
            elif cmd == "s":
                eng.save_current()
            elif cmd == "n":
                eng.skip()
            elif cmd == "p":
                show_taste(eng.taste)
            elif cmd in ("h", "?"):
                print(HELP)
            else:
                threading.Thread(target=eng.words, args=(cmd,), daemon=True).start()
    except KeyboardInterrupt:
        pass
    finally:
        eng.close()


def cmd_serve(a) -> None:
    from .server import serve

    def make_engine() -> Engine:
        print("ピアノを読み込み中…", flush=True)
        return Engine(Path(a.sf2), a.seed, on_message=lambda m: print("  " + m, flush=True), on_section=announce,
                      speaker=not a.no_speaker)
    serve(make_engine, a.host, a.port)


def cmd_render(a) -> None:
    taste = Taste(DATA, a.seed)
    piano = Piano(Path(a.sf2))
    comp = Composer(taste.sample_piece, taste.sample_section, a.seed)
    secs, t = [], int(SR * 0.3)
    while True:
        sec = comp.next_section()
        if sec.piece > a.pieces:
            break
        for n in sec.notes:
            piano.schedule(t + int(n.t * SR), n.pitch, n.vel, int(n.dur * SR))
        secs.append((t, sec))
        print(f"曲{sec.piece} {sec.part:<5} {sec.key:<9} {sec.meter}/4 ♩={sec.bpm:.0f} {' '.join(sec.chords)}")
        t += int(sec.duration * SR)
    total = t + SR * 2
    audio = np.concatenate([piano.render(min(SR, total - i)) for i in range(0, total, SR)])
    out = Path(a.out)
    write_wav(out, audio)
    save_midi(out.with_suffix(".mid"), secs, secs[0][1].bpm)
    peak = float(np.abs(audio).max())
    print(f"→ {out}（{total / SR:.0f} 秒, peak {peak:.2f}）と {out.with_suffix('.mid')}")


def cmd_learn(a) -> None:
    from .reference import analyze
    taste = Taste(DATA)
    ests = []
    for f in a.files:
        try:
            e = analyze(f)
        except Exception as ex:  # noqa: BLE001
            print(f"  × {f}: {ex}")
            continue
        ests.append(e)
        print(f"  {Path(f).name}: " + ", ".join(f"{PARAMS[k][0]} {v:.2f}" for k, v in e.items()))
    if not ests:
        return
    keys = {k for e in ests for k in e}
    target = {k: float(np.mean([e[k] for e in ests if k in e])) for k in keys}
    taste.blend(target, a.weight, {"files": [str(f) for f in a.files]})
    print(f"{len(ests)} 曲から取り込みました（重み {a.weight}）")
    show_taste(taste)


def main() -> None:
    ap = argparse.ArgumentParser(prog="piano", description="落ち着いて優雅なピアノを即興で弾き続ける AI")
    ap.add_argument("--sf2", default=str(DEFAULT_SF2))
    ap.add_argument("--seed", type=int, default=None)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("play")
    sv = sub.add_parser("serve")
    sv.add_argument("--host", default="127.0.0.1", help="スマホから使うなら 0.0.0.0（同じネットワークの誰でも操作できる）")
    sv.add_argument("--port", type=int, default=8765)
    sv.add_argument("--no-speaker", action="store_true", help="PC のスピーカーでは鳴らさず、ブラウザへの配信だけにする")
    r = sub.add_parser("render")
    r.add_argument("out", nargs="?", default="render.wav")
    r.add_argument("-n", "--pieces", type=int, default=1)
    le = sub.add_parser("learn")
    le.add_argument("files", nargs="+")
    le.add_argument("-w", "--weight", type=float, default=0.5)
    sub.add_parser("show")
    sub.add_parser("reset")
    a = ap.parse_args()
    if a.cmd in (None, "play"):
        cmd_play(a)
    elif a.cmd == "serve":
        cmd_serve(a)
    elif a.cmd == "render":
        cmd_render(a)
    elif a.cmd == "learn":
        cmd_learn(a)
    elif a.cmd == "show":
        show_taste(Taste(DATA))
    elif a.cmd == "reset":
        p = DATA / "taste.json"
        if p.exists():
            p.rename(p.with_suffix(f".{int(time.time())}.bak"))
        print("好みを初期値に戻しました（前のものは .bak に残しています）")


if __name__ == "__main__":
    main()
