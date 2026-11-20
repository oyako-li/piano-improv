"""好みのモデル。

好み = スタイル空間（0..1 の 15 次元）上の中心 mu。曲ごとに mu のまわりを少し探索して弾き、
「良い」と言われた演奏の方へ mu を寄せ、「微妙」と言われた演奏から遠ざける。
評価の数が増えるほど探索の幅を狭める。評価はすべて ratings.jsonl に残す（後で学習モデルに使う）。
"""
from __future__ import annotations

import json
import math
import random
import time
from pathlib import Path

from .composer import PIECE_PARAMS
from .style import DEFAULTS, NAMES, clip01


class Taste:
    def __init__(self, data_dir: Path, seed: int | None = None):
        self.dir = data_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "taste.json"
        self.log_path = self.dir / "ratings.jsonl"
        self.rng = random.Random(seed)
        self.mu = dict(DEFAULTS)
        self.n = 0
        self.refs: list[dict] = []
        if self.path.exists():
            d = json.loads(self.path.read_text())
            self.mu.update({k: v for k, v in d.get("mu", {}).items() if k in self.mu})
            self.n = d.get("n", 0)
            self.refs = d.get("refs", [])

    # ---------------------------------------------------------------- 保存
    def save(self) -> None:
        self.path.write_text(json.dumps({"mu": self.mu, "n": self.n, "refs": self.refs},
                                        ensure_ascii=False, indent=1))

    def _log(self, rec: dict) -> None:
        rec["ts"] = time.time()
        with self.log_path.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ---------------------------------------------------------------- 探索
    @property
    def spread(self) -> float:
        return max(0.05, 0.16 / math.sqrt(1 + self.n / 8))

    def sample_piece(self) -> dict:
        s = self.spread
        return {k: clip01(self.mu[k] + self.rng.gauss(0, s)) for k in NAMES}

    def sample_section(self, piece: dict) -> dict:
        """曲の中では調・拍子・テンポは固定し、ほかを少しだけ揺らす。"""
        s = self.spread * 0.5
        return {k: piece[k] if k in PIECE_PARAMS else clip01(piece[k] + self.rng.gauss(0, s)) for k in NAMES}

    # ---------------------------------------------------------------- 学習
    def rate(self, theta: dict, r: float, meta: dict | None = None) -> None:
        lr = 0.3 if r > 0 else 0.15
        for k in NAMES:
            self.mu[k] = clip01(self.mu[k] + lr * r * (theta[k] - self.mu[k]))
        self.n += 1
        self._log({"kind": "rating", "r": r, "theta": theta, **(meta or {})})
        self.save()

    def nudge(self, deltas: dict, text: str) -> dict:
        applied = {}
        for k, d in deltas.items():
            if k in self.mu and d:
                self.mu[k] = clip01(self.mu[k] + float(d))
                applied[k] = round(float(d), 2)
        self._log({"kind": "words", "text": text, "deltas": applied})
        self.save()
        return applied

    def set_value(self, name: str, value: float) -> None:
        """UI のスライダーで直接決めた値。"""
        if name not in self.mu:
            raise KeyError(name)
        old = self.mu[name]
        self.mu[name] = clip01(float(value))
        self._log({"kind": "manual", "param": name, "from": old, "to": self.mu[name]})
        self.save()

    def blend(self, target: dict, weight: float, source: dict) -> None:
        for k, v in target.items():
            if k in self.mu:
                self.mu[k] = clip01((1 - weight) * self.mu[k] + weight * v)
        self.refs.append(source)
        self._log({"kind": "reference", "target": target, "weight": weight, **source})
        self.save()
