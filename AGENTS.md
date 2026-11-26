# AGENTS.md — Amane（piano-improv）を動かす・直す AI へ

## 動かす

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
tools/get_piano.sh                      # 音源（assets/ に約 1.2GB。コミットしない）
.venv/bin/piano render -n 1 /tmp/x.wav  # 音を出さずに確かめるならこれ（WAV と MIDI を書く）
.venv/bin/piano play                    # 鳴らす（Linux: pacat か pw-cat が要る）
.venv/bin/piano serve                   # UI サーバー http://127.0.0.1:8765/
```

`PIANO_DATA` で好みと評価の置き場所を変えられます（既定は `data/`）。試すときは空のディレクトリを渡すと、人の好みを汚しません。
`--seed` で乱数を固定すると、同じ曲が再現できます。

## 構成

処理の流れは [docs/architecture.md](docs/architecture.md)。要点：

- `engine.py` が中心。CLI（`cli.py`）と UI サーバー（`server.py`）は、エンジンを操作するだけ
- `composer.py` は設定 θ（17 次元、[docs/style.md](docs/style.md)）を受け取って、秒単位の音符列を返す。音には触らない
- `taste.py` は好み（次元ごとの平均と不確かさ）。`data/ratings.jsonl` の記録を頭から積み上げて作る（ベイズ）。`style.py` が 17 次元の定義
- `reference.py` は MIDI から、`listen.py` は音声・URL から（書き起こしてから `reference.estimate`）好みを推定する。`listen.py` の依存（Basic Pitch・librosa・yt-dlp）は任意で、`[listen]` の extra
- サーバーは標準ライブラリのみ。画面は `pianoimprov/web/character.html` 1 枚（`/` で開く。ビルドなし。three.js と three-vrm は `web/vendor/` に同梱）

## 直すときの決まり

- **聴いた感想で直すときは、先に統計で確かめる**。「余白が少ない」と言われたら、`space` を 0 と 1 で `render` して、休符の割合が本当に変わるかを数える。設定が効いていなかった前例があります（余白・ヨナ抜き）
- **好みの次元を増やす・意味を変える**ときは、`style.py`、`composer.py`、`words.py` の辞書、`docs/style.md` をそろえて直す。既存の `data/taste.json` は名前で読むので、知らない名前は初期値で埋まります
- **言葉の辞書**（`words.py` の `RULES`）は、正規表現 → 変化量。1 回の指示で動くのは各値 ±0.25 まで
- **旋律・和音・伴奏の作りを変えたら `tools/melody_stats.py` で前の版と比べる**（`git worktree add` で前の版を出して、その場所を渡す）。止まる小節・音の重なり・濁りが増えていないか
- **曲の形（`composer.PLANS` のエネルギー、エネルギーの効かせ方）を変えたら `tools/form_stats.py` で数え直す**（`--set drama=0` と `drama=1` も）。参考の録音と比べた表は [docs/style.md](docs/style.md)
- **好みの学び方（`taste.py`）を変えたら `tools/eval_taste.py` で前と比べる**（`--seed 1` で決めて `--seed 2` で確かめる）。推定の誤差が変わったら `taste.LISTEN_ERR` も直す
- **音声からの推定を変えたら `tools/eval_listen.py` で数え直す**（`--seed 1` で決めて `--seed 2` で確かめる。同じ組で決めて同じ組で測らない）。結果は [docs/listen.md](docs/listen.md) に書く
- **和音の進行の読み取り（`harmony.py`）を変えたら `tools/eval_chords.py` で数え直す**（`--seed 1` で決めて別の seed で確かめる）。借りた進行で弾いたときの濁りは、`tools/melody_stats.py` の `Taste.sample_piece` に進行を差して数えた（docs/listen.md）
- **旋律の癖の読み取り（`habits.py`）を変えたら `tools/eval_habits.py` で数え直す**（`--seed 1` で決めて `--seed 2` で確かめる）
- **耳コピの書き起こし（`cover.py`）を変えたら `tools/eval_cover.py` で音の当たり方を数え直す**（`--seed 1` で決めて `--seed 2` で確かめる）
- **画面から渡せる URL の場所（`listen.ALLOWED_HOSTS`）を広げない**。yt-dlp は任意の URL を取りに行けるので、認証の無いサーバーから内側のサービスを叩かれる
- **GPU を前提にしない**。LLM は `PIANO_LLM` を設定したときだけ使う任意の機能のまま
- **UI サーバーに認証はない**。既定の `127.0.0.1` を変えない。インターネットに出す変更（`0.0.0.0` を既定にする、Funnel など）はしない

## やってはいけないこと

- `assets/`（音源）、`data/`（その人の好み・評価・録音）、`*.wav` をコミットする
- 音源を別の場所に配り直す（取ってくるのは `tools/get_piano.sh` で FreePats から。クレジットは Alexander Holm, CC BY 3.0）
- 確かめていないことを README の「確かめたこと」に書く

## 試聴ページ（GitHub Pages）

`docs/` を main ブランチから公開しています（`docs/index.html`）。デモを作り直すとき：

```bash
D=$(mktemp -d)
PIANO_DATA=$D .venv/bin/piano --seed 7 render -n 3 /tmp/demo.wav
ffmpeg -y -i /tmp/demo.wav -c:a libmp3lame -b:a 160k docs/demo.mp3
cp /tmp/demo.mid docs/demo.mid
PIANO_DATA=$D .venv/bin/python tools/demo_timeline.py 7 3 > docs/demo.json   # 曲・セクションの時刻（ページの表示用）
```
