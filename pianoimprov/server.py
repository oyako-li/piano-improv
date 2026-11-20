"""UI サーバー（標準ライブラリのみ）。音は PC のスピーカーから鳴り、ブラウザはリモコンになる。

  GET  /                     画面
  GET  /api/state            いまの状態（JSON）
  GET  /api/events           状態の変化を Server-Sent Events で流す
  GET  /stream               演奏をそのまま MP3 で配信（ブラウザの <audio> で聴く）
  GET  /api/params           パラメータの説明
  GET  /api/recordings       保存した MIDI の一覧
  GET  /recordings/<name>    保存した MIDI のダウンロード
  POST /api/rate     {"r": 1, "at": 12.3}  評価（-2..2）。at は聴いた時刻（配信の遅れの補正、省略可）
  POST /api/words    {"text": "..."}   言葉の指示
  POST /api/param    {"name": "tempo", "value": 0.3}
  POST /api/next | /api/save | /api/pause | /api/resume
"""
from __future__ import annotations

import json
import queue
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
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
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
                self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/state":
                self._json(eng.state())
            elif path == "/api/params":
                self._json([{"name": k, "label": d, "lo": a, "hi": b} for k, (d, a, b, _) in PARAMS.items()])
            elif path == "/api/recordings":
                self._json(eng.recordings())
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
                    text = str(b.get("text", "")).strip()[:200]
                    if not text:
                        return self._json({"error": "text is empty"}, 400)
                    threading.Thread(target=eng.words, args=(text,), daemon=True).start()
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
