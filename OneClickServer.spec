# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 設定。build.bat から使います（フォルダ形式 = 誤検知が少なく起動も速い）
import os
from PyInstaller.utils.hooks import collect_submodules

hidden = collect_submodules("oneclick")
extra_datas, extra_bins = [], []
try:  # 専用の窓（pywebview）。入っていなければ Edge の窓で動く exe になる
    from PyInstaller.utils.hooks import collect_all
    for pkg in ("webview", "clr_loader", "pythonnet"):
        d, b, h = collect_all(pkg)
        extra_datas += d; extra_bins += b; hidden += h
except Exception as e:
    print("pywebview なしで作ります:", e)

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=extra_bins,
    datas=[("oneclick/web", "oneclick/web"),
           ("oneclick/minecraft/web", "oneclick/minecraft/web")] + extra_datas,
    hiddenimports=hidden + ["winreg"],
    excludes=["tkinter", "unittest", "pydoc_data", "test"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="OneClickServer",
    console=bool(os.environ.get("OCS_CONSOLE")),   # 通常は黒い画面を出さない（調査用ビルドだけ出す）
    upx=False,              # UPX は誤検知が増えるので使わない
)
coll = COLLECT(exe, a.binaries, a.datas, name="OneClickServer", upx=False)
