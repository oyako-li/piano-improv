"""旋律のつながりと濁りを、作曲した音符（声部つき）から数える。作曲を直す前と後を比べる。

  .venv/bin/python tools/melody_stats.py            # このリポジトリの作曲で 24 曲（初期の好み、seed 1..24）
  .venv/bin/python tools/melody_stats.py <場所>     # 別の checkout（git worktree add で前の版を出して比べる）

数えるもの
  止まる小節      フレーズの 2・3 小節目で、旋律が 1 音以下の小節の割合（旋律が前へ進まない）
  音形の使い回し  旋律の 3 つの音程の並びが、同じセクションで前にも出た割合（動機から作れているか）
  音の重なり      旋律の音が次の旋律の音と 0.15 秒より長く重なる割合（線がにじむ）
  隣で濁る        旋律と伴奏が、同時に半音・短 9 度で隣り合う割合（0.06 秒以内の重なりは除く）
  和音と濁る長さ  旋律の音が、和音にない音で、鳴っている伴奏と音名で半音違う長さの割合（調の外の和音の上と中で分ける）
  境目の跳び      セクションの境目で旋律が 9 半音より大きく跳ぶ割合
  跳躍            となりの旋律音との音程が 6 半音以上（増 4 度以上）の割合
  戻らない跳躍    6 半音以上跳んだあと、次の音が反対向きに 1〜2 音階（4 半音以内）で戻らない割合
  続けて跳ぶ      6 半音以上の跳躍が 2 回続く割合（跳躍のうち）
  順次進行        となりの旋律音との音程が 2 半音以内（同じ音を除く）の割合
  ジグザグ        4 半音以上動いて、すぐ反対へ 4 半音以上動く割合（となりの音程の組のうち）
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
import pianoimprov.composer as C  # noqa: E402
from pianoimprov.taste import Taste  # noqa: E402
from pianoimprov.theory import scale_pcs  # noqa: E402

N = 24
log: list[tuple] = []
_orig = C.Composer._compose


def _spy(self, p, part, th, cadence, *a, **k):
    sec = _orig(self, p, part, th, cadence, *a, **k)
    tonic, minor = p.templates["B_key"] if part.rstrip("'") == "B" else (p.tonic, p.minor)
    log.append((p.no, sec, set(scale_pcs(tonic, minor))))
    return sec


C.Composer._compose = _spy
for seed in range(1, N + 1):
    t = Taste(Path(tempfile.mkdtemp()), seed)
    comp = C.Composer(t.sample_piece, t.sample_section, seed)
    while comp.next_section().part != "outro":
        pass

c = dict(stall=0, mid=0, over=0, pairs=0, rep=0, grams=0, adj=0, mels=0, bound=0, nb=0, leap=0, unres=0, dbl=0,
         step=0, moves=0, zig=0, ivpairs=0)
clash = {True: [0.0, 0.0], False: [0.0, 0.0]}     # 調の外の和音か → [濁る長さ, 長さ]
last = {}
for piece, sec, scale in log:
    acc = [x for x in sec.notes if x.part != "mel"]
    mel = sorted((x for x in sec.notes if x.part == "mel"), key=lambda x: x.t)
    if mel:
        if piece in last:
            c["nb"] += 1
            c["bound"] += abs(mel[0].pitch - last[piece]) > 9
        last[piece] = mel[-1].pitch
    for m in mel:
        snd = [o for o in acc if o.t <= m.t + 0.02 and m.t < o.t + o.dur - 0.06]
        during = {o.pitch % 12 for o in acc if o.t < m.t + m.dur and m.t < o.t + o.dur}
        c["mels"] += 1
        c["adj"] += any(abs(m.pitch - o.pitch) in (1, 13) for o in snd)
        pcs = {o.pitch % 12 for o in snd}
        pc = m.pitch % 12
        r = clash[bool(pcs - scale)]
        r[0] += m.dur * (pc not in during and any((pc + d) % 12 in pcs for d in (1, -1)))
        r[1] += m.dur
    if sec.part in ("intro", "outro"):
        continue
    bars = len(sec.chords)
    bar_len = sec.duration / bars
    per = [0] * bars
    for x in mel:
        per[min(bars - 1, int(x.t / bar_len))] += 1
    for b in range(bars):
        if b % 4 in (1, 2):
            c["mid"] += 1
            c["stall"] += per[b] <= 1
    for a, b in zip(mel, mel[1:]):
        c["pairs"] += 1
        c["over"] += (a.t + a.dur) - b.t > 0.15
    iv = [b.pitch - a.pitch for a, b in zip(mel, mel[1:])]
    for i, x in enumerate(iv):
        if x != 0:
            c["moves"] += 1
            c["step"] += abs(x) <= 2
        if i + 1 < len(iv):
            c["ivpairs"] += 1
            c["zig"] += abs(x) >= 4 and abs(iv[i + 1]) >= 4 and x * iv[i + 1] < 0
        if abs(x) >= 6:
            c["leap"] += 1
            if i + 1 < len(iv):
                y = iv[i + 1]
                c["unres"] += not (y * x < 0 and abs(y) <= 4)
                c["dbl"] += abs(y) >= 6
    seen = set()
    for g in (tuple(iv[i:i + 3]) for i in range(len(iv) - 2)):
        c["grams"] += 1
        c["rep"] += g in seen
        seen.add(g)


def pc_(a, b):
    return f"{100 * a / max(b, 1e-9):4.1f}%"


print(f"{root}（{N} 曲）")
print(f"  止まる小節 {pc_(c['stall'], c['mid'])}  音形の使い回し {pc_(c['rep'], c['grams'])}  音の重なり {pc_(c['over'], c['pairs'])}")
print(f"  隣で濁る {pc_(c['adj'], c['mels'])}  和音と濁る長さ：調の中の和音 {pc_(*clash[False])} / 調の外の和音 {pc_(*clash[True])}"
      f"  境目の跳び {pc_(c['bound'], c['nb'])}")
print(f"  跳躍 {pc_(c['leap'], c['pairs'])}  戻らない跳躍 {pc_(c['unres'], c['leap'])}  続けて跳ぶ {pc_(c['dbl'], c['leap'])}"
      f"  順次進行 {pc_(c['step'], c['moves'])}  ジグザグ {pc_(c['zig'], c['ivpairs'])}")
