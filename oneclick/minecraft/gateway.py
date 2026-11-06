"""ポートの「門番」：マイクラのポートに来た通信を見分ける。

- マイクラからの接続 → そのままサーバー（中の別ポート）に流す
- 「新しい友達用パックください」（HTTP の GET）→ このソフトが答える

これで、ポート開放や playit の設定を増やさずに、友達用パックを自動で配れる。
"""
from __future__ import annotations

import collections
import json
import os
import socket
import threading
import time

PACK_JSON = "/mcmh/pack.json"
PACK_FILE = "/mcmh/pack.mrpack"
BUF = 64 * 1024


class Gateway:
    FLOOD_PER_MIN = 30   # 1分間にこれより多くつないでくる相手は、しばらく入れない

    def __init__(self, public_port: int, pack_info_func, log=print, blocked_func=None, on_event=None):
        self.public_port = public_port
        self.blocked = blocked_func or (lambda: set())   # () -> BAN した IP の集まり
        self.on_event = on_event or (lambda kind, ip, info=None: None)
        self.port_map: collections.OrderedDict[int, str] = collections.OrderedDict()  # サーバー側の番号 → 本当の IP
        self.recent: collections.deque = collections.deque(maxlen=300)                 # (時刻, IP)
        self._hits: dict[str, collections.deque] = {}
        self._flood_until: dict[str, float] = {}
        self.internal_port = None
        self.pack_info = pack_info_func     # () -> (情報dict, ファイルのパス) か None
        self.log = log
        self.sock = None
        self.alive = False
        self.connections = 0
        self.pack_downloads = 0
        self._slots = threading.BoundedSemaphore(300)

    def start(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name != "nt":
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", self.public_port))
        s.listen(64)
        self.sock = s
        self.alive = True
        threading.Thread(target=self._accept, daemon=True).start()

    def close(self):
        self.alive = False
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass

    def _accept(self):
        while self.alive:
            try:
                c, addr = self.sock.accept()
            except OSError:
                return
            ip = addr[0]
            if ip.startswith("::ffff:"):
                ip = ip[7:]
            if not self._allow(ip):
                try:
                    c.close()
                except OSError:
                    pass
                continue
            if not self._slots.acquire(blocking=False):
                c.close()  # つなぎすぎ（いたずら対策）
                continue
            threading.Thread(target=self._handle_slot, args=(c, ip), daemon=True).start()

    def _allow(self, ip: str) -> bool:
        now = time.time()
        try:
            if ip in self.blocked():
                self.on_event("blocked", ip)
                return False
        except Exception:  # noqa: BLE001  BAN の一覧が読めなくても止めない
            pass
        if self._flood_until.get(ip, 0) > now:
            return False
        q = self._hits.setdefault(ip, collections.deque(maxlen=self.FLOOD_PER_MIN + 1))
        q.append(now)
        if ip not in ("127.0.0.1", "::1") and len(q) > self.FLOOD_PER_MIN and now - q[0] < 60:
            self._flood_until[ip] = now + 300
            self.on_event("flood", ip, {"count": len(q)})
            return False
        if len(self._hits) > 2000:
            self._hits.clear()
        return True

    def real_ip(self, local_port: int) -> str | None:
        return self.port_map.get(int(local_port))

    def _handle_slot(self, c, ip=None):
        try:
            self._handle(c, ip)
        finally:
            self._slots.release()

    def _handle(self, c: socket.socket, ip=None):
        try:
            c.settimeout(15)
            head = c.recv(8, socket.MSG_PEEK)
            if not head:
                c.close()
                return
            if head.startswith((b"GET ", b"HEAD ")):
                self._http(c)
                return
            self._relay(c, ip)
        except OSError:
            try:
                c.close()
            except OSError:
                pass

    # ---------------- マイクラの通信をそのまま流す ----------------
    def _relay(self, c, ip=None):
        if not self.internal_port:
            c.close()
            return
        try:
            up = socket.create_connection(("127.0.0.1", self.internal_port), timeout=10)
        except OSError:
            c.close()  # サーバーが止まっている
            return
        self.connections += 1
        if ip:
            self.recent.append((time.time(), ip))
            try:
                self.port_map[up.getsockname()[1]] = ip
                while len(self.port_map) > 500:
                    self.port_map.popitem(last=False)
            except OSError:
                pass
        c.settimeout(None)
        up.settimeout(None)
        for s in (c, up):
            try:
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass

        def pipe(a, b):
            try:
                while True:
                    data = a.recv(BUF)
                    if not data:
                        break
                    b.sendall(data)
            except OSError:
                pass
            finally:
                for s in (a, b):
                    try:
                        s.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    try:
                        s.close()
                    except OSError:
                        pass
        threading.Thread(target=pipe, args=(up, c), daemon=True).start()
        pipe(c, up)

    # ---------------- 友達用パックを配る ----------------
    def _http(self, c):
        data = b""
        while b"\r\n\r\n" not in data and len(data) < 8192:
            chunk = c.recv(1024)
            if not chunk:
                break
            data += chunk
        line = data.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        parts = line.split()
        method = parts[0] if parts else "GET"
        path = parts[1].split("?", 1)[0] if len(parts) > 1 else "/"
        got = self.pack_info()
        if path == PACK_JSON and got:
            body = json.dumps(got[0], ensure_ascii=False).encode()
            self._send(c, 200, "application/json; charset=utf-8", body, method)
        elif path == PACK_FILE and got and os.path.exists(got[1]):
            size = os.path.getsize(got[1])
            hdr = (f"HTTP/1.0 200 OK\r\nContent-Type: application/x-modrinth-modpack+zip\r\n"
                   f"Content-Length: {size}\r\nConnection: close\r\n\r\n").encode()
            c.sendall(hdr)
            if method != "HEAD":
                with open(got[1], "rb") as f:
                    while True:
                        b = f.read(BUF)
                        if not b:
                            break
                        c.sendall(b)
                self.pack_downloads += 1
            c.close()
        else:
            self._send(c, 404, "text/plain; charset=utf-8", "このアドレスはマイクラのサーバーです。".encode(), method)

    @staticmethod
    def _send(c, code, ctype, body, method):
        reason = {200: "OK", 404: "Not Found"}[code]
        c.sendall((f"HTTP/1.0 {code} {reason}\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n"
                   "Connection: close\r\n\r\n").encode() + (b"" if method == "HEAD" else body))
        c.close()


def internal_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p
