"""UI サーバー（標準ライブラリのみ）。音は PC のスピーカーから鳴り、ブラウザはリモコンになる。

  GET  /                     画面（キャラクターがピアノを弾く。評価・言葉・一時停止・保存もここから）
  GET  /api/state            いまの状態（JSON）
  GET  /api/events           状態の変化を Server-Sent Events で流す
  GET  /stream               演奏をそのまま MP3 で配信（ブラウザの <audio> で聴く）
  GET  /api/params           パラメータの説明
  GET  /api/notes?from=&to=  その時間（エンジンの秒）の音符（キャラの演奏用）
  GET  /character            / へ移動（前の URL）
  GET  /vrm/character.vrm    キャラのモデル（data/character.vrm か環境変数 PIANO_VRM。公開しない）
  GET  /api/recordings       保存した MIDI の一覧
  GET  /api/snapshots        しまった好み（saved）と、記録から呼び出せる過去の時点（history）
  GET  /api/taste?at=        その時刻（epoch 秒、省略でいま）の好み
  GET  /recordings/<name>    保存した MIDI のダウンロード
  POST /api/rate     {"r": 1, "at": 12.3}  評価（-2..2）。at は聴いた時刻（配信の遅れの補正、省略可）
  POST /api/words    {"text": "..."}   言葉の指示
  POST /api/learn    {"src": "https://youtu.be/..."}  その曲を聴いて曲調へ好みを寄せる（YouTube・ニコニコ・SoundCloud）
  POST /api/perform  {"src": "https://youtu.be/..."}  その曲を書き起こして弾く（耳コピ）。そのあとはその曲に寄せて即興する
  POST /api/param    {"name": "tempo", "value": 0.3}
  POST /api/next | /api/save | /api/pause | /api/resume
  POST /api/snapshots         {"name": "雨の夜", "at": 1764000000}  好みをしまう（at は省略でいま）
  POST /api/snapshots/load    {"id": 3} か {"at": 1764000000}       しまった好み・過去の時点の好みを呼び出す
  POST /api/snapshots/delete  {"id": 3}
"""
from __future__ import annotations

import json
import mimetypes
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from .audio import SR
from .engine import Engine
from .style import PARAMS

STATIC = Path(__file__).resolve().parent / "web"


# 曲の URL に添える、頼み方だけの言葉（これだけなら言葉の指示としては扱わない）
PLAY_WORDS = r"(この曲|これ|の曲|みたいな|みたいに|っぽい|っぽく|風に|風の|感じで|演奏|弾いて|ひいて|弾く|して|ください|下さい|お願い|を|で|に|の|[、。,.!！?？\s　])"


def make_handler(eng: Engine):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # アクセスログは出さない
            pass

        # --------------------------------------------------------- 送信
        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            extra = extra or {}
            if "Cache-Control" not in extra:
                self.send_header("Cache-Control", "no-store")
            for k, v in extra.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            try:
                return json.loads(self.rfile.read(n))
            except json.JSONDecodeError:
                return {}

        # --------------------------------------------------------- GET
        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                self._send(200, (STATIC / "character.html").read_bytes(), "text/html; charset=utf-8")
            elif path in ("/character", "/character.html"):      # 前の URL（ブックマーク用）
                self._send(302, b"", "text/plain", {"Location": "/"})
            elif path.startswith("/vendor/"):
                f = (STATIC / path.lstrip("/")).resolve()
                if STATIC.resolve() in f.parents and f.is_file():
                    ctype = "text/javascript; charset=utf-8" if f.suffix == ".js" else (mimetypes.guess_type(f.name)[0] or "application/octet-stream")
                    self._send(200, f.read_bytes(), ctype, {"Cache-Control": "max-age=86400"})
                else:
                    self._json({"error": "not found"}, 404)
            elif path == "/vrm/character.vrm":
                f = Path(os.environ.get("PIANO_VRM") or eng.data / "character.vrm")
                if f.is_file():
                    self._send(200, f.read_bytes(), "model/gltf-binary")
                else:
                    self._json({"error": "キャラのモデルがありません（data/character.vrm に置くか PIANO_VRM を指定）"}, 404)
            elif path == "/api/notes":
                q = dict(x.split("=", 1) for x in self.path.partition("?")[2].split("&") if "=" in x)
                try:
                    a, b = float(q["from"]), float(q["to"])
                except (KeyError, ValueError):
                    return self._json({"error": "from と to（秒）が要ります"}, 400)
                self._json({"notes": eng.notes(a, min(b, a + 30))})
            elif path == "/api/state":
                self._json(eng.state())
            elif path == "/api/params":
                self._json([{"name": k, "label": d, "lo": a, "hi": b} for k, (d, a, b, _) in PARAMS.items()])
            elif path == "/api/recordings":
                self._json(eng.recordings())
            elif path == "/api/snapshots":
                self._json(eng.snapshot_list())
            elif path == "/api/taste":
                q = dict(x.split("=", 1) for x in self.path.partition("?")[2].split("&") if "=" in x)
                try:
                    at = float(q["at"]) if q.get("at") else None
                except ValueError:
                    return self._json({"error": "at は epoch 秒"}, 400)
                st = eng.taste.state(at)
                self._json({"at": at, "mu": st["mu"], "n": st["n"], "refs": st["refs"],
                            "sd": {k: round(v ** -0.5, 4) for k, v in st["prec"].items()}})
            elif path.startswith("/recordings/"):
                name = Path(path).name
                f = eng.data / "recordings" / name
                if name.endswith(".mid") and f.is_file():
                    self._send(200, f.read_bytes(), "audio/midi",
                               {"Content-Disposition": f'attachment; filename="{name}"'})
                else:
                    self._json({"error": "not found"}, 404)
            elif path == "/api/events":
                self._events()
            elif path == "/stream":
                self._stream()
            else:
                self._json({"error": "not found"}, 404)

        def _events(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            last, last_sent = -1, 0.0
            try:
                while eng.running:
                    now = time.time()
                    if eng.version != last or now - last_sent > 1.0:   # 進み具合のため 1 秒ごとにも送る
                        last, last_sent = eng.version, now
                        data = json.dumps(eng.state(), ensure_ascii=False)
                        self.wfile.write(f"data: {data}\n\n".encode())
                        self.wfile.flush()
                    time.sleep(0.2)
            except (BrokenPipeError, ConnectionResetError):
                pass
            self.close_connection = True

        def _stream(self) -> None:
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                return self._json({"error": "ffmpeg がないので配信できません"}, 503)
            q = eng.out.tap()
            proc = subprocess.Popen(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "f32le", "-ar", str(SR), "-ac", "2",
                 "-i", "pipe:0", "-c:a", "libmp3lame", "-b:a", "192k", "-flush_packets", "1", "-f", "mp3", "pipe:1"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            alive = [True]

            def feed() -> None:
                try:
                    while alive[0] and eng.running:
                        try:
                            proc.stdin.write(q.get(timeout=1))
                        except queue.Empty:
                            continue
                except (BrokenPipeError, ValueError, OSError):
                    pass

            threading.Thread(target=feed, daemon=True).start()
            eng.listeners += 1
            eng.version += 1
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                while eng.running:
                    chunk = proc.stdout.read1(4096)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                alive[0] = False
                eng.out.untap(q)
                eng.listeners -= 1
                eng.version += 1
                proc.kill()
                proc.wait()
                self.close_connection = True

        # --------------------------------------------------------- POST
        def do_POST(self):
            path = self.path.split("?")[0]
            b = self._body()
            try:
                if path == "/api/rate":
                    r = max(-2.0, min(2.0, float(b.get("r", 0))))
                    if not r:
                        return self._json({"error": "r must be non-zero"}, 400)
                    at = b.get("at")
                    ok = eng.rate(r, at=float(at) if at is not None else None)
                    return self._json({"ok": ok} if ok else {"error": "まだ何も鳴っていません"}, 200 if ok else 409)
                if path == "/api/words":
                    text = str(b.get("text", "")).strip()[:600]
                    if not text:
                        return self._json({"error": "text is empty"}, 400)
                    # 「演奏して https://…」のように言葉に曲の URL が入っていたら、その曲を聴いて寄せる。
                    # 残りの言葉が「演奏して」「っぽく」だけでなければ、言葉の指示としても受ける
                    m = re.search(r"https?://[^\s　]+", text)
                    if m:
                        from .listen import allowed_url
                        if not allowed_url(m.group(0)):
                            return self._json({"error": "YouTube・ニコニコ・SoundCloud の URL だけ渡せます"}, 400)
                        rest = (text[:m.start()] + " " + text[m.end():]).strip()
                        # 「演奏して」「弾いて」は、その曲を耳コピで弾く。「っぽく」「みたいに」はその曲に寄せて即興する
                        cover = re.search(r"演奏|弾いて|ひいて|弾く|弾け|耳コピ", rest) and not re.search(r"っぽ|みたい|風|寄せ|雰囲気", rest)
                        threading.Thread(target=eng.perform if cover else eng.learn, args=(m.group(0),), daemon=True).start()
                        if re.sub(PLAY_WORDS, "", rest):
                            threading.Thread(target=eng.words, args=(rest[:200],), daemon=True).start()
                        return self._json({"ok": True, "perform" if cover else "learn": m.group(0)})
                    text = text[:200]
                    threading.Thread(target=eng.words, args=(text,), daemon=True).start()
                    return self._json({"ok": True})
                if path == "/api/learn":
                    from .listen import allowed_url
                    src = str(b.get("src", "")).strip()[:500]
                    if not allowed_url(src):
                        return self._json({"error": "YouTube・ニコニコ・SoundCloud の URL だけ渡せます"}, 400)
                    threading.Thread(target=eng.learn, args=(src,), daemon=True).start()
                    return self._json({"ok": True})
                if path == "/api/perform":
                    from .listen import allowed_url
                    src = str(b.get("src", "")).strip()[:500]
                    if not allowed_url(src):
                        return self._json({"error": "YouTube・ニコニコ・SoundCloud の URL だけ渡せます"}, 400)
                    threading.Thread(target=eng.perform, args=(src,), daemon=True).start()
                    return self._json({"ok": True})
                if path == "/api/param":
                    eng.set_param(str(b["name"]), float(b["value"]))
                    return self._json({"ok": True})
                if path == "/api/next":
                    eng.skip()
                    return self._json({"ok": True})
                if path == "/api/save":
                    p = eng.save_current()
                    return self._json({"ok": True, "file": p.name} if p else {"error": "まだ何も鳴っていません"},
                                      200 if p else 409)
                if path == "/api/snapshots":
                    at = b.get("at")
                    sid = eng.snapshot_save(str(b.get("name", "")), float(at) if at is not None else None)
                    return self._json({"ok": True, "id": sid})
                if path == "/api/snapshots/load":
                    if b.get("id") is not None:
                        ok = eng.snapshot_load(sid=int(b["id"]))
                    else:
                        ok = eng.snapshot_load(at=float(b["at"]))
                    return self._json({"ok": True} if ok else {"error": "その好みはありません"}, 200 if ok else 404)
                if path == "/api/snapshots/delete":
                    ok = eng.snapshots.delete(int(b["id"]))
                    return self._json({"ok": ok}, 200 if ok else 404)
                if path == "/api/pause":
                    eng.pause()
                    return self._json({"ok": True})
                if path == "/api/resume":
                    eng.resume()
                    return self._json({"ok": True})
            except (KeyError, ValueError, TypeError) as e:
                return self._json({"error": f"bad request: {e}"}, 400)
            self._json({"error": "not found"}, 404)

    return Handler


def serve(make_engine: Callable[[], Engine], host: str = "127.0.0.1", port: int = 8765) -> None:
    # ポートを先に確保する（使用中なら、ピアノを鳴らし始める前に失敗させる）
    try:
        httpd = ThreadingHTTPServer((host, port), BaseHTTPRequestHandler)
    except OSError as e:
        raise SystemExit(f"ポート {port} を使えません（{e.strerror}）。もう起動していないか確認するか --port で変えてください")
    httpd.daemon_threads = True
    eng = make_engine()
    httpd.RequestHandlerClass = make_handler(eng)
    shown = "127.0.0.1" if host in ("0.0.0.0", "") else host
    print(f"UI: http://{shown}:{port}/" + ("（同じネットワークの他の端末からも開けます）" if host == "0.0.0.0" else ""),
          flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        eng.close()
