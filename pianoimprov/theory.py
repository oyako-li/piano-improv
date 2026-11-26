"""和音・音階・進行の語彙。"""
from __future__ import annotations

import random
from dataclasses import dataclass

QUALITIES: dict[str, list[int]] = {
    "maj": [0, 4, 7], "add9": [0, 4, 7, 14], "maj7": [0, 4, 7, 11], "maj9": [0, 4, 7, 11, 14],
    "69": [0, 4, 7, 9, 14], "maj7#11": [0, 4, 7, 11, 14, 18],
    "min": [0, 3, 7], "madd9": [0, 3, 7, 14], "m7": [0, 3, 7, 10], "m9": [0, 3, 7, 10, 14],
    "m11": [0, 3, 7, 10, 14, 17],
    "dom7": [0, 4, 7, 10], "dom9": [0, 4, 7, 10, 14], "7sus4": [0, 5, 7, 10], "9sus4": [0, 5, 7, 10, 14],
    "sus2": [0, 2, 7], "sus4": [0, 5, 7],
}

# family -> 色彩の弱い順
LADDERS: dict[str, list[str]] = {
    "maj": ["maj", "add9", "maj7", "maj9", "69"],
    "maj7": ["maj7", "maj9", "maj7#11"],
    "min": ["min", "madd9", "m7", "m9", "m11"],
    "dom": ["dom7", "7sus4", "dom9", "9sus4"],
    "sus": ["sus2", "sus4", "7sus4", "9sus4"],
}

MAJOR = [0, 2, 4, 5, 7, 9, 11]
MINOR = [0, 2, 3, 5, 7, 8, 10]
# ヨナ抜き（長調は 4 と 7 度、短調は 2 と 6 度を抜く）
PENTA_MAJOR = [0, 2, 4, 7, 9]
PENTA_MINOR = [0, 3, 5, 7, 10]

LABELS = {"maj": "", "min": "m", "dom7": "7", "dom9": "9", "madd9": "m(add9)", "69": "6/9",
          "maj7#11": "maj7(#11)"}
NOTE_NAMES = ["C", "D♭", "D", "E♭", "E", "F", "F♯", "G", "A♭", "A", "B♭", "B"]

# (主音からの半音, family, 低音の主音からの半音 or None)
Spec = tuple[int, str, int | None]

FILM_MAJOR: list[list[Spec]] = [
    [(5, "maj", None), (7, "dom", None), (4, "min", None), (9, "min", None)],             # 王道進行
    [(5, "maj", None), (7, "dom", None), (4, "min", None), (9, "min", None),
     (2, "min", None), (7, "sus", None), (0, "maj", None), (0, "maj", None)],
    [(0, "maj", None), (7, "maj", 11), (9, "min", None), (4, "min", 7),
     (5, "maj", None), (0, "maj", 4), (2, "min", None), (7, "sus", None)],                # カノン風の下降バス
    [(9, "min", None), (5, "maj", None), (7, "dom", None), (0, "maj", None)],
    [(5, "maj", None), (5, "min", None), (0, "maj", None), (0, "maj", None)],             # IV → IVm → I
    [(0, "maj", None), (4, "dom", None), (9, "min", None), (0, "dom", 10),
     (5, "maj", None), (5, "min", None), (0, "maj", None), (7, "sus", None)],
    [(2, "min", None), (7, "dom", None), (4, "min", None), (9, "min", None)],
    [(5, "maj", None), (4, "min", None), (2, "min", None), (0, "maj", None)],
]

IMPRESSIONIST_MAJOR: list[list[Spec]] = [
    [(5, "maj7", None), (0, "maj7", None)],                                              # 揺れる IVmaj7 ↔ Imaj7
    [(0, "maj7", None), (2, "maj", None)],                                               # リディア的 I → II
    [(0, "maj7", None), (10, "maj7", None), (8, "maj7", None), (10, "maj7", None)],       # 平行和音の下降
    [(9, "min", None), (2, "min", None)],                                                # ドリア的な揺れ
    [(0, "sus", None), (5, "sus", None), (7, "sus", None), (0, "sus", None)],
    [(0, "maj7", None), (9, "min", None), (2, "min", None), (7, "sus", None)],
    [(4, "min", None), (5, "maj7", None), (4, "min", None), (2, "min", None)],
    [(0, "maj7", None), (8, "maj7", None), (5, "maj7", None), (0, "maj7", None)],         # 長三度下の遠い和音
]

FILM_MINOR: list[list[Spec]] = [
    [(0, "min", None), (8, "maj", None), (3, "maj", None), (10, "maj", None)],
    [(8, "maj", None), (10, "maj", None), (0, "min", None), (0, "min", None)],
    [(0, "min", None), (10, "maj", None), (8, "maj", None), (7, "dom", None)],
    [(5, "min", None), (7, "sus", None), (0, "min", None), (0, "min", None)],
    [(8, "maj7", None), (7, "min", None), (5, "min", None), (0, "min", None)],
    [(0, "min", None), (7, "min", 10), (8, "maj", None), (3, "maj", 7),
     (5, "min", None), (0, "min", 3), (2, "min", None), (7, "dom", None)],
]

IMPRESSIONIST_MINOR: list[list[Spec]] = [
    [(0, "min", None), (5, "maj", None)],                                                # ドリア i → IV
    [(0, "min", None), (8, "maj7", None)],
    [(0, "min", None), (10, "sus", None), (8, "maj7", None), (10, "sus", None)],
    [(0, "min", None), (3, "maj7", None), (5, "min", None), (3, "maj7", None)],
]


@dataclass
class Chord:
    root: int            # 0..11
    intervals: list[int]
    bass: int            # 0..11
    name: str

    @property
    def pcs(self) -> set[int]:
        return {(self.root + i) % 12 for i in self.intervals}

    @property
    def core_pcs(self) -> set[int]:
        """根音・3度(またはsus)・5度・7度。旋律の強拍で安定する音。"""
        return {(self.root + i) % 12 for i in self.intervals if i < 12}

    def label(self) -> str:
        s = NOTE_NAMES[self.root] + LABELS.get(self.name, self.name)
        if self.bass != self.root:
            s += "/" + NOTE_NAMES[self.bass]
        return s


def realize(spec: Spec, tonic: int, color: float, rng: random.Random, force_family: str | None = None) -> Chord:
    off, family, bass_off = spec
    family = force_family or family
    ladder = LADDERS[family]
    idx = int(round(color * (len(ladder) - 1) + rng.gauss(0, 0.7)))
    q = ladder[max(0, min(len(ladder) - 1, idx))]
    root = (tonic + off) % 12
    bass = (tonic + bass_off) % 12 if bass_off is not None else root
    return Chord(root, QUALITIES[q], bass, q)


def choose_template(minor: bool, impressionism: float, rng: random.Random) -> list[Spec]:
    if rng.random() < impressionism:
        pool = IMPRESSIONIST_MINOR if minor else IMPRESSIONIST_MAJOR
    else:
        pool = FILM_MINOR if minor else FILM_MAJOR
    return rng.choice(pool)


def scale_pcs(tonic: int, minor: bool, penta: bool = False) -> list[int]:
    base = (PENTA_MINOR if minor else PENTA_MAJOR) if penta else (MINOR if minor else MAJOR)
    return sorted((tonic + d) % 12 for d in base)


def scale_pitches(tonic: int, minor: bool, lo: int = 36, hi: int = 100) -> list[int]:
    pcs = set(scale_pcs(tonic, minor))
    return [p for p in range(lo, hi + 1) if p % 12 in pcs]


def chord_scale_pcs(tonic: int, minor: bool, chord: "Chord") -> set[int]:
    """その和音の上で旋律が歩く音階。曲の調の音階のうち、和音にある調の外の音と半音でぶつかる音を、
    和音の音に替える（例: B♭ 長調で G♭maj9 なら D・G・A を D♭・G♭・A♭ に。リディアの ♯11 も同じ）。
    調の中の和音なら、曲の調の音階そのまま。"""
    pcs = set(scale_pcs(tonic, minor))
    for x in chord.pcs - pcs:
        pcs -= {(x + 1) % 12, (x - 1) % 12}
        pcs.add(x)
    return pcs | chord.core_pcs


def nearest_with_pc(target: int, pcs: set[int], lo: int = 0, hi: int = 127) -> int:
    best, bd = target, 999
    for p in range(max(lo, target - 12), min(hi, target + 12) + 1):
        if p % 12 in pcs and abs(p - target) < bd:
            best, bd = p, abs(p - target)
    return best


def key_name(tonic: int, minor: bool) -> str:
    return f"{NOTE_NAMES[tonic]} {'minor' if minor else 'major'}"
