"""即興の作曲エンジン。

1 曲 = いくつかのセクション（intro / A / B / S（サビ）/ A' / outro）。曲ごとに調・拍子・テンポ・動機を決め、
セクションごとに和声・伴奏・旋律をその場で作る。出力は秒単位の音符列。

曲の形（起承転結）は、セクションごとのエネルギー（0..1）で作る。起（A）は静かに、承（A'）で少し厚く、
転（B）で盛り上げていき、サビ（S）が頂点。結で A の主題を強く戻してから引いていく。
エネルギーは強さ・伴奏の厚さ・旋律の音域と細かさに効き、セクションの中でも小節ごとに次へ向けて上げ下げする。
どれだけ盛り上げるかは好みの drama（0 なら平らに流れる）。
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Callable

from .style import Style
from .theory import (Chord, choose_template, chord_scale_pcs, key_name, nearest_with_pc, realize,
                     scale_pcs)

PIECE_PARAMS = ("tempo", "triple", "darkness", "impressionism", "drama")

# (セクション, 始めのエネルギー, 終わりのエネルギー)。tools/form_stats.py で参考の録音と弧を比べて決めた
PLANS: list[list[tuple[str, float, float]]] = [
    # 起 承 転 サビ 結（主題を強く戻す）
    [("intro", .15, .2), ("A", .25, .35), ("A'", .35, .5), ("B", .5, .8), ("S", .85, 1.0),
     ("A'", .8, .55), ("outro", .35, .05)],
    # サビを 2 回（間に A を挟む）
    [("intro", .15, .2), ("A", .25, .4), ("B", .45, .75), ("S", .85, .95), ("A'", .55, .5),
     ("S", .85, 1.0), ("outro", .4, .05)],
    # サビを続けて 2 回（2 回目はもう一段上げる）
    [("intro", .15, .2), ("A", .25, .35), ("A'", .35, .5), ("B", .5, .8), ("S", .8, .9),
     ("S'", .9, 1.0), ("outro", .4, .05)],
    # 短い形（サビなし。B を山にする）
    [("intro", .15, .2), ("A", .25, .4), ("B", .5, .9), ("A'", .75, .5), ("outro", .35, .05)],
]
CALM = 0.35        # drama = 0 のときのエネルギー（どのセクションもこの高さで平らに流れる）
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
    index: int = 0                      # 曲の中で何番目のセクションか
    plan: tuple[str, ...] = ()          # 曲のセクションの並び（画面の進みのバー用）
    title: str = ""                     # 耳コピで弾く曲の題名（part が "cover" のとき）


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
    plan: list[tuple[str, float, float]]
    templates: dict[str, list] = field(default_factory=dict)
    motifs: dict[str, tuple] = field(default_factory=dict)
    pos: int = 0
    habits: dict | None = None      # 渡した曲の旋律の癖（habits.habits）。寄せている間だけ


class Composer:
    def __init__(self, sample_piece: Callable[[], dict], sample_section: Callable[[dict], dict],
                 seed: int | None = None):
        self.rng = random.Random(seed)
        self.sample_piece = sample_piece
        self.sample_section = sample_section
        self.piece: _Piece | None = None
        self.piece_no = 0
        self.prev_mel = 70
        self.prev_iv = 0                  # 旋律の最後の動き（半音）と、最後に跳んでから何音たったか
        self.since_leap = 9
        self.prev_bass = 43

    # ------------------------------------------------------------------ 曲
    def start_new_piece(self) -> None:
        self.piece = None

    def piece_done(self) -> bool:
        """いまの曲を弾き終えたか（次の next_section で新しい曲になるか）。"""
        return self.piece is None or self.piece.pos >= len(self.piece.plan)

    def wrap_up_piece(self) -> None:
        """いまの曲を早めに締める（次のセクションを終わりにして、その次から新しい曲）。"""
        p = self.piece
        if p is not None and p.pos < len(p.plan) - 1:
            p.pos = len(p.plan) - 1

    def _new_piece(self) -> _Piece:
        rng = self.rng
        th = self.sample_piece()
        song = th.pop("_song", None) or {}   # 渡した曲から読んだもの。寄せている間だけ来る
        prog = song.get("progression")       # 進行（harmony.progression）
        minor = rng.random() < th["darkness"]
        tonic = rng.choice(MINOR_TONICS if minor else MAJOR_TONICS)
        meter = 3 if rng.random() < th["triple"] else 4
        plan = list(rng.choice(PLANS))
        if prog:
            # 調と長短もその曲に合わせる（進行は主音からの半音なので、長短が違うと別の進行になる）
            tonic, minor = int(prog["tonic"]), bool(prog["minor"])
            th["harmonic_pace"] = 1.0 if prog.get("beats", 8) <= meter * 1.5 else 0.0
        self.piece_no += 1
        p = _Piece(self.piece_no, th, tonic, minor, meter, plan)
        p.habits = song.get("melody")        # 旋律の癖（habits.habits）
        p.templates["A"] = [tuple(x) for x in prog["A"]] if prog else choose_template(minor, th["impressionism"], rng)
        if prog and prog.get("S"):
            p.templates["S"] = [tuple(x) for x in prog["S"]]
        for part in ("B",) if "S" in p.templates else ("B", "S"):
            # サビは頂点なので、足場のはっきりした進行（映画音楽寄り）から選ぶ。印象派寄りの sus だけの進行だと、
            # 3 度がなくどこにも落ち着かなかった
            imp = th["impressionism"] * (0.3 if part == "S" else 1.0)
            p.templates[part] = choose_template(minor, imp, rng)
            for _ in range(12):
                differs = all(p.templates[part] is not p.templates[q] for q in ("A", "B", "S") if q != part and q in p.templates)
                grounded = part != "S" or sum(f == "sus" for _, f, _ in p.templates[part]) * 2 < len(p.templates[part])
                if differs and grounded:
                    break
                p.templates[part] = choose_template(minor, imp, rng)
        # B は転調することがある
        p.templates["B_key"] = (tonic, minor)
        if rng.random() < 0.35:
            if rng.random() < th["impressionism"]:
                p.templates["B_key"] = ((tonic + rng.choice([8, 4, 5])) % 12, minor)
            else:
                # 平行調: 短調 → 短3度上の長調、長調 → 短3度下の短調
                p.templates["B_key"] = ((tonic + 3) % 12, False) if minor else ((tonic + 9) % 12, True)
        # 平行調へ移ると長短が変わるので、B の進行は移った先の長短で選び直す（前は元の長短のままで、
        # 「A major」なのに Am の進行を弾き、長調の旋律とぶつかっていた）
        b_minor = p.templates["B_key"][1]
        if b_minor != minor:
            p.templates["B"] = choose_template(b_minor, th["impressionism"], rng)
        return p

    def next_section(self) -> Section:
        if self.piece is None or self.piece.pos >= len(self.piece.plan):
            self.piece = self._new_piece()
        p = self.piece
        part, e0, e1 = p.plan[p.pos]
        before_outro = p.pos == len(p.plan) - 2
        p.pos += 1
        th = self.sample_section(p.theta)
        g = th.get("drama", 0.6)
        energy = (CALM + (e0 - CALM) * g, CALM + (e1 - CALM) * g)
        sec = self._compose(p, part, th, cadence=before_outro or part == "outro", energy=energy)
        sec.index, sec.plan = p.pos - 1, tuple(x[0] for x in p.plan)
        return sec

    # ------------------------------------------------------------ セクション
    def _compose(self, p: _Piece, part: str, th: dict, cadence: bool,
                 energy: tuple[float, float] = (CALM, CALM)) -> Section:
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

        # ---- エネルギー（小節ごとに、始め → 終わりへ）
        e0, e1 = energy
        ebar = [e0 + (e1 - e0) * (i / max(1, bars - 1)) for i in range(bars)]
        emid = (e0 + e1) / 2
        lift = round(7 * max(0.0, emid - CALM))          # 盛り上がるほど旋律を高く（最大 +5 くらい）
        center = st.melody_center + (3 if base == "B" else 0) + lift
        acc_v = st.base_velocity - 10
        evs: list[_Ev] = []

        # ---- 伴奏（静かなところは和音を置く、盛り上がるほど流れて厚く）
        pattern = self._pick_pattern(th, B)
        if emid > 0.75 and pattern != "ripple":
            pattern = "arp"
        elif emid < 0.25 and pattern in ("arp", "pulse", "ripple"):
            pattern = "sway" if B == 3 else "block"
        if part == "outro":
            pattern = "block"
        for bar, c in enumerate(chords):
            second = bar > 0 and chords[bar - 1] is c
            evs += self._accomp_bar(c, bar * B, B, pattern, center, acc_v, second, rng, ebar[bar])

        # ---- 旋律
        if part != "intro":
            evs += self._melody(p, part, chords, B, center, st, th, tonic, minor, cadence, rng, ebar)
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

        # ---- 旋律は線にする：次の旋律の音の少しあとで切る（前はペダルで次の音の上まで伸ばしていて、
        # 旋律が響きのかたまりになってにじんでいた）。響かせるのは伴奏
        mel = sorted((e for e in evs if e.part == "mel"), key=lambda e: e.beat)
        for a, b in zip(mel, mel[1:]):
            if b.beat > a.beat:
                a.dur = min(a.dur, b.beat - a.beat + 0.08)

        # ---- 旋律音（半拍以上）と半音（短9度）でぶつかる伴奏の内声を抜く
        mel_long = [e for e in evs if e.part == "mel" and e.dur >= 0.5]   # 伴奏を厚くしたので半拍以上の音も見る
        # 低音は小節でいちばん低い音（とそのオクターブ上）。前は「その拍でいちばん低い音」としていたので、
        # 1 拍に 1 音のアルペジオでは全部の音が低音扱いになり、旋律とぶつかる内声が残っていた
        bar_bass: dict[int, int] = {}
        for e in evs:
            if e.part == "lh":
                k = int(e.beat // B)
                bar_bass[k] = min(bar_bass.get(k, 127), e.pitch)

        def is_bass(e: _Ev) -> bool:
            low = bar_bass.get(int(e.beat // B), -1)
            return e.pitch in (low, low + 12)
        # 旋律の下に添えた音（オクターブ重ね・和声音）が、伴奏と半音・短 9 度でぶつかるなら入れない
        lh = [e for e in evs if e.part == "lh"]
        evs = [e for e in evs if e.part != "rh" or not any(
            o.beat < e.beat + e.dur and e.beat < o.beat + o.dur and abs(o.pitch - e.pitch) in (1, 13) for o in lh)]
        keep = []
        for e in evs:
            if e.part == "lh" and not is_bass(e) and any(
                    m.beat < e.beat + e.dur and e.beat < m.beat + m.dur
                    and ((m.pitch - e.pitch) in (1, 13) or (e.pitch - m.pitch) == 1) for m in mel_long):
                continue
            keep.append(e)
        evs = keep

        # ---- 強さ：エネルギーで上げ下げする（その小節の高さ。0.35 で今までどおり）
        for e in evs:
            eb = ebar[min(int(e.beat // B), bars - 1)]
            e.vel += (eb - CALM) * (34 if e.part == "mel" else 30)

        # ---- 時間に直す（ルバート）
        # テンポの揺れはフレーズに沿ってなめらかに：ゆっくり動く揺れ ＋ 4 小節の終わりへ向けて少し広げる。
        # （前は小節ごとに独立に揺らしていて、拍がよろけて雑に聞こえた）
        spb = 60.0 / st.bpm
        factors = []
        drift = 0.0
        for bar in range(bars):
            drift = 0.6 * drift + rng.gauss(0, 0.012 * th["rubato"])
            f = 1 + drift
            if (bar + 1) % 4 == 0:
                f += 0.08 * th["rubato"]
            elif (bar + 1) % 4 == 3:
                f += 0.025 * th["rubato"]
            if part == "outro":
                f += 0.15 * bar + 0.25 * th["rubato"] * bar
            factors.append(max(0.85, f))
        cum = [0.0]
        for f in factors:
            cum.append(cum[-1] + B * spb * f)

        def sec(beat: float) -> float:
            bar = min(int(beat // B), bars - 1)
            return cum[bar] + (beat - bar * B) * spb * factors[bar]

        # 打鍵のずれ：同じ拍の音はそろえて（拍ごとに同じずれ）、1 音ずつのずれは小さく。旋律は少し先に出す
        # （前は 1 音ずつ独立に ±6〜18ms ずらしていて、和音がばらけて雑に聞こえた）
        beat_off: dict[float, float] = {}
        grp = 0.003 + 0.006 * th["rubato"]
        notes = []
        for e in evs:
            k = round(e.beat * 4) / 4
            if k not in beat_off:
                beat_off[k] = rng.gauss(0, grp)
            lead = -0.012 if e.part == "mel" else 0.0
            t0 = sec(e.beat) + e.roll + beat_off[k] + lead + rng.gauss(0, 0.003)
            t1 = sec(min(e.beat + e.dur, bars * B)) + max(0.0, e.beat + e.dur - bars * B) * spb + e.roll
            notes.append(Note(max(0.0, t0), max(0.08, t1 - t0), e.pitch,
                              max(12, min(115, int(e.vel))), e.part))
        duration = cum[-1] + (3.0 if part == "outro" else 0.0)
        notes = _fix_overlaps(notes)
        if part == "intro":
            self.prev_mel = center      # 曲の頭だけは中央から。ほかは前のセクションの最後の音から続ける
        return Section(notes, duration, p.no, part, key_name(tonic, minor), B, st.bpm,
                       [c.label() for c in chords], th)

    # ----------------------------------------------------------------- 伴奏
    def _pick_pattern(self, th: dict, B: int) -> str:
        # 細かいアルペジオ（16 分）は ripple の確率で。ほかは flow で選ぶ
        if self.rng.random() < th.get("ripple", 0.0):
            return "ripple"
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
                    v: int, second: bool, rng: random.Random, e: float = CALM) -> list[_Ev]:
        bass = self._bass_pitch(c.bass) if not second else self.prev_bass
        ceiling = min(center - 4, 74 + round(4 * max(0.0, e - CALM)))
        vo = self._voicing(c, bass, ceiling, n=3 + round(3 * e))     # 静か 3 音 〜 頂点 6 音
        ev: list[_Ev] = []
        if e > 0.72 and bass - 12 >= 28 and not second:
            ev.append(_Ev(start, B, bass - 12, v + 2, "lh"))          # 盛り上がりでは低音をオクターブで
        if pattern == "ripple":
            # 16 分の細かいアルペジオ：低音を置いて、その上を和音の音で旋律のすぐ下まで上り下りする
            # （「忘れじの最果て」のような、中〜高音域がずっと細かく流れる伴奏）
            if not second:
                ev.append(_Ev(start, B, bass, v + 4, "lh"))
            top = min(center - 3, 79)
            tones = []
            for q in range(max(bass + 7, 50), top + 1):
                if q % 12 in c.pcs and (not tones or q - tones[-1] >= 3):
                    tones.append(q)
            tones = tones[-7:] or [bass + 12]
            path = list(range(len(tones))) + list(range(len(tones) - 2, 0, -1))
            off = len(path) // 2 if second else 0
            for i in range(B * 4):
                k = path[(i + off) % len(path)]
                acc = 3 if i % 4 == 0 else 0
                ev.append(_Ev(start + i * 0.25, 0.25, tones[k], v - 6 + acc, "lh"))
        elif pattern == "arp":
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
    def _habit_bar(self, B: int, cells: dict, rng: random.Random) -> list[tuple]:
        """渡した曲のリズムの癖で 1 小節：拍ごとに、その曲で 1 拍の中の鳴り始めの並び（"x.x." など）を
        その割合で選ぶ。音は次の鳴り始めまで伸ばす（"...." の拍は前の音を伸ばす。小節の頭なら休み）。"""
        names, w = list(cells), list(cells.values())
        onsets = []
        for beat in range(B):
            cell = rng.choices(names, w)[0]
            onsets += [beat + k / 4 for k, ch in enumerate(cell) if ch == "x"]
        return [(a, (onsets[i + 1] if i + 1 < len(onsets) else B) - a) for i, a in enumerate(onsets)]

    def _rhythm_bar(self, B: int, d: float, s: float, rng: random.Random, cadence: bool,
                    habits: dict | None = None) -> list[tuple]:
        """(拍, 長さ) のリスト。None の音高は休符扱いしない（休符は入れない）。"""
        if habits and not cadence:
            return self._habit_bar(B, habits["cells"], rng)
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
                th: dict, tonic: int, minor: bool, cadence: bool, rng: random.Random,
                ebar: list[float] | None = None) -> list[_Ev]:
        bars = len(chords)
        base = part.rstrip("'")
        ebar = ebar or [CALM] * bars
        emid = sum(ebar) / len(ebar)
        # 盛り上がるほど細かく、休みを少なく
        d = min(1.0, max(0.0, th["density"] + 0.45 * (emid - CALM)))
        s = min(1.0, max(0.0, th["space"] - 0.6 * (emid - CALM)))
        # 旋律は小節ごとに「その和音に合わせた音階」で歩く。曲の調の音階のままだと、調の外の和音
        # （G♭maj9、♯11 など）の上で半音ぶつかり、わざと外したように聞こえた（調の外の和音の上で、
        # 旋律の長さの 24〜34% が伴奏と半音で濁っていた）
        key_penta = set(scale_pcs(tonic, minor, penta=True))
        bar_pcs = [chord_scale_pcs(tonic, minor, c) for c in chords]
        bar_scale = [[q for q in range(36, 101) if q % 12 in pcs] for pcs in bar_pcs]
        bar_penta = [(key_penta & pcs) or pcs for pcs in bar_pcs]
        lo, hi = center - 10, min(86, center + 10)

        # 動機（2 小節）。同じ曲の同じ部分では使い回す
        if base in p.motifs:
            rhythm, contour = p.motifs[base]
        else:
            # 動機は 1 小節目に 2 音以上、2 小節で 4 音以上（1 音だけの小節では形にならない）
            for _ in range(8):
                rhythm = [(a, b) for a, b in self._rhythm_bar(B, d, s * 0.6, rng, False, p.habits)]
                rhythm += [(a + B, b) for a, b in self._rhythm_bar(B, d, s, rng, False, p.habits)]
                if sum(a < B for a, _ in rhythm) >= 2 and len(rhythm) >= 4:
                    break
            contour = None
            if part != "outro":
                p.motifs[base] = (rhythm, None)

        # 4 小節のフレーズ = 動機（2 小節）→ 展開（動機の 1 小節目を、高さをずらしてもう一度 = ゼクエンツ）→ 終止。
        # 8 小節なら 前半は半終止、後半は終止（問いと答え）。旋律の材料はすべて動機から作る。
        # （前は 3 小節目をその場の乱数で作っていて、長い音 1 つで止まったり、動機と関係ない音になったりして、
        # 旋律がつながって聞こえなかった）
        plan: list[tuple[str, float]] = []
        if part == "outro":
            plan = [("free", 0), ("cad", 1), ("hold", 2)]
        else:
            for ph in range(bars // 4):
                plan += [("motif", 0), ("motif", 1), ("dev", 2), ("cad" if ph % 2 == 0 else "end", 3)]

        notes: list[tuple[float, float, str]] = []   # (拍, 長さ, 種類)
        for bar, (kind, _) in enumerate(plan):
            o = bar * B
            if kind == "motif":
                half = 0 if (bar % 4) == 0 else B
                notes += [(o + a - half, b, "motif") for a, b in rhythm if half <= a < half + B]
            elif kind == "dev":
                notes += [(o + a, b, "dev") for a, b in rhythm if a < B]
            elif kind == "free":
                notes += [(o + a, b, "free") for a, b in self._rhythm_bar(B, d, s, rng, False, p.habits)]
            elif kind in ("cad", "end"):
                notes += [(o + a, b, kind) for a, b in self._rhythm_bar(B, d, s, rng, True)]
            else:  # hold
                notes.append((o, B * 1.5, "end"))

        prev = max(lo, min(hi, self.prev_mel))
        last_iv = self.prev_iv              # 前のセクションの最後の動きを引き継ぐ（境目でも決まりを効かせる）
        since_leap = self.since_leap
        stored = contour          # 前のセクションで決めた動機の音程（なければ今回決める）
        recorded: list[int] = []
        occ_j = [0, 0]
        head = prev               # そのフレーズで動機を始めた音（展開はここからずらす）
        max_leap = 5 + round(4 * th["leapiness"])     # 跳躍の上限（半音）。跳躍の好み 0 で 5、1 で 9
        turn = 3 + round(3 * th["leapiness"])         # これ以上動いたら次は一歩戻る（初期値で 4 半音）
        dev_bar, dev_j = -1, 0
        out: list[_Ev] = []
        for t, dur, kind in notes:
            bar = min(int(t // B), bars - 1)
            c = chords[bar]
            scale, penta = bar_scale[bar], bar_penta[bar]
            strong = (t % B) == 0 or (B == 4 and (t % B) == 2) or dur >= 1.5
            x = (t % (4 * B)) / (4 * B)
            target = center - 3 + 7 * math.sin(math.pi * x)

            steps_src = None
            if kind == "dev":
                if bar != dev_bar:
                    dev_bar, dev_j = bar, 0
                j = dev_j
                dev_j += 1
                steps_src = stored or recorded
                if j >= len(steps_src):
                    steps_src = None
            if kind == "motif":
                occ = 0 if (bar % 8) < 4 else 1
                j = occ_j[occ]
                occ_j[occ] += 1
                steps_src = stored if occ == 0 else (stored or recorded)
                if steps_src is not None and j >= len(steps_src):
                    steps_src = None

            if kind == "end":
                # フレーズの終わりは主和音の音で落ち着く。ただし今の和音にもある音だけ（前は和音を見ずに選んで、
                # D の和音の上で G を伸ばす、のように長い音が濁っていた）
                home = {tonic} if rng.random() < 0.6 else {(tonic + (3 if minor else 4)) % 12, (tonic + 7) % 12}
                pcs = (home & c.core_pcs) or ({tonic, (tonic + (3 if minor else 4)) % 12, (tonic + 7) % 12} & c.core_pcs) \
                    or c.core_pcs
                pitch = nearest_with_pc(int(prev), pcs, lo, hi)
            elif kind == "cad":
                pcs = c.core_pcs - {c.root} or c.core_pcs
                pitch = nearest_with_pc(int(prev), pcs, lo, hi)
            elif kind == "dev" and j == 0:
                # ゼクエンツの頭：前半のフレーズは上へ（盛り上げる）、後半は下へ（落ち着く方へ）
                seq = rng.choice([1, 1, 2]) * (1 if (bar % 8) < 4 else -1)
                pitch = _step(scale, head, seq)
                if _clash(pitch, c) or _avoid(pitch, c):
                    pitch = nearest_with_pc(pitch, c.core_pcs, lo, hi)
            elif steps_src is not None and j > 0:
                # 動機の繰り返し: 音階上の音程を保って、今の和音へ移す
                pitch = _step(scale, prev, steps_src[j])
                if (strong or dur >= 1) and _avoid(pitch, c):
                    pitch = nearest_with_pc(pitch, c.core_pcs, lo, hi)
            elif strong or (steps_src is not None and j == 0):
                pool = c.core_pcs if rng.random() < 0.7 else c.pcs
                cands = [q for q in range(lo, hi + 1) if q % 12 in pool]
                # 強拍は、前の音にいちばん近い和音の音へ歩いて着く。目標の高さは上がるか下がるかを決める程度
                # （和音の音どうしは 3〜5 半音離れているので、近さを強く見ないと跳び跳びになる）
                w_step = 2.4 - 1.2 * th["leapiness"]
                # 同じ音にとどまるより 1 歩動く。渡した曲で同じ音が続くなら、その割合でとどまってよい
                stay = 2.4 * w_step if not (p.habits and rng.random() < p.habits["steps"][0]) else -9.0
                pitch = min(cands, key=lambda q: abs(q - prev) * w_step + abs(q - target) * 0.15
                            + (stay if q == prev else 0.0) + rng.random() * 1.0
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
                if p.habits:
                    # 渡した曲の音の動き（音階で何歩）の割合で。同じ音にとどまる割合もその曲に合わせる
                    hs = p.habits["steps"]
                    steps = rng.choices(range(len(hs)), hs)[0]
                    steps = min(steps, 4)
                if not (lo <= _step(scale, prev, direction * steps) <= hi):
                    direction = -direction
                pitch = _step(scale, prev, direction * steps) if steps else prev
                if steps and pitch % 12 not in penta and rng.random() < th["pentatonic"]:
                    pitch = _step(scale, pitch, direction)
                if dur >= 1 and _avoid(pitch, c):
                    pitch = nearest_with_pc(pitch, c.core_pcs, lo, hi)
            if pitch % 12 not in penta and kind != "end" and rng.random() < th["pentatonic"]:
                alt = [q for q in range(pitch - 2, pitch + 3) if q % 12 in penta and not _clash(q, c)]
                if alt:
                    pitch = min(alt, key=lambda q: abs(q - pitch))
            pitch = max(lo, min(hi, pitch))
            if pitch % 12 not in bar_pcs[bar]:          # 端で丸めたり五音へ寄せたりして外れた音を戻す
                pitch = nearest_with_pc(pitch, bar_pcs[bar], lo, hi)
            # 強拍と長い音は、和音と半音でぶつからない音に。渡した曲の癖で弾くときは短い音も（細かい音が増えて、
            # 伴奏と半音で隣り合う割合が 0.8% → 1.7% に増えたため）
            if (strong or dur >= 1 or p.habits) and _clash(pitch, c):
                pitch = nearest_with_pc(pitch, c.core_pcs, lo, hi)
            # 動きを整える。歌える旋律は、ほとんど隣の音へ順次進行で動き、跳んだら反対へ一歩戻る。
            # - 跳躍の上限（max_leap）を超える動きと増 4 度は、同じ向きで上限以内の音にする
            # - turn 半音以上動いたら、次は反対へ順次進行（2 半音以内）で戻る（大きく動くのを続けない）
            # （前は「5 半音以上跳んだら 4 半音以内で戻す」で、4〜5 半音の往復＝ジグザグになっていた）
            if True:
                ok_pcs = c.core_pcs if (strong or dur >= 1) else bar_pcs[bar]
                iv = pitch - prev
                if abs(iv) >= turn and since_leap < 2 and abs(last_iv) < turn:
                    # 跳んでから 2 音たつまでは跳ばない：同じ向きに順次進行で
                    way = 1 if iv > 0 else -1
                    near = [prev + way, prev + way * 2]
                    cands = [q for q in near if q % 12 in bar_pcs[bar] and not _clash(q, c)]
                    if cands:
                        pitch = cands[0]
                elif abs(last_iv) >= turn and not (iv * last_iv < 0 and abs(iv) <= 2):
                    back = -1 if last_iv > 0 else 1
                    near = [prev + back, prev + back * 2]
                    cands = [q for q in near if q % 12 in bar_pcs[bar] and not _clash(q, c)] or \
                            [q for q in near + [prev + back * 3] if q % 12 in ok_pcs]
                    if cands:
                        pitch = cands[0]
                elif abs(iv) > max_leap or abs(iv) == 6:
                    way = 1 if iv > 0 else -1
                    cands = [q for q in range(prev, prev + way * (max_leap + 1), way)
                             if q != prev and abs(q - prev) != 6 and q % 12 in ok_pcs and lo - 2 <= q <= hi + 2]
                    if cands:
                        pitch = min(cands, key=lambda q: abs(q - pitch))
            if kind == "motif" and (bar % 8) < 4 and stored is None:
                recorded.append(0 if not recorded else _scale_dist(scale, prev, pitch))
            if kind == "motif" and j == 0:
                head = pitch
            last_iv = pitch - prev
            since_leap = 0 if abs(last_iv) >= turn else since_leap + 1
            prev = pitch

            vel = st.base_velocity + 8 + 6 * math.sin(math.pi * x) + (3 if strong else 0) + rng.gauss(0, 3)
            out.append(_Ev(t, dur, pitch, vel, "mel"))
            # 頂点では旋律をオクターブで重ねる（拍頭と長い音）
            if ebar[bar] > 0.78 and (strong or dur >= 1) and pitch - 12 >= center - 14:
                out.append(_Ev(t, dur, pitch - 12, vel - 10, "rh"))
            # 長い音の下に和声音を添える
            if dur >= 2 and rng.random() < th["color"] * 0.35:
                below = [q for q in range(pitch - 9, pitch - 2) if q % 12 in c.pcs]
                if below:
                    out.append(_Ev(t, dur, rng.choice(below), vel - 14, "rh"))
        if base in p.motifs and p.motifs[base][1] is None and recorded:
            p.motifs[base] = (rhythm, recorded)
        self.prev_mel = prev
        self.prev_iv, self.since_leap = last_iv, since_leap
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
