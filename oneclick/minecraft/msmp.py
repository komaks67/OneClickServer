"""管理用の窓口（Minecraft Server Management Protocol・1.21.9 以降）につなぐ係。

WebSocket ＋ JSON-RPC 2.0 を、Python の標準の道具だけで話す。
- このPCの中（127.0.0.1）だけで待ち受けるようにして、合言葉（40文字）で守る
- 参加・退出・ホワイトリストの変更などの「お知らせ」も受け取れる
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import socket
import string
import struct
import threading
import time


class MsmpError(Exception):
    pass


def new_secret() -> str:
    chars = string.ascii_letters + string.digits
    return "".join(secrets.choice(chars) for _ in range(40))


class WebSocket:
    """最低限の WebSocket（RFC 6455）。テキストだけ。"""

    def __init__(self, host: str, port: int, secret: str, origin="http://127.0.0.1", timeout=5.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET / HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nAuthorization: Bearer {secret}\r\n"
               f"Origin: {origin}\r\n\r\n")
        self.sock.sendall(req.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise MsmpError("窓口が切断しました")
            head += chunk
            if len(head) > 65536:
                raise MsmpError("おかしな返事です")
        header, self._buf = head.split(b"\r\n\r\n", 1)
        status = header.split(b"\r\n", 1)[0]
        if b" 101 " not in status:
            code = status.decode("latin-1", "replace")
            if b"401" in status:
                raise MsmpError("合言葉が違うため、窓口に入れませんでした")
            raise MsmpError(f"窓口につなげませんでした（{code}）")
        self.sock.settimeout(None)
        self._send_lock = threading.Lock()

    def send_text(self, text: str):
        data = text.encode("utf-8")
        hdr = bytearray([0x81])
        n = len(data)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126)
            hdr += struct.pack(">H", n)
        else:
            hdr.append(0x80 | 127)
            hdr += struct.pack(">Q", n)
        mask = os.urandom(4)
        hdr += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        with self._send_lock:
            self.sock.sendall(bytes(hdr) + masked)

    def _send_control(self, opcode: int, payload: bytes = b""):
        mask = os.urandom(4)
        frame = bytes([0x80 | opcode, 0x80 | len(payload)]) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self._send_lock:
            self.sock.sendall(frame)

    def _read(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise MsmpError("窓口が切断しました")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def recv_text(self) -> str:
        parts = []
        while True:
            b1, b2 = self._read(2)
            fin, opcode = b1 & 0x80, b1 & 0x0F
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            if n > 64 * 1024 * 1024:
                raise MsmpError("大きすぎる返事です")
            mask = self._read(4) if b2 & 0x80 else None
            payload = self._read(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:
                raise MsmpError("窓口が閉じられました")
            if opcode == 0x9:
                self._send_control(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            parts.append(payload)
            if fin:
                return b"".join(parts).decode("utf-8", "replace")

    def close(self):
        try:
            self._send_control(0x8)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class Client:
    """JSON-RPC の呼び出しと、お知らせの受け取り。"""

    def __init__(self, host, port, secret, on_notify=None):
        self.ws = WebSocket(host, port, secret)
        self.on_notify = on_notify
        self._id = 0
        self._wait: dict[int, list] = {}
        self._lock = threading.Lock()
        self.alive = True
        self.schema: dict[str, list[str]] = {}
        threading.Thread(target=self._reader, daemon=True).start()
        try:
            disc = self.call_raw("rpc.discover", None, timeout=5)
            for m in disc.get("methods", []):
                self.schema[m.get("name")] = [p.get("name") for p in m.get("params", [])]
        except MsmpError:
            pass

    def _reader(self):
        try:
            while self.alive:
                msg = json.loads(self.ws.recv_text())
                if "id" in msg and msg["id"] in self._wait:
                    slot = self._wait[msg["id"]]
                    slot.append(msg)
                    slot[0].set()
                elif "method" in msg and self.on_notify:
                    try:
                        self.on_notify(msg["method"], msg.get("params"))
                    except Exception:  # noqa: BLE001
                        pass
        except (MsmpError, OSError, ValueError):
            pass
        finally:
            self.alive = False
            for slot in list(self._wait.values()):
                slot[0].set()

    def call_raw(self, method, params, timeout=8):
        if not self.alive:
            raise MsmpError("窓口とつながっていません")
        with self._lock:
            self._id += 1
            mid = self._id
        ev = threading.Event()
        self._wait[mid] = [ev]
        req = {"jsonrpc": "2.0", "method": method, "id": mid}
        if params is not None:
            req["params"] = params
        self.ws.send_text(json.dumps(req, ensure_ascii=False))
        ev.wait(timeout)
        slot = self._wait.pop(mid, [None])
        if len(slot) < 2:
            raise MsmpError("窓口から返事がありませんでした")
        msg = slot[1]
        if "error" in msg:
            err = msg["error"] or {}
            raise MsmpError(f"窓口でエラー: {err.get('message', err)}")
        return msg.get("result")

    def call(self, method: str, *args):
        """名前つきで送れるときは名前つきで、だめなら順番で送る。"""
        names = self.schema.get(method)
        if args and names and len(names) == len(args):
            try:
                return self.call_raw(method, dict(zip(names, args, strict=True)))
            except MsmpError as e:
                if "param" not in str(e).lower():
                    raise
        return self.call_raw(method, list(args) if args else None)

    def close(self):
        self.alive = False
        self.ws.close()


class Link:
    """サーバーが動いている間、窓口につなぎっぱなしにする（切れたらつなぎ直す）。"""

    def __init__(self, get_conf, on_notify=None, log=None):
        self.get_conf = get_conf        # () -> (host, port, secret) か None
        self.on_notify = on_notify
        self.log = log or (lambda *_: None)
        self.client: Client | None = None
        self.error: str | None = None
        self._stop = threading.Event()
        self._thread = None

    @property
    def connected(self):
        return bool(self.client and self.client.alive)

    def start(self, is_running):
        self._stop.clear()
        if self._thread and self._thread.is_alive():
            return

        def loop():
            while not self._stop.is_set() and is_running():
                if not self.connected:
                    conf = self.get_conf()
                    if conf:
                        try:
                            self.client = Client(*conf, on_notify=self.on_notify)
                            self.error = None
                        except (MsmpError, OSError) as e:
                            self.error = str(e)
                time.sleep(2)
            self.stop()
        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self.client:
            self.client.close()
            self.client = None

    def call(self, method, *args):
        if not self.connected:
            raise MsmpError("窓口とつながっていません")
        return self.client.call(method, *args)
