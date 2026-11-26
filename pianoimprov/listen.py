"""音声（ファイルか URL）から曲調を推定する。`piano learn` と画面の「この曲みたいに」から使う。

  1. URL なら yt-dlp で音声だけを一時ディレクトリに取る（解析が済んだら消す。保存も配布もしない）
  2. ffmpeg で 22.05kHz モノラルの WAV にし、長い曲は頭の MAX_SECONDS 秒だけ使う
  3. Basic Pitch（Spotify、ONNX 版を CPU で）で音符に書き起こす
  4. テンポは書き起こしからは分からないので、librosa で音声から推定する
  5. 音符を拍に直して reference.estimate に渡す（MIDI から推定するのと同じ換算）

追加の依存（任意）：pip install -e '.[listen]'。URL を使うなら yt-dlp、どちらにも ffmpeg が要る。
推定の精度は docs/listen.md（自分の演奏を音にして書き起こし、元の設定とどれだけ合うかを数えた結果）。
"""
from __future__ import annotations

import contextlib
import io
import json
import logging
import re
import shutil
import subprocess
import tempfile
import warnings
from pathlib import Path

import numpy as np

from .reference import estimate

MAX_SECONDS = 360          # 解析に使う長さ（頭から）。長い動画で CPU を使いすぎないため
PULSE_DOUBLE = 2.0
MAX_SOURCE_SECONDS = 1800  # これより長い動画は取りに行かない（作業用 BGM の 3 時間などを避ける）
SR = 22050
# 推定しない値：強弱（書き起こしの音量は録音・ミックスの音量に左右されるので使わない）、
# 拍子（音声から確かめる方法を用意していない）
# 旋律の音の多さは、書き起こしでは旋律と伴奏が分かれないので当たらなかった（当て推量と同じ）
SKIP = {"softness", "triple", "density"}
# 書き起こしの音符は MIDI と違って旋律と伴奏が分かれないので、MIDI 向けの換算からずれる。
# その分を直線で補正する（0..1 に切る前の推定値 → a・推定値 + b）。tools/eval_listen.py --seed 1 の 24 曲で決め、
# --seed 2 の別の 24 曲で確かめた（docs/listen.md）
FIX: dict[str, tuple[float, float]] = {
    "leapiness": (0.126, 0.277),
    "register": (0.453, 0.339),
    "color": (0.635, 0.064),
}

URL_RE = re.compile(r"https?://\S+")


class ListenError(Exception):
    """利用者に見せてよい失敗の理由。"""


def is_url(s: str) -> bool:
    return bool(URL_RE.fullmatch(s.strip()))


def _need(cmd: str, hint: str) -> str:
    path = shutil.which(cmd)
    if not path:
        # venv の中にだけある場合（yt-dlp を pip で入れたとき）
        import sys
        cand = Path(sys.executable).parent / cmd
        if cand.exists():
            return str(cand)
        raise ListenError(f"{cmd} がありません（{hint}）")
    return path


def fetch(url: str, workdir: Path) -> tuple[Path, str]:
    """URL の音声を workdir に取る。（ファイル, 題名）を返す。"""
    ytdlp = _need("yt-dlp", "pip install -e '.[listen]'")
    info = subprocess.run([ytdlp, "--no-playlist", "--dump-single-json", "--skip-download", url],
                          capture_output=True, text=True, timeout=120)
    if info.returncode != 0:
        raise ListenError("その URL から音声を取れませんでした" + _last_line(info.stderr))
    meta = json.loads(info.stdout)
    dur = meta.get("duration") or 0
    if dur and dur > MAX_SOURCE_SECONDS:
        raise ListenError(f"長すぎます（{dur // 60:.0f} 分）。{MAX_SOURCE_SECONDS // 60} 分までの曲にしてください")
    if meta.get("is_live"):
        raise ListenError("ライブ配信は聴けません")
    r = subprocess.run([ytdlp, "--no-playlist", "-f", "bestaudio/best", "--no-part", "--quiet", "--no-warnings",
                        "-o", str(workdir / "src.%(ext)s"), url],
                       capture_output=True, text=True, timeout=600)
    files = [p for p in workdir.glob("src.*")]
    if r.returncode != 0 or not files:
        raise ListenError("音声を取れませんでした" + _last_line(r.stderr))
    return files[0], str(meta.get("title") or url)


def _last_line(s: str) -> str:
    lines = [x for x in (s or "").strip().splitlines() if x.strip()]
    return f"（{lines[-1][:160]}）" if lines else ""


def to_wav(src: Path, dst: Path, start: float = 0.0) -> Path:
    ffmpeg = _need("ffmpeg", "音声の変換に要ります")
    r = subprocess.run([ffmpeg, "-y", "-v", "error", "-ss", str(start), "-t", str(MAX_SECONDS), "-i", str(src),
                        "-ac", "1", "-ar", str(SR), str(dst)], capture_output=True, text=True, timeout=300)
    if r.returncode != 0 or not dst.exists():
        raise ListenError("音声を読めませんでした" + _last_line(r.stderr))
    return dst


def transcribe(wav: Path, onset: float = 0.5, frame: float = 0.3) -> list[tuple[float, float, int, float]]:
    """[(開始秒, 終了秒, 音高, 強さ 0..1)]。onset・frame は Basic Pitch のしきい値（下げると音を多く拾う）"""
    try:
        logging.getLogger().setLevel(logging.ERROR)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from basic_pitch import ICASSP_2022_MODEL_PATH
            from basic_pitch.inference import Model, predict
    except ImportError as e:
        raise ListenError(f"書き起こしのモデルが入っていません（pip install -e '.[listen]'）: {e}") from e
    global _MODEL
    if _MODEL is None:
        onnx = Path(str(ICASSP_2022_MODEL_PATH)).with_suffix(".onnx")
        _MODEL = Model(onnx if onnx.exists() else ICASSP_2022_MODEL_PATH)
    with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):   # 「Predicting MIDI…」を出さない
        warnings.simplefilter("ignore")
        _, _, events = predict(str(wav), _MODEL, onset_threshold=onset, frame_threshold=frame)
    return [(float(s), float(e), int(p), float(a)) for s, e, p, a, *_ in events]


_MODEL = None


def tempo(wav: Path) -> float:
    """♩ の BPM。拍の取り違え（倍・半分）は、この生成器の範囲（46〜96）に入る方へ寄せる。

    librosa は速い曲を半分のテンポと読むことがある。音の立ち上がりの自己相関で、推定したテンポ T の周期より
    倍の速さ 2T の周期の方がはっきり強ければ（比が PULSE_DOUBLE を超えたら）倍にする。
    2T/T の比は、生成した 48 曲でテンポが当たったものが最大 1.45、ゆっくりで細かいアルペジオの録音が 1.36、
    速いジャズの録音が 2.71 だった（docs/listen.md）。打鍵の詰まりで決めると、ゆっくりで細かい曲を倍と読んでしまった"""
    import librosa
    y, sr = librosa.load(str(wav), sr=SR, mono=True)
    hop = 512
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    bpm = float(np.atleast_1d(librosa.feature.tempo(onset_envelope=onset, sr=sr, hop_length=hop, start_bpm=60))[0])
    while bpm > 120:
        bpm /= 2
    while bpm < 40:
        bpm *= 2
    ac = librosa.autocorrelate(onset - onset.mean())
    ac = ac / (ac[0] + 1e-9)
    at = lambda b: ac[min(len(ac) - 1, int(round(60 / b * sr / hop)))]   # noqa: E731
    if at(bpm) > 0 and at(bpm * 2) / at(bpm) > PULSE_DOUBLE:
        bpm *= 2
    return bpm


def beat_times(wav: Path, bpm: float) -> np.ndarray:
    """拍の時刻（秒）。テンポは tempo() で決めたものに固定して、拍の位置だけを librosa に探させる。"""
    import librosa
    y, sr = librosa.load(str(wav), sr=SR, mono=True)
    hop = 512
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    _, beats = librosa.beat.beat_track(onset_envelope=onset, sr=sr, hop_length=hop, bpm=bpm, units="time")
    return np.asarray(beats, dtype=float)


def loudness_arc(y: np.ndarray, sr: int = SR) -> tuple[float, float]:
    """（音量の幅, 盛り上がり）dB。4 秒ごとの音量で、幅は上位 10% − 下位 10%（最後の 8 秒を除く）、
    盛り上がりは山（3 窓の移動平均の最大）− 最初の 12 秒の平均。tools/form_stats.py と同じ物差し。"""
    W = 4.0
    rms = np.array([20 * np.log10(np.sqrt(np.mean(y[int(t * sr):int((t + W) * sr)] ** 2)) + 1e-9)
                    for t in np.arange(0, len(y) / sr - W / 2, W)])
    if len(rms) < 4:
        return 0.0, 0.0
    body = rms[: max(3, len(rms) - 2)]
    body = body[body > -50]
    smooth = np.convolve(rms, np.ones(3) / 3, mode="same")
    width = float(np.percentile(body, 90) - np.percentile(body, 10)) if len(body) > 2 else 0.0
    return width, float(smooth[1:-1].max() - rms[: max(1, int(12 / W))].mean())


# 盛り上がり（dB）→ drama。この生成器で drama を 0 / 1 に固定して 8 曲ずつ弾くと、盛り上がりの中央値は
# +3.3 / +10.7dB だった（tools/form_stats.py --set drama=0|1）。その間を直線で
DRAMA_DB = (3.3, 10.7)
# 1 拍あたりの打鍵 → ripple。（ripple = 0 の打鍵数, 1 の打鍵数）
RIPPLE_OPB = (2.44, 4.62)


def analyze_audio(wav: Path, events: list | None = None) -> tuple[dict, dict]:
    """（推定した好みの値, 途中の数値）。events は書き起こし済みなら渡す（同じ音声を 2 度書き起こさない）"""
    events = transcribe(wav) if events is None else events
    if len(events) < 32:
        raise ListenError("音が少なすぎて、曲調を読めませんでした")
    bpm = tempo(wav)
    k = bpm / 60
    tnotes = sorted((s * k, (e - s) * k, p, int(round(20 + a * 107)), 0) for s, e, p, a in events)
    raw = estimate(tnotes, bpm, None, velocity=False, clip=False)
    import librosa
    y, _ = librosa.load(str(wav), sr=SR, mono=True)
    _, build = loudness_arc(y)
    if len(y) / SR >= 60:              # 短い断片では曲の弧が分からない
        raw["drama"] = (build - DRAMA_DB[0]) / (DRAMA_DB[1] - DRAMA_DB[0])
    # 伴奏の細かさ：1 拍あたりの打鍵の時刻の数（0.05 秒以内は同じ打鍵）
    on = np.unique(np.round(np.array([s for s, _, _, _ in events]) / 0.05))
    span = max(e for _, e, _, _ in events) - min(s for s, _, _, _ in events)
    opb = len(on) / max(span, 1.0) * 60 / bpm
    raw["ripple"] = (opb - RIPPLE_OPB[0]) / (RIPPLE_OPB[1] - RIPPLE_OPB[0])
    est = {}
    for key, v in raw.items():
        if key in SKIP:
            continue
        a, b = FIX.get(key, (1.0, 0.0))
        est[key] = float(min(1, max(0, a * v + b)))
    from .habits import blend, habits, melody_line, to_beats
    from .harmony import key_of, progression
    try:
        prog = progression(events, bpm)
    except Exception:  # noqa: BLE001  進行が読めなくても、曲調の推定は使う
        prog = None
    try:
        tonic, minor = (prog["tonic"], prog["minor"]) if prog else key_of(events)
        mel = habits(to_beats(melody_line(events), beat_times(wav, bpm)), tonic, minor)
        mel = blend(mel) if mel else None
    except Exception:  # noqa: BLE001  旋律の癖も同じ
        mel = None
    return est, {"bpm": round(bpm, 1), "notes": len(events), "raw": raw, "progression": prog, "melody": mel}


def listen(src: str) -> tuple[dict, dict]:
    """src は URL か音声ファイルのパス。（推定した好みの値, {"title", "bpm", "notes"}）を返す。"""
    with tempfile.TemporaryDirectory(prefix="amane-listen-") as tmp:
        tmp = Path(tmp)
        if is_url(src):
            f, title = fetch(src.strip(), tmp)
        else:
            f = Path(src).expanduser()
            if not f.is_file():
                raise ListenError(f"ファイルがありません: {src}")
            title = f.stem
        # 頭の 10 秒は前置き（無音・語り・ロゴ）のことが多いので、長い曲は少し飛ばす
        start = 10.0 if _duration(f) > MAX_SECONDS * 0.5 else 0.0
        est, info = analyze_audio(to_wav(f, tmp / "x.wav", start))
    return est, {"title": title, **info}


def song_wav(src: str, dst: Path) -> tuple[Path, str]:
    """曲（URL か音声ファイル）を、頭から（MAX_SECONDS まで）wav に。（wav, 題名）"""
    tmp = dst.parent
    if is_url(src):
        f, title = fetch(src.strip(), tmp)
    else:
        f = Path(src).expanduser()
        if not f.is_file():
            raise ListenError(f"ファイルがありません: {src}")
        title = f.stem
    return to_wav(f, dst, 0.0), title


def transcribe_song(src: str) -> tuple[list[tuple[float, float, int, float]], dict, dict, list[list] | None]:
    """曲を頭から（MAX_SECONDS まで）書き起こす。弾くため（耳コピ）。
    （Basic Pitch の音符 [(開始秒, 終了秒, 音高, 強さ)], 推定した好みの値, {"title", "bpm", "progression", "melody", …},
      Transkun の音符 [開始秒, 長さ秒, 音高, 強さ]（入っていなければ None））
    曲調の推定は Basic Pitch の書き起こしで換算を決めてあるので、推定には Basic Pitch を使う。"""
    from .cover import has_transkun, transcribe_piano
    with tempfile.TemporaryDirectory(prefix="amane-perform-") as tmp:
        wav, title = song_wav(src, Path(tmp) / "x.wav")
        events = transcribe(wav)
        est, info = analyze_audio(wav, events)
        piano = transcribe_piano(wav) if has_transkun() else None
    return events, est, {"title": title, **info}, piano


def _duration(f: Path) -> float:
    probe = shutil.which("ffprobe")
    if not probe:
        return 0.0
    r = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


# 画面（認証の無い UI サーバー）から渡せる URL の場所。yt-dlp は任意の URL を取りに行けるので、
# 同じネットワークの人に、PC から内側のサービスを叩かせないよう、音楽の置き場だけに絞る
ALLOWED_HOSTS = ("youtube.com", "youtu.be", "nicovideo.jp", "nico.ms", "soundcloud.com")


def allowed_url(url: str) -> bool:
    from urllib.parse import urlparse
    u = urlparse(url.strip())
    host = (u.hostname or "").lower()
    return u.scheme in ("http", "https") and any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


def describe(est: dict, info: dict, before: dict, after: dict) -> str:
    """覚えたことを短い日本語に。"""
    from .style import PARAMS
    moved = [(k, after[k] - before[k]) for k in est if abs(after[k] - before[k]) >= 0.02]
    moved.sort(key=lambda kv: -abs(kv[1]))
    head = f"♩={info['bpm']:.0f} の曲"
    if not moved:
        return head + "。今の好みとほとんど同じでした"
    # 言葉の指示（engine.words）と同じ書き方
    return head + " → " + ", ".join(f"{PARAMS[k][0]}{'+' if d > 0 else ''}{d:.2f}" for k, d in moved)
