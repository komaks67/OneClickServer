"""OneClickServer の「殻(ホーム画面)」。このPCの中だけで動く小さな Web サーバー。

- /            ホーム(サーバー一覧・新しく作る・引き継ぎ・更新)
- /g/<ゲーム>/  ARK・Palworld・Terraria の画面
- /go/minecraft マイクラの画面へ移る(マイクラ部分は従来どおり別のポートで動く)
"""
from __future__ import annotations

import json
import mimetypes
import os
import secrets
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from . import appinfo, discordhub, migrate, paths, registry, selfupdate, steamgames
from .runtime import RES_DIR

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
MIME = {".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml",
        ".png": "image/png", ".json": "application/json"}

# 画面から呼べる、ゲーム管理係の機能(画面の名前 → 管理係の関数名)
GAME_ACTIONS = {
    "get_all": "get_all", "get_state": "state", "save_settings": "save_settings", "install": "install_or_update",
    "start": "start", "stop": "stop", "restart": "restart", "backup": "backup", "restore": "restore",
    "open_folder": "open_folder", "add_firewall": "add_firewall", "diagnose": "diagnose",
    "playit_toggle": "playit_toggle", "test_discord": "test_discord", "admin_command": "admin_command",
    "status_detail": "status_detail", "prune_backups": "prune_backups", "check_updates": "check_updates",
}


class UserError(Exception):
    pass


class Shell:
    def __init__(self):
        self.token = secrets.token_urlsafe(24)
        self.httpd: ThreadingHTTPServer | None = None
        self.mc_port = 0
        self.last_ping = 0.0
        self.settings = paths.load_json(paths.APP_SETTINGS, {})
        self.update_state = {"running": False, "lines": [], "error": ""}
        self._upd_lock = threading.Lock()
        self.on_shutdown = None   # main.py が入れる(後片付けの関数)
        self.notice = ""          # 前回の更新に失敗したときなどの、1回だけ出すお知らせ

    # ---------- アプリの設定 ----------
    def get_setting(self, key, default=None):
        return self.settings.get(key, default)

    def set_setting(self, key, value):
        self.settings[key] = value
        paths.save_json(paths.APP_SETTINGS, self.settings)

    # ---------- 起動 ----------
    def start(self, port=0) -> ThreadingHTTPServer:
        shell = self

        class H(Handler):
            pass
        H.shell = shell
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self.httpd

    @property
    def port(self) -> int:
        return self.httpd.server_address[1]

    def home_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    # ---------- ホームの機能 ----------
    def get_home(self):
        running = registry.any_running()
        req = selfupdate.required()
        return {
            "version": appinfo.VERSION,
            "cards": registry.home_cards(),
            "choices": registry.GAME_CHOICES,
            "update_required": req,
            "update": self.update_state,
            "anything_running": running,
            "theme": self.get_setting("theme", "auto"),
            "migrated": migrate.last_report(),
            "migrate_dismissed": bool(self.get_setting("migrate_dismissed", False)),
            "windows": os.name == "nt",
            "notice": self._pop_notice(),
        }

    def get_app_settings(self):
        from . import tray
        from .minecraft import autostart
        return {"resident": bool(self.get_setting("resident", True)), "resident_available": tray.available(),
                "windows_start": autostart.is_enabled(), "windows_start_available": autostart.supported()}

    def set_app_settings(self, resident=None, windows_start=None):
        from .minecraft import autostart
        if resident is not None:
            self.set_setting("resident", bool(resident))
        if windows_start is not None:
            try:
                autostart.set_enabled(bool(windows_start))
            except Exception as e:  # noqa: BLE001
                raise UserError(f"Windows の起動設定を変えられませんでした: {e}")
        return self.get_app_settings()

    def _pop_notice(self):
        n, self.notice = self.notice, ""
        return n

    def create_game(self, key):
        if key == "minecraft":
            return {"ok": True, "url": "/go/minecraft"}
        if key not in steamgames.BY_KEY:
            raise UserError("知らないゲームです。")
        m = steamgames.get(key)
        if not registry.steam_exists(key):
            m._write_settings()      # 設定ファイルを作る = 「作った」ことになる
        return {"ok": True, "url": f"/g/{key}/"}

    def get_theme(self):
        return {"theme": self.get_setting("theme", "auto")}

    def set_theme(self, theme):
        if theme not in ("auto", "light", "dark"):
            raise UserError("明るさの指定が正しくありません。")
        self.set_setting("theme", theme)
        return {"ok": True}

    def migration_scan(self, path=""):
        extra = [path] if path else []
        found = migrate.scan(extra_paths=extra)
        if path and not any(os.path.normcase(os.path.abspath(f["path"])) == os.path.normcase(os.path.abspath(path))
                            for f in found):
            return {"found": found, "message": "指定したフォルダには、古い OneClickServer のデータが見つかりませんでした。"}
        return {"found": found, "message": ""}

    def migration_run(self, path):
        if registry.steam_running():
            raise UserError("いま動いているサーバーがあります。止めてから、やり直してください。")
        try:
            return {"ok": True, **migrate.run(path)}
        except migrate.MigrateError as e:
            raise UserError(str(e)) from e

    def migration_undo(self):
        try:
            return {"ok": True, **migrate.undo_last()}
        except migrate.MigrateError as e:
            raise UserError(str(e)) from e

    def migration_dismiss(self):
        self.set_setting("migrate_dismissed", True)
        return {"ok": True}

    # ---------- ソフトの更新 ----------
    def update_check(self):
        try:
            return selfupdate.check()
        except Exception as e:  # noqa: BLE001
            return {"current": appinfo.VERSION, "latest": None, "newer": False, "can_apply": False, "safe": False,
                    "page": appinfo.RELEASES_PAGE, "notes": "", "message": f"更新を調べられませんでした: {e}"}

    def update_apply(self):
        with self._upd_lock:
            if self.update_state["running"]:
                return {"ok": True}
            self.update_state = {"running": True, "lines": [], "error": ""}
        threading.Thread(target=self._update_worker, daemon=True).start()
        return {"ok": True}

    def _update_worker(self):
        class Job:
            def write(_self, text):
                self.update_state["lines"] = (self.update_state["lines"] + [str(text)])[-8:]
        try:
            selfupdate.apply(Job(), registry.any_running, self.shutdown)
        except selfupdate.UpdateError as e:
            self.update_state["error"] = str(e)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.update_state["error"] = f"更新でエラーが起きました: {e}"
        finally:
            if self.update_state["error"]:
                self.update_state["running"] = False

    def shutdown(self, _stop_servers=False):
        """更新のために、ソフトを終了する(動いているサーバーは止めない)。"""
        def later():
            time.sleep(0.5)
            if self.on_shutdown:
                try:
                    self.on_shutdown()
                except Exception:  # noqa: BLE001
                    pass
            os._exit(0)
        threading.Thread(target=later, daemon=True).start()
        return {"ok": True}

    # ---------- Discord のボット(4ゲーム共通) ----------
    @staticmethod
    def _hub(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except discordhub.HubError as e:
            raise UserError(str(e)) from None

    def bot_get(self):
        return self._hub(discordhub.bot_get)

    def bot_set(self, token=None, channel=None, users=None, alerts=None):
        return self._hub(discordhub.bot_set, token, channel, users, alerts)

    def bot_invite(self):
        return self._hub(discordhub.bot_invite)

    def bot_channels(self):
        return self._hub(discordhub.bot_channels)

    def bot_test(self):
        return self._hub(discordhub.bot_test)

    def bot_panel(self, sid):
        return self._hub(discordhub.bot_panel, str(sid))

    def open_url(self, url):
        """Discord の開発者ページ・招待リンクだけ、ブラウザで開く。"""
        import webbrowser
        url = str(url)
        if not (url.startswith("https://discord.com/developers/") or url.startswith("https://discord.com/oauth2/authorize?")):
            raise UserError("開けるのは、Discord のページだけです。")
        webbrowser.open(url)
        return {"ok": True}

    HOME_ACTIONS = {"get_app_settings", "set_app_settings", "bot_get", "bot_set", "bot_invite", "bot_channels", "bot_test", "bot_panel", "open_url", "get_home", "create_game", "get_theme", "set_theme", "migration_scan", "migration_run", "migration_undo",
                    "migration_dismiss", "update_check", "update_apply"}


class Handler(BaseHTTPRequestHandler):
    shell: Shell = None  # type: ignore[assignment]
    server_version = "oneclickserver"

    def log_message(self, fmt, *args):
        pass

    # ---------- 共通 ----------
    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        port = self.server.server_address[1]
        return host in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        port = self.server.server_address[1]
        return origin in (f"http://127.0.0.1:{port}", f"http://localhost:{port}")

    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8", cache=False, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("X-Frame-Options", "DENY")
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                             "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                             "base-uri 'none'; form-action 'none'")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if not cache:
            self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _page(self, name, **repl):
        with open(os.path.join(WEB_DIR, name), "rb") as f:
            body = f.read().replace(b"__TOKEN__", self.shell.token.encode())
        for k, v in repl.items():
            body = body.replace(k.encode(), str(v).encode())
        return self._send(200, body, "text/html; charset=utf-8")

    # ---------- GET ----------
    def do_GET(self):
        if not self._host_ok():
            return self._send(403, b"forbidden", "text/plain")
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            return self._page("home.html")
        if path == "/go/minecraft":
            if not self.shell.mc_port:
                return self._send(503, "マイクラの部分が起動していません。".encode("utf-8"), "text/plain; charset=utf-8")
            return self._send(302, b"", "text/plain", extra={"Location": f"http://127.0.0.1:{self.shell.mc_port}/"})
        if path.startswith("/g/"):
            parts = path.strip("/").split("/")
            if len(parts) == 2 and parts[1] in steamgames.BY_KEY:
                steamgames.get(parts[1])
                return self._page("game.html")
            return self._send(404, b"not found", "text/plain")
        if path.startswith("/static/"):
            rel = path[len("/static/"):]
            full = os.path.abspath(os.path.join(WEB_DIR, rel))
            if not full.startswith(os.path.abspath(WEB_DIR) + os.sep) or not os.path.isfile(full):
                return self._send(404, b"not found", "text/plain")
            ext = os.path.splitext(full)[1].lower()
            ctype = MIME.get(ext) or mimetypes.guess_type(full)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            with open(full, "rb") as f:
                return self._send(200, f.read(), ctype)
        return self._send(404, b"not found", "text/plain")

    # ---------- POST ----------
    def do_POST(self):
        if not self._host_ok() or not self._origin_ok() or self.headers.get("X-Token") != self.shell.token:
            return self._send(403, b"forbidden", "text/plain")
        path = urlsplit(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        if length > 2 * 1024 * 1024:
            return self._json(413, {"error": "データが大きすぎます"})
        try:
            params = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except ValueError:
            return self._json(400, {"error": "おかしなデータです"})
        if not isinstance(params, dict):
            return self._json(400, {"error": "おかしなデータです"})
        try:
            if path == "/api/ping":
                self.shell.last_ping = time.time()
                return self._json(200, {"ok": True})
            if path.startswith("/api/"):
                name = path[len("/api/"):]
                if name not in Shell.HOME_ACTIONS:
                    return self._json(404, {"error": f"不明な機能: {name}"})
                return self._json(200, getattr(self.shell, name)(**params))
            if path.startswith("/g/"):
                parts = path.strip("/").split("/")     # g, <key>, api, <name>
                if len(parts) == 4 and parts[2] == "api" and parts[1] in steamgames.BY_KEY:
                    self.shell.last_ping = time.time()
                    fn = GAME_ACTIONS.get(parts[3])
                    if not fn:
                        return self._json(404, {"error": f"不明な機能: {parts[3]}"})
                    m = steamgames.get(parts[1])
                    args = params.get("args") or []
                    if not isinstance(args, list):
                        return self._json(400, {"error": "おかしなデータです"})
                    return self._json(200, getattr(m, fn)(*args))
            return self._send(404, b"not found", "text/plain")
        except UserError as e:
            return self._json(400, {"error": str(e)})
        except TypeError as e:
            traceback.print_exc()
            return self._json(400, {"error": f"呼び出し方がおかしいです: {e}"})
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._json(500, {"error": f"エラーが起きました: {e}"})


def resource_path(rel: str) -> str:
    return os.path.join(RES_DIR, rel)
