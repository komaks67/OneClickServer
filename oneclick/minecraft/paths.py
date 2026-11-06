"""保存場所と設定ファイル。ソフトのフォルダの中の data/ にすべて保存する。"""
from __future__ import annotations

import json
import os
import re
import sys

IS_WINDOWS = sys.platform.startswith("win")

from .runtime import APP_DIR

from ..paths import game_dir as _game_dir  # noqa: E402

# マイクラの保存場所: data/minecraft(従来の data フォルダと同じ中身)
BASE_DIR = os.environ.get("MCMH_DATA") or _game_dir("minecraft")
SERVERS_DIR = os.path.join(BASE_DIR, "servers")
JAVA_DIR = os.path.join(BASE_DIR, "java")
TOOLS_DIR = os.path.join(BASE_DIR, "tools")
CACHE_DIR = os.path.join(BASE_DIR, "cache")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")


def ensure_dirs():
    for d in (BASE_DIR, SERVERS_DIR, JAVA_DIR, TOOLS_DIR, CACHE_DIR):
        os.makedirs(d, exist_ok=True)


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _foreign_profile(path: str) -> bool:
    """別の人のユーザーフォルダ（C:\\Users\\他の人\\…）を指していたら True。
    設定（data フォルダ）を他のPCにコピーしたときに起きる。"""
    if not IS_WINDOWS or not path:
        return False
    home = os.path.normcase(os.path.abspath(os.path.expanduser("~")))
    p = os.path.normcase(os.path.abspath(path))
    users = os.path.dirname(home)
    if os.path.dirname(p) == users or p.startswith(users + os.path.sep):
        return not (p == home or p.startswith(home + os.path.sep))
    return False


# ---------- 鍵（Webhook・APIキー・トークン）を、このPCのこのユーザーでしか読めない形で保存する ----------
# Windows の DPAPI を使う。data フォルダを他の人や他のPCにコピーしても、鍵は読めない（空になる）。
SECRET_KEYS = ("discord_webhook", "cf_key")
SECRET_NESTED = (("duckdns", "token"),)
PREFIX = "dpapi:"


def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))  # 呼び出しが終わるまで消えないように、変数に持っておく
    src = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = BLOB()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(out))  # 0x1 = 確認画面を出さない
    if not ok:
        raise OSError("DPAPI に失敗しました")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def _seal(value: str) -> str:
    if not value or not IS_WINDOWS or value.startswith(PREFIX):
        return value
    import base64
    try:
        return PREFIX + base64.b64encode(_dpapi(value.encode("utf-8"), True)).decode("ascii")
    except Exception:  # noqa: BLE001  暗号化できなくても、保存はできるようにする
        return value


def _unseal(value: str) -> str:
    if not isinstance(value, str) or not value.startswith(PREFIX):
        return value
    import base64
    try:
        return _dpapi(base64.b64decode(value[len(PREFIX):]), False).decode("utf-8")
    except Exception:  # noqa: BLE001  別のPC・別のユーザーのものは読めない → 空にする
        return ""


def _map_secrets(s: dict, fn) -> dict:
    out = dict(s)
    for k in SECRET_KEYS:
        if isinstance(out.get(k), str):
            out[k] = fn(out[k])
    for parent, k in SECRET_NESTED:
        if isinstance(out.get(parent), dict) and isinstance(out[parent].get(k), str):
            out[parent] = {**out[parent], k: fn(out[parent][k])}
    return out


OWNER_FILE = os.path.join(BASE_DIR, ".owner")


def _owner_id() -> str:
    """このPC・このユーザーの目印（名前そのものは残さず、ハッシュにする）。"""
    import getpass
    import hashlib
    import platform
    try:
        who = getpass.getuser()
    except Exception:  # noqa: BLE001
        who = ""
    return hashlib.sha256(f"{platform.node()}|{who}".encode("utf-8", "replace")).hexdigest()[:16]


def data_is_foreign() -> bool:
    """data フォルダが、ほかのPC・ほかの人のものだったら True（友達に data を渡してしまった事故の検出）。
    目印の無い古い data は、このPCのものとみなす。"""
    try:
        with open(OWNER_FILE, encoding="utf-8") as f:
            return f.read().strip() != _owner_id()
    except OSError:
        return False


def adopt_data():
    """この data を、このPC用にする（目印を書きかえる）。"""
    os.makedirs(BASE_DIR, exist_ok=True)
    with open(OWNER_FILE, "w", encoding="utf-8") as f:
        f.write(_owner_id())


def _stamp_owner():
    if not os.path.exists(OWNER_FILE):
        try:
            adopt_data()
        except OSError:
            pass


FOREIGN_DATA = False


def load_settings() -> dict:
    global FOREIGN_DATA
    _stamp_owner()
    FOREIGN_DATA = data_is_foreign()
    raw = load_json(SETTINGS_FILE, {})
    s = _map_secrets(raw, _unseal)
    if FOREIGN_DATA:
        # 暗号化された鍵は、このPC・このユーザーで読めたものだけが残るので、そのまま使ってよい。
        # 暗号化されていない（平文の）鍵は、ほかの人のものかもしれないので捨てる。
        def _plain(v):
            return isinstance(v, str) and v != "" and not v.startswith(PREFIX)
        for k in SECRET_KEYS:
            if _plain(raw.get(k)):
                s[k] = ""
        for parent, key in SECRET_NESTED:
            if isinstance(raw.get(parent), dict) and _plain(raw[parent].get(key)) and isinstance(s.get(parent), dict):
                s[parent][key] = ""
        s["minecraft_dir"] = ""
    if _foreign_profile(s.get("minecraft_dir", "")):
        s["minecraft_dir"] = default_minecraft_dir()  # 他の人のPCの場所が入っていたら、このPCの場所に直す
    s.setdefault("minecraft_dir", default_minecraft_dir())
    return s


def save_settings(s: dict):
    save_json(SETTINGS_FILE, _map_secrets(s, _seal))


def default_minecraft_dir() -> str:
    if IS_WINDOWS:
        return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), ".minecraft")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/minecraft")
    return os.path.expanduser("~/.minecraft")


def safe_name(name: str) -> str:
    """フォルダ名に使えない文字を取りのぞく。"""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .")
    return name or "server"
