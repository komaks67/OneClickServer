"""インターネット通信まわり（標準ライブラリだけで動く）。

テスト用に `set_fake_handler()` で通信を差し替えられる。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import ssl
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from .appinfo import VERSION as APP_VERSION  # noqa: E402
APP_NAME = "mc-multi-helper"
USER_AGENT = f"{APP_NAME}/{APP_VERSION} (Windows desktop helper for Minecraft multiplayer servers)"

# テスト用：URL を受け取って (bytes, status) を返す関数
_fake_handler = None


def set_fake_handler(func):
    global _fake_handler
    _fake_handler = func


class NetError(Exception):
    pass


def _ctx():
    return ssl.create_default_context()


def request(url: str, data=None, method=None, headers=None, timeout=30, retries=2) -> bytes:
    """URL を開いて中身(bytes)を返す。失敗したら少し待って再挑戦する。"""
    if _fake_handler is not None:
        return _fake_handler(url, data, method)
    h = {"User-Agent": USER_AGENT}
    if headers:
        h.update(headers)
    body = None
    if data is not None:
        if isinstance(data, (dict, list)):
            body = json.dumps(data).encode("utf-8")
            h.setdefault("Content-Type", "application/json")
        elif isinstance(data, str):
            body = data.encode("utf-8")
        else:
            body = data
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=body, headers=h, method=method)
            with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            # 4xx は再挑戦しても無駄
            if 400 <= e.code < 500 and e.code != 429:
                raise NetError(f"{url} → エラー {e.code}") from e
            last = e
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = e
        time.sleep(1.5 * (i + 1))
    raise NetError(f"{url} に接続できませんでした（{last}）")


def get_json(url: str, params: dict | None = None, **kw):
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    return json.loads(request(url, **kw).decode("utf-8"))


def post_json(url: str, payload, **kw):
    return json.loads(request(url, data=payload, method="POST", **kw).decode("utf-8"))


def download(url: str, dest: str, sha1: str | None = None, sha512: str | None = None,
             progress=None, timeout=60, sha256: str | None = None, md5: str | None = None) -> str:
    """ファイルをダウンロードする。ハッシュ（指紋）が合わなければ消してエラーにする。"""
    os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(dest)), suffix=".part")
    os.close(fd)
    try:
        if _fake_handler is not None:
            content = _fake_handler(url, None, "GET")
            with open(tmp, "wb") as f:
                f.write(content)
        else:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as r, open(tmp, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0)
                done = 0
                while True:
                    chunk = r.read(1024 * 256)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if progress and total:
                        progress(done / total)
        if sha1 and file_hash(tmp, "sha1") != sha1.lower():
            raise NetError(f"ダウンロードしたファイルが壊れています: {os.path.basename(dest)}")
        if sha512 and file_hash(tmp, "sha512") != sha512.lower():
            raise NetError(f"ダウンロードしたファイルが壊れています: {os.path.basename(dest)}")
        if sha256 and file_hash(tmp, "sha256") != sha256.lower():
            raise NetError(f"ダウンロードしたファイルが壊れています: {os.path.basename(dest)}")
        if md5 and file_hash(tmp, "md5") != md5.lower():
            raise NetError(f"ダウンロードしたファイルが壊れています: {os.path.basename(dest)}")
        shutil.move(tmp, dest)
        return dest
    except urllib.error.URLError as e:
        raise NetError(f"ダウンロード失敗: {url}（{e}）") from e
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def file_hash(path: str, algo: str = "sha1") -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
