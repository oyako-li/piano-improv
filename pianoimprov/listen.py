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
ONSETS_PER_BEAT_MAX = 3.25
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


def transcribe(wav: Path) -> list[tuple[float, float, int, float]]:
    """[(開始秒, 終了秒, 音高, 強さ 0..1)]"""
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
        _, _, events = predict(str(wav), _MODEL)
    return [(float(s), float(e), int(p), float(a)) for s, e, p, a, *_ in events]


_MODEL = None


def tempo(wav: Path) -> float:
    """♩ の BPM。拍の取り違え（倍・半分）は、この生成器の範囲（46〜96）に入る方へ寄せる。"""
    import librosa
    y, sr = librosa.load(str(wav), sr=SR, mono=True)
    onset = librosa.onset.onset_strength(y=y, sr=sr)
    bpm = float(np.atleast_1d(librosa.feature.tempo(onset_envelope=onset, sr=sr, start_bpm=60))[0])
    while bpm > 120:
        bpm /= 2
    while bpm < 40:
        bpm *= 2
    return bpm


def analyze_audio(wav: Path) -> tuple[dict, dict]:
    """（推定した好みの値, 途中の数値）"""
    events = transcribe(wav)
    if len(events) < 32:
        raise ListenError("音が少なすぎて、曲調を読めませんでした")
    bpm = tempo(wav)
    # 速い曲を遅いテンポ（半分・2/3）と取り違えると、1 拍に打鍵が詰まる。生成した 48 曲でテンポが当たったものは
    # 拍あたり 3.13 以下、取り違えた 2 曲は 3.4〜3.6、速いジャズは 5.2 だった。超えたら倍にする
    # （この生成器のテンポは ♩=96 が上限なので、それより速ければ 1.5 倍でも 2 倍でも同じ値になる）
    onsets = np.unique(np.round(np.array([e[0] for e in events]) / 0.06))
    span = max(e[1] for e in events) - min(e[0] for e in events)
    if span > 0 and len(onsets) / span * 60 / bpm > ONSETS_PER_BEAT_MAX:
        bpm *= 2
    k = bpm / 60
    tnotes = sorted((s * k, (e - s) * k, p, int(round(20 + a * 107)), 0) for s, e, p, a in events)
    raw = estimate(tnotes, bpm, None, velocity=False, clip=False)
    est = {}
    for key, v in raw.items():
        if key in SKIP:
            continue
        a, b = FIX.get(key, (1.0, 0.0))
        est[key] = float(min(1, max(0, a * v + b)))
    return est, {"bpm": round(bpm, 1), "notes": len(events), "raw": raw}


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
