"""配信の音と、画面の「聞こえている時刻」のずれを測る。

/character は「聞こえている時刻 = 配信を始めたときの generated + audio.currentTime」で手を動かす。
ここでは同じ手順で配信を録って、音の立ち上がりと /api/notes の音符の時刻を突き合わせ、
音が計算よりどれだけ遅れて届くかを出す。スピーカーやイヤホンの遅れは含まない。

  python3 tools/measure_sync.py [回数] [--url http://127.0.0.1:8765] [--sec 25]
要るもの: numpy, ffmpeg
"""
import argparse, json, subprocess, time, urllib.request
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("runs", nargs="?", type=int, default=3)
ap.add_argument("--url", default="http://127.0.0.1:8765")
ap.add_argument("--sec", type=float, default=25)
a = ap.parse_args()
SR, HOP = 48000, 240                                     # 5ms ごと

for i in range(a.runs):
    start = json.load(urllib.request.urlopen(a.url + "/api/state"))["generated"]
    r = urllib.request.urlopen(a.url + "/stream"); t0 = time.time(); buf = b""
    while time.time() - t0 < a.sec:
        buf += r.read1(65536)
    r.close()
    pcm = subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "mp3", "-i", "-", "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"],
                         input=buf, capture_output=True).stdout
    x = np.frombuffer(pcm, np.float32); n = len(x) // HOP
    le = np.log(np.sqrt((x[:n * HOP].reshape(n, HOP) ** 2).mean(1) + 1e-12))
    flux = np.maximum(0, np.diff(le, prepend=le[0]))      # 音の立ち上がり
    end = min(start + len(x) / SR, start + 29.9)
    notes = json.load(urllib.request.urlopen(f"{a.url}/api/notes?from={start:.3f}&to={end:.3f}"))["notes"]
    imp = np.zeros(n)
    for t, _d, _p, v, _h in notes:
        k = round((t - start) * SR / HOP)
        if 0 <= k < n:
            imp[k] += v / 127
    score = {L: float(np.dot(flux[max(0, L):n + min(0, L)], imp[max(0, -L):n - max(0, L)])) for L in range(-100, 101)}
    best = max(score, key=score.get)
    print(f"{i + 1}: 音符 {len(notes)} 個 / {len(x) / SR:.0f} 秒  音の遅れ {best * HOP / SR * 1000:+.0f} ms"
          f"  （山の鋭さ {score[best] / np.median(list(score.values())):.1f}。2 未満なら当てにならない）")
