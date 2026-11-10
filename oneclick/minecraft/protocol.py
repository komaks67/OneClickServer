"""招待リンク（mcmh://join/アドレス）を、このソフトで開けるようにする（Windows）。

友達のPCにもこのソフトがあれば、招待リンクをクリックするだけで「参加」の画面が開き、
サーバーから友達用パックを自動でダウンロードして入れられる。
"""
from __future__ import annotations

import os
import re
import sys
import urllib.parse

SCHEME = "mcmh"
ADDR_RE = re.compile(r"^[A-Za-z0-9.\-\[\]:]{3,255}$")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def link_for(address: str) -> str:
    return f"{SCHEME}://join/{address}"


def parse(url: str) -> str | None:
    """mcmh://join/host:port → host:port（おかしな形なら None）。"""
    if not url:
        return None
    u = url.strip().strip('"')
    if not u.lower().startswith(SCHEME + "://"):
        return None
    rest = u[len(SCHEME) + 3:]
    if rest.lower().startswith("join/"):
        addr = rest[5:]
    elif rest.lower().startswith("join?"):
        addr = urllib.parse.parse_qs(rest[5:]).get("addr", [""])[0]
    else:
        return None
    addr = urllib.parse.unquote(addr).strip().rstrip("/")
    return addr if ADDR_RE.match(addr) else None


def register() -> bool:
    """HKEY_CURRENT_USER に登録する（管理者の権限はいらない）。Windows 以外では何もしない。"""
    if not sys.platform.startswith("win"):
        return False
    import winreg
    from . import runtime
    cmd = runtime.launch_command('--join "%1"' if runtime.FROZEN else '-Join "%1"')
    base = rf"Software\Classes\{SCHEME}"
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base) as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "URL:OneClickServerMC 招待リンク")
            winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\shell\open\command") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, cmd)
        return True
    except OSError:
        return False
