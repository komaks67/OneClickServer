"""リリース用のzipと、指紋（SHA-256）のファイルを作る。

使い方: make_release.bat をダブルクリック（先に build.bat で exe を作っておく）
できるもの: release\\OneClickServer_win.zip と release\\OneClickServer_win.zip.sha256
"""
import hashlib
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from oneclick import appinfo  # noqa: E402

SRC = os.path.join(ROOT, "dist", appinfo.APP_NAME)
OUT_DIR = os.path.join(ROOT, "release")
ZIP = os.path.join(OUT_DIR, f"{appinfo.APP_NAME}_win.zip")


def fail(msg):
    print("\n[失敗] " + msg)
    sys.exit(1)


def main():
    exe = os.path.join(SRC, appinfo.APP_NAME + ".exe")
    internal = os.path.join(SRC, "_internal")
    if not os.path.isfile(exe):
        fail(f"{exe} がありません。先に build.bat を実行してください。")
    if not (os.path.isfile(os.path.join(internal, "base_library.zip")) or os.path.isdir(os.path.join(internal, "encodings"))):
        fail("_internal の中に base_library.zip がありません。Python 3.13（3.12 / 3.11）で、build.bat を作り直してください。")
    os.makedirs(OUT_DIR, exist_ok=True)
    if os.path.exists(ZIP):
        os.remove(ZIP)
    n = 0
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for base, dirs, files in os.walk(SRC):
            rel_base = os.path.relpath(base, SRC)
            if rel_base == ".":
                dirs[:] = [d for d in dirs if d.lower() != "data"]   # 設定・鍵・ワールドは入れない
            for fn in files:
                if rel_base == "." and fn.lower() in ("error.log", "update.log"):
                    continue
                full = os.path.join(base, fn)
                arc = os.path.join(appinfo.APP_NAME, os.path.relpath(full, SRC)).replace(os.sep, "/")
                z.write(full, arc)
                n += 1
    h = hashlib.sha256()
    with open(ZIP, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    digest = h.hexdigest()
    with open(ZIP + ".sha256", "w", encoding="utf-8") as f:
        f.write(f"{digest}  {os.path.basename(ZIP)}\n")
    mb = os.path.getsize(ZIP) / 1024 / 1024
    print(f"\n作りました（{n}ファイル / {mb:.1f}MB）")
    print(f"  {ZIP}")
    print(f"  {ZIP}.sha256")
    print(f"  SHA-256: {digest}\n")
    ver = appinfo.VERSION
    print("次の手順で、GitHub に公開してください:")
    print(f"  1. https://github.com/{appinfo.RELEASES_REPO}/releases/new を開く")
    print(f"  2. 「Choose a tag」に v{ver} と入力して「Create new tag」を選ぶ")
    print(f"  3. タイトルに v{ver} 、説明に、変わったことを書く（友達に見えます）")
    print("  4. 上の2つのファイル（zip と .sha256）を、両方ともドラッグして追加する")
    print("  5. 「Publish release」を押す")
    print("\n※ 版の数字は oneclick\\appinfo.py の VERSION です。前より大きくしないと、更新として扱われません。")


if __name__ == "__main__":
    main()
