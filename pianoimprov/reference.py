"""好きな曲（MIDI）からスタイルを推定する。

推定できるものだけを返す（テンポ・拍子・音の多さ・音域・跳躍・長短・和音の色彩・強弱）。
余白とヨナ抜き度は、検証したところ当てにならなかったので推定しない。
旋律はトラックが分かれていれば一番高いトラック、なければ「上位の音域で一番高い音」で近似する。
"""
from __future__ import annotations

from pathlib import Path

import mido
import numpy as np

# Krumhansl–Kessler の調性プロファイル
KK_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KK_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def _notes(path: Path):
    mid = mido.MidiFile(path)
    tpb = mid.ticks_per_beat
    tempo, num = None, None
    notes = []   # (beat, dur_beats, pitch, vel, track)
    for ti, track in enumerate(mid.tracks):
        t = 0
        on: dict[tuple[int, int], tuple[int, int]] = {}
        for msg in track:
            t += msg.time
            if msg.type == "set_tempo" and tempo is None:
                tempo = msg.tempo
            elif msg.type == "time_signature" and num is None:
                num = msg.numerator
            elif msg.type == "note_on" and msg.velocity > 0:
                if getattr(msg, "channel", 0) != 9:
                    on[(msg.channel, msg.note)] = (t, msg.velocity)
            elif msg.type in ("note_off", "note_on"):
                k = (msg.channel, msg.note)
                if k in on:
                    t0, v = on.pop(k)
                    notes.append((t0 / tpb, (t - t0) / tpb, msg.note, v, ti))
    return sorted(notes), (60_000_000 / tempo if tempo else None), num


def analyze(path: str | Path) -> dict:
    path = Path(path)
    tnotes, bpm, num = _notes(path)
    if len(tnotes) < 16:
        raise ValueError(f"{path.name}: 音符が少なすぎます")
    return estimate(tnotes, bpm, num)


def estimate(tnotes: list[tuple], bpm: float | None, num: int | None, *, velocity: bool = True,
             clip: bool = True) -> dict:
    """音符の列 [(拍, 長さ(拍), 音高, 強さ, トラック)] から、推定できる好みの値（0..1）を返す。
    bpm・num が分からなければ None（その値は推定しない）。velocity=False なら強弱を使わない。
    clip=False なら 0..1 に切らずに返す（音声からの推定で、あとから補正をかけるため）。"""
    notes = [n[:4] for n in tnotes]
    if len(notes) < 16:
        raise ValueError("音符が少なすぎます")
    est: dict[str, float] = {}
    if bpm:
        est["tempo"] = (bpm - 46) / 50
    if num:
        est["triple"] = 1.0 if num in (3, 6, 9) else 0.0

    onsets: dict[float, list] = {}
    for b, d, p, v in notes:
        onsets.setdefault(round(b * 4) / 4, []).append((p, v, d))
    times = sorted(onsets)
    span = times[-1] - times[0] + 1
    # 旋律: トラックが分かれていれば一番高いトラック、なければ上位 30% の音域にある一番高い音
    tracks: dict[int, list] = {}
    for b, d, p, v, ti in tnotes:
        tracks.setdefault(ti, []).append((b, p))
    tracks = {k: v for k, v in tracks.items() if len(v) >= 16}
    if len(tracks) >= 2:
        mt = max(tracks, key=lambda k: np.median([p for _, p in tracks[k]]))
        mel_t = sorted({round(b * 4) / 4: p for b, p in sorted(tracks[mt])}.items())
    else:
        floor = float(np.percentile([p for _, _, p, _ in notes], 70))
        mel_t = [(t, max(onsets[t])[0]) for t in times if max(onsets[t])[0] >= floor]
    mel = np.array([p for _, p in mel_t])
    # 以下の換算は、この生成器の出力で設定値と推定値を突き合わせて決めた（粗い）
    est["density"] = (len(mel) / span - 0.3) / 1.2 / 0.25 * 0.8 - 0.6
    est["register"] = (float(np.median(mel)) - 64) / 14
    iv = np.abs(np.diff(mel))
    iv = iv[(iv > 0) & (iv <= 12)]
    if len(iv):
        est["leapiness"] = ((float(np.mean(iv)) - 2) / 3 - 0.6) / 0.25

    # 調と長短
    pc = np.zeros(12)
    for b, d, p, v in notes:
        pc[p % 12] += d
    best = max(((float(np.corrcoef(np.roll(prof, k), pc)[0, 1]), k, minor)
                for minor, prof in ((False, KK_MAJOR), (True, KK_MINOR)) for k in range(12)))
    _, tonic, minor = best
    est["darkness"] = 0.8 if minor else 0.2

    # 和音の色彩: 2 拍ごとに打鍵された音の種類のうち、三和音に収まらない割合（ペダルの影響を除く）
    extra = []
    for t in np.arange(times[0], times[-1] + 1, 2.0):
        pcs = {p % 12 for b, d, p, v in notes if t <= b < t + 2}
        if len(pcs) >= 3:
            fit = max(len(pcs & {r % 12, (r + 3 + mj) % 12, (r + 7) % 12}) for r in range(12) for mj in (0, 1))
            extra.append((len(pcs) - fit) / len(pcs))
    if extra:
        est["color"] = (float(np.mean(extra)) / 0.4 - 0.5) / 0.37

    vels = np.array([v for _, _, _, v in notes])
    if velocity and vels.std() > 4:          # 強弱の情報がある MIDI だけ
        est["softness"] = (78 - float(vels.mean())) / 40

    if not clip:
        return {k: float(v) for k, v in est.items()}
    return {k: float(min(1, max(0, v))) for k, v in est.items()}
