"""好みのモデル（継続学習）。

好み = スタイル空間（0..1 の 17 次元）の、次元ごとの「平均 mu と確かさ（精度 = 1/分散）」。
評価・言葉・好きな曲は、すべて ratings.jsonl に出来事として残し、好みはその記録を頭から積み上げて作る
（ベイズの逐次更新。次元ごとの正規分布で、観測の当たりやすさで重みを付ける）。

- 好きな曲：推定した値を、その次元の推定の誤差（docs/listen.md）と「1 曲は好みの一部でしかない」幅で観測する。
  同じ曲を何度渡しても 1 曲分（最後の推定だけを使う）。前の版は渡すたびに半分ずつ寄せていたので、
  最後に渡した曲が好みをほぼ上書きし、前の曲を忘れていた
- 「いい」：その演奏の値を観測する（評価が強いほど確か）。「微妙」：その演奏から少し遠ざける
- 言葉：「その時の好み ＋ 変化量」を強めに観測する。画面で直接決めた値は、さらに強く
- 忘却：出来事 1 つごとに、確かさを初期の確かさへ少し戻す（好みが変わっていくのに付いていく。半減は約 7 件）
- 探索：確かでない次元ほど広く試す（曲ごとに mu のまわりを、その次元の不確かさに比例した幅でずらす）
- やり直し：reset は記録に「reset」を書き、それより後だけを積み上げる（記録は消さない）
- 渡した曲に寄せる：好みは全部の曲をならした値なので、1 曲渡しても 2 割ほどしか動かない（4 曲覚えたあとで約 16%）。
  「この曲っぽく」に応えるため、曲を渡したら次の数曲は、その曲の推定値を中心に弾く（focus。記録には残さない）。
  曲から読んだ調と和音の進行（harmony.py）と旋律の癖（habits.py）があれば、寄せの強い曲（1〜3 曲目）はそれで弾く。
  長く続く好みはベイズのまま
- しまう・呼び出す：好み（mu と確かさ）は名前を付けて data/taste.db にしまえる（snapshots.py）。
  呼び出すと記録に「load」を書き、そこから積み上げ直す。記録から、過去の任意の時刻の好みも作り直せる
"""
from __future__ import annotations

import json
import math
import random
import time
from pathlib import Path

from .composer import PIECE_PARAMS
from .style import DEFAULTS, NAMES, clip01

PRIOR_SD = 0.25          # 何も知らないときの不確かさ
FORGET = 0.90            # 出来事 1 つごとに、確かさの (1 - FORGET) を初期へ戻す（tools/eval_taste.py で決めた）
SONG_SD = 0.15           # 好きな曲 1 曲と、その人の好み全体とのずれ
# 音声からの推定の誤差（docs/listen.md、別の 24 曲 seed 2 の平均絶対誤差）。ここにない次元は 0.25
LISTEN_ERR = {"tempo": 0.06, "color": 0.19, "darkness": 0.28, "register": 0.15, "leapiness": 0.30,
              "drama": 0.27, "ripple": 0.17}
LIKE_SD = 0.30           # 「いい」1 回（r=1）の観測の幅
DISLIKE_LR = 0.15        # 「微妙」で遠ざける割合
WORDS_SD = 0.12          # 言葉の指示
MANUAL_SD = 0.05         # 直接決めた値
EXPLORE = (0.05, 0.16)   # 探索の幅（最小, 最大）
FOCUS = (1.0, 1.0, 0.6, 0.3)   # 曲を渡したあと、1 曲目・2 曲目…をどれだけその曲に寄せるか（そのあとは好みに戻る）
FOCUS_SD = 0.04          # 寄せている次元の揺らし幅（その曲から離れすぎない）


class Taste:
    def __init__(self, data_dir: Path, seed: int | None = None):
        self.dir = data_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "taste.json"
        self.log_path = self.dir / "ratings.jsonl"
        self.rng = random.Random(seed)
        self.base = dict(DEFAULTS)          # 積み上げの出発点
        self.focus: dict | None = None      # 渡した曲に寄せている間 {"target", "title", "i"}
        if not self.log_path.exists() and self.path.exists():
            # 記録のない古い好み（手で作った taste.json など）は、出発点にする
            d = json.loads(self.path.read_text())
            self.base.update({k: v for k, v in d.get("mu", {}).items() if k in self.base})
        self._replay()

    # ---------------------------------------------------------------- 積み上げ
    def _events(self, until: float | None = None) -> tuple[dict | None, list[dict]]:
        """積み上げる出来事。最後の reset か load より後だけ（load ならその好みから始める）。
        until を渡すと、その時刻までの出来事だけ（その時点の好みを作り直すため）。"""
        if not self.log_path.exists():
            return None, []
        evs = []
        for line in self.log_path.read_text().splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if until is not None and float(e.get("ts", 0)) > until:
                break
            evs.append(e)
        last = max((i for i, e in enumerate(evs) if e.get("kind") in ("reset", "load")), default=-1)
        start = evs[last] if last >= 0 and evs[last].get("kind") == "load" else None
        return start, evs[last + 1:]

    def _build(self, until: float | None = None) -> dict:
        """記録から好みを作る（self は変えない）。"""
        p0 = 1 / PRIOR_SD ** 2
        start, evs = self._events(until)
        self.mu = dict(self.base)
        self.prec = {k: p0 for k in NAMES}
        n, refs = 0, []
        if start:                                              # しまっておいた好みを呼び出した
            self.mu.update({k: clip01(float(v)) for k, v in start.get("mu", {}).items() if k in self.mu})
            self.prec.update({k: float(v) for k, v in start.get("prec", {}).items() if k in self.prec})
            n, refs = int(start.get("n", 0)), list(start.get("refs", []))
        # 同じ曲は最後に聴いた 1 回だけ
        last_of = {_ref_key(e): i for i, e in enumerate(evs) if e.get("kind") == "reference"}
        for i, e in enumerate(evs):
            kind = e.get("kind")
            if kind == "reference" and last_of.get(_ref_key(e)) != i:
                continue
            for k in NAMES:                                   # 忘却
                self.prec[k] = p0 + FORGET * (self.prec[k] - p0)
            if kind == "reference":
                w = max(1e-3, float(e.get("weight", 0.5))) / 0.5   # 重み 0.5 で 1 曲分
                for k, v in e.get("target", {}).items():
                    if k in self.mu:
                        sd2 = LISTEN_ERR.get(k, 0.25) ** 2 + SONG_SD ** 2
                        self._observe(k, float(v), sd2 / w)
                refs = [r for r in refs if _ref_key(r) != _ref_key(e)]
                refs.append({x: e[x] for x in ("src", "title", "bpm", "files") if x in e})
            elif kind == "rating":
                r, th = float(e.get("r", 0)), e.get("theta", {})
                if r > 0:
                    for k, v in th.items():
                        if k in self.mu:
                            self._observe(k, float(v), LIKE_SD ** 2 / r)
                elif r < 0:
                    for k, v in th.items():
                        if k in self.mu:
                            self.mu[k] = clip01(self.mu[k] + DISLIKE_LR * -r * (self.mu[k] - float(v)))
                n += 1
            elif kind == "words":
                for k, d in e.get("deltas", {}).items():
                    if k in self.mu:
                        self._observe(k, clip01(self.mu[k] + float(d)), WORDS_SD ** 2)
            elif kind == "manual":
                k = e.get("param")
                if k in self.mu:
                    self._observe(k, float(e["to"]), MANUAL_SD ** 2)
        out = {"mu": self.mu, "prec": self.prec, "n": n, "refs": refs,
               "events": len(evs), "ts": float(evs[-1]["ts"]) if evs else (float(start["ts"]) if start else None)}
        return out

    def _replay(self) -> None:
        st = self._build()
        self.mu, self.prec, self.n, self.refs = st["mu"], st["prec"], st["n"], st["refs"]

    def state(self, until: float | None = None) -> dict:
        """いまの（until を渡せばその時刻の）好み。しまっておく形（mu・prec・n・refs）。"""
        keep = self.mu, self.prec
        try:
            st = self._build(until)
        finally:
            self.mu, self.prec = keep
        return {"mu": dict(st["mu"]), "prec": dict(st["prec"]), "n": st["n"], "refs": st["refs"], "ts": st["ts"]}

    def load(self, st: dict, label: str) -> None:
        """しまっておいた好みを呼び出す。記録に load を書き、ここから先はその好みに積み上げる
        （記録は消さないので、呼び出す前の好みにもいつでも戻れる）。"""
        self.focus = None
        self._log({"kind": "load", "label": label, "mu": st["mu"], "prec": st["prec"],
                   "n": st.get("n", 0), "refs": st.get("refs", [])})
        self._replay()
        self.save()

    def _observe(self, k: str, v: float, var: float) -> None:
        """正規分布どうしの掛け合わせ：精度で重みを付けた平均。"""
        p = 1 / var
        self.mu[k] = clip01((self.prec[k] * self.mu[k] + p * v) / (self.prec[k] + p))
        self.prec[k] += p

    def sd(self, k: str) -> float:
        return 1 / math.sqrt(self.prec[k])

    # ---------------------------------------------------------------- 保存
    def save(self) -> None:
        """taste.json は今の好みの写し（読むのは記録の方）。"""
        self.path.write_text(json.dumps({"mu": self.mu, "sd": {k: round(self.sd(k), 4) for k in NAMES},
                                         "n": self.n, "refs": self.refs}, ensure_ascii=False, indent=1))

    def _log(self, rec: dict) -> None:
        rec["ts"] = time.time()
        with self.log_path.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _add(self, rec: dict) -> None:
        self._log(rec)
        self._replay()
        self.save()

    def reset(self) -> None:
        self.focus = None
        self._log({"kind": "reset"})
        self.base = dict(DEFAULTS)
        self._replay()
        self.save()

    # ---------------------------------------------------------------- 探索
    def explore_sd(self, k: str) -> float:
        return min(EXPLORE[1], max(EXPLORE[0], 0.6 * self.sd(k)))

    @property
    def spread(self) -> float:
        return sum(self.explore_sd(k) for k in NAMES) / len(NAMES)

    def sample_piece(self) -> dict:
        center, sd = dict(self.mu), {k: self.explore_sd(k) for k in NAMES}
        f, prog = self.focus, None
        if f is not None:
            w = FOCUS[f["i"]]
            if w >= 0.6 and f.get("song"):
                prog = f["song"]
            for k, v in f["target"].items():
                if k in center:
                    center[k] = (1 - w) * center[k] + w * v
                    sd[k] = min(sd[k], FOCUS_SD + (1 - w) * sd[k])
            f["i"] += 1
            if f["i"] >= len(FOCUS):
                self.focus = None
        th = {k: clip01(center[k] + self.rng.gauss(0, sd[k])) for k in NAMES}
        if prog:
            th["_song"] = prog          # 進行と旋律の癖。作曲側（Composer._new_piece）が取り出す
        return th

    # ---------------------------------------------------------------- 渡した曲に寄せる
    def set_focus(self, target: dict, title: str = "", prog: dict | None = None, melody: dict | None = None) -> None:
        """prog は渡した曲の進行（harmony.progression）、melody は旋律の癖（habits.habits）。
        寄せ方が 6 割以上の曲では、その進行と旋律の癖で弾く。"""
        song = {"progression": prog, "melody": melody} if (prog or melody) else None
        self.focus = {"target": {k: clip01(float(v)) for k, v in target.items() if k in self.mu},
                      "title": title, "i": 0, "song": song}

    def _shift_focus(self, k: str, to: float | None = None, delta: float = 0.0) -> None:
        """寄せている間の言葉・スライダーは、寄せ先にも効かせる（でないと「ゆっくり」が効かない）。"""
        if self.focus and k in self.focus["target"]:
            t = self.focus["target"]
            t[k] = clip01(to if to is not None else t[k] + delta)

    def sample_section(self, piece: dict) -> dict:
        """曲の中では調・拍子・テンポは固定し、ほかを少しだけ揺らす。"""
        return {k: piece[k] if k in PIECE_PARAMS else clip01(piece[k] + self.rng.gauss(0, self.explore_sd(k) * 0.5))
                for k in NAMES}

    # ---------------------------------------------------------------- 学習
    def rate(self, theta: dict, r: float, meta: dict | None = None) -> None:
        self._add({"kind": "rating", "r": r, "theta": theta, **(meta or {})})

    def nudge(self, deltas: dict, text: str) -> dict:
        applied = {k: round(float(d), 2) for k, d in deltas.items() if k in self.mu and d}
        for k, d in applied.items():
            self._shift_focus(k, delta=d)
        self._add({"kind": "words", "text": text, "deltas": applied})
        return applied

    def set_value(self, name: str, value: float) -> None:
        """UI のスライダーで直接決めた値。"""
        if name not in self.mu:
            raise KeyError(name)
        self._shift_focus(name, to=float(value))
        self._add({"kind": "manual", "param": name, "from": self.mu[name], "to": clip01(float(value))})

    def blend(self, target: dict, weight: float, source: dict) -> None:
        """好きな曲から推定した値を取り込む。weight 0.5 で 1 曲分（大きいほど強く信じる）。"""
        self._add({"kind": "reference", "target": target, "weight": weight, **source})


def _ref_key(e: dict) -> str:
    return _song_id(e.get("src")) or str(e.get("files") or e.get("title") or e.get("ts"))


def _song_id(src) -> str | None:
    """同じ曲の URL を 1 つにまとめる。YouTube は &list=・&rv=・&index= などが付いても同じ動画
    （前は URL の文字列で比べていたので、同じ曲を 2〜3 曲分に数えていた）。"""
    if not src:
        return None
    from urllib.parse import parse_qs, urlparse
    u = urlparse(str(src).strip())
    host = (u.hostname or "").removeprefix("www.").removeprefix("m.").removeprefix("music.")
    if host.endswith("youtube.com"):
        v = parse_qs(u.query).get("v", [None])[0] or (u.path.split("/")[2] if u.path.startswith(("/shorts/", "/live/")) else None)
        if v:
            return "yt:" + v
    if host == "youtu.be" and u.path.strip("/"):
        return "yt:" + u.path.strip("/").split("/")[0]
    if host.endswith("nicovideo.jp") or host == "nico.ms":
        return "nico:" + u.path.rstrip("/").split("/")[-1]
    return str(src).strip()
