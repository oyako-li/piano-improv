"""旋律の癖を、書き起こした音符から数える（渡した曲の旋律の癖に寄せるため）。

旋律そのもの（音の並び）は写さない。数えるのは次の 2 つの分布だけ：
  - リズム：1 拍を 16 分音符 4 つに分け、旋律の音が鳴り始める位置の並び（"x.x." なら 8 分 2 つ）。
    1 拍ごとに数えて割合にする（"...." は前の音を伸ばしているか休み）
  - 音の動き：となりの旋律音が、音階の上で何歩離れているか（0 = 同じ音、1 = 隣の音 … 5 以上）

旋律は「同時に鳴り始めた音のうちいちばん高い音」で近似する（melody_line）。拍は librosa の拍の推定（テンポは listen.tempo で決めたもの）。
当たり方は tools/eval_habits.py（答えの分かっている amane 自身の曲を書き起こして数えた。docs/listen.md）。
"""
from __future__ import annotations

from collections import Counter

import numpy as np

from .theory import scale_pitches

CELLS = [f"{a}{b}{c}{d}" for a in "x." for b in "x." for c in "x." for d in "x."]
MAX_STEP = 5
MEL_PCT = 70        # 旋律とみなす高さ（全体の音の高さの何 % より上か）
MEL_DROP = 7        # 前の旋律音よりこれ以上低い音は伴奏とみなす（半音）


def melody_line(events: list[tuple[float, float, int, float]]) -> list[tuple[float, int]]:
    """書き起こし [(開始秒, 終了秒, 音高, 強さ)] → 旋律 [(開始秒, 音高)]。
    同時に鳴り始めた音（0.05 秒以内）のうちいちばん高い音で、全体の上位 30% の高さにあるもの。
    ただし直前の旋律 4 音の中央の高さより 7 半音を超えて低いものは伴奏とみなして飛ばす（1 音だけと比べると、
    高い音が 1 つ入ったあと旋律を全部飛ばした）（飛ばさないと、旋律の切れ目に
    伴奏の音が入り、5 歩以上の跳躍が 30〜56% に見えた。本当は 0〜2%。tools/eval_habits.py の seed 1）"""
    if not events:
        return []
    floor = float(np.percentile([p for _, _, p, _ in events], MEL_PCT))
    groups: list[list[tuple[float, int]]] = []
    for s, _, p, _ in sorted(events):
        if groups and s - groups[-1][0][0] <= 0.05:
            groups[-1].append((s, p))
        else:
            groups.append([(s, p)])
    out: list[tuple[float, int]] = []
    for g in groups:
        top = max(q for _, q in g)
        ref = float(np.median([q for _, q in out[-4:]])) if out else top    # 直前の 4 音の中央の高さ
        if top < floor or top < ref - MEL_DROP:
            continue
        out.append((g[0][0], top))
    return out


def to_beats(line: list[tuple[float, int]], beat_times: np.ndarray) -> list[tuple[float, int]]:
    """秒 → 拍（推定した拍の時刻の間を直線で）。拍の外の音は捨てる。"""
    bt = np.asarray(beat_times, dtype=float)
    if len(bt) < 4:
        return []
    out = []
    for t, p in line:
        if bt[0] <= t < bt[-1]:
            i = int(np.searchsorted(bt, t, side="right") - 1)
            out.append((i + (t - bt[i]) / (bt[i + 1] - bt[i]), p))
    return out


def habits(mel: list[tuple[float, int]], tonic: int, minor: bool) -> dict | None:
    """旋律 [(拍, 音高)] → {"cells": {"x.x.": 割合, ...}, "steps": [0 歩, 1 歩, … 5 歩以上 の割合], "n": 音の数}"""
    if len(mel) < 16:
        return None
    mel = sorted(mel)
    grid: dict[int, set[int]] = {}
    for b, _ in mel:
        slot = int(round((b % 1) * 4))
        beat = int(b) + (1 if slot == 4 else 0)
        grid.setdefault(beat, set()).add(slot % 4)
    first, last = int(mel[0][0]), int(mel[-1][0])
    cells = Counter("".join("x" if k in grid.get(i, ()) else "." for k in range(4)) for i in range(first, last + 1))
    total = sum(cells.values())
    scale = scale_pitches(tonic, minor, 24, 108)
    idx = [min(range(len(scale)), key=lambda i: abs(scale[i] - p)) for _, p in mel]
    steps = Counter(min(MAX_STEP, abs(b - a)) for a, b in zip(idx, idx[1:]))
    ns = sum(steps.values())
    return {"cells": {c: round(cells[c] / total, 4) for c in CELLS if cells[c]},
            "steps": [round(steps[k] / ns, 4) for k in range(MAX_STEP + 1)], "n": len(mel)}


# amane 自身の旋律の癖（tools/eval_habits.py --seed 1 の 12 曲、作曲した旋律で数えた平均）
AMANE = {"cells": {"x...": 0.429, "....": 0.297, "x.x.": 0.172, "..x.": 0.069, "xxx.": 0.033},
         "steps": [0.209, 0.47, 0.209, 0.086, 0.018, 0.008]}
# 読んだ癖をどれだけ使うか。読み取りは、16 分と跳躍を多めに読む（伴奏の細かい音が旋律に混ざる）。
# 当たるのは 8 分の割合と隣の音へ動く割合くらいなので（docs/listen.md）、amane 自身の癖と半分ずつ混ぜる
TRUST = 0.5


def blend(h: dict, trust: float = TRUST) -> dict:
    """読んだ癖を amane 自身の癖と混ぜる（作曲に渡す形）。"""
    cells = {c: trust * h["cells"].get(c, 0) + (1 - trust) * AMANE["cells"].get(c, 0) for c in CELLS}
    return {"cells": {c: round(v, 4) for c, v in cells.items() if v > 0},
            "steps": [round(trust * a + (1 - trust) * b, 4) for a, b in zip(h["steps"], AMANE["steps"])]}


def tvd(a: dict | list, b: dict | list) -> float:
    """2 つの分布の違い（全変動距離、0 = 同じ、1 = まったく重ならない）。"""
    if isinstance(a, dict):
        keys = set(a) | set(b)
        return 0.5 * sum(abs(a.get(k, 0) - b.get(k, 0)) for k in keys)
    return 0.5 * sum(abs(x - y) for x, y in zip(a, b))
