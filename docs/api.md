# HTTP API（`piano serve`）

標準ライブラリの HTTP サーバーです（既定は `127.0.0.1:8765`）。認証はありません。

| メソッド | パス | 内容 |
|---|---|---|
| GET | `/` | 画面（キャラクターが弾く。評価・言葉・一時停止・保存もここから） |
| GET | `/character` | `/` へ移動（前の URL） |
| GET | `/api/notes?from=&to=` | その時間（エンジンの秒）の音符。キャラの手と鍵盤を動かすため |
| GET | `/vrm/character.vrm` | キャラのモデル（`data/character.vrm` か `PIANO_VRM`） |
| GET | `/api/state` | いまの状態（JSON） |
| GET | `/api/events` | 状態の変化を Server-Sent Events で流す（変化があったとき、なくても 1 秒ごと） |
| GET | `/stream` | 演奏を MP3（192kbps）で配信。ffmpeg が必要 |
| GET | `/api/params` | 好みの 15 次元の説明 |
| GET | `/api/recordings` | 保存した MIDI の一覧 |
| GET | `/recordings/<name>.mid` | 保存した MIDI のダウンロード |
| POST | `/api/rate` | `{"r": 1, "at": 12.3}` 評価（-2〜2、0 以外）。`at` は聴いた時刻（エンジンの秒、配信の遅れの補正。省略可） |
| POST | `/api/words` | `{"text": "もっと静かに"}` 言葉の指示 |
| POST | `/api/learn` | `{"src": "https://youtu.be/..."}` その曲を聴いて曲調へ好みを寄せる。YouTube・ニコニコ・SoundCloud の URL だけ（[listen.md](listen.md)）。すぐ返り、結果は `/api/events` のメッセージ（`listen` → `learn` か `warn`）で届く |
| POST | `/api/param` | `{"name": "tempo", "value": 0.3}` 好みの値を直接変える（画面からは使っていない） |
| POST | `/api/next` | 次の曲へ |
| POST | `/api/save` | いまの曲を MIDI で保存 |
| POST | `/api/pause` / `/api/resume` | 一時停止 / 再開（再開は新しい曲から） |

## `/api/state` の形

```json
{
  "version": 123,                 // 状態が変わるたびに増える
  "paused": false,
  "listeners": 1,                 // /stream を聴いている数
  "now": 812.4,                   // いま鳴っている位置（秒）
  "generated": 818.1,             // どこまで作ったか（秒）
  "current": {"piece": 12, "part": "A", "key": "E♭ major", "meter": 3, "bpm": 58,
              "chords": ["E♭6/9", "Cm9"], "elapsed": 8.2, "length": 33.1},
  "recent": [{"start": 780.0, "end": 812.0, "piece": 12, "part": "intro", "key": "E♭ major", "...": "..."}],
  "taste": {"mu": {"tempo": 0.3, "...": 0.5}, "n": 4, "spread": 0.13},
  "messages": [{"t": 1763680000.0, "kind": "rate", "text": "♥ 覚えました（…）"}]
}
```

配信を聴いているブラウザは、`/stream` を開いた時点の `generated` と `<audio>` の `currentTime` から「いま聞こえている時刻」を出し、`recent` のどのセクションかを決めて表示し、評価の `at` に入れます。
