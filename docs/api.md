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
| GET | `/api/params` | 好みの 17 次元の説明 |
| GET | `/api/recordings` | 保存した MIDI の一覧 |
| GET | `/api/snapshots` | しまった好み（`saved`）と、記録から戻せる日ごとの時点（`history`） |
| GET | `/api/taste?at=` | その時刻（epoch 秒。省略でいま）の好み（mu・sd・n・refs）。記録から作り直す |
| GET | `/recordings/<name>.mid` | 保存した MIDI のダウンロード |
| POST | `/api/rate` | `{"r": 1, "at": 12.3}` 評価（-2〜2、0 以外）。`at` は聴いた時刻（エンジンの秒、配信の遅れの補正。省略可） |
| POST | `/api/words` | `{"text": "もっと静かに"}` 言葉の指示 |
|  |  | 言葉に曲の URL が入っていれば（「演奏して https://…」）、その曲を聴いて寄せる（`/api/learn` と同じ）。残りが頼み方の言葉だけでなければ、言葉の指示としても受ける |
| POST | `/api/perform` | `{"src": "https://youtu.be/..."}` その曲を頭から（6 分まで）書き起こして、いまの曲を締めたあとに弾く（耳コピ）。そのあとはその曲に寄せて即興する。耳コピは曲ごとに `data/covers/` に残り、同じ曲をまた頼むと元の音と聴き比べて 1 回分詰めてから弾く。チャットで「演奏して」「弾いて」＋ URL でも同じ |
| POST | `/api/learn` | `{"src": "https://youtu.be/..."}` その曲を聴いて曲調へ好みを寄せる。YouTube・ニコニコ・SoundCloud の URL だけ（[listen.md](listen.md)）。すぐ返り、結果は `/api/events` のメッセージ（`listen` → `learn` か `warn`）で届く |
| POST | `/api/param` | `{"name": "tempo", "value": 0.3}` 好みの値を直接変える（画面からは使っていない） |
| POST | `/api/next` | 次の曲へ |
| POST | `/api/save` | いまの曲を MIDI で保存 |
| POST | `/api/pause` / `/api/resume` | 一時停止 / 再開（再開は新しい曲から） |
| POST | `/api/snapshots` | `{"name": "雨の夜", "at": 1764000000}` いまの（at の時刻の）好みを `data/taste.db` にしまう |
| POST | `/api/snapshots/load` | `{"id": 3}` か `{"at": 1764000000}` しまった好み・過去の時点の好みを呼び出す（次のセクションから） |
| POST | `/api/snapshots/delete` | `{"id": 3}` しまった好みを消す |

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
  "recent": [{"start": 780.0, "end": 812.0, "piece": 12, "part": "intro", "key": "E♭ major", "...": "...",
              "index": 0, "plan": ["intro", "A", "A'", "B", "S", "A'", "outro"]}],   // 直近 6 セクション。index は曲の中の何番目か、plan は曲のセクションの並び
  "taste": {"mu": {"tempo": 0.3, "...": 0.5}, "n": 4, "spread": 0.13, "focus": {"title": "渡した曲", "left": 3}},
  "messages": [{"t": 1763680000.0, "kind": "rate", "text": "♥ 覚えました（…）"}]
}
```

配信を聴いているブラウザは、`/stream` を開いた時点の `generated` と `<audio>` の `currentTime` から「いま聞こえている時刻」を出し、`recent` のどのセクションかを決めて表示し、評価の `at` に入れます。
