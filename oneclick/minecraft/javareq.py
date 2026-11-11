"""MOD が必要とする Java のバージョンを、MOD の中身を読んで調べる。

- MOD(jar)の中の .class ファイルの「作られた Java の版」と、fabric.mod.json の「java」の条件を読む
- マイクラの標準の Java(遊ぶ人のランチャーが使う Java)より新しい Java が要る MOD を見つける
  → 遊ぶ人のPCでは、起動した直後に止まって入れなくなる原因になる
- サーバー側は、MOD が要る一番新しい Java で起動する(Java は古い部品も動かせる)
"""
from __future__ import annotations

import io
import json
import os
import re
import zipfile

_CACHE: dict = {}          # (パス, サイズ, 更新時刻) -> 必要な Java(整数) か None
MAX_CLASSES = 60           # 1つの jar で調べる .class の数(全部は読まない)


def _spread(items: list, n: int) -> list:
    if len(items) <= n:
        return items
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


def need_from_zip(z: zipfile.ZipFile) -> int | None:
    """開いた jar から、必要な Java の番号(8・17・21 など)を求める。分からなければ None。"""
    need = 0
    # 1) fabric.mod.json の depends.java(例: ">=21")
    try:
        if "fabric.mod.json" in z.namelist():
            dep = (json.loads(z.read("fabric.mod.json").decode("utf-8", "replace")).get("depends") or {}).get("java")
            for cond in (dep if isinstance(dep, list) else [dep]):
                m = re.search(r">=?\s*(\d+)", str(cond or ""))
                if m:
                    need = max(need, int(m.group(1)))
    except (ValueError, KeyError, AttributeError):
        pass
    # 2) .class の先頭(0xCAFEBABE のあと、版の番号)。META-INF の下(複数版の予備)は数えない
    classes = [n for n in z.namelist() if n.endswith(".class") and not n.startswith("META-INF/")]
    for name in _spread(sorted(classes), MAX_CLASSES):
        try:
            with z.open(name) as f:
                head = f.read(8)
        except (OSError, zipfile.BadZipFile, RuntimeError):
            continue
        if len(head) == 8 and head[:4] == b"\xca\xfe\xba\xbe":
            major = int.from_bytes(head[6:8], "big")
            if major >= 52:
                need = max(need, major - 44)
    return need if need >= 8 else None


def jar_need(path: str) -> int | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (path, st.st_size, int(st.st_mtime))
    if key in _CACHE:
        return _CACHE[key]
    try:
        with zipfile.ZipFile(path) as z:
            val = need_from_zip(z)
    except (OSError, zipfile.BadZipFile):
        val = None
    if len(_CACHE) > 2000:
        _CACHE.clear()
    _CACHE[key] = val
    return val


def scan_mods(mods_dir: str) -> list[dict]:
    """mods フォルダの jar ごとに、必要な Java。[{file, need}]"""
    out = []
    if not os.path.isdir(mods_dir):
        return out
    for fn in sorted(os.listdir(mods_dir)):
        if fn.lower().endswith(".jar"):
            out.append({"file": fn, "need": jar_need(os.path.join(mods_dir, fn))})
    return out


def standard_java(mc: str) -> int:
    """そのマイクラの標準の Java(遊ぶ人のランチャーが使うもの)。ネットは使わない。"""
    from . import loaders
    return loaders.guess_java(mc or "")


def summarize(mods_dir: str, mc: str, base: int | None = None) -> dict:
    """{"base": 標準の Java, "need": MOD が要る一番新しい Java, "high": [標準より新しい Java が要るMOD]}"""
    base = base or standard_java(mc)
    mods = scan_mods(mods_dir)
    high = [m for m in mods if m["need"] and m["need"] > base]
    need = max([base] + [m["need"] for m in mods if m["need"]])
    return {"base": base, "need": need, "high": high, "count": len(mods)}


def server_java(prof, base: int | None = None) -> int:
    """サーバーを動かす Java の番号。MOD が要る一番新しい Java(なければ、マイクラの標準)。"""
    try:
        base = base or prof.get("java_major") or standard_java(prof.get("mc", ""))
        if prof.get("loader") in (None, "vanilla"):
            return int(base)
        return int(summarize(prof.mods_dir, prof.get("mc", ""), int(base))["need"])
    except Exception:  # noqa: BLE001  調べられなくても、いつもどおり起動する
        return int(base or 21)


def _names(high: list[dict], limit=6) -> str:
    names = [re.sub(r"\.jar$", "", m["file"], flags=re.I) for m in high]
    s = "、".join(names[:limit])
    return s + (f" ほか{len(names) - limit}個" if len(names) > limit else "")


def issue(prof) -> dict | None:
    """画面に出す「注意」1件(なければ None)。conflicts の issue と同じ形。"""
    from .conflicts import _issue
    if prof.get("loader") in (None, "vanilla"):
        return None
    s = summarize(prof.mods_dir, prof.get("mc", ""), None)
    if not s["high"]:
        return None
    return _issue(
        "warn", f"Java {s['need']} が必要なMODがあります（{len(s['high'])}個）",
        f"{_names(s['high'])}。マイクラの標準は Java {s['base']} なので、遊ぶ人のPCが標準のままだと、"
        "ゲームを起動した直後に止まって、入れません。",
        "遊ぶ人には、友達用パックの「参加する」ボタンを使ってもらってください。Java が自動で用意されます。"
        "サーバーは、自動で Java " + str(s["need"]) + " で動きます。",
        None, [m["file"] for m in s["high"]])


def mods_note(prof) -> str | None:
    """MODを入れた直後に出す一言(なければ None)。"""
    i = issue(prof)
    return f"{i['title']}: {i['detail']}" if i else None


def need_for_dir(mods_dir: str, mc: str) -> dict:
    """遊ぶ人のPC側(参加パックを入れたあと)で使う。"""
    return summarize(mods_dir, mc)


__all__ = ["jar_need", "scan_mods", "summarize", "server_java", "issue", "mods_note", "need_for_dir",
           "need_from_zip", "standard_java", "io"]
