"""書き起こした音符から、調と和音の進行を読む（渡した曲の進行に寄せるため）。

1. 調：音の長さで重みを付けた音高クラスの分布を、Krumhansl–Kessler のプロファイルと比べる
2. 和音：半拍ごとに鳴っている音の分布（クロマ）と最低音を取り、和音の型（根音 12 × 種類）と比べる。
   そのままだと細かく揺れるので、和音を変えるたびに罰を払う最短経路（Viterbi）でならす
3. 進行の型：和音の並びから、いちばんよく繰り返す 4 つ組を A、それと違う次の 4 つ組を S（サビ）にする。
   amane の進行の型（theory.Spec：主音からの半音・種類・低音）に直して返す

当たり方は tools/eval_chords.py で、答えの分かっている amane 自身の演奏を書き起こして数えた（docs/listen.md）。
"""
from __future__ import annotations

from collections import Counter

import numpy as np

from .reference import KK_MAJOR, KK_MINOR
from .theory import Spec

# 和音の型（根音 0 のとき、音高クラス → 重み）と、amane の進行の型での種類
TEMPLATES: dict[str, tuple[dict[int, float], str]] = {
    "maj": ({0: 1, 4: 1, 7: .8}, "maj"),
    "min": ({0: 1, 3: 1, 7: .8}, "min"),
    "dom7": ({0: 1, 4: 1, 7: .8, 10: .8}, "dom"),
    "maj7": ({0: 1, 4: 1, 7: .8, 11: .8}, "maj7"),
    "m7": ({0: 1, 3: 1, 7: .8, 10: .8}, "min"),
    "add9": ({0: 1, 4: 1, 7: .8, 2: .6}, "maj"),
    "madd9": ({0: 1, 3: 1, 7: .8, 2: .6}, "min"),
    "maj9": ({0: 1, 4: 1, 7: .8, 11: .8, 2: .6}, "maj7"),
    "m9": ({0: 1, 3: 1, 7: .8, 10: .8, 2: .6}, "min"),
    "sus4": ({0: 1, 5: 1, 7: .8}, "sus"),
    "sus2": ({0: 1, 2: 1, 7: .8}, "sus"),
}
# 3 度のない sus は、9th の入った長短の和音（3 度が弱く鳴る）と取り違えやすいので、少し選びにくく
PRIOR = {"sus4": -0.04, "sus2": -0.08}
QUALS = list(TEMPLATES)
BASS_W = 0.35        # 最低音が根音のときの加点（転回形・分数和音より基本形を選びやすく）
SWITCH = 1.5         # 和音を変える罰（tools/eval_chords.py の seed 1 で 0〜2 を比べて決めた）
KEY_TONIC_W = 0.5     # 調：主和音が鳴っている割合の重み
KEY_LAST_W = 0.1      # 調：最後の和音が主和音なら
STEP = 0.5           # 拍。和音を見る細かさ

_TPL = np.zeros((12 * len(QUALS), 12))
for qi, q in enumerate(QUALS):
    for r in range(12):
        for iv, w in TEMPLATES[q][0].items():
            _TPL[qi * 12 + r, (r + iv) % 12] = w
_TPL /= np.linalg.norm(_TPL, axis=1, keepdims=True)


def key_of(notes: list[tuple[float, float, int, float]], segs: list | None = None) -> tuple[int, bool]:
    """（主音 0..11, 短調か）。notes は [(開始, 終了, 音高, 強さ)]（秒でも拍でもよい）。
    音の分布だけだと、属調（5 度上）や平行調と取り違えやすい（amane 自身の 24 曲で 6 曲）。
    和音の列 segs を渡すと、分布で上位 4 つの調のうち、主和音が長く鳴っていて、最後が主和音の調を選ぶ
    （tools/eval_chords.py で 18/24 → 22/24）"""
    pc = np.zeros(12)
    for s, e, p, a in notes:
        pc[p % 12] += (e - s) * (0.5 + a)
    cands = sorted(((float(np.corrcoef(np.roll(prof, k), pc)[0, 1]), k, minor)
                    for minor, prof in ((False, KK_MAJOR), (True, KK_MINOR)) for k in range(12)), reverse=True)
    if not segs:
        return cands[0][1], cands[0][2]
    tot = sum(e - s for s, e, *_ in segs) or 1.0

    def score(c):
        corr, k, minor = c
        share = sum(e - s for s, e, r, q, _ in segs if r == k and (TEMPLATES[q][1] == "min") == minor) / tot
        return corr + KEY_TONIC_W * share + KEY_LAST_W * (segs[-1][2] == k)
    best = max(cands[:4], key=score)
    return best[1], best[2]


def chords(notes: list[tuple[float, float, int, float]], bpm: float,
           switch: float | None = None) -> list[tuple[float, float, int, str, int]]:
    """[(開始秒, 終了秒, 根音, 種類, 最低音)]。notes は書き起こし [(開始秒, 終了秒, 音高, 強さ 0..1)]。"""
    switch = SWITCH if switch is None else switch
    if not notes:
        return []
    step = STEP * 60 / bpm
    t0 = min(s for s, *_ in notes)
    t1 = max(e for _, e, *_ in notes)
    n = max(1, int(np.ceil((t1 - t0) / step)))
    chroma = np.zeros((n, 12))
    low = np.full(n, 999)
    for s, e, p, a in notes:
        i0, i1 = int((s - t0) / step), int(np.ceil((e - t0) / step))
        for i in range(max(0, i0), min(n, max(i1, i0 + 1))):
            ws, we = t0 + i * step, t0 + (i + 1) * step
            ov = max(0.0, min(e, we) - max(s, ws))
            if ov <= 0 and i != i0:
                continue
            # 鳴り始めの窓は強く（減衰した音の残りより、打った音の方が和音を表す）
            chroma[i, p % 12] += (ov + (0.5 * step if i == i0 else 0)) * (0.3 + a)
            low[i] = min(low[i], p)
    norm = np.linalg.norm(chroma, axis=1, keepdims=True)
    obs = (chroma / np.maximum(norm, 1e-9)) @ _TPL.T                       # (n, 12*Q) の似ている度合い
    obs += np.repeat([PRIOR.get(q, 0.0) for q in QUALS], 12)
    for i in range(n):
        if low[i] < 999:
            obs[i, [qi * 12 + low[i] % 12 for qi in range(len(QUALS))]] += BASS_W
        if norm[i, 0] < 1e-6:
            obs[i] = 0                                                       # 無音はどの和音でもない
    # Viterbi：和音を変えるたびに switch を払う
    S = obs.shape[1]
    score = obs[0].copy()
    back = np.zeros((n, S), dtype=int)
    for i in range(1, n):
        best = int(np.argmax(score))
        stay = score
        move = score[best] - switch
        back[i] = np.where(stay >= move, np.arange(S), best)
        score = np.maximum(stay, move) + obs[i]
    path = [int(np.argmax(score))]
    for i in range(n - 1, 0, -1):
        path.append(int(back[i, path[-1]]))
    path.reverse()
    out = []
    for i, st in enumerate(path):
        q, r = QUALS[st // 12], st % 12
        b = int(low[i] % 12) if low[i] < 999 else r
        if out and out[-1][2] == r and out[-1][3] == q:
            s, _, _, _, bs = out[-1]
            out[-1] = (s, t0 + (i + 1) * step, r, q, bs)
        else:
            out.append((t0 + i * step, t0 + (i + 1) * step, r, q, b))
    return out


def progression(notes: list[tuple[float, float, int, float]], bpm: float) -> dict | None:
    """渡した曲の進行を、amane の進行の型に直す。
    {"tonic", "minor", "A": [Spec], "S": [Spec] | None, "beats": 和音 1 つの平均の長さ（拍）, "seq": [ラベル]}"""
    segs = chords(notes, bpm)
    beat = 60 / bpm
    segs = [x for x in segs if x[1] - x[0] >= 0.75 * beat]                  # 1 拍に満たない揺れは捨てる
    merged: list[list] = []
    for s, e, r, q, b in segs:
        if merged and merged[-1][2] == r and merged[-1][3] == q:
            merged[-1][1] = e
        else:
            merged.append([s, e, r, q, b])
    if len(merged) < 4:
        return None
    tonic, minor = key_of(notes, merged)
    specs: list[Spec] = []
    for s, e, r, q, b in merged:
        fam = TEMPLATES[q][1]
        # 分数和音の低音は使わない。読んだ低音を付けると、旋律と伴奏が半音で隣り合う割合が 0.8% → 1.1% に増えた
        # （tools/melody_stats.py を、覚えた 5 曲の進行で回した）。低音なしでも amane 自身の型（0.6%）より高い。原因はまだ調べていない
        bass = None
        specs.append(((r - tonic) % 12, fam, bass))
    key = lambda sp: (sp[0], sp[1])                                          # noqa: E731  低音は数えない
    grams = Counter(tuple(key(x) for x in specs[i:i + 4]) for i in range(len(specs) - 3))
    ranked = [g for g, c in grams.most_common() if c >= 2] or [g for g, _ in grams.most_common(1)]

    def rot(g):
        return {g[i:] + g[:i] for i in range(len(g))}

    a = ranked[0]
    s = next((g for g in ranked[1:] if g not in rot(a) and len(set(g)) >= 2), None)
    first = {key(x): x for x in reversed(specs)}                             # 低音は最初に出てきたもの
    to_spec = lambda g: [first[x] for x in g] if g else None                 # noqa: E731
    beats = float(np.mean([(e - s0) / beat for s0, e, *_ in merged]))
    names = ["C", "D♭", "D", "E♭", "E", "F", "F♯", "G", "A♭", "A", "B♭", "B"]
    return {"tonic": tonic, "minor": minor, "A": to_spec(a), "S": to_spec(s), "beats": round(beats, 2),
            "seq": [names[r] + {"maj": "", "min": "m", "dom7": "7", "maj7": "maj7", "m7": "m7",
                                "add9": "add9", "madd9": "m(add9)", "maj9": "maj9", "m9": "m9",
                                "sus4": "sus4", "sus2": "sus2"}[q] for _, _, r, q, _ in merged]}
