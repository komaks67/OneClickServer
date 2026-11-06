"""PC（Windows）にログインしたときに、このソフトを自動で起動する設定。

Windows の「スタートアップ」（レジストリの Run）に登録する。管理者の権限はいらない。
起動したときは画面を出さずに、「PC起動時に自動で起動」をONにしたサーバーだけを動かす。
"""
from __future__ import annotations

import os
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "OneClickServer"
OLD_NAME2 = "OneClickServerMC"  # 統合前の名前(残っていたら片付ける)
OLD_NAME = "MCMultiHelper"  # 前の名前（残っていたら片付ける）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def command() -> str:
    from . import runtime
    return runtime.launch_command("--autostart")


def supported() -> bool:
    return sys.platform.startswith("win")


def is_enabled() -> bool:
    if not supported():
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            val, _ = winreg.QueryValueEx(k, NAME)
            return bool(val)
    except OSError:
        return False


def set_enabled(on: bool):
    if not supported():
        raise RuntimeError("PC起動時の自動起動は Windows だけで使えます")
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        for old in (OLD_NAME, OLD_NAME2):
            try:
                winreg.DeleteValue(k, old)
            except OSError:
                pass
        if on:
            winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, command())
        else:
            try:
                winreg.DeleteValue(k, NAME)
            except FileNotFoundError:
                pass
