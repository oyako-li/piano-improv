"""スタイルのパラメータ。好みはすべて 0..1 のベクトルとして持つ。"""
from __future__ import annotations

from dataclasses import dataclass

# name: (説明, 0 のとき, 1 のとき, 初期値)
PARAMS: dict[str, tuple[str, str, str, float]] = {
    "tempo":         ("テンポ", "とても遅い(♩=46)", "やや速い(♩=96)", 0.30),
    "density":       ("旋律の音の多さ", "まばら", "細かく動く", 0.35),
    "space":         ("余白・休符", "途切れない", "間が多い", 0.45),
    "color":         ("和音の色彩(テンション)", "素朴な三和音", "9th/11th 多め", 0.60),
    "impressionism": ("和声の方向", "映画音楽・王道進行", "印象派・平行和音", 0.50),
    "darkness":      ("短調の多さ", "明るい", "暗い・切ない", 0.35),
    "register":      ("旋律の音域", "低め", "高め", 0.45),
    "softness":      ("弱さ", "しっかり", "ささやくように", 0.65),
    "flow":          ("伴奏の流れ", "和音を置く・静止", "アルペジオで流れる", 0.55),
    "ripple":        ("伴奏の細かさ", "8分でゆったり", "16分の細かいアルペジオ", 0.15),
    "rubato":        ("揺らぎ(ルバート)", "メトロノーム的", "大きく揺れる", 0.50),
    "pentatonic":    ("五音音階寄り", "全音階", "ヨナ抜き・和風", 0.35),
    "leapiness":     ("旋律の跳躍", "順次進行", "大きく跳ぶ", 0.35),
    "pedal":         ("ペダルの深さ", "ドライ", "響きを重ねる", 0.60),
    "harmonic_pace": ("和音の変わる速さ", "2小節ごと", "1小節ごと", 0.45),
    "triple":        ("3拍子の多さ", "4拍子", "3拍子", 0.40),
    "drama":         ("盛り上がりの大きさ", "平らに流れる", "大きく盛り上がる", 0.60),
}
NAMES = list(PARAMS)
DEFAULTS = {k: v[3] for k, v in PARAMS.items()}


def clip01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


@dataclass
class Style:
    """0..1 の値を、作曲に使う実際の値に読み替える。"""
    v: dict[str, float]

    def __getattr__(self, name: str) -> float:
        try:
            return self.__dict__["v"][name]
        except KeyError as e:
            raise AttributeError(name) from e

    @property
    def bpm(self) -> float:
        return 46 + 50 * self.v["tempo"]

    @property
    def melody_center(self) -> int:
        return int(round(64 + 14 * self.v["register"]))

    @property
    def base_velocity(self) -> int:
        return int(round(80 - 34 * self.v["softness"]))
