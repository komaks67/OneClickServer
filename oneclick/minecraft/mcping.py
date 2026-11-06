"""マイクラのサーバーに「入れる状態か」を確かめる（サーバー一覧の表示と同じ方法）。"""
from __future__ import annotations

import json
import random
import re
import socket
import struct
import time


def _varint(n: int) -> bytes:
    out = b""
    n &= 0xFFFFFFFF
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out += bytes([b | 0x80])
        else:
            return out + bytes([b])


def _read_varint(sock) -> int:
    n = shift = 0
    while True:
        b = sock.recv(1)
        if not b:
            raise ConnectionError("サーバーが途中で切断しました")
        n |= (b[0] & 0x7F) << shift
        if not b[0] & 0x80:
            return n
        shift += 7
        if shift > 35:
            raise ConnectionError("おかしな返事が来ました")


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        c = sock.recv(n - len(buf))
        if not c:
            raise ConnectionError("サーバーが途中で切断しました")
        buf += c
    return buf


def srv_lookup(host: str, timeout=3.0) -> tuple[str, int] | None:
    """_minecraft._tcp.<host> の SRV レコード（本当の行き先とポート）を調べる。"""
    qid = random.randint(0, 65535)
    name = f"_minecraft._tcp.{host}"
    q = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    for part in name.split("."):
        q += bytes([len(part)]) + part.encode()
    q += b"\x00" + struct.pack(">HH", 33, 1)
    for server in ("1.1.1.1", "8.8.8.8"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(timeout)
            s.sendto(q, (server, 53))
            data, _ = s.recvfrom(2048)
            s.close()
        except OSError:
            continue
        try:
            ancount = struct.unpack(">H", data[6:8])[0]
            pos = len(q)
            for _ in range(ancount):
                pos = _skip_name(data, pos)
                typ, _cls, _ttl, rdlen = struct.unpack(">HHIH", data[pos:pos + 10])
                pos += 10
                if typ == 33:
                    _prio, _w, port = struct.unpack(">HHH", data[pos:pos + 6])
                    target = _read_name(data, pos + 6)
                    return target.rstrip("."), port
                pos += rdlen
        except (struct.error, IndexError):
            return None
        return None
    return None


def _skip_name(data, pos):
    while True:
        ln = data[pos]
        if ln == 0:
            return pos + 1
        if ln & 0xC0 == 0xC0:
            return pos + 2
        pos += ln + 1


def _read_name(data, pos, depth=0):
    parts = []
    while depth < 10:
        ln = data[pos]
        if ln == 0:
            break
        if ln & 0xC0 == 0xC0:
            ptr = struct.unpack(">H", data[pos:pos + 2])[0] & 0x3FFF
            parts.append(_read_name(data, ptr, depth + 1))
            break
        parts.append(data[pos + 1:pos + 1 + ln].decode("ascii", "replace"))
        pos += ln + 1
    return ".".join(p for p in parts if p)


def parse_address(addr: str) -> tuple[str, int, bool]:
    addr = addr.strip()
    m = re.match(r"^\[([^\]]+)\](?::(\d+))?$", addr)  # [IPv6]:port
    if m:
        return m.group(1), int(m.group(2) or 25565), bool(m.group(2))
    if addr.count(":") == 1:
        h, p = addr.split(":")
        return h, int(p), True
    return addr, 25565, False


def _plain_text(desc) -> str:
    if isinstance(desc, str):
        return re.sub(r"§.", "", desc)
    if isinstance(desc, dict):
        return _plain_text(desc.get("text", "")) + "".join(_plain_text(x) for x in desc.get("extra", []))
    if isinstance(desc, list):
        return "".join(_plain_text(x) for x in desc)
    return ""


def ping(address: str, timeout=5.0) -> dict:
    host, port, explicit = parse_address(address)
    if not explicit and not re.match(r"^[\d.]+$", host):
        srv = srv_lookup(host)
        if srv:
            host, port = srv
    t0 = time.time()
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.settimeout(timeout)
        hb = host.encode()
        payload = _varint(0) + _varint(767) + _varint(len(hb)) + hb + struct.pack(">H", port) + _varint(1)
        s.sendall(_varint(len(payload)) + payload)
        s.sendall(_varint(1) + _varint(0))
        _read_varint(s)
        if _read_varint(s) != 0:
            raise ConnectionError("マイクラのサーバーではないようです")
        ln = _read_varint(s)
        data = json.loads(_recv_exact(s, ln).decode("utf-8", "replace"))
    ms = int((time.time() - t0) * 1000)
    players = data.get("players", {})
    return {"ok": True, "ms": ms, "version": (data.get("version") or {}).get("name", "?"),
            "online": players.get("online", 0), "max": players.get("max", 0),
            "motd": _plain_text(data.get("description", "")).strip(),
            "modded": bool(data.get("forgeData") or data.get("modinfo") or data.get("isModded")),
            "host": host, "port": port}
