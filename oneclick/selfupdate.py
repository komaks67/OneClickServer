"""ソフトの自動更新（GitHub の「リリース」から、新しい版を取ってきて入れ替える）。

安全のための決まり:
- 更新を取りにいく場所は、appinfo.RELEASES_REPO の「リリース」だけ（ソフトの中に固定）
- ダウンロードしたzipは、リリースに書かれた指紋（SHA-256）と合ったときだけ使う。指紋がない版は入れない
- zipの中身が正しい形（exe と _internal/base_library.zip）か、入れ替える前に確かめる
- data フォルダ（サーバー・ワールド・設定）には、さわらない
- 入れ替えに失敗したら、元の版に自動でもどす
- サーバーが動いているあいだは、更新しない
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile

from . import appinfo, net
from .paths import DATA_DIR as BASE_DIR
from .runtime import APP_DIR, FROZEN

IS_WINDOWS = sys.platform.startswith("win")
CACHE_DIR = os.path.join(BASE_DIR, "cache")

API_LATEST = f"https://api.github.com/repos/{appinfo.RELEASES_REPO}/releases/latest"
DOWNLOAD_PREFIX = f"https://github.com/{appinfo.RELEASES_REPO}/releases/download/"
ASSET_NAME = f"{appinfo.APP_NAME}_win.zip"
UPDATE_DIR = os.path.join(CACHE_DIR, "update")
FAIL_FILE = os.path.join(BASE_DIR, "update_failed.txt")

_cache: dict = {"time": 0.0, "info": None}
_lock = threading.Lock()


class UpdateError(Exception):
    pass


def parse_version(v: str) -> tuple:
    nums = [int(x) for x in re.findall(r"\d+", str(v or ""))][:4]
    return tuple(nums + [0] * (4 - len(nums)))


def _sha256_from(text: str) -> str | None:
    m = re.search(r"\b([0-9a-fA-F]{64})\b", text or "")
    return m.group(1).lower() if m else None


def _pick_hash(assets: list[dict], asset: dict) -> str | None:
    """リリースに書かれた指紋を探す。GitHub が付ける digest と、横に置いた .sha256 ファイルの両方を見る。"""
    found = []
    d = asset.get("digest") or ""
    if d.lower().startswith("sha256:"):
        found.append(_sha256_from(d))
    side = next((a for a in assets if a.get("name") == asset["name"] + ".sha256"), None)
    if side and str(side.get("browser_download_url", "")).startswith(DOWNLOAD_PREFIX):
        try:
            found.append(_sha256_from(net.request(side["browser_download_url"], timeout=20, retries=1).decode("utf-8", "replace")))
        except net.NetError:
            pass
    found = [h for h in found if h]
    if len(set(found)) > 1:
        raise UpdateError("指紋が食いちがっているので、安全のため更新を止めました。")
    return found[0] if found else None


def check(force: bool = True) -> dict:
    """新しい版があるか調べる。"""
    info = {"current": appinfo.VERSION, "latest": None, "newer": False, "can_apply": FROZEN and IS_WINDOWS,
            "page": appinfo.RELEASES_PAGE, "notes": "", "asset_url": None, "sha256": None, "size": 0,
            "safe": False, "message": ""}
    try:
        data = net.get_json(API_LATEST, headers={"Accept": "application/vnd.github+json"}, timeout=20, retries=1)
    except net.NetError as e:
        if "404" in str(e):
            info["message"] = "まだ、公開された新しい版はありません。"
        else:
            info["message"] = "更新を調べられませんでした。インターネットにつながっているか確認してください。"
        return _remember(info)
    tag = str(data.get("tag_name") or "")
    info["latest"] = tag.lstrip("vV") or None
    info["notes"] = str(data.get("body") or "")[:3000]
    info["page"] = data.get("html_url") or appinfo.RELEASES_PAGE
    info["newer"] = bool(info["latest"]) and parse_version(info["latest"]) > parse_version(appinfo.VERSION)
    assets = data.get("assets") or []
    asset = next((a for a in assets if a.get("name") == ASSET_NAME), None)
    if asset:
        url = str(asset.get("browser_download_url") or "")
        info["asset_url"] = url if url.startswith(DOWNLOAD_PREFIX) else None
        info["size"] = int(asset.get("size") or 0)
        try:
            info["sha256"] = _pick_hash(assets, asset)
        except UpdateError as e:
            info["message"] = str(e)
    info["safe"] = bool(info["asset_url"] and info["sha256"])
    if info["newer"] and not info["safe"] and not info["message"]:
        info["message"] = "この版には、安全を確かめるための指紋がないので、自動では入れません。リリースのページから入れてください。"
    return _remember(info)


def _remember(info: dict) -> dict:
    with _lock:
        _cache["time"] = time.time()
        _cache["info"] = info
    return info


def cached() -> dict | None:
    """画面に出す、簡単な情報（通信はしない）。"""
    with _lock:
        i = _cache["info"]
    if not i:
        return None
    return {"latest": i["latest"], "newer": i["newer"], "can_apply": i["can_apply"] and i["safe"]}


def required() -> dict | None:
    """強制更新が必要なときの情報(必要なければ None)。通信はしない。

    新しい版があり、安全に自動で入れられる(指紋つき・exe で動作中)ときだけ返す。"""
    with _lock:
        i = _cache["info"]
    if i and i["newer"] and i["safe"] and i["can_apply"]:
        return {"latest": i["latest"], "notes": i["notes"], "size": i["size"]}
    return None


def last_checked() -> float:
    with _lock:
        return _cache["time"]


# ---------------- 入れ替え ----------------

def _safe_extract(zip_path: str, dest: str, log) -> str:
    """zipを展開して、exe のあるフォルダを返す。"""
    if os.path.isdir(dest):
        shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest, exist_ok=True)
    root_abs = os.path.abspath(dest)
    with zipfile.ZipFile(zip_path) as z:
        bad = z.testzip()
        if bad:
            raise UpdateError(f"zipがこわれています（{bad}）")
        for info in z.infolist():
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or ":" in name or ".." in name.split("/"):
                raise UpdateError(f"危険なパスが入っているので、中止しました: {name}")
            target = os.path.abspath(os.path.join(dest, *name.split("/")))
            if not (target == root_abs or target.startswith(root_abs + os.sep)):
                raise UpdateError(f"危険なパスが入っているので、中止しました: {name}")
        log("zipを展開しています…")
        z.extractall(dest)
    exe = f"{appinfo.APP_NAME}.exe"
    for base, _dirs, files in os.walk(dest):
        if exe in files:
            return base
    raise UpdateError("zipの中に、本体（exe）が見つかりません。")


def _verify_tree(root: str) -> None:
    if not os.path.isfile(os.path.join(root, f"{appinfo.APP_NAME}.exe")):
        raise UpdateError("本体（exe）がありません。")
    internal = os.path.join(root, "_internal")
    if not os.path.isdir(internal):
        raise UpdateError("_internal フォルダがありません。作り方がまちがっている版なので、入れません。")
    if not (os.path.isfile(os.path.join(internal, "base_library.zip")) or os.path.isdir(os.path.join(internal, "encodings"))):
        raise UpdateError("Python の部品（base_library.zip）が入っていません。この版は起動できないので、入れません。")


# 入れ替えのバッチ。日本語は入れない（cmd は UTF-8 の日本語が入ると、行やラベルを読みまちがえて、固まることがある）。
# フォルダの場所などは、バッチの中に書かず、環境変数（APP / NEW / DATA / EXE）で渡す。
SCRIPT = r"""@echo off
setlocal
set "LOG=%DATA%\update.log"
echo [%date% %time%] update start >> "%LOG%"
set /a n=0
set "RENAMED=0"
:retry
if exist "%APP%\_internal.old" rmdir /s /q "%APP%\_internal.old" 2>nul
if exist "%APP%\%EXE%.old" del /f /q "%APP%\%EXE%.old" 2>nul
if not exist "%APP%\_internal" goto skiprename
ren "%APP%\_internal" _internal.old 2>nul
if not errorlevel 1 goto renamed
set /a n+=1
if %n% GEQ 60 goto rollback
ping -n 2 127.0.0.1 >nul
goto retry
:renamed
set "RENAMED=1"
echo [%date% %time%] old app closed after %n% tries >> "%LOG%"
:skiprename
if exist "%APP%\%EXE%" copy /y "%APP%\%EXE%" "%APP%\%EXE%.old" >nul
robocopy "%NEW%" "%APP%" /E /XD "%NEW%\data" /R:15 /W:2 /NFL /NDL /NJH /NJS /NP >> "%LOG%"
if errorlevel 8 goto rollback
echo [%date% %time%] update ok >> "%LOG%"
if exist "%APP%\_internal.old" rmdir /s /q "%APP%\_internal.old"
if exist "%APP%\%EXE%.old" del /f /q "%APP%\%EXE%.old"
start "" "%APP%\%EXE%"
goto end
:rollback
echo [%date% %time%] update failed, rolling back >> "%LOG%"
if not "%RENAMED%"=="1" goto restoreexe
if exist "%APP%\_internal" rmdir /s /q "%APP%\_internal"
ren "%APP%\_internal.old" _internal
:restoreexe
if exist "%APP%\%EXE%.old" copy /y "%APP%\%EXE%.old" "%APP%\%EXE%" >nul
if exist "%APP%\%EXE%.old" del /f /q "%APP%\%EXE%.old"
echo failed > "%DATA%\update_failed.txt"
start "" "%APP%\%EXE%"
:end
exit /b 0
"""


def _script(app_dir: str = "", new_root: str = "", pid: int = 0, data_dir: str = "") -> str:
    """入れ替えのバッチ(英数字だけ)。引数は、前の版との互換のためで、使いません。"""
    return SCRIPT


def _env(app_dir: str, new_root: str, data_dir: str) -> dict:
    env = dict(os.environ)
    env.update({"APP": app_dir, "NEW": new_root, "DATA": data_dir, "EXE": f"{appinfo.APP_NAME}.exe"})
    return env


def apply(job, any_server_running, shutdown) -> dict:
    """新しい版をダウンロードして、入れ替えの用意をして、ソフトを終了する。"""
    log = job.write
    if not (FROZEN and IS_WINDOWS):
        raise UpdateError("いまは「Python」で動かしているので、自動では更新できません。リリースのページから、新しい版を入れてください。")
    if any_server_running():
        raise UpdateError("サーバーが動いています。先に「■ 停止」で止めてから、更新してください。")
    log("新しい版を調べています…")
    info = check()
    if not info["newer"]:
        raise UpdateError("すでに、いちばん新しい版です。")
    if not info["safe"]:
        raise UpdateError(info["message"] or "安全を確かめられないので、更新しません。")
    os.makedirs(UPDATE_DIR, exist_ok=True)
    zip_path = os.path.join(UPDATE_DIR, ASSET_NAME)
    last = [-1]

    def progress(p):
        step = int(p * 10)
        if step != last[0]:
            last[0] = step
            log(f"ダウンロード中… {step * 10}%")

    log(f"版 {info['latest']} をダウンロードします（{info['size'] // (1024 * 1024)}MB）")
    net.download(info["asset_url"], zip_path, sha256=info["sha256"], progress=progress, timeout=120)
    log("指紋（SHA-256）が合っていました。安全です。")
    new_root = _safe_extract(zip_path, os.path.join(UPDATE_DIR, "new"), log)
    _verify_tree(new_root)
    try:
        os.remove(zip_path)
    except OSError:
        pass
    bat = os.path.join(UPDATE_DIR, "apply_update.bat")
    with open(bat, "w", encoding="ascii", newline="\r\n") as f:
        f.write(SCRIPT)
    log("ソフトをいったん終了して、入れ替えます。数秒後に、自動で開き直します。")
    # 窓なし(CREATE_NO_WINDOW) + 新しいグループ。DETACHED_PROCESS は付けない
    # (いっしょに付けると、部品(find など)が、毎回あたらしい黒い画面を開いて、固まる)
    flags = 0x08000000 | 0x00000200
    subprocess.Popen(["cmd.exe", "/d", "/c", bat], creationflags=flags, close_fds=True, cwd=UPDATE_DIR,
                     env=_env(os.path.abspath(APP_DIR), os.path.abspath(new_root), os.path.abspath(BASE_DIR)),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    shutdown(False)
    return {"ok": True, "version": info["latest"]}


def pop_failure() -> str | None:
    """前回の更新に失敗して、元にもどしたときのお知らせ（1回だけ）。"""
    if os.path.exists(FAIL_FILE):
        try:
            os.remove(FAIL_FILE)
        except OSError:
            pass
        return "前回の更新に失敗したので、元の版にもどしました。データは無事です。もう一度ためすか、リリースのページから入れてください。"
    return None


def cleanup() -> None:
    """起動したときに、前回の更新の残りを片づける。"""
    shutil.rmtree(UPDATE_DIR, ignore_errors=True)
    if FROZEN:
        for name in ("_internal.old", f"{appinfo.APP_NAME}.exe.old"):
            p = os.path.join(APP_DIR, name)
            try:
                shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
            except OSError:
                pass
