"""即興の作曲エンジン。

1 曲 = いくつかのセクション（intro / A / B / A' / outro）。曲ごとに調・拍子・テンポ・動機を決め、
セクションごとに和声・伴奏・旋律をその場で作る。出力は秒単位の音符列。
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Callable

from .style import Style
from .theory import (Chord, choose_template, key_name, nearest_with_pc, realize,
                     scale_pcs, scale_pitches)

PIECE_PARAMS = ("tempo", "triple", "darkness", "impressionism")

PLANS = [
    ["intro", "A", "A'", "B", "A'", "outro"],
    ["intro", "A", "B", "A'", "outro"],
    ["intro", "A", "A'", "outro"],
    ["intro", "A", "B", "B'", "A'", "outro"],
]
MAJOR_TONICS = [1, 3, 5, 7, 0, 2, 8, 10]
MINOR_TONICS = [9, 2, 4, 1, 6, 11, 0]


@dataclass
class Note:
    t: float      # 秒（セクション先頭から）
    dur: float    # 秒
    pitch: int
    vel: int
    part: str     # "mel" / "rh" / "lh"


@dataclass
class Section:
    notes: list[Note]
    duration: float
    piece: int
    part: str
    key: str
    meter: int
    bpm: float
    chords: list[str]
    theta: dict[str, float]


@dataclass
class _Ev:
    beat: float
    dur: float
    pitch: int
    vel: int
    part: str
    roll: float = 0.0     # 秒。分散和音のずらし


@dataclass
class _Piece:
    no: int
    theta: dict[str, float]
    tonic: int
    minor: bool
    meter: int
    plan: list[str]
    templates: dict[str, list] = field(default_factory=dict)
    motifs: dict[str, tuple] = field(default_factory=dict)
    pos: int = 0


class Composer:
    def __init__(self, sample_piece: Callable[[], dict], sample_section: Callable[[dict], dict],
                 seed: int | None = None):
        self.rng = random.Random(seed)
        self.sample_piece = sample_piece
        self.sample_section = sample_section
        self.piece: _Piece | None = None
        self.piece_no = 0
        self.prev_mel = 70
        self.prev_bass = 43

    # ------------------------------------------------------------------ 曲
    def start_new_piece(self) -> None:
        self.piece = None

    def _new_piece(self) -> _Piece:
        rng = self.rng
        th = self.sample_piece()
        minor = rng.random() < th["darkness"]
        tonic = rng.choice(MINOR_TONICS if minor else MAJOR_TONICS)
        meter = 3 if rng.random() < th["triple"] else 4
        plan = list(rng.choice(PLANS))
        self.piece_no += 1
        p = _Piece(self.piece_no, th, tonic, minor, meter, plan)
        p.templates["A"] = choose_template(minor, th["impressionism"], rng)
        p.templates["B"] = choose_template(minor, th["impressionism"], rng)
        for _ in range(4):
            if p.templates["B"] is not p.templates["A"]:
                break
            p.templates["B"] = choose_template(minor, th["impressionism"], rng)
        # B は転調することがある
        p.templates["B_key"] = (tonic, minor)
        if rng.random() < 0.35:
            if rng.random() < th["impressionism"]:
                p.templates["B_key"] = ((tonic + rng.choice([8, 4, 5])) % 12, minor)
            else:
                # 平行調: 短調 → 短3度上の長調、長調 → 短3度下の短調
                p.templates["B_key"] = ((tonic + 3) % 12, False) if minor else ((tonic + 9) % 12, True)
        return p

    def next_section(self) -> Section:
        if self.piece is None or self.piece.pos >= len(self.piece.plan):
            self.piece = self._new_piece()
        p = self.piece
        part = p.plan[p.pos]
        last_a = part == "A'" and p.pos == len(p.plan) - 2
        p.pos += 1
        th = self.sample_section(p.theta)
        return self._compose(p, part, th, cadence=last_a or part == "outro")

    # ------------------------------------------------------------ セクション
    def _compose(self, p: _Piece, part: str, th: dict, cadence: bool) -> Section:
        rng = self.rng
        st = Style(th)
        B = p.meter
        base = part.rstrip("'")
        if base == "B":
            tonic, minor = p.templates["B_key"]
        else:
            tonic, minor = p.tonic, p.minor

        bars = {"intro": 4 if th["space"] > 0.6 else 2, "outro": 3}.get(part, 8)
        bars_per_chord = 1 if rng.random() < th["harmonic_pace"] else 2

        # ---- 和声
        if part == "intro":
            specs = p.templates["A"][:max(1, bars // bars_per_chord)]
        elif part == "outro":
            sub = (5, "maj7", None) if not minor else (5, "min", None)
            specs = [sub, (0, "min" if minor else "maj", None), (0, "min" if minor else "maj", None)]
            bars_per_chord = 1
        else:
            tpl = p.templates[base]
            n = bars // bars_per_chord
            specs = [tpl[i % len(tpl)] for i in range(n)]
            if cadence:
                specs[-1] = (0, "min" if minor else "maj", None)
        chords: list[Chord] = []
        for s in specs:
            c = realize(s, tonic, th["color"], rng)
            chords += [c] * bars_per_chord
        chords = (chords + [chords[-1]] * bars)[:bars]

        center = st.melody_center + (3 if base == "B" else 0)
        acc_v = st.base_velocity - 10
        evs: list[_Ev] = []

        # ---- 伴奏
        pattern = self._pick_pattern(th, B)
        if part == "outro":
            pattern = "block"
        for bar, c in enumerate(chords):
            second = bar > 0 and chords[bar - 1] is c
            evs += self._accomp_bar(c, bar * B, B, pattern, center, acc_v, second, rng)

        # ---- 旋律
        if part != "intro":
            evs += self._melody(p, part, chords, B, center, st, th, tonic, minor, cadence, rng)
        elif th["space"] < 0.5 and rng.random() < 0.5:
            # 前奏で高い音をひとつ置く
            c = chords[-1]
            evs.append(_Ev((bars - 1) * B, B, nearest_with_pc(center + 7, c.core_pcs), st.base_velocity - 4, "mel"))

        if part == "outro":
            # 最後の和音: 低音から旋律の下までを分散で
            c = chords[-1]
            last = bars - 1
            lo = self._bass_pitch(c.bass)
            tones = [lo] + [q for q in range(lo + 7, center + 1) if q % 12 in c.pcs][::2][:6]
            for i, q in enumerate(tones):
                evs.append(_Ev(last * B, B * 1.5, q, acc_v + 2 - i, "lh", roll=0.07 * i))

        # ---- ペダル: 和音が変わるまで響かせる
        changes = [bar * B for bar in range(bars) if bar == 0 or chords[bar] is not chords[bar - 1]] + [bars * B]
        ped = th["pedal"]
        for e in evs:
            nxt = next((c for c in changes if c > e.beat + 1e-6), bars * B)
            if e.part == "lh":
                e.dur = max(e.dur, e.dur + (nxt - e.beat - e.dur) * min(1.0, ped * 1.6))
            else:
                e.dur = max(e.dur, min(nxt - e.beat, e.dur + ped * 1.5))

        # ---- 長い旋律音と半音（短9度）でぶつかる伴奏の内声を抜く
        mel_long = [e for e in evs if e.part == "mel" and e.dur >= 1]
        bass_beats = {}
        for e in evs:
            if e.part == "lh":
                bass_beats[e.beat] = min(bass_beats.get(e.beat, 127), e.pitch)
        keep = []
        for e in evs:
            if e.part == "lh" and e.pitch != bass_beats.get(e.beat) and any(
                    m.beat < e.beat + e.dur and e.beat < m.beat + m.dur
                    and ((m.pitch - e.pitch) in (1, 13) or (e.pitch - m.pitch) == 1) for m in mel_long):
                continue
            keep.append(e)
        evs = keep

        # ---- 時間に直す（ルバート）
        spb = 60.0 / st.bpm
        factors = []
        for bar in range(bars):
            f = 1 + rng.gauss(0, 0.03 * th["rubato"])
            if (bar + 1) % 4 == 0:
                f += 0.12 * th["rubato"]
            if part == "outro":
                f += 0.15 * bar + 0.25 * th["rubato"] * bar
            factors.append(max(0.8, f))
        cum = [0.0]
        for f in factors:
            cum.append(cum[-1] + B * spb * f)

        def sec(beat: float) -> float:
            bar = min(int(beat // B), bars - 1)
            return cum[bar] + (beat - bar * B) * spb * factors[bar]

        jitter = 0.006 + 0.012 * th["rubato"]
        notes = []
        for e in evs:
            t0 = sec(e.beat) + e.roll + rng.gauss(0, jitter)
            t1 = sec(min(e.beat + e.dur, bars * B)) + max(0.0, e.beat + e.dur - bars * B) * spb + e.roll
            notes.append(Note(max(0.0, t0), max(0.08, t1 - t0), e.pitch,
                              max(12, min(115, int(e.vel))), e.part))
        duration = cum[-1] + (3.0 if part == "outro" else 0.0)
        notes = _fix_overlaps(notes)
        self.prev_mel = center
        return Section(notes, duration, p.no, part, key_name(tonic, minor), B, st.bpm,
                       [c.label() for c in chords], th)

    # ----------------------------------------------------------------- 伴奏
    def _pick_pattern(self, th: dict, B: int) -> str:
        f = th["flow"]
        w = {"arp": f * 1.4, "pulse": f * 0.35, "sway": (1 - f) * (1.0 if B == 3 else 0.6),
             "block": (1 - f) * 0.7}
        r = self.rng.random() * sum(w.values())
        for k, v in w.items():
            r -= v
            if r <= 0:
                return k
        return "arp"

    def _bass_pitch(self, pc: int) -> int:
        cands = [q for q in range(36, 51) if q % 12 == pc]
        b = min(cands, key=lambda q: abs(q - self.prev_bass))
        self.prev_bass = b
        return b

    def _voicing(self, c: Chord, bass: int, ceiling: int, n: int = 4) -> list[int]:
        out: list[int] = []
        for q in range(bass + 3, ceiling):
            if q % 12 not in c.pcs:
                continue
            if q < 52 and q < bass + 7:      # 低い3度は濁るので避ける
                continue
            if out and q - out[-1] < 2:
                continue
            out.append(q)
            if len(out) >= n:
                break
        return out or [bass + 12]

    def _accomp_bar(self, c: Chord, start: float, B: int, pattern: str, center: int,
                    v: int, second: bool, rng: random.Random) -> list[_Ev]:
        bass = self._bass_pitch(c.bass) if not second else self.prev_bass
        ceiling = min(center - 4, 74)
        vo = self._voicing(c, bass, ceiling)
        ev: list[_Ev] = []
        if pattern == "arp":
            tones = [bass] + vo
            up = list(range(len(tones)))
            path = up + up[-2:0:-1]
            if second:
                path = path[1:] + path[:1]
            for i in range(B * 2):
                k = path[i % len(path)]
                ev.append(_Ev(start + i * 0.5, 0.5, tones[k], v + (5 if k == 0 else 0) - (i % 2) * 3, "lh"))
        elif pattern == "sway":
            if not second or rng.random() < 0.6:
                ev.append(_Ev(start, B, bass, v + 4, "lh"))
            for q in vo[:3]:
                ev.append(_Ev(start + 1, B - 1, q, v - 4, "lh"))
            if B == 4:
                for q in vo[:3]:
                    ev.append(_Ev(start + 3, 1, q, v - 9, "lh"))
        elif pattern == "pulse":
            ev.append(_Ev(start, B, bass, v + 2, "lh"))
            for beat in range(B):
                for q in vo[:3]:
                    ev.append(_Ev(start + beat, 1, q, v - 8 + (2 if beat == 0 else 0), "lh"))
        else:  # block
            if not second or rng.random() < 0.4:
                tones = [bass] + vo
                for i, q in enumerate(tones):
                    ev.append(_Ev(start, B, q, v + (4 if i == 0 else -2) - (6 if second else 0), "lh",
                                  roll=0.03 * i))
        return ev

    # ----------------------------------------------------------------- 旋律
    def _rhythm_bar(self, B: int, d: float, s: float, rng: random.Random, cadence: bool) -> list[tuple]:
        """(拍, 長さ) のリスト。None の音高は休符扱いしない（休符は入れない）。"""
        if cadence:
            ev = []
            start = 0.0
            if rng.random() < 0.4 and B >= 3:
                ev.append((0.0, 1.0))
                start = 1.0
            length = B - start - (1.0 if (B - start) > 2 and rng.random() < s else 0.0)
            ev.append((start, length))
            return ev
        ev, pos = [], 0.0
        while pos < B - 1e-9:
            rem = B - pos
            opts: list[tuple[float, list, float]] = [
                (1, [(0, 1)], 1.0),
                (1, [(0, .5), (.5, .5)], d * 1.6),
                (1, [], s * s * (3.0 if pos > 0 else 1.0)),
                (1, [(0, .25), (.25, .25), (.5, .5)], d * d * 0.5),
            ]
            if rem >= 2:
                opts += [(2, [(0, 2)], (1 - d) * 1.5), (2, [(0, 1.5), (1.5, .5)], 0.7)]
            if rem >= 3:
                opts.append((3, [(0, 3)], (1 - d) * 0.7))
            r = rng.random() * sum(o[2] for o in opts)
            for length, cell, w in opts:
                r -= w
                if r <= 0:
                    break
            ev += [(pos + a, b) for a, b in cell]
            pos += length
        return ev

    def _melody(self, p: _Piece, part: str, chords: list[Chord], B: int, center: int, st: Style,
                th: dict, tonic: int, minor: bool, cadence: bool, rng: random.Random) -> list[_Ev]:
        bars = len(chords)
        base = part.rstrip("'")
        d, s = th["density"], th["space"]
        scale = scale_pitches(tonic, minor)
        penta = set(scale_pcs(tonic, minor, penta=True))
        lo, hi = center - 10, min(86, center + 10)

        # 動機（2 小節）。同じ曲の同じ部分では使い回す
        if base in p.motifs:
            rhythm, contour = p.motifs[base]
        else:
            rhythm = [(a, b) for a, b in self._rhythm_bar(B, d, s * 0.6, rng, False)]
            rhythm += [(a + B, b) for a, b in self._rhythm_bar(B, d, s, rng, False)]
            contour = None
            if part != "outro":
                p.motifs[base] = (rhythm, None)

        # 8 小節: 動機 動機 応答 半終止 / 動機' 動機' 応答 終止
        plan: list[tuple[str, float]] = []
        if part == "outro":
            plan = [("free", 0), ("cad", 1), ("hold", 2)]
        else:
            for ph in range(bars // 4):
                plan += [("motif", 0), ("motif", 1), ("free", 2), ("cad" if ph % 2 == 0 else "end", 3)]

        notes: list[tuple[float, float, str]] = []   # (拍, 長さ, 種類)
        for bar, (kind, _) in enumerate(plan):
            o = bar * B
            if kind == "motif":
                half = 0 if (bar % 4) == 0 else B
                notes += [(o + a - half, b, "motif") for a, b in rhythm if half <= a < half + B]
            elif kind == "free":
                if rng.random() < s * 0.4:
                    continue                          # 旋律を休んで伴奏だけにする（息つぎ）
                notes += [(o + a, b, "free") for a, b in self._rhythm_bar(B, d, s, rng, False)]
            elif kind in ("cad", "end"):
                notes += [(o + a, b, kind) for a, b in self._rhythm_bar(B, d, s, rng, True)]
            else:  # hold
                notes.append((o, B * 1.5, "end"))

        prev = max(lo, min(hi, self.prev_mel))
        last_iv = 0
        stored = contour          # 前のセクションで決めた動機の音程（なければ今回決める）
        recorded: list[int] = []
        occ_j = [0, 0]
        out: list[_Ev] = []
        for t, dur, kind in notes:
            bar = min(int(t // B), bars - 1)
            c = chords[bar]
            strong = (t % B) == 0 or (B == 4 and (t % B) == 2) or dur >= 1.5
            x = (t % (4 * B)) / (4 * B)
            target = center - 3 + 7 * math.sin(math.pi * x)

            steps_src = None
            if kind == "motif":
                occ = 0 if (bar % 8) < 4 else 1
                j = occ_j[occ]
                occ_j[occ] += 1
                steps_src = stored if occ == 0 else (stored or recorded)
                if steps_src is not None and j >= len(steps_src):
                    steps_src = None

            if kind == "end":
                pcs = {tonic} if rng.random() < 0.6 else {(tonic + (3 if minor else 4)) % 12, (tonic + 7) % 12}
                pitch = nearest_with_pc(int(prev), pcs, lo, hi)
            elif kind == "cad":
                pcs = c.core_pcs - {c.root} or c.core_pcs
                pitch = nearest_with_pc(int(target), pcs, lo, hi)
            elif steps_src is not None and j > 0:
                # 動機の繰り返し: 音階上の音程を保って、今の和音へ移す
                pitch = _step(scale, prev, steps_src[j])
                if (strong or dur >= 1) and _avoid(pitch, c):
                    pitch = nearest_with_pc(pitch, c.core_pcs, lo, hi)
            elif strong or (steps_src is not None and j == 0):
                pool = c.core_pcs if rng.random() < 0.7 else c.pcs
                cands = [q for q in range(lo, hi + 1) if q % 12 in pool]
                w_step = 1.2 - th["leapiness"]
                pitch = min(cands, key=lambda q: abs(q - prev) * w_step + abs(q - target) * 0.45
                            + (3.0 if q == prev else 0.0) + rng.random() * 2.5
                            + (th["pentatonic"] * 6.0 if q % 12 not in penta else 0.0)
                            + (2.0 if _clash(q, c) else 0.0))
            else:
                direction = 1 if target > prev else -1
                if abs(last_iv) >= 5:
                    direction = -1 if last_iv > 0 else 1        # 跳躍のあとは反対へ
                elif rng.random() < 0.3:
                    direction = -direction
                r = rng.random()
                lp = th["leapiness"]
                steps = 1 if r > lp * 0.6 else (2 if r > lp * 0.25 else rng.randint(3, 5))
                if not (lo <= _step(scale, prev, direction * steps) <= hi):
                    direction = -direction
                pitch = _step(scale, prev, direction * steps)
                if pitch % 12 not in penta and rng.random() < th["pentatonic"]:
                    pitch = _step(scale, pitch, direction)
                if dur >= 1 and _avoid(pitch, c):
                    pitch = nearest_with_pc(pitch, c.core_pcs, lo, hi)
            if pitch % 12 not in penta and kind != "end" and rng.random() < th["pentatonic"]:
                alt = [q for q in range(pitch - 2, pitch + 3) if q % 12 in penta and not _clash(q, c)]
                if alt:
                    pitch = min(alt, key=lambda q: abs(q - pitch))
            pitch = max(lo, min(hi, pitch))
            if kind == "motif" and (bar % 8) < 4 and stored is None:
                recorded.append(0 if not recorded else _scale_dist(scale, prev, pitch))
            last_iv = pitch - prev
            prev = pitch

            vel = st.base_velocity + 8 + 6 * math.sin(math.pi * x) + (3 if strong else 0) + rng.gauss(0, 3)
            out.append(_Ev(t, dur, pitch, vel, "mel"))
            # 長い音の下に和声音を添える
            if dur >= 2 and rng.random() < th["color"] * 0.35:
                below = [q for q in range(pitch - 9, pitch - 2) if q % 12 in c.pcs]
                if below:
                    out.append(_Ev(t, dur, rng.choice(below), vel - 14, "rh"))
        if base in p.motifs and p.motifs[base][1] is None and recorded:
            p.motifs[base] = (rhythm, recorded)
        self.prev_mel = prev
        return out


def _step(scale: list[int], pitch: int, steps: int) -> int:
    idx = min(range(len(scale)), key=lambda i: abs(scale[i] - pitch))
    return scale[max(0, min(len(scale) - 1, idx + steps))]


def _scale_dist(scale: list[int], a: int, b: int) -> int:
    ia = min(range(len(scale)), key=lambda i: abs(scale[i] - a))
    ib = min(range(len(scale)), key=lambda i: abs(scale[i] - b))
    return ib - ia


def _avoid(pitch: int, c: Chord) -> bool:
    """和音の構成音の半音上（例: 長3度の上の4度）は、長く伸ばすと濁る。"""
    pc = pitch % 12
    return pc not in c.pcs and any((pc - x) % 12 == 1 for x in c.core_pcs)


def _clash(pitch: int, c: Chord) -> bool:
    """和音の構成音と半音でぶつかる（長7度の響きを含む）。"""
    pc = pitch % 12
    return pc not in c.pcs and any((pc - x) % 12 in (1, 11) for x in c.pcs)


def _fix_overlaps(notes: list[Note]) -> list[Note]:
    """同じ高さの音が重なると次の打鍵が切れるので、前の音を短くする。"""
    by_pitch: dict[int, list[Note]] = {}
    for n in sorted(notes, key=lambda n: n.t):
        by_pitch.setdefault(n.pitch, []).append(n)
    out = []
    for lst in by_pitch.values():
        keep: list[Note] = []
        for n in lst:
            if keep and n.t - keep[-1].t < 0.03:
                keep[-1].vel = max(keep[-1].vel, n.vel)
                keep[-1].dur = max(keep[-1].dur, n.dur)
                continue
            if keep and keep[-1].t + keep[-1].dur > n.t - 0.01:
                keep[-1].dur = max(0.03, n.t - 0.01 - keep[-1].t)
            keep.append(n)
        out += keep
    return sorted(out, key=lambda n: n.t)
