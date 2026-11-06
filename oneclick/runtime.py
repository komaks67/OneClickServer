"""exe(PyInstaller)で動いているか、普通の Python で動いているかを見分ける。

- APP_DIR : data フォルダを置く場所(exe のとなり / ソフトのフォルダ)
- RES_DIR : 画面のファイル(web)がある場所
"""
from __future__ import annotations

import os
import sys

FROZEN = bool(getattr(sys, "frozen", False))

if FROZEN:
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
    RES_DIR = getattr(sys, "_MEIPASS", APP_DIR)
else:
    # このファイルは <ソフトのフォルダ>/oneclick/runtime.py
    APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    RES_DIR = APP_DIR


def launch_command(extra: str = "") -> str:
    """Windows の登録(自動起動 / 招待リンク)に使う、このソフトを起動するコマンド。"""
    if FROZEN:
        base = f'"{os.path.abspath(sys.executable)}"'
    else:
        exe = sys.executable
        w = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(w):
            exe = w
        base = f'"{exe}" "{os.path.join(APP_DIR, "main.py")}"'
    return f"{base} {extra}".rstrip()
