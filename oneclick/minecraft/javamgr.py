"""Java（マイクラを動かすのに必要なソフト）を探す・なければ自動で用意する。"""
from __future__ import annotations

import glob
import os
import platform
import re
import shutil
import subprocess
import tarfile
import zipfile

from . import net
from .paths import IS_WINDOWS, JAVA_DIR

NO_WINDOW = 0x08000000 if IS_WINDOWS else 0
JAVA_EXE = "java.exe" if IS_WINDOWS else "java"


def java_major(java_path: str) -> int | None:
    """java -version を実行して、メジャー番号（8, 17, 21 など）を返す。"""
    try:
        r = subprocess.run([java_path, "-version"], capture_output=True, text=True,
                           errors="replace", timeout=15, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r'version "(\d+)(?:\.(\d+))?', (r.stderr or "") + (r.stdout or ""))
    if not m:
        return None
    major = int(m.group(1))
    if major == 1 and m.group(2):  # 1.8.0 → 8
        major = int(m.group(2))
    return major


def _candidates():
    # 1) このソフトが用意した Java
    for p in glob.glob(os.path.join(JAVA_DIR, "*", "**", "bin", JAVA_EXE), recursive=True):
        yield p
    # 2) JAVA_HOME
    jh = os.environ.get("JAVA_HOME")
    if jh:
        yield os.path.join(jh, "bin", JAVA_EXE)
    # 3) PATH の java
    w = shutil.which("java")
    if w:
        yield w
    # 4) Windows のよくある場所
    if IS_WINDOWS:
        for root in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            for vendor in ("Java", "Eclipse Adoptium", "Microsoft", "Zulu", "BellSoft", "Amazon Corretto"):
                for p in glob.glob(os.path.join(root, vendor, "*", "bin", JAVA_EXE)):
                    yield p


def find_java(required: int) -> str | None:
    """必要なバージョンの Java を探す。見つからなければ None。"""
    checked = set()
    for p in _candidates():
        p = os.path.abspath(p)
        if p in checked or not os.path.isfile(p):
            continue
        checked.add(p)
        if java_major(p) == required:
            return p
    return None


def _arch():
    m = platform.machine().lower()
    if m in ("arm64", "aarch64"):
        return "aarch64"
    return "x64"


def _os():
    if IS_WINDOWS:
        return "windows"
    if platform.system() == "Darwin":
        return "mac"
    return "linux"


def install_java(required: int, log=print) -> str:
    """Eclipse Temurin（無料の Java）をダウンロードして data/java に入れる。"""
    dest_root = os.path.join(JAVA_DIR, str(required))
    last_err = None
    for image in ("jre", "jdk"):
        try:
            assets = net.get_json(
                f"https://api.adoptium.net/v3/assets/latest/{required}/hotspot",
                {"architecture": _arch(), "image_type": image, "os": _os(), "vendor": "eclipse"})
        except net.NetError as e:
            last_err = e
            continue
        if not assets:
            continue
        pkg = assets[0]["binary"]["package"]
        log(f"Java {required} をダウンロード中…（{pkg['name']}）")
        archive = os.path.join(JAVA_DIR, pkg["name"])
        net.download(pkg["link"], archive, sha256=pkg.get("checksum"))
        if os.path.isdir(dest_root):
            shutil.rmtree(dest_root, ignore_errors=True)
        os.makedirs(dest_root, exist_ok=True)
        log("Java を展開中…")
        if archive.endswith(".zip"):
            with zipfile.ZipFile(archive) as z:
                _safe_extract_zip(z, dest_root)
        else:
            with tarfile.open(archive) as t:
                t.extractall(dest_root, filter="data") if hasattr(tarfile, "data_filter") else t.extractall(dest_root)
        os.remove(archive)
        found = glob.glob(os.path.join(dest_root, "**", "bin", JAVA_EXE), recursive=True)
        if found:
            if not IS_WINDOWS:
                os.chmod(found[0], 0o755)
            log(f"Java {required} の準備ができました。")
            return found[0]
    raise net.NetError(f"Java {required} を用意できませんでした（{last_err or '配布ファイルなし'}）")


def ensure_java(required: int, log=print) -> str:
    return find_java(required) or install_java(required, log)


def _safe_extract_zip(z: zipfile.ZipFile, dest: str):
    """zip の中に「フォルダの外へ書き込む」ような危ない名前があれば止める。"""
    dest_abs = os.path.abspath(dest)
    for info in z.infolist():
        target = os.path.abspath(os.path.join(dest, info.filename))
        if not (target == dest_abs or target.startswith(dest_abs + os.sep)):
            raise ValueError(f"危険なファイル名を見つけたので中止しました: {info.filename}")
    z.extractall(dest)


safe_extract_zip = _safe_extract_zip
