"""音を出す側。SoundFont を tinysoundfont で鳴らし、PulseAudio/PipeWire（pacat）へ流す。

打鍵はサンプル単位で予約し、オーディオのブロックを打鍵の位置で区切って生成するので、
スレッドの揺れでリズムが崩れない。
"""
from __future__ import annotations

import heapq
import itertools
import queue
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path

import numpy as np
import tinysoundfont

SR = 44100
BLOCK = 512


class Piano:
    def __init__(self, sf2: Path, gain: float = 4.0):
        self.synth = tinysoundfont.Synth(gain=gain, samplerate=SR)
        sfid = self.synth.sfload(str(sf2))
        self.synth.program_select(0, sfid, 0, 0)
        self.lock = threading.Lock()
        self.events: list = []          # (sample, order, seq, kind, pitch, vel)
        self.seq = itertools.count()
        self.sample = 0                 # 生成済みサンプル数

    def schedule(self, t_sample: int, pitch: int, vel: int, dur_samples: int) -> None:
        with self.lock:
            heapq.heappush(self.events, (t_sample, 1, next(self.seq), "on", pitch, vel))
            heapq.heappush(self.events, (t_sample + dur_samples, 0, next(self.seq), "off", pitch, 0))

    def cancel_all(self) -> None:
        """予約を全部捨て、鳴っている音を離す（自然に減衰する）。"""
        with self.lock:
            self.events = []
            self.synth.notes_off(0)

    def render(self, n: int) -> np.ndarray:
        out = np.empty(n * 2, dtype=np.float32)
        pos = 0
        while pos < n:
            with self.lock:
                while self.events and self.events[0][0] <= self.sample + pos:
                    _, _, _, kind, pitch, vel = heapq.heappop(self.events)
                    if kind == "on":
                        self.synth.noteon(0, pitch, vel)
                    else:
                        self.synth.noteoff(0, pitch)
                nxt = self.events[0][0] - self.sample if self.events else n
            end = max(pos + 1, min(n, nxt))
            self.synth.generate(end - pos, memoryview(out[pos * 2:end * 2]).cast("B"))
            pos = end
        self.sample += n
        return np.tanh(out)        # なめらかなリミッター（小さい音はほぼそのまま、大きい和音だけ丸める）


class LiveOutput:
    """生成した音を、PC のスピーカー（pacat）と、ブラウザへの配信（聴き手ごとのキュー）に流す。

    スピーカーがあればパイプの詰まりが再生の速さになる。スピーカーなし（配信だけ）のときは時計で速さを合わせる。
    """

    def __init__(self, piano: Piano, latency_ms: int = 150, speaker: bool = True):
        self.piano = piano
        self.proc = None
        self.latency = int(SR * latency_ms / 1000) if speaker else 0
        if speaker:
            cmd = None
            if shutil.which("pacat"):
                cmd = ["pacat", "--format=float32le", "--channels=2", f"--rate={SR}",
                       f"--latency-msec={latency_ms}", "--client-name=piano-improv", "--stream-name=improv"]
            elif shutil.which("pw-cat"):
                cmd = ["pw-cat", "--playback", "--format=f32", "--channels=2", f"--rate={SR}", "-"]
            if cmd is None:
                raise RuntimeError("pacat も pw-cat も見つかりません（--no-speaker なら不要）")
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        self.taps: list[queue.Queue] = []
        self.taps_lock = threading.Lock()
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    @property
    def now(self) -> int:
        """いま PC のスピーカーから聞こえているあたりのサンプル位置。"""
        return max(0, self.piano.sample - self.latency)

    def tap(self) -> queue.Queue:
        """配信用。これ以降に生成された音（float32 ステレオのバイト列）が届くキューを返す。"""
        q: queue.Queue = queue.Queue(maxsize=400)       # 約 4.6 秒分
        with self.taps_lock:
            self.taps.append(q)
        return q

    def untap(self, q: queue.Queue) -> None:
        with self.taps_lock:
            if q in self.taps:
                self.taps.remove(q)

    def _run(self) -> None:
        t0 = time.monotonic()
        try:
            while self.running:
                data = self.piano.render(BLOCK).tobytes()
                with self.taps_lock:
                    for q in self.taps:
                        try:
                            q.put_nowait(data)
                        except queue.Full:          # 聴き手が遅れすぎたら古い音を捨てる
                            try:
                                q.get_nowait()
                                q.put_nowait(data)
                            except (queue.Empty, queue.Full):
                                pass
                if self.proc is not None:
                    self.proc.stdin.write(data)
                else:
                    ahead = self.piano.sample / SR - (time.monotonic() - t0)
                    if ahead > 0.05:
                        time.sleep(ahead - 0.05)
        except (BrokenPipeError, ValueError):
            self.running = False

    def close(self) -> None:
        self.running = False
        self.thread.join(timeout=1)
        if self.proc is not None:
            try:
                self.proc.stdin.close()
            except Exception:  # noqa: BLE001
                pass
            self.proc.terminate()


def write_wav(path: Path, audio: np.ndarray) -> None:
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
