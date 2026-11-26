"""耳コピ（曲を書き起こして、そのまま弾く）の書き起こしと、曲ごとの保存。

書き起こしはピアノ専用の Transkun（Yan & Duan 2024、重みはパッケージに同梱、CPU で動く）を使う。
入っていなければ Basic Pitch（listen.transcribe、楽器を問わない書き起こし）で写す。

答えの分かっている amane 自身の曲で比べると（tools/eval_cover.py、各 6 曲、docs/listen.md）、
  Basic Pitch：F 値 0.72〜0.75（余計な音が多い）・強さの誤差 18〜21
  Transkun   ：F 値 0.99・強さの誤差 9〜10
書き起こしの速さは、この PC の CPU で音の長さの 0.2 倍くらい（3 分の曲で 40 秒ほど）。

前は「弾くたびに、元の音と自分のピアノで弾いた音を聴き比べて詰める」をしていたが、やめた。比べる物差し
（響きの違い）が、F 値 0.72 と 0.99 の書き起こしでほとんど同じ値になり、詰めると強さが本当の強さから
遠ざかった（Transkun で 10 → 27）。耳コピは曲ごとに data/covers/ に残し、同じ曲はそれを弾く。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def has_transkun() -> bool:
    try:
        import transkun  # noqa: F401
    except ImportError:
        return False
    return True


def transcribe_piano(wav: Path) -> list[list]:
    """Transkun で書き起こす。[開始秒, 長さ秒, 音高, 強さ 1..127]。サステインペダルの間は、離すまで伸ばす。"""
    import mido
    out = wav.with_suffix(".mid")
    r = subprocess.run([sys.executable, "-m", "transkun.transcribe", str(wav), str(out)],
                       capture_output=True, text=True, timeout=1800)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError("Transkun で書き起こせませんでした：" + (r.stderr.strip().splitlines() or ["?"])[-1][:200])
    notes, on, pedal, t = [], {}, [], 0.0
    down = None
    for m in mido.MidiFile(out):
        t += m.time
        if m.type == "control_change" and m.control == 64:
            if m.value >= 64 and down is None:
                down = t
            elif m.value < 64 and down is not None:
                pedal.append((down, t))
                down = None
        elif m.type == "note_on" and m.velocity > 0:
            on[m.note] = (t, m.velocity)
        elif m.type in ("note_off", "note_on") and m.note in on:
            s, v = on.pop(m.note)
            notes.append([s, t, m.note, v])
    if down is not None:
        pedal.append((down, t))
    res = []
    for s, e, p, v in sorted(notes):
        for a, b in pedal:                      # 離した時にペダルを踏んでいれば、ペダルを上げるまで
            if a <= e < b:
                e = b
                break
        res.append([round(s, 4), round(min(8.0, e - s), 4), int(p), int(v)])
    return res


def load(path: Path) -> tuple[list[list], dict] | None:
    """（耳コピの音, 曲の情報）"""
    if not path.is_file():
        return None
    d = json.loads(path.read_text())
    return d["notes"], d.get("info", {})


def save(path: Path, notes: list[list], info: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"notes": notes, "info": info}, ensure_ascii=False))
