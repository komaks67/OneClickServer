"""サーバー管理の補助部品: パソコンの状態・Steam の更新確認・参加人数の読み取り。

どれも「調べられなければ None を返すだけ」で、サーバー本体の動きには口を出しません。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse

from .. import net

IS_WIN = sys.platform.startswith("win")
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
GB = 1073741824


# ---------- パソコンの状態 ----------
def memory_gb() -> tuple[float | None, float | None]:
    """(メモリ全体, いま使える分) を GB で返す。分からなければ None。"""
    try:
        import psutil  # type: ignore
        m = psutil.virtual_memory()
        return m.total / GB, m.available / GB
    except Exception:  # noqa: BLE001
        pass
    if IS_WIN:
        try:
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong),
                            ("total", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong),
                            ("a", ctypes.c_ulonglong), ("b", ctypes.c_ulonglong),
                            ("c", ctypes.c_ulonglong), ("d", ctypes.c_ulonglong),
                            ("e", ctypes.c_ulonglong)]
            m = MS()
            m.l = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.total / GB, m.avail / GB
        except Exception:  # noqa: BLE001
            return None, None
    try:
        info = {}
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                k, _, v = line.partition(":")
                info[k] = int(v.split()[0]) * 1024
        return info["MemTotal"] / GB, info.get("MemAvailable", info["MemFree"]) / GB
    except Exception:  # noqa: BLE001
        return None, None


def disk_free_gb(path) -> float | None:
    """path があるドライブの空き(GB)。まだ無いフォルダなら、あるところまでさかのぼって調べる。"""
    p = os.path.abspath(str(path))
    while p and not os.path.exists(p):
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    try:
        return shutil.disk_usage(p).free / GB
    except OSError:
        return None


def process_memory_mb(names, pid=None) -> float | None:
    """サーバーのプロセス(と、その子)が使っているメモリ(MB)。psutil が無ければ None。"""
    try:
        import psutil  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    want = {n.lower() for n in names}
    total = 0
    found = False
    try:
        procs = []
        if pid:
            try:
                root = psutil.Process(pid)
                procs = [root] + root.children(recursive=True)
            except psutil.Error:
                procs = []
        for p in psutil.process_iter(["name", "memory_info"]):
            if (p.info.get("name") or "").lower() in want and p.pid not in {x.pid for x in procs}:
                procs.append(p)
        for p in procs:
            try:
                total += p.memory_info().rss
                found = True
            except psutil.Error:
                continue
    except Exception:  # noqa: BLE001
        return None
    return total / 1048576 if found else None


# ---------- Steam の更新確認 ----------
def parse_buildid(text: str) -> str | None:
    """steamcmd の app_info_print の出力から、公開版(public)の buildid を取り出す。"""
    i = text.find('"branches"')
    if i < 0:
        return None
    m = re.search(r'"public"\s*\{[^{}]*?"buildid"\s*"(\d+)"', text[i:], re.S)
    return m.group(1) if m else None


def installed_buildid(server_dir, appid) -> str | None:
    """入っているサーバーの buildid(steamapps/appmanifest_<id>.acf から)。"""
    p = os.path.join(str(server_dir), "steamapps", f"appmanifest_{appid}.acf")
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            m = re.search(r'"buildid"\s*"(\d+)"', f.read())
        return m.group(1) if m else None
    except OSError:
        return None


def steam_latest_build(steamcmd_exe, appid, timeout=120) -> str | None:
    """SteamCMD に、いちばん新しい版の buildid をたずねる(ダウンロードはしない)。"""
    if not os.path.exists(str(steamcmd_exe)):
        return None
    cmd = [str(steamcmd_exe), "+login", "anonymous", "+app_info_update", "1",
           "+app_info_print", str(appid), "+quit"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=NOWIN)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_buildid(r.stdout.decode("utf-8", "replace"))


# ---------- Steam ワークショップ(ARK ASE の MOD) ----------
WORKSHOP_API = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"


def workshop_details(ids) -> dict:
    """{id: {"title":…, "updated": 更新した時刻(秒)}}。調べられなければ空。"""
    ids = [str(int(i)) for i in ids]
    if not ids:
        return {}
    form = {"itemcount": str(len(ids)), "format": "json"}
    for n, i in enumerate(ids):
        form[f"publishedfileids[{n}]"] = i
    try:
        raw = net.request(WORKSHOP_API, data=urllib.parse.urlencode(form).encode("ascii"), method="POST",
                          headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
        data = json.loads(raw.decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for d in (data.get("response") or {}).get("publishedfiledetails") or []:
        if str(d.get("result")) != "1":
            continue
        try:
            out[str(d["publishedfileid"])] = {"title": str(d.get("title") or d["publishedfileid"]),
                                              "updated": int(d.get("time_updated") or 0)}
        except (KeyError, ValueError, TypeError):
            continue
    return out


# ---------- 参加人数 ----------
def count_listplayers(text: str) -> int:
    """ARK の listplayers の返事から人数を数える(「0. 名前, 1234…」の行)。"""
    return len([l for l in (text or "").splitlines() if re.match(r"\s*\d+\.\s+\S", l)])


def fmt_ago(sec: float) -> str:
    sec = int(sec)
    if sec < 90:
        return "さきほど"
    if sec < 5400:
        return f"{sec // 60}分前"
    if sec < 172800:
        return f"{sec // 3600}時間前"
    return f"{sec // 86400}日前"


def now() -> float:
    return time.time()
