"""演奏の本体。CLI と UI サーバーの両方からこれを操作する。"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable

import mido

from . import words
from .audio import SR, LiveOutput, Piano
from .composer import Composer, Section
from .style import PARAMS
from .snapshots import Snapshots
from .taste import FOCUS, Taste

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("PIANO_DATA", ROOT / "data"))
DEFAULT_SF2 = ROOT / "assets" / "SalamanderGrandPiano-SF2-V3+20200602" / "SalamanderGrandPiano-V3+20200602.sf2"


def save_midi(path: Path, secs: list[tuple[int, Section]], bpm: float) -> None:
    mid = mido.MidiFile(ticks_per_beat=480)
    tr = mido.MidiTrack()
    mid.tracks.append(tr)
    tempo = mido.bpm2tempo(bpm)
    tr.append(mido.MetaMessage("set_tempo", tempo=tempo))
    t0 = secs[0][0]
    evs = []
    for start, sec in secs:
        base = (start - t0) / SR
        for n in sec.notes:
            evs.append((base + n.t, 1, n.pitch, n.vel))
            evs.append((base + n.t + n.dur, 0, n.pitch, 0))
    evs.sort(key=lambda e: (e[0], e[1]))
    last = 0
    for t, on, p, v in evs:
        tick = int(mido.second2tick(t, 480, tempo))
        tr.append(mido.Message("note_on" if on else "note_off", note=p, velocity=v, time=tick - last))
        last = tick
    mid.save(path)


def cover_notes(events: list) -> list[list]:
    """書き起こし [(開始秒, 終了秒, 音高, 強さ 0..1)] → 耳コピの音 [開始秒, 長さ秒, 音高, 強さ 1..127]（元の音声と同じ時刻）。
    小さすぎる音・短すぎる音は捨てる。"""
    return [[round(s, 4), round(min(8.0, e - s), 4), int(p), int(max(1, min(110, 25 + a * 85)))]
            for s, e, p, a in sorted(events) if a >= 0.12 and e - s >= 0.04 and 21 <= p <= 108]


def _cover_section(notes: list[list], info: dict, theta: dict) -> Section | None:
    """耳コピの音を、弾けるセクションに（最初の音の 0.3 秒前から）。"""
    from .composer import Note
    from .theory import key_name
    if len(notes) < 32:
        return None
    t0 = min(n[0] for n in notes) - 0.3
    out = [Note(s - t0, d, p, v, "lh" if p < 60 else "rh") for s, d, p, v in notes]
    prog = info.get("progression")
    key = key_name(prog["tonic"], prog["minor"]) if prog else ""
    return Section(out, max(n.t + n.dur for n in out) + 1.5, 0, "cover", key, 4, float(info["bpm"]), [],
                   theta, 0, ("cover",), info["title"])


def _spec_names(prog: dict) -> list[str]:
    """読んだ進行 A を、その曲の調の和音名で。"""
    from .theory import NOTE_NAMES
    suf = {"maj": "", "maj7": "maj7", "min": "m", "dom": "7", "sus": "sus"}
    return [NOTE_NAMES[(prog["tonic"] + o) % 12] + suf[f] for o, f, _ in prog["A"]]


class Engine:
    """先読みしてセクションを作りピアノに予約する。評価・言葉・スキップ・保存・一時停止を受ける。"""

    LOOKAHEAD = 6.0   # 秒

    def __init__(self, sf2: Path = DEFAULT_SF2, seed: int | None = None, data: Path = DATA,
                 on_message: Callable[[str], None] | None = None,
                 on_section: Callable[[Section], None] | None = None, speaker: bool = True):
        self.data = data
        self.taste = Taste(data, seed)
        self.snapshots = Snapshots(data)
        self.piano = Piano(Path(sf2))
        self.out = LiveOutput(self.piano, speaker=speaker)
        self.composer = Composer(self.taste.sample_piece, self.taste.sample_section, seed)
        self.timeline: list[tuple[int, int, Section]] = []    # (開始サンプル, 終了サンプル, section)
        self.until = int(SR * 0.3)
        self.lock = threading.RLock()
        self.running = True
        self.paused = False
        self.listeners = 0                # ブラウザで配信を聴いている数
        self.messages: deque[dict] = deque(maxlen=40)
        self.version = 0                  # 状態が変わるたびに増える（UI の更新検知用）
        self.on_message = on_message
        self.on_section = on_section
        self._announced: int | None = None
        self._learning = threading.Lock()   # 曲を聴いて覚えるのは一度に 1 曲
        self.pending: deque[tuple[Section, dict, dict, str]] = deque()   # 曲の切れ目で弾く耳コピ
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    # ------------------------------------------------------------------ 内部
    def _say(self, text: str, kind: str = "info") -> None:
        self.messages.append({"t": time.time(), "kind": kind, "text": text})
        self.version += 1
        if self.on_message:
            self.on_message(text)

    def _loop(self) -> None:
        while self.running:
            with self.lock:
                while not self.paused and self.until - self.piano.sample < self.LOOKAHEAD * SR:
                    if self.pending and self.composer.piece_done():
                        sec = self._start_cover()
                    else:
                        sec = self.composer.next_section()
                    start = self.until
                    for n in sec.notes:
                        self.piano.schedule(start + int(n.t * SR), n.pitch, n.vel, int(n.dur * SR))
                    self.until = start + int(sec.duration * SR)
                    self.timeline.append((start, self.until, sec))
                    self.timeline = self.timeline[-60:]
            cur = self.current()
            if cur and id(cur) != self._announced:
                self._announced = id(cur)
                self.version += 1
                if self.on_section:
                    self.on_section(cur)
            time.sleep(0.05)

    def _start_cover(self) -> Section:
        """曲の切れ目で、耳コピの曲を弾き始める。そのあとの即興はその曲に寄せる。"""
        sec, est, info, src = self.pending.popleft()
        self.composer.piece_no += 1
        sec.piece = self.composer.piece_no
        self.taste.set_focus(est, info["title"], info.get("progression"), info.get("melody"))
        self.composer.start_new_piece()
        return sec

    def _current_entry(self, at: int | None = None) -> tuple[int, int, Section] | None:
        now = self.out.now if at is None else at
        for entry in reversed(self.timeline):
            if entry[0] <= now < entry[1]:
                return entry
        return None

    # ------------------------------------------------------------------ 操作
    def current(self) -> Section | None:
        e = self._current_entry()
        return e[2] if e else None

    def rate(self, r: float, text: str | None = None, at: float | None = None) -> bool:
        """at: 聴き手がその音を聞いた時刻（エンジンの秒）。配信で遅れて聴いている人のため。"""
        e = self._current_entry(None if at is None else int(at * SR))
        if e is None:
            return False
        cur = e[2]
        if cur.part == "cover":
            self._say(f"いまは「{cur.title[:40]}」の耳コピなので、好みとしては覚えません（このあとの即興で教えてください）", "warn")
            return True
        meta = {"piece": cur.piece, "part": cur.part, "key": cur.key}
        if text:
            meta["text"] = text
        self.taste.rate(cur.theta, r, meta)
        self._say(f"{'♥' if r > 0 else '×'} 覚えました（曲{cur.piece} {cur.part}・評価 {self.taste.n} 回目）",
                  "rate")
        return True

    def words(self, text: str) -> None:
        """言葉の指示。LLM を使うと時間がかかるので、呼び出し側でスレッドにしてよい。"""
        deltas, rating, how = words.interpret(text, self.taste.mu)
        if deltas:
            applied = self.taste.nudge(deltas, text)
            self.version += 1
            desc = ", ".join(f"{PARAMS[k][0]}{'+' if v > 0 else ''}{v:.2f}" for k, v in applied.items())
            self._say(f"「{text}」→ {desc}（{how}・次のセクションから）", "words")
        elif rating:
            self.rate(rating, text)
        else:
            self._say(f"「{text}」はうまく読めませんでした（{how}）。PIANO_LLM を設定すると LLM で読みます", "warn")

    def learn(self, src: str, weight: float = 0.5) -> None:
        """曲（URL か音声ファイル）を聴いて、その曲調へ好みを寄せる。数十秒かかるので呼び出し側でスレッドにする。"""
        from .listen import ListenError, describe, listen
        if not self._learning.acquire(blocking=False):
            self._say("いま別の曲を聴いています。終わってからもう一度渡してください", "warn")
            return
        try:
            self._say("その曲を聴いてきます（少しかかります）", "listen")
            try:
                est, info = listen(src)
            except ListenError as e:
                self._say(f"聴けませんでした：{e}", "warn")
                return
            except Exception as e:  # noqa: BLE001  書き起こしの中の予期しない失敗も、画面に出して止まらない
                self._say(f"聴けませんでした（{type(e).__name__}: {e}）", "warn")
                return
            before = dict(self.taste.mu)
            self.taste.blend(est, weight, {"src": src, "title": info["title"], "bpm": info["bpm"]})
            # 好み（全部の曲をならした値）は少ししか動かないので、次の数曲はその曲に寄せて弾く
            prog = info.get("progression")
            self.taste.set_focus(est, info["title"], prog, info.get("melody"))
            with self.lock:
                self.composer.wrap_up_piece()
            self.version += 1
            d = describe(est, info, before, self.taste.mu)
            moved = f"好みは {d.split(' → ', 1)[1]}" if " → " in d else "好みはほとんど変わりませんでした"
            self._say(f"「{info['title'][:60]}」を覚えました（♩={info['bpm']:.0f} の曲）。"
                      f"いまの曲を締めて、次の曲からしばらくこの曲に寄せて弾きます"
                      + (f"（進行 {' → '.join(_spec_names(prog))} を借ります）" if prog else "") + f"。{moved}", "learn")
        finally:
            self._learning.release()

    # ------------------------------------------------------------------ 好みをしまう・呼び出す
    def snapshot_save(self, name: str, at: float | None = None) -> int:
        """いまの（at を渡せばその時刻の）好みを、名前を付けて taste.db にしまう。"""
        name = name.strip()[:60] or time.strftime("%m/%d %H:%M の好み", time.localtime(at or time.time()))
        sid = self.snapshots.add(name, self.taste.state(at))
        self.version += 1
        self._say(f"好みをしまいました：{name}", "snap")
        return sid

    def snapshot_load(self, sid: int | None = None, at: float | None = None) -> bool:
        """しまった好み（sid）か、記録のある時刻（at）の好みを呼び出す。次のセクションから効く。"""
        if sid is not None:
            st = self.snapshots.get(sid)
            if st is None:
                return False
            label = st["name"]
        elif at is not None:
            st = self.taste.state(at)
            label = time.strftime("%m/%d %H:%M の好み", time.localtime(at))
        else:
            return False
        self.taste.load(st, label)
        self.version += 1
        self._say(f"好みを呼び出しました：{label}（次のセクションから）", "snap")
        return True

    def snapshot_list(self) -> dict:
        """しまった好みと、記録から呼び出せる過去の時点（日ごとの最後の出来事）。"""
        days: dict[str, float] = {}
        if self.taste.log_path.exists():
            for line in self.taste.log_path.read_text().splitlines():
                try:
                    ts = float(json.loads(line).get("ts", 0))
                except (json.JSONDecodeError, ValueError, TypeError):
                    continue
                if ts:
                    days[time.strftime("%Y-%m-%d", time.localtime(ts))] = ts
        return {"saved": self.snapshots.list(),
                "history": [{"day": d, "at": t} for d, t in sorted(days.items(), reverse=True)[:30]]}

    def perform(self, src: str) -> None:
        """曲（URL か音声ファイル）を書き起こして、そのまま弾く（耳コピ）。いまの曲を締めたあとに弾き、
        そのあとはその曲に寄せて即興する。書き起こしはピアノ専用の Transkun（なければ Basic Pitch。cover.py）。
        耳コピは data/covers/ に曲ごとに残し、同じ曲はそれを弾く。1〜2 分かかるので呼び出し側でスレッドにする。"""
        from . import cover
        from .listen import ListenError, transcribe_song
        from .taste import _song_id
        if not self._learning.acquire(blocking=False):
            self._say("いま別の曲を聴いています。終わってからもう一度渡してください", "warn")
            return
        try:
            path = self.data / "covers" / (re.sub(r"[^\w.-]", "_", _song_id(src) or src)[:120] + ".json")
            had = cover.load(path)
            # 前に Basic Pitch で写した曲は、Transkun が入っていれば写し直す
            if had and had[1].get("transcriber") != "transkun" and cover.has_transkun():
                had = None
            if had:
                notes, info = had
                note = "前に写したもの"
            else:
                self._say("その曲を聴いて、耳で写してきます（1〜2 分かかります）", "listen")
                try:
                    events, est, info, piano = transcribe_song(src)
                except ListenError as e:
                    self._say(f"聴けませんでした：{e}", "warn")
                    return
                except Exception as e:  # noqa: BLE001
                    self._say(f"聴けませんでした（{type(e).__name__}: {e}）", "warn")
                    return
                notes = piano if piano else cover_notes(events)
                info = {**info, "est": est, "transcriber": "transkun" if piano else "basic-pitch"}
                info.pop("raw", None)
                cover.save(path, notes, info)
                note = "ピアノ専用の書き起こし" if piano else "Basic Pitch で書き起こし"
                self.taste.blend(est, 0.5, {"src": src, "title": info["title"], "bpm": info["bpm"]})
            sec = _cover_section(notes, info, dict(self.taste.mu))
            if sec is None:
                self._say("音が少なすぎて、写せませんでした", "warn")
                return
            with self.lock:
                self.composer.wrap_up_piece()
                self.pending.append((sec, info["est"], info, src))
            self.version += 1
            m, s = divmod(round(sec.duration), 60)
            self._say(f"「{info['title'][:60]}」を耳で写しました（{m} 分 {s:02d} 秒・{len(notes)} 音・{note}）。"
                      "いまの曲を締めたら弾きます。そのあとはこの曲に寄せて即興します", "learn")
        finally:
            self._learning.release()

    def set_param(self, name: str, value: float) -> None:
        self.taste.set_value(name, value)
        self.version += 1

    def skip(self) -> None:
        with self.lock:
            self.piano.cancel_all()
            self.composer.start_new_piece()
            self.until = self.piano.sample + int(SR * 1.5)
            now = self.out.now
            self.timeline = [x for x in self.timeline if x[0] < now]
        self._say("次の曲へ")

    def pause(self) -> None:
        with self.lock:
            if self.paused:
                return
            self.paused = True
            self.piano.cancel_all()
            now = self.out.now
            self.timeline = [(s, min(e, now), sec) for s, e, sec in self.timeline if s < now]
        self._say("一時停止")

    def resume(self) -> None:
        with self.lock:
            if not self.paused:
                return
            self.composer.start_new_piece()
            self.until = self.piano.sample + int(SR * 0.5)
            self.paused = False
        self._say("再開（新しい曲から）")

    def save_current(self) -> Path | None:
        cur = self.current()
        if cur is None:
            return None
        secs = [(s, sec) for s, e, sec in self.timeline if sec.piece == cur.piece]
        path = self.data / "recordings" / time.strftime(f"%Y%m%d_%H%M%S_piece{cur.piece}.mid")
        path.parent.mkdir(parents=True, exist_ok=True)
        save_midi(path, secs, secs[0][1].bpm)
        self._say(f"保存しました: {path.name}", "save")
        return path

    def recordings(self) -> list[str]:
        d = self.data / "recordings"
        return sorted((p.name for p in d.glob("*.mid")), reverse=True) if d.exists() else []

    def notes(self, start: float, end: float) -> list[list]:
        """エンジンの秒で [start, end) に鳴り始める・鳴っている音符。キャラの手と鍵盤を動かすため。
        1 つは [開始秒, 長さ, 音高, 強さ, 手]（手は "l" か "r"）。"""
        a, b = int(start * SR), int(end * SR)
        out = []
        with self.lock:
            entries = list(self.timeline)
        for s, e, sec in entries:
            if e <= a or s >= b:
                continue
            base = s / SR
            for n in sec.notes:
                t0 = base + n.t
                if t0 + n.dur >= start and t0 < end:
                    out.append([round(t0, 4), round(n.dur, 4), n.pitch, n.vel, "l" if n.part == "lh" else "r"])
        out.sort()
        return out

    def state(self) -> dict:
        e = self._current_entry()
        now = self.out.now
        cur = None
        if e:
            s, end, sec = e
            cur = {"piece": sec.piece, "part": sec.part, "key": sec.key, "meter": sec.meter,
                   "bpm": round(sec.bpm), "chords": sec.chords,
                   "elapsed": (now - s) / SR, "length": (end - s) / SR}
        recent = [{"start": s / SR, "end": e / SR, "piece": sec.piece, "part": sec.part, "key": sec.key,
                   "meter": sec.meter, "bpm": round(sec.bpm), "chords": sec.chords,
                   "index": sec.index, "plan": list(sec.plan), "title": sec.title}
                  for s, e, sec in self.timeline[-6:]]
        return {
            "version": self.version, "paused": self.paused, "current": cur, "listeners": self.listeners,
            "now": now / SR, "generated": self.piano.sample / SR, "recent": recent,
            "taste": {"mu": self.taste.mu, "n": self.taste.n, "spread": self.taste.spread,
                      "focus": ({"title": self.taste.focus["title"], "left": len(FOCUS) - self.taste.focus["i"]}
                                if self.taste.focus else None)},
            "messages": list(self.messages)[-12:],
        }

    def close(self) -> None:
        self.running = False
        self.piano.cancel_all()
        time.sleep(0.6)
        self.out.close()
