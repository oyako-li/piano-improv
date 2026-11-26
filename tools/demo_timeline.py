"""render と同じ乱数で曲を作り直し、セクションの開始時刻・調・和音と、音符を JSON で出す（試聴ページの表示用）。

音符は [開始秒, 長さ, 音高, 強さ, 手]（手は "l" か "r"）。ページのピアノの鍵盤を動かすのに使う。

  PIANO_DATA=<render と同じ> python tools/demo_timeline.py <seed> <曲数> > docs/demo.json
"""
import json
import sys

from pianoimprov.composer import Composer
from pianoimprov.engine import DATA
from pianoimprov.taste import Taste

seed, pieces = int(sys.argv[1]), int(sys.argv[2])
taste = Taste(DATA, seed)
comp = Composer(taste.sample_piece, taste.sample_section, seed)
pos, out, notes = 0.3, [], []            # render は 0.3 秒から鳴らし始める
while True:
    s = comp.next_section()
    if s.piece > pieces:
        break
    out.append({"t": round(pos, 1), "piece": s.piece, "part": s.part, "key": s.key, "meter": s.meter,
                "bpm": round(s.bpm), "chords": s.chords})
    notes += [[round(pos + n.t, 3), round(n.dur, 3), n.pitch, n.vel, "l" if n.part == "lh" else "r"] for n in s.notes]
    pos += s.duration
out.append({"t": round(pos, 1), "end": True})
print(json.dumps({"sections": out, "notes": sorted(notes)}, ensure_ascii=False, separators=(",", ":")))
