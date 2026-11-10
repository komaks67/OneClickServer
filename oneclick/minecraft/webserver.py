"""画面（ブラウザ）とやりとりする小さなWebサーバー。このPCの中だけで動く。"""
from __future__ import annotations

import hmac
import json
import mimetypes
import os
import secrets
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import api, net
from .paths import CACHE_DIR, SERVERS_DIR

MIME = {".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml",
        ".png": "image/png", ".jpg": "image/jpeg", ".json": "application/json"}

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
HOME_URL = ""  # 統合版のホームの住所(main.py が入れる)
TOKEN = secrets.token_urlsafe(24)
last_ping = 0.0
MAX_UPLOAD = 1024 * 1024 * 1024  # 1GB（引っ越しファイルの zip だけ、もっと大きくてよい）
MAX_UPLOAD_ZIP = 16 * 1024 * 1024 * 1024


class Handler(BaseHTTPRequestHandler):
    server_version = "mc-multi-helper"

    def log_message(self, fmt, *args):  # 画面がうるさくならないように記録しない
        pass

    # ---------- 共通 ----------
    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        port = self.server.server_address[1]
        return host in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _origin_ok(self) -> bool:
        """ほかのサイト（ブラウザで開いている別のページ）から勝手に呼ばれないようにする。"""
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        port = self.server.server_address[1]
        return origin in (f"http://127.0.0.1:{port}", f"http://localhost:{port}")

    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8", cache=False):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("X-Frame-Options", "DENY")
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                             "font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https:; connect-src 'self'; "
                             "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")   # ほかのサイトのページから、画像などとして読み込ませない
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Referrer-Policy", "no-referrer")
        if not cache:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    # ---------- GET（画面のファイル） ----------
    def do_GET(self):
        if not self._host_ok():
            return self._send(403, b"forbidden", "text/plain")
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            with open(os.path.join(WEB_DIR, "index.html"), "rb") as f:
                body = f.read().replace(b"__TOKEN__", TOKEN.encode()).replace(b"__HOME_URL__", HOME_URL.encode())
            return self._send(200, body, "text/html; charset=utf-8")
        if path == "/icon":
            return self._icon()
        if path.startswith("/static/"):
            rel = path[len("/static/"):]
            full = os.path.abspath(os.path.join(WEB_DIR, rel))
            if not full.startswith(os.path.abspath(WEB_DIR) + os.sep) or not os.path.isfile(full):
                return self._send(404, b"not found", "text/plain")
            ext = os.path.splitext(full)[1].lower()
            # Windows のレジストリで種類がおかしくなることがあるので、画面のファイルは決めうち
            ctype = MIME.get(ext) or mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            with open(full, "rb") as f:
                return self._send(200, f.read(), ctype, cache=ext in (".svg", ".png", ".jpg"))
        return self._send(404, b"not found", "text/plain")

    def _icon(self):
        """アイテムの画像。例: /icon?id=minecraft:diamond&mc=1.21.1&s=サーバーのID"""
        from urllib.parse import parse_qs, urlsplit
        from . import items as items_mod
        q = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
        mods_dir = None
        sid = q.get("s") or ""
        if sid and "/" not in sid and "\\" not in sid and sid not in (".", "..") and "\x00" not in sid:
            d = os.path.join(SERVERS_DIR, sid, "mods")
            if os.path.isdir(d) and os.path.realpath(d).startswith(os.path.realpath(SERVERS_DIR) + os.sep):
                mods_dir = d
        try:
            data = items_mod.icon_png(q.get("id", ""), q.get("mc", ""), mods_dir)
        except Exception:  # noqa: BLE001
            data = None
        if not data:
            return self._send(404, b"", "image/png")
        return self._send(200, data, "image/png", cache=True)

    # ---------- POST（機能を呼ぶ） ----------
    def do_POST(self):
        global last_ping
        if not self._host_ok() or not self._origin_ok() or \
                not hmac.compare_digest((self.headers.get("X-Token") or "").encode(), TOKEN.encode()):
            return self._send(403, b"forbidden", "text/plain")
        path = self.path.split("?", 1)[0]
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0:
            return self._json(400, {"error": "おかしなデータです"})
        if path == "/api/upload":
            name = os.path.basename(self.headers.get("X-Filename", "upload.bin"))
            from urllib.parse import unquote
            name = unquote(name)
            if length > (MAX_UPLOAD_ZIP if name.lower().endswith(".zip") else MAX_UPLOAD):
                return self._json(413, {"error": "ファイルが大きすぎます"})
            d = os.path.join(CACHE_DIR, "uploads")
            os.makedirs(d, exist_ok=True)
            ext = os.path.splitext(name)[1].lower()
            if ext not in (".mrpack", ".jar", ".zip"):
                return self._json(400, {"error": ".mrpack か .jar のファイルを選んでください"})
            dest = os.path.join(d, uuid.uuid4().hex + ext)
            left = length
            with open(dest, "wb") as f:
                while left > 0:
                    chunk = self.rfile.read(min(left, 1024 * 1024))
                    if not chunk:
                        break
                    f.write(chunk)
                    left -= len(chunk)
            return self._json(200, {"path": dest, "name": name})
        if not path.startswith("/api/"):
            return self._send(404, b"not found", "text/plain")
        name = path[len("/api/"):]
        if length > 4 * 1024 * 1024:
            return self._json(413, {"error": "データが大きすぎます"})
        try:
            params = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except ValueError:
            return self._json(400, {"error": "おかしなデータです"})
        if not isinstance(params, dict):
            return self._json(400, {"error": "おかしなデータです"})
        if name == "ping":
            last_ping = time.time()
            return self._json(200, {"ok": True})
        func = api.ACTIONS.get(name)
        if not func:
            return self._json(404, {"error": f"不明な機能: {name}"})
        try:
            return self._json(200, func(**params) if params else func())
        except api.UserError as e:
            return self._json(400, {"error": str(e)})
        except net.NetError as e:
            return self._json(502, {"error": f"インターネットにつながりませんでした。（{e}）"})
        except TypeError as e:
            traceback.print_exc()
            return self._json(400, {"error": f"呼び出し方がおかしいです: {e}"})
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._json(500, {"error": f"エラーが起きました: {e}"})


def _clean_uploads(max_age=2 * 24 * 3600):
    """前に選んだファイルの残り（2日より古いもの）を消す。ディスクがいっぱいにならないように。"""
    d = os.path.join(CACHE_DIR, "uploads")
    try:
        for fn in os.listdir(d):
            f = os.path.join(d, fn)
            if os.path.isfile(f) and time.time() - os.path.getmtime(f) > max_age:
                os.remove(f)
    except OSError:
        pass


def start(port=0) -> ThreadingHTTPServer:
    _clean_uploads()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd
