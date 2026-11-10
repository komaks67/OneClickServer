"""友達側：参加しているサーバーに、新しい友達用パックがないか確かめて、あれば入れ直す。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import tempfile
import urllib.request

from . import mcping, net
from .gateway import PACK_FILE, PACK_JSON

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class UpdateError(Exception):
    pass


def _base_urls(address: str) -> list[str]:
    host, port, explicit = mcping.parse_address(address)
    targets = []
    if not explicit and not re.match(r"^[\d.]+$", host):
        srv = mcping.srv_lookup(host)
        if srv:
            targets.append(srv)
    targets.append((host, port))
    out = []
    for h, p in targets:
        h = f"[{h}]" if ":" in h else h
        out.append(f"http://{h}:{p}")
    return list(dict.fromkeys(out))


def _get(url: str, timeout=8) -> bytes:
    if net._fake_handler is not None:  # テスト用
        return net._fake_handler(url, None, "GET")
    try:
        with _opener.open(urllib.request.Request(url, headers={"User-Agent": "mc-multi-helper"}), timeout=timeout) as r:
            return r.read()
    except (OSError, socket.timeout) as e:
        raise UpdateError(str(e)) from e


def check(address: str, local_info=None) -> dict:
    """サーバーの最新パックの情報を取る。local_info は同じPCのサーバーのとき用（自分で立てたサーバー）。"""
    if local_info:
        return local_info
    last = None
    for base in _base_urls(address):
        try:
            data = json.loads(_get(base + PACK_JSON).decode("utf-8"))
            data["_base"] = base
            return data
        except (UpdateError, ValueError, net.NetError) as e:
            last = e
    raise UpdateError(f"サーバーにつながりませんでした（{last}）")


def download(info: dict, dest_dir: str) -> str:
    base = info.get("_base")
    if not base:
        raise UpdateError("ダウンロード先がわかりません")
    data = _get(base + PACK_FILE, timeout=60)
    if info.get("sha256") and hashlib.sha256(data).hexdigest() != info["sha256"]:
        raise UpdateError("ダウンロードしたパックが壊れています。もう一度ためしてください。")
    os.makedirs(dest_dir, exist_ok=True)
    fd, path = tempfile.mkstemp(suffix=".mrpack", dir=dest_dir)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path
