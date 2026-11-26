"""好みの学び方（pianoimprov/taste.py）を、模擬の聴き手で前の方式と比べる。

聴き手は本当の好み h（17 次元）を持つ。好きな曲を渡すときは、h から少しずれた曲を、音声推定の誤差つきで渡す。
演奏を聴いて「いい / 微妙」を言うときは、その演奏の値 θ が h に近ければ「いい」、遠ければ「微妙」、間なら何も言わない。
比べるのは、最後の好み mu と h の差（平均絶対誤差、小さいほどよい）。

  .venv/bin/python tools/eval_taste.py [--users 200] [--seed 1]

場面
  曲だけ        好きな曲を 8 曲（それぞれ 1 回）
  同じ曲を重ねる 8 曲のうち最後の 1 曲を 3 回渡す（前の方式はその曲に上書きされる）
  曲と評価      曲 6 曲と、演奏 40 区切りへの評価
  好みが変わる  前半 h で 8 曲、後半 h′（別の好み）で 8 曲。後半の好みにどれだけ付いていけるか
"""
from __future__ import annotations

import argparse
import random
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pianoimprov.style import DEFAULTS, NAMES, clip01  # noqa: E402
from pianoimprov.taste import LISTEN_ERR, SONG_SD, Taste  # noqa: E402

LISTEN_KEYS = list(LISTEN_ERR)      # 音声から推定できる次元


class Old:
    """前の方式：曲は重み 0.5 で寄せる、評価は θ へ寄せる / 遠ざける、探索の幅は評価の数だけで決める。"""

    def __init__(self, rng):
        self.mu, self.n, self.rng = dict(DEFAULTS), 0, rng

    def blend(self, target, weight, source):
        for k, v in target.items():
            self.mu[k] = clip01((1 - weight) * self.mu[k] + weight * v)

    def rate(self, theta, r, meta=None):
        lr = 0.3 if r > 0 else 0.15
        for k in NAMES:
            self.mu[k] = clip01(self.mu[k] + lr * r * (theta[k] - self.mu[k]))
        self.n += 1

    def sample_piece(self):
        s = max(0.05, 0.16 / (1 + self.n / 8) ** 0.5)
        return {k: clip01(self.mu[k] + self.rng.gauss(0, s)) for k in NAMES}


def song(h, rng):
    """h から少しずれた曲を、推定の誤差つきで読んだ値（推定できる次元だけ）。"""
    return {k: clip01(h[k] + rng.gauss(0, SONG_SD) + rng.gauss(0, LISTEN_ERR[k] * 1.25)) for k in LISTEN_KEYS}


def rate_some(model, h, n, rng):
    for _ in range(n):
        th = model.sample_piece()
        d = np.mean([abs(th[k] - h[k]) for k in NAMES])
        if d < 0.10:
            model.rate(th, 1.0)
        elif d > 0.16:
            model.rate(th, -1.0)


def err(model, h, keys=NAMES):
    return float(np.mean([abs(model.mu[k] - h[k]) for k in keys]))


def run(scene: str, make, rng) -> tuple[float, float]:
    h = {k: rng.uniform(0.15, 0.85) for k in NAMES}
    m = make()
    if scene == "曲だけ":
        for i in range(8):
            m.blend(song(h, rng), 0.5, {"src": f"s{i}"})
    elif scene == "同じ曲を重ねる":
        for i in range(7):
            m.blend(song(h, rng), 0.5, {"src": f"s{i}"})
        last = song(h, rng)
        for _ in range(3):
            m.blend(last, 0.5, {"src": "same"})
    elif scene == "曲と評価":
        for i in range(6):
            m.blend(song(h, rng), 0.5, {"src": f"s{i}"})
            rate_some(m, h, 7, rng)
    elif scene == "好みが変わる":
        for i in range(8):
            m.blend(song(h, rng), 0.5, {"src": f"a{i}"})
        h = {k: rng.uniform(0.15, 0.85) for k in NAMES}
        for i in range(8):
            m.blend(song(h, rng), 0.5, {"src": f"b{i}"})
    return err(m, h, LISTEN_KEYS), err(m, h)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--users", type=int, default=200)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    print(f"{'場面':<12}{'前の方式':>16}{'今の方式':>16}   （mu と本当の好みの平均絶対誤差：推定できる 7 次元 / 全 17 次元）")
    for scene in ("曲だけ", "同じ曲を重ねる", "曲と評価", "好みが変わる"):
        res = {"old": [], "new": []}
        for u in range(a.users):
            for name in res:
                rng = random.Random(a.seed * 100_000 + u)       # 前と今で同じ聴き手・同じ曲
                if name == "old":
                    make = lambda: Old(random.Random(u))  # noqa: E731
                else:
                    make = lambda: Taste(Path(tempfile.mkdtemp()), u)  # noqa: E731
                res[name].append(run(scene, make, rng))
        cell = lambda r: f"{np.mean([x[0] for x in r]):.3f} / {np.mean([x[1] for x in r]):.3f}"  # noqa: E731
        print(f"{scene:<12}{cell(res['old']):>16}{cell(res['new']):>16}")


if __name__ == "__main__":
    main()
