"""演奏の本体。CLI と UI サーバーの両方からこれを操作する。"""
from __future__ import annotations

import os
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
from .taste import Taste

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


class Engine:
    """先読みしてセクションを作りピアノに予約する。評価・言葉・スキップ・保存・一時停止を受ける。"""

    LOOKAHEAD = 6.0   # 秒

    def __init__(self, sf2: Path = DEFAULT_SF2, seed: int | None = None, data: Path = DATA,
                 on_message: Callable[[str], None] | None = None,
                 on_section: Callable[[Section], None] | None = None, speaker: bool = True):
        self.data = data
        self.taste = Taste(data, seed)
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
                   "meter": sec.meter, "bpm": round(sec.bpm), "chords": sec.chords}
                  for s, e, sec in self.timeline[-6:]]
        return {
            "version": self.version, "paused": self.paused, "current": cur, "listeners": self.listeners,
            "now": now / SR, "generated": self.piano.sample / SR, "recent": recent,
            "taste": {"mu": self.taste.mu, "n": self.taste.n, "spread": self.taste.spread},
            "messages": list(self.messages)[-12:],
        }

    def close(self) -> None:
        self.running = False
        self.piano.cancel_all()
        time.sleep(0.6)
        self.out.close()
