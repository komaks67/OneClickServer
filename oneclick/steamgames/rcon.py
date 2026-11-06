"""とても小さな RCON(Source 方式)クライアント。ARK・Palworld で使う。"""
import socket
import struct


class Rcon:
    """とても小さな RCON(Source 方式)クライアント。"""

    def __init__(self, host, port, password, timeout=5):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self._id = 0
        self._send(3, password)
        # 認証の応答は 2 つ届くことがある
        for _ in range(2):
            rid, typ, _ = self._recv()
            if typ == 2:
                if rid == -1:
                    raise PermissionError("RCON のパスワードが違います")
                return

    def _send(self, typ, body):
        self._id += 1
        data = struct.pack("<ii", self._id, typ) + body.encode("utf-8") + b"\x00\x00"
        self.sock.sendall(struct.pack("<i", len(data)) + data)

    def _recv(self):
        raw = b""
        while len(raw) < 4:
            chunk = self.sock.recv(4 - len(raw))
            if not chunk:
                raise ConnectionError("接続が切れました")
            raw += chunk
        size = struct.unpack("<i", raw)[0]
        data = b""
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise ConnectionError("接続が切れました")
            data += chunk
        rid, typ = struct.unpack("<ii", data[:8])
        return rid, typ, data[8:-2].decode("utf-8", "replace")

    def command(self, cmd):
        self._send(2, cmd)
        try:
            return self._recv()[2]
        except socket.timeout:
            return ""

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass
