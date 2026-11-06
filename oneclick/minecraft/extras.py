"""ワールドの点検・サーバーの引っ越し（書き出し／読みこみ）・ひな形。"""
from __future__ import annotations

import gzip
import json
import os
import re
import struct
import zlib
import zipfile


# ---------------------------------------------------------------- ワールドの点検
_COMP_OK = {1, 2, 3, 4, 127}


def _check_chunk(blob: bytes) -> str | None:
    """チャンクの中身（長さ4バイト + 圧縮の種類1バイト + データ）が読めるか。"""
    if len(blob) < 5:
        return "短すぎる"
    n = struct.unpack(">I", blob[:4])[0]
    c = blob[4]
    if n < 1 or n - 1 > len(blob) - 5 + 4096 * 1024:
        return "長さがおかしい"
    if c > 128:
        return None  # 外のファイル(.mcc)に入っているものです。ここでは調べない
    if c not in _COMP_OK:
        return f"圧縮の種類がおかしい({c})"
    data = blob[5:4 + n]
    try:
        if c == 2:
            zlib.decompressobj().decompress(data, 1 << 20)
        elif c == 1:
            gzip.GzipFile(fileobj=__import__("io").BytesIO(data)).read(1 << 20)
    except Exception:  # noqa: BLE001
        return "中身が読めない（こわれている）"
    return None


def scan_region_file(path: str, sample: int = 6) -> list[str]:
    """1つの .mca ファイルを調べる。問題の説明を返す（空なら問題なし）。"""
    problems: list[str] = []
    size = os.path.getsize(path)
    if size == 0:
        return problems
    if size < 8192:
        return ["ファイルが小さすぎる（途中で切れている）"]
    with open(path, "rb") as f:
        head = f.read(8192)
        locs = struct.unpack(">1024I", head[:4096])
        used = [(i, (v >> 8), v & 0xFF) for i, v in enumerate(locs) if v]
        bad = 0
        seen: dict[int, int] = {}
        for i, off, cnt in used:
            if off < 2 or cnt == 0 or (off + cnt) * 4096 > size + 4095:
                bad += 1
                continue
            for s in range(off, off + cnt):
                if s in seen:
                    bad += 1
                    break
                seen[s] = i
        if bad:
            problems.append(f"場所の表がおかしいチャンクが {bad} 個")
        step = max(1, len(used) // sample)
        broken = 0
        for i, off, cnt in used[::step][:sample]:
            if off < 2 or cnt == 0 or (off + cnt) * 4096 > size + 4095:
                continue
            f.seek(off * 4096)
            blob = f.read(min(cnt * 4096, 4096 * 64))
            if _check_chunk(blob):
                broken += 1
        if broken:
            problems.append(f"中身が読めないチャンクがあった（調べた {min(sample, len(used))} 個のうち {broken} 個）")
    return problems


def scan_world(world_dir: str, log=lambda s: None) -> dict:
    out = {"files": 0, "problems": [], "world": os.path.basename(world_dir)}
    if not os.path.isdir(world_dir):
        out["problems"].append({"file": "ワールド", "msg": "ワールドのフォルダがまだありません（一度も起動していない）"})
        return out
    lv = os.path.join(world_dir, "level.dat")
    if os.path.exists(lv):
        try:
            with gzip.open(lv, "rb") as f:
                f.read(1 << 20)
        except Exception:  # noqa: BLE001
            out["problems"].append({"file": "level.dat", "msg": "ワールドの基本データが読めません（こわれている）。level.dat_old があれば、それに入れかえると直ることがあります"})
    else:
        out["problems"].append({"file": "level.dat", "msg": "ワールドの基本データがありません"})
    roots = [world_dir]
    parent = os.path.dirname(world_dir)
    base = os.path.basename(world_dir)
    for extra in (f"{base}_nether", f"{base}_the_end"):
        d = os.path.join(parent, extra)
        if os.path.isdir(d):
            roots.append(d)
    files = []
    for root in roots:
        for dp, _dn, fn in os.walk(root):
            if os.path.basename(dp) == "region":
                files += [os.path.join(dp, x) for x in fn if x.endswith(".mca")]
    for i, path in enumerate(files):
        out["files"] += 1
        try:
            for msg in scan_region_file(path):
                out["problems"].append({"file": os.path.relpath(path, parent), "msg": msg})
        except Exception as e:  # noqa: BLE001
            out["problems"].append({"file": os.path.relpath(path, parent), "msg": f"調べられなかった（{e}）"})
        if i % 20 == 0:
            log(f"調べています… {i + 1}/{len(files)}")
    return out


# ---------------------------------------------------------------- サーバーの引っ越し
SKIP_DIRS = {"backups", "logs", "crash-reports", "cache", ".mixin.out", "debug"}
SKIP_FILES = {"session.lock"}
SECRET_KEY = re.compile(r"secret|token|password|webhook|api_?key|ddns|playit", re.I)
SECRET_PROPS = ("rcon.password", "management-server-secret")


def _clean_profile(raw: bytes) -> bytes:
    try:
        d = json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return raw
    for k in list(d):
        if SECRET_KEY.search(k) or k in ("public_address",):
            d.pop(k, None)
    return json.dumps(d, ensure_ascii=False, indent=1).encode("utf-8")


def _clean_props(raw: bytes) -> bytes:
    lines = []
    for ln in raw.decode("utf-8", "replace").splitlines():
        k = ln.split("=", 1)[0].strip()
        lines.append(f"{k}=" if k in SECRET_PROPS else ln)
    return ("\n".join(lines) + "\n").encode("utf-8")


def export_server(server_dir: str, out_zip: str, log=lambda s: None) -> dict:
    total = 0
    files = []
    for dp, dn, fn in os.walk(server_dir):
        dn[:] = [d for d in dn if not (dp == server_dir and d in SKIP_DIRS)]
        for x in fn:
            if x in SKIP_FILES or x.endswith(".tmp"):
                continue
            p = os.path.join(dp, x)
            files.append(p)
            try:
                total += os.path.getsize(p)
            except OSError:
                pass
    log(f"{len(files)} 個のファイル（約 {total // (1024 * 1024)} MB）をまとめています…")
    root = os.path.basename(server_dir)
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        for i, p in enumerate(files):
            rel = os.path.relpath(p, server_dir).replace(os.sep, "/")
            try:
                if rel == "mcmh.json":
                    with open(p, "rb") as f:
                        z.writestr(rel, _clean_profile(f.read()))
                elif rel == "server.properties":
                    with open(p, "rb") as f:
                        z.writestr(rel, _clean_props(f.read()))
                else:
                    z.write(p, rel)
            except OSError:
                continue  # 使われていて読めないファイルは飛ばす
            if i % 200 == 0:
                log(f"まとめています… {i + 1}/{len(files)}")
    return {"files": len(files), "mb": total // (1024 * 1024), "name": root}


def inspect_export(zip_path: str) -> dict:
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        if "mcmh.json" not in names:
            raise ValueError("このファイルは、このソフトの「引っ越しファイル」ではありません。")
        for n in names:
            if n.startswith(("/", "\\")) or ".." in n.replace("\\", "/").split("/") or ":" in n.split("/")[0]:
                raise ValueError("ファイルの中に、あぶない場所の指定があったので、やめました。")
        d = json.loads(z.read("mcmh.json").decode("utf-8"))
    return {"name": d.get("name") or "サーバー", "mc": d.get("mc"), "loader": d.get("loader")}


def import_server(zip_path: str, dest_dir: str, log=lambda s: None) -> None:
    os.makedirs(dest_dir)
    base = os.path.realpath(dest_dir)
    with zipfile.ZipFile(zip_path) as z:
        members = z.namelist()
        for i, n in enumerate(members):
            target = os.path.realpath(os.path.join(dest_dir, n))
            if target != base and not target.startswith(base + os.sep):
                raise ValueError("あぶない場所のファイルがあったので、やめました。")
            z.extract(n, dest_dir)
            if i % 200 == 0:
                log(f"ひらいています… {i + 1}/{len(members)}")


# ---------------------------------------------------------------- ひな形
TEMPLATES = [
    {"id": "survival", "title": "普通のサバイバル", "desc": "友達とのんびり冒険です。敵は出ます。PvPはありません。",
     "props": {"gamemode": "survival", "difficulty": "normal", "pvp": "false", "spawn-monsters": "true", "force-gamemode": "false"},
     "mods": "light"},
    {"id": "build", "title": "みんなで建築", "desc": "クリエイティブで建てまくります。敵は出ません。",
     "props": {"gamemode": "creative", "difficulty": "peaceful", "pvp": "false", "spawn-monsters": "false", "force-gamemode": "true"},
     "mods": "build"},
    {"id": "adventure", "title": "冒険・ハード", "desc": "手ごたえのある冒険です。ダンジョンや新しい世界づくりが得意なMODと相性がいいです。",
     "props": {"gamemode": "survival", "difficulty": "hard", "pvp": "false", "spawn-monsters": "true", "force-gamemode": "false"},
     "mods": "adventure"},
    {"id": "pvp", "title": "対戦・ミニゲーム", "desc": "PvPができます。入った人は全員アドベンチャー（勝手に壊せない）。",
     "props": {"gamemode": "adventure", "difficulty": "normal", "pvp": "true", "spawn-monsters": "false", "force-gamemode": "true"},
     "mods": "light"},
    {"id": "relax", "title": "のんびり・はじめて向け", "desc": "やさしい設定です。はじめての友達と遊ぶときです。",
     "props": {"gamemode": "survival", "difficulty": "easy", "pvp": "false", "spawn-monsters": "true", "force-gamemode": "false"},
     "mods": "easy"},
]
