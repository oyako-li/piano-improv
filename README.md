<h1 align="center">Amane</h1>

<p align="center"><sub>天音（あまね）— リポジトリ名は piano-improv</sub></p>

<p align="center"><b>落ち着いて優雅なピアノを、PC でずっと即興で弾き続ける AI。<br>聴きながら「いい」「微妙」と伝えると、だんだん自分の好みに寄っていきます。</b></p>

<p align="center">
  <a href="https://oyako-li.github.io/piano-improv/"><img src="docs/screen.jpg" alt="Amane の画面" width="760"></a>
</p>

<p align="center">
  <a href="https://oyako-li.github.io/piano-improv/"><img src="https://img.shields.io/badge/%E2%96%B6%20%E8%A9%A6%E8%81%B4-%E3%83%87%E3%83%A2%E6%BC%94%E5%A5%8F%203%20%E6%9B%B2-7a5a3c?style=for-the-badge&labelColor=2b2420" alt="デモ演奏を聴く"></a>
  <a href="#はじめかた"><img src="https://img.shields.io/badge/run-locally-a08060?style=for-the-badge&labelColor=2b2420" alt="はじめかた"></a>
</p>

- **方向は 2 つ**：印象派（サティ・ドビュッシー的な平行和音・リディア・揺れる maj7）と、日本の映画音楽系（王道進行・IV→IVm→I・カノン風の下降バス・ヨナ抜き）。その間のどこが好きかを、聴きながら探します
- **1 曲に起承転結がある**：起（A）→ 承 → 転（B）で盛り上げて → サビが頂点 → 主題を強く戻して引いていく。盛り上がりの大きさは好みの「ドラマ」で、作業用に平らにもできます（[docs/style.md](docs/style.md)）
- **終わらない**：その曲を、調・拍子・テンポ・動機を変えながら次々に弾きます
- **好みの教え方は 3 つ**：聴きながら評価する／「もっと静かに」「雨の夜っぽく」と言葉で言う／好きな曲を渡す（YouTube などの URL・音声ファイル・MIDI。その曲調を覚えます）
- **GPU はいりません**：作曲は音楽理論のルールと乱数。CPU ですぐ動きます。LLM は言葉の解釈に使うこともできる、という程度の任意です
- **音はグランドピアノ**：Salamander Grand Piano（ヤマハ C5 の録音）をサンプル単位のタイミングで鳴らします
- **ブラウザから操作・試聴**：UI サーバーを立てると、スマホなどから評価や言葉の指示ができ、演奏も MP3 で配信されます

## はじめかた

Linux（PipeWire / PulseAudio）で確かめています。Python 3.11 以上と、配信を使うなら ffmpeg が要ります。

```bash
git clone https://github.com/oyako-li/piano-improv.git
cd piano-improv
python3 -m venv .venv && .venv/bin/pip install -e .
tools/get_piano.sh          # ピアノの音（Salamander Grand Piano, 約 1.2GB）を assets/ に入れる
.venv/bin/piano play        # 弾き始める
```

### ターミナルで聴く・教える

演奏中に打てるもの（入力して Enter）:

| 入力 | 意味 |
|---|---|
| `+` / `++` | いい / すごくいい（いま聴こえている部分の設定の方へ好みを寄せる） |
| `-` / `--` | 微妙 / かなり微妙（その設定から遠ざける） |
| 言葉 | 「もっと静かに」「雨の夜っぽく」「3拍子で」「少し明るく」など。次のセクションから反映 |
| `s` | いま弾いている曲を MIDI で保存（`data/recordings/`） |
| `n` | 次の曲へ |
| `p` | いまの好みを表示 |
| `q` | 終わる |

### ブラウザから操作する

```bash
.venv/bin/piano serve                    # http://127.0.0.1:8765/
.venv/bin/piano serve --no-speaker       # PC では鳴らさず、ブラウザへの配信だけ
```

開くと、3D のキャラクターがピアノの前に座って、聞こえている音に合わせて鍵盤を弾いています（試作）。
「▶ 聴く」で、その端末にも MP3（192kbps）で配信されます。配信は数秒遅れますが、鍵盤の動きと評価は「その端末で聞こえていた部分」に合わせます。
いまの曲・調・和音、評価、言葉の指示（YouTube などの URL を貼ると、その曲を聴いて曲調を覚えます）、次の曲 / 演奏の一時停止と再開 / MIDI で保存、保存した曲のダウンロード、場面の画像保存と全画面ができます。
キー操作は `+` いい、`-` 微妙、`n` 次の曲、`p` 演奏を止める / 再開、`s` 曲を残す、`Space` 聴く / 止める、`f` 全画面。好みの値を直接動かすスライダーはありません（言葉か評価で伝えます）。

キャラのモデル（VRM）は自分で用意して `data/character.vrm` に置くか、環境変数 `PIANO_VRM` で指定します（このリポジトリには入っていません）。モデルが無くても、キャラが出ないだけで、ほかの操作はできます。
three.js と three-vrm は `pianoimprov/web/vendor/` に同梱しているので、ネットにつながらなくても動きます。

認証はありません。ほかの端末から使うときは、信頼できるネットワークの中だけにしてください（[docs/serve.md](docs/serve.md) に、常駐のさせ方と Tailscale での公開の例があります）。

### そのほかのコマンド

```bash
.venv/bin/piano render -n 2 out.wav     # 2 曲を WAV と MIDI に書き出す
.venv/bin/piano learn 好きな曲.mid ...   # 好きな曲から好みを推定して取り込む（-w で重み）。MIDI・音声・URL を渡せる
.venv/bin/piano show                    # いまの好み
.venv/bin/piano relisten                # 覚えた曲を今の版の推定で聴き直す
.venv/bin/piano reset                   # 好みを初期値に戻す（記録は残る）
.venv/bin/piano snap save 雨の夜        # いまの好みを名前を付けてしまう（data/taste.db）
.venv/bin/piano snap                    # しまった好みと、戻せる日の一覧
.venv/bin/piano snap load 3             # しまった好みを呼び出す（--at "2026-11-24 23:00" でその時刻の好み）
```

音声と URL から推定するには追加の依存が要ります（`pip install -e '.[listen]'` と `pip install --no-deps 'basic-pitch==0.4.0'`）。何を推定してどれだけ当たるかは [docs/listen.md](docs/listen.md) にあります。

チャットで「演奏して ＋ URL」と送ると、その曲を書き起こしてそのまま弾きます（耳コピ）。ピアノ専用の書き起こし Transkun を入れると正確になります（`pyproject.toml` の `cover` の手順。CPU で動きます）。耳コピは手元で聴くためのもので、`data/covers/` と録音は公開しないでください。

言葉の指示は普段キーワード辞書で読みます。辞書で読めない言い回しをローカル LLM に読ませるには、
`PIANO_LLM=gemma3:latest .venv/bin/piano play` のように Ollama のモデル名を渡します。

## しくみ

```mermaid
flowchart LR
    U["聴く人<br/>評価・言葉・好きな曲"] --> TA["好み<br/>17 次元"]
    TA -- "少しずらした設定で探索" --> CO["作曲<br/>和声 → 伴奏 → 旋律"]
    CO -- "音符列" --> A["ピアノ<br/>Salamander Grand"]
    A -- "スピーカー / MP3 配信" --> U
```

- **好み**は、テンポ・音の多さ・余白・和音の色彩・印象派⇔映画音楽・短調の多さ・ペダルなど 17 個の 0〜1 の値です（一覧は [docs/style.md](docs/style.md)）
- 曲ごと・セクションごとに、好みの値のまわりで少しずらした設定を引いて弾きます。評価・言葉・好きな曲は記録に残し、好みはその記録から積み上げます（ベイズの逐次更新。同じ曲は何度渡しても 1 曲分、古いものはゆっくり薄れる）。まだ確かでない値ほど広く試します（[docs/architecture.md](docs/architecture.md)）
- **作曲**は、2 小節の動機を音階上の音程を保ったまま次の和音へ移して繰り返します。強拍は和音の音、弱拍は順次進行、跳躍のあとは反対へ戻ります。長い旋律音と短 9 度でぶつかる伴奏の内声は抜き、ペダルは和音が変わるまで響かせます

モジュールの関係と処理の流れは [docs/architecture.md](docs/architecture.md)、HTTP API は [docs/api.md](docs/api.md) にあります。

## 中身

```
pianoimprov/
  cli.py         コマンド（play / serve / render / learn / relisten / show / reset / snap）
  snapshots.py   好みをしまう所（data/taste.db）
  server.py      UI サーバー（標準ライブラリのみ）・SSE・MP3 配信
  engine.py      演奏の本体：先読み・評価・言葉・一時停止・保存（CLI と UI サーバーの共通部分）
  composer.py    曲とセクションを作る
  theory.py      和音・音階・進行の型
  style.py       好みの 17 次元の定義
  taste.py       好みの保存と学習（data/taste.json, data/ratings.jsonl）
  words.py       言葉 → 好みの変化
  reference.py   好きな曲の MIDI → 好みの推定
  listen.py      好きな曲の音声・URL → 書き起こし → 好みの推定（任意の依存）
  audio.py       ピアノの音源と出力（pacat / pw-cat、配信用のタップ）
  web/character.html 画面（キャラが弾く・操作もここ）
tools/get_piano.sh  音源のダウンロード
docs/          説明と試聴ページ（GitHub Pages）
```

`data/`（自分の好みと評価の記録）と `assets/`（音源）はコミットしません。

## 確かめたこと / まだのこと

- 確かめた：設定を動かすと旋律の統計（音の多さ・跳躍・ヨナ抜き度）が実際に変わること、短 9 度の衝突が長い旋律音の約 3% まで減ったこと、ライブ再生と配信、評価が配信の遅れを補正して正しいセクションに付くこと
- `learn`（好きな曲からの推定）は粗いです。自作の出力で突き合わせると、色彩・跳躍・音の多さ・音域は設定に沿って動きますが、余白とヨナ抜き度は当てにならなかったので推定から外しています。実在の曲での精度は未確認です
- 音声・URL からの推定は、自作の演奏 48 曲を MP3 にして数えました。テンポは 48 曲すべてで当たり、長調か短調か・和音の色彩・音域は MIDI からの推定とよく一致します（[docs/listen.md](docs/listen.md)）。実際の録音で確かめたのはピアノの 2 曲（ジムノペディ、ジャズ）だけで、歌もの・オーケストラは試していません
- 音楽として好みかどうかは、聴いてみないと分かりません。[試聴ページ](https://oyako-li.github.io/piano-improv/)に、初期設定のままの 3 曲があります

## この先

- 評価が溜まったら、評価を予測するモデル（ガウス過程など）で探索を賢くする
- ルールの作曲に加えて、好きな曲で微調整した記号音楽モデルに旋律を任せる
- MIDI キーボードの入力に応える

## ライセンス

- プログラム：MIT License（[LICENSE](LICENSE)）
- ピアノの音：[Salamander Grand Piano](https://freepats.zenvoid.org/Piano/acoustic-grand-piano.html)（Alexander Holm、CC BY 3.0）。このリポジトリには入っていません。`tools/get_piano.sh` が FreePats の SF2 版を取ってきます
- 試聴ページの演奏（`docs/demo.mp3`）：このプログラムが作曲し、Salamander Grand Piano で鳴らしたものです。音源のクレジット（Alexander Holm, CC BY 3.0）を添えれば、自由に使って構いません。楽譜（`docs/demo.mid`）は CC0 です

---

**Amane** (repo: piano-improv) — an AI that improvises calm, elegant piano music forever, somewhere between Impressionism (Satie, Debussy) and Japanese film scores, and learns your taste as you listen. Rate what you hear (`+` / `-`), tell it in words ("quieter", "like a rainy night"), or give it songs you like (a YouTube URL, an audio file, or MIDI) and it picks up their tempo, mode, harmonic color and register. Composition is rule-based music theory plus randomness (no GPU), played with the Salamander Grand Piano. Run `piano play` in a terminal or `piano serve` for a browser UI with MP3 streaming. [Listen to a demo](https://oyako-li.github.io/piano-improv/). See [AGENTS.md](AGENTS.md) for notes aimed at AI coding assistants.
