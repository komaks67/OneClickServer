"""Discord のボットで、サーバーを操作する（ボタンを押すと 起動・停止・状態）。

・ボットは、このソフトが動いている間だけ Discord につながる。
・スラッシュコマンドは登録しない（同じボットをほかのプログラムでも使っているとき、じゃましないため）。
・追加のライブラリは使わない（WebSocket も、標準の機能だけで話す）。
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import json
import os
import socket
import ssl
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import appinfo

API = os.environ.get("MCMH_DISCORD_API") or "https://discord.com/api/v10"
GATEWAY = os.environ.get("MCMH_DISCORD_GATEWAY") or "wss://gateway.discord.gg/?v=10&encoding=json"
USER_AGENT = f"DiscordBot (https://github.com/komaks67/OneClickServer, {appinfo.VERSION})"


class DiscordError(Exception):
    pass


# ---------------------------------------------------------------- REST（メッセージを送る など）

_ERR_JA = {
    401: "ボットのトークンが違います。設定を見直してください。",
    403: "ボットに権限がありません。そのチャンネルで「メッセージを送る」「公開スレッドを作る」「スレッドでメッセージを送る」を許可してください。",
    404: "チャンネル（またはメッセージ）が見つかりません。チャンネルIDと、ボットがそのサーバーに入っているかを確かめてください。",
}


def rest(method: str, path: str, token: str | None = None, body: dict | None = None, timeout=15) -> dict:
    url = API + path
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    headers = {"User-Agent": USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bot " + token
    for attempt in range(3):
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return json.loads(raw.decode("utf-8")) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if e.code == 429 and attempt < 2:      # 送りすぎ。言われた時間だけ待って、もう一度
                try:
                    wait = float(json.loads(detail).get("retry_after", 1))
                except (ValueError, AttributeError):
                    wait = 1.0
                time.sleep(min(max(wait, 0.2), 10))
                continue
            raise DiscordError(_ERR_JA.get(e.code) or f"Discord から断られました（{e.code}）: {detail}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise DiscordError(f"Discord につながりませんでした（{e}）") from e
    raise DiscordError("Discord につながりませんでした")


# ---------------------------------------------------------------- WebSocket（標準の機能だけで）

class WSClosed(Exception):
    def __init__(self, code=None, reason=""):
        super().__init__(f"closed {code} {reason}")
        self.code = code
        self.reason = reason


class WebSocket:
    def __init__(self, url: str, timeout=20):
        u = urllib.parse.urlsplit(url)
        secure = u.scheme == "wss"
        host, port = u.hostname, u.port or (443 if secure else 80)
        raw = socket.create_connection((host, port), timeout=timeout)
        self.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host) if secure else raw
        key = base64.b64encode(os.urandom(16)).decode()
        path = (u.path or "/") + ("?" + u.query if u.query else "")
        self.sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nUser-Agent: {USER_AGENT}\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise WSClosed(None, "handshake")
            head += chunk
            if len(head) > 65536:
                raise WSClosed(None, "handshake too long")
        head, self.buf = head.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        if " 101 " not in lines[0] + " ":
            raise WSClosed(None, lines[0])
        want = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        got = next((ln.split(":", 1)[1].strip() for ln in lines[1:] if ln.lower().startswith("sec-websocket-accept:")), "")
        if got != want:
            raise WSClosed(None, "bad accept")
        self.lock = threading.Lock()

    def _read(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise WSClosed(None, "eof")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _send_frame(self, opcode: int, payload: bytes):
        mask = os.urandom(4)
        n = len(payload)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self.lock:
            self.sock.sendall(head + mask + masked)

    def send(self, text: str):
        self._send_frame(1, text.encode("utf-8"))

    def recv(self) -> str:
        """次のテキストを返す。切れたら WSClosed。"""
        parts, kind = [], None
        while True:
            b1, b2 = self._read(2)
            fin, opcode = b1 & 0x80, b1 & 0x0F
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            if n > 32 * 1024 * 1024:
                raise WSClosed(None, "too big")
            mask = self._read(4) if b2 & 0x80 else None
            data = self._read(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if opcode == 8:
                code = struct.unpack(">H", data[:2])[0] if len(data) >= 2 else None
                raise WSClosed(code, data[2:].decode("utf-8", "replace"))
            if opcode == 9:
                self._send_frame(10, data)
                continue
            if opcode == 10:
                continue
            if opcode in (1, 2):
                kind, parts = opcode, [data]
            elif opcode == 0:
                parts.append(data)
            if fin and kind is not None:
                return b"".join(parts).decode("utf-8", "replace")

    def close(self):
        try:
            self._send_frame(8, struct.pack(">H", 1000))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------- ボット本体（Discord につなぎっぱなしにして、ボタンを待つ）

FATAL = {4004: "ボットのトークンが違います。", 4010: "設定がおかしいです（shard）。", 4011: "サーバーが多すぎます（shard が必要）。",
         4012: "Discord の版が合いません。", 4013: "設定がおかしいです（intents）。", 4014: "許可されていない機能を使おうとしました。"}


class Bot:
    def __init__(self, token: str, on_interaction, on_ready=None, log=lambda s: None):
        self.token = token
        self.on_interaction = on_interaction
        self.on_ready = on_ready or (lambda: None)
        self.log = log
        self.status = "つないでいます…"
        self.ok = False
        self.user = ""
        self.app_id = ""
        self._stop = threading.Event()
        self._ws: WebSocket | None = None
        self._seq = None
        self._session = None
        self._resume_url = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self._stop.set()
        ws = self._ws
        if ws:
            ws.close()

    # ---- つなぐ・つなぎ直す
    def _run(self):
        wait = 2
        while not self._stop.is_set():
            try:
                self._session_loop()
                wait = 2
            except WSClosed as e:
                self.ok = False
                if e.code in FATAL:
                    self.status = "つなげません: " + FATAL[e.code]
                    return
                if e.code in (4007, 4009):       # つなぎ直しができない → 最初からやり直す
                    self._session = None
                self.status = "つなぎ直しています…"
            except Exception as e:  # noqa: BLE001  ネットが切れた など
                self.ok = False
                self.status = f"つなぎ直しています…（{str(e)[:80]}）"
            if self._stop.wait(wait):
                return
            wait = min(wait * 2, 60)
        self.status = "止めました"

    def _session_loop(self):
        url = self._resume_url if (self._session and self._resume_url) else GATEWAY
        if "encoding=json" not in url:
            url += ("&" if "?" in url else "?") + "v=10&encoding=json"
        ws = self._ws = WebSocket(url)
        hb_stop = threading.Event()
        try:
            hello = json.loads(ws.recv())
            interval = (hello.get("d") or {}).get("heartbeat_interval", 41250) / 1000
            ws.sock.settimeout(interval * 2 + 10)

            def beat():
                if hb_stop.wait(interval * 0.5):
                    return
                while not hb_stop.is_set():
                    try:
                        ws.send(json.dumps({"op": 1, "d": self._seq}))
                    except OSError:
                        return
                    if hb_stop.wait(interval):
                        return
            threading.Thread(target=beat, daemon=True).start()
            if self._session:
                ws.send(json.dumps({"op": 6, "d": {"token": self.token, "session_id": self._session, "seq": self._seq}}))
            else:
                ws.send(json.dumps({"op": 2, "d": {"token": self.token, "intents": 0,
                                                   "properties": {"os": "windows", "browser": "OneClickServerMC", "device": "OneClickServerMC"}}}))
            while not self._stop.is_set():
                msg = json.loads(ws.recv())
                op = msg.get("op")
                if msg.get("s") is not None:
                    self._seq = msg["s"]
                if op == 0:
                    self._dispatch(msg.get("t"), msg.get("d") or {})
                elif op == 1:
                    ws.send(json.dumps({"op": 1, "d": self._seq}))
                elif op == 7:                        # Discord から「つなぎ直して」
                    raise WSClosed(None, "reconnect")
                elif op == 9:                        # つなぎ直しができない
                    if not msg.get("d"):
                        self._session = None
                    time.sleep(2)
                    raise WSClosed(None, "invalid session")
        finally:
            hb_stop.set()
            try:
                ws.sock.close()
            except OSError:
                pass
            self._ws = None

    def _dispatch(self, t, d):
        if t == "READY":
            self._session = d.get("session_id")
            self._resume_url = d.get("resume_gateway_url")
            self.user = (d.get("user") or {}).get("username") or ""
            self.app_id = str((d.get("application") or {}).get("id") or "")
            self.ok = True
            self.status = f"つながっています（{self.user}）"
            if self.on_ready:
                threading.Thread(target=self._safe, args=(self.on_ready,), daemon=True).start()
        elif t == "RESUMED":
            self.ok = True
            self.status = f"つながっています（{self.user}）"
        elif t == "INTERACTION_CREATE":
            threading.Thread(target=self._safe, args=(self.on_interaction, d), daemon=True).start()

    def _safe(self, fn, *a):
        try:
            fn(*a)
        except Exception as e:  # noqa: BLE001
            self.log(f"Discord ボットの処理でエラー: {e}")

    # ---- ボタンへの返事
    def reply(self, inter: dict, text: str = "", defer=False, embed: dict | None = None):
        """ボタンを押した人にだけ見える返事。defer=True なら「考え中…」を出して、あとで edit() で書きかえる。"""
        if defer:
            body = {"type": 5, "data": {"flags": 64}}
        else:
            data = {"flags": 64}
            if text:
                data["content"] = text[:1900]
            if embed:
                data["embeds"] = [embed]
            body = {"type": 4, "data": data}
        rest("POST", f"/interactions/{inter['id']}/{inter['token']}/callback", None, body)

    def edit(self, inter: dict, text: str = "", embed: dict | None = None):
        app = inter.get("application_id") or self.app_id
        data = {"content": text[:1900]}
        if embed:
            data["embeds"] = [embed]
        rest("PATCH", f"/webhooks/{app}/{inter['token']}/messages/@original", None, data)

    def send(self, channel: str, text: str = "", components=None, embed: dict | None = None) -> dict:
        body = {"allowed_mentions": {"parse": []}}
        if text:
            body["content"] = text[:1900]
        if embed:
            body["embeds"] = [embed]
        if components:
            body["components"] = components
        return rest("POST", f"/channels/{channel}/messages", self.token, body)

    def make_thread(self, channel: str, message: str, name: str) -> dict:
        return rest("POST", f"/channels/{channel}/messages/{message}/threads", self.token,
                    {"name": name[:90], "auto_archive_duration": 10080})


def server_key(server_id: str) -> str:
    """ボタンに入れる、サーバーの短い目印（名前が長くても、決まった長さになる）。"""
    return hashlib.sha1(str(server_id).encode("utf-8")).hexdigest()[:16]


def panel_components(server_id: str) -> list:
    def btn(style, label, act):
        return {"type": 2, "style": style, "label": label, "custom_id": f"ocs:{act}:{server_key(server_id)}"}
    return [{"type": 1, "components": [btn(3, "▶ 起動", "start"), btn(4, "■ 停止", "stop"), btn(2, "📊 状態", "status")]}]


# ---- 見た目（カード）と、招待リンク
COLORS = {"ok": 0x2ECC71, "wait": 0xF1C40F, "stop": 0x95A5A6, "bad": 0xE74C3C, "info": 0x3498DB}

# ボットに必要な権限: チャンネルを見る・書く・埋め込み(カード)・履歴・公開スレッドを作る・スレッドに書く
PERMISSIONS = (1 << 10) | (1 << 11) | (1 << 14) | (1 << 16) | (1 << 35) | (1 << 38)


def card(title: str, desc: str = "", kind: str = "info", fields=None, footer: str = "") -> dict:
    """Discord の「カード」（埋め込み）。色の帯・見出し・項目・時刻つき。"""
    e = {"title": title[:250], "color": COLORS.get(kind, COLORS["info"]),
         "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    if desc:
        e["description"] = desc[:3500]
    fs = [{"name": str(n)[:250], "value": (str(v) or "-")[:1000], "inline": bool(inl)} for n, v, inl in (fields or [])][:10]
    if fs:
        e["fields"] = fs
    if footer:
        e["footer"] = {"text": footer[:100]}
    return e


def app_id_from_token(token: str) -> str:
    """トークンの最初の部分（base64）が、ボット自身のID。"""
    try:
        head = token.split(".")[0]
        raw = base64.urlsafe_b64decode(head + "=" * (-len(head) % 4)).decode("ascii")
        return raw if raw.isdigit() else ""
    except Exception:  # noqa: BLE001
        return ""


def invite_url(token: str) -> str:
    cid = app_id_from_token(token)
    if not cid:
        return ""
    return f"https://discord.com/oauth2/authorize?client_id={cid}&permissions={PERMISSIONS}&scope=bot"
