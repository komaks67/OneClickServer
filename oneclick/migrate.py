"""統合前の OneClickServerMC / OneClickServerARK のデータを、統合版に引き継ぐ。

方針(データを失わないために):
- 同じドライブなら「移動」(一瞬で終わる)。ちがうドライブなら「コピー」(元は消さない)
- 引き継ぐ前に、小さな設定ファイルだけは zip でバックアップしておく
- 何をしたかを記録(journal)しておき、「元に戻す」で戻せる
- 古いソフトが動いているあいだは、引き継がない
- すでに統合版にある同じ名前のものは、上書きしない(名前を変えて入れる)
"""
from __future__ import annotations

import json
import os
import shutil
import time
import zipfile
from datetime import datetime

from . import paths


class MigrateError(Exception):
    pass


# ---------------- 古いソフトを見つける ----------------

def classify(folder: str) -> str | None:
    """フォルダが、古い MC 版か ARK 版のものなら "mc" / "ark" を返す。"""
    data = os.path.join(folder, "data")
    if not os.path.isdir(data):
        return None
    settings = paths.load_json(os.path.join(data, "settings.json"), {})
    if os.path.isdir(os.path.join(data, "servers")) or os.path.exists(os.path.join(folder, "OneClickServerMC.exe")):
        return "mc"
    if os.path.exists(os.path.join(folder, "OneClickServerARK.exe")) or (
            isinstance(settings, dict) and "session_name" in settings
            and ("profiles" in settings or "admin_password" in settings)):
        return "ark"
    if isinstance(settings, dict) and ("profiles" in settings and "game" in settings):
        return "ark"
    return None


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def scan(extra_paths=None, limit=400) -> list:
    """よくある場所から、古いソフトのフォルダを探す。"""
    roots = []
    app = os.path.abspath(os.path.dirname(paths.DATA_DIR))
    roots.append(os.path.dirname(app))          # このソフトと同じ場所に並んでいるフォルダ
    roots.append(app)
    home = os.path.expanduser("~")
    for sub in ("Desktop", "Downloads", "Documents", "OneDrive/Desktop", "OneDrive/Documents"):
        roots.append(os.path.join(home, *sub.split("/")))
    found, seen, count = [], set(), 0
    cands = []
    for r in roots:
        if not os.path.isdir(r):
            continue
        cands.append(r)
        try:
            names = sorted(os.listdir(r))
        except OSError:
            continue
        for n in names:
            p = os.path.join(r, n)
            if os.path.isdir(p):
                cands.append(p)
                try:
                    for n2 in sorted(os.listdir(p))[:60]:
                        p2 = os.path.join(p, n2)
                        if os.path.isdir(p2):
                            cands.append(p2)
                except OSError:
                    pass
    for p in list(extra_paths or []) + cands:
        count += 1
        if count > limit * 10:
            break
        ap = os.path.abspath(p)
        key = os.path.normcase(ap)
        if key in seen or _same(ap, os.path.dirname(paths.DATA_DIR)):
            continue
        seen.add(key)
        kind = classify(ap)
        if kind:
            found.append({"path": ap, "kind": kind, "summary": summarize(ap, kind)})
    return found


def summarize(folder: str, kind: str) -> str:
    data = os.path.join(folder, "data")
    if kind == "mc":
        sd = os.path.join(data, "servers")
        n = len([x for x in os.listdir(sd) if os.path.isdir(os.path.join(sd, x))]) if os.path.isdir(sd) else 0
        return f"マイクラのサーバー {n} 個"
    parts = []
    if os.path.isdir(os.path.join(data, "server")):
        parts.append("ASA")
    if os.path.isdir(os.path.join(data, "server_ase")):
        parts.append("ASE")
    return "ARK のサーバー(" + "・".join(parts) + ")" if parts else "ARK の設定"


# ---------------- 古いソフトが動いていないか ----------------

def _old_running(kind: str, folder: str) -> str:
    data = os.path.join(folder, "data")
    if kind == "mc":
        info = paths.load_json(os.path.join(data, "instance.json"), {})
        if info.get("port"):
            import urllib.request
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{info['port']}/api/ping", data=b"{}", method="POST",
                                             headers={"X-Token": info.get("token", ""), "Content-Type": "application/json"})
                urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=2).read()
                return "古い OneClickServerMC が動いています。画面を閉じて、サーバーも止めてから、やり直してください。"
            except Exception:  # noqa: BLE001
                pass
    else:
        pid_file = os.path.join(data, "server.pid")
        if os.path.exists(pid_file) and os.name == "nt":
            import subprocess
            try:
                pid = open(pid_file, encoding="utf-8").read().strip()
                out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                                     text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.lower()
                if "arkascendedserver" in out or "shootergameserver" in out:
                    return "ARK のサーバーが動いています。先に停止してから、やり直してください。"
            except Exception:  # noqa: BLE001
                pass
    return ""


# ---------------- 動かす道具 ----------------

class Journal:
    def __init__(self):
        self.path = os.path.join(paths.MIGRATE_DIR, "journal.json")
        self.data = paths.load_json(self.path, {"runs": []})

    def add(self, run):
        self.data["runs"].append(run)
        paths.save_json(self.path, self.data)

    def last_undoable(self):
        for r in reversed(self.data["runs"]):
            if not r.get("undone"):
                return r
        return None

    def save(self):
        paths.save_json(self.path, self.data)


def _unique(dst: str) -> str:
    if not os.path.exists(dst):
        return dst
    n = 2
    while os.path.exists(f"{dst}_imported{n}"):
        n += 1
    return f"{dst}_imported{n}"


def _same_volume(a: str, b: str) -> bool:
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False


def transfer(src: str, dst: str, steps: list, report: list, label: str):
    """src を dst へ。同じドライブなら移動、ちがえばコピー。すでにあれば別名で入れる。"""
    if not os.path.exists(src):
        return
    dst_parent = os.path.dirname(dst)
    os.makedirs(dst_parent, exist_ok=True)
    final = _unique(dst)
    if final != dst:
        report.append(f"{label}: 同じ名前がすでにあったので、{os.path.basename(final)} として入れました。")
    if _same_volume(src, dst_parent):
        os.replace(src, final) if os.path.isfile(src) else os.rename(src, final)
        steps.append({"op": "move", "src": src, "dst": final})
        report.append(f"{label}: 移動しました。")
    else:
        if os.path.isdir(src):
            shutil.copytree(src, final)
        else:
            shutil.copy2(src, final)
        steps.append({"op": "copy", "src": src, "dst": final})
        report.append(f"{label}: コピーしました(元のデータはそのまま残してあります)。")


def safety_backup(kind: str, folder: str) -> str:
    """設定などの小さなファイルを zip に取っておく。"""
    os.makedirs(paths.MIGRATE_DIR, exist_ok=True)
    name = datetime.now().strftime(f"before_{kind}_%Y%m%d_%H%M%S.zip")
    target = os.path.join(paths.MIGRATE_DIR, name)
    data = os.path.join(folder, "data")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        if os.path.isdir(data):
            for fn in os.listdir(data):
                p = os.path.join(data, fn)
                if os.path.isfile(p) and os.path.getsize(p) < 5_000_000:
                    z.write(p, f"data/{fn}")
        if kind == "mc":
            sd = os.path.join(data, "servers")
            if os.path.isdir(sd):
                for sid in os.listdir(sd):
                    for fn in ("profile.json", "server.properties", "whitelist.json", "ops.json"):
                        p = os.path.join(sd, sid, fn)
                        if os.path.isfile(p):
                            z.write(p, f"data/servers/{sid}/{fn}")
        else:
            for sub in ("server", "server_ase"):
                cfg = os.path.join(data, sub, "ShooterGame", "Saved", "Config")
                if os.path.isdir(cfg):
                    for base, _d, files in os.walk(cfg):
                        for fn in files:
                            p = os.path.join(base, fn)
                            if os.path.getsize(p) < 5_000_000:
                                z.write(p, os.path.relpath(p, folder))
    return target


# ---------------- マイクラの引き継ぎ ----------------

def import_mc(folder: str) -> dict:
    data = os.path.join(folder, "data")
    dest = paths.game_dir("minecraft")
    steps, report = [], []
    backup = safety_backup("mc", folder)
    os.makedirs(dest, exist_ok=True)
    # servers は 1 つずつ入れる(統合版で先に作ったサーバーと名前がぶつかっても、消さない)
    sd = os.path.join(data, "servers")
    if os.path.isdir(sd):
        for sid in sorted(os.listdir(sd)):
            transfer(os.path.join(sd, sid), os.path.join(dest, "servers", sid), steps, report, f"サーバー「{sid}」")
    for name, label in (("java", "Java"), ("tools", "道具(playit など)"), ("settings.json", "設定"),
                        ("cache", "ダウンロードの控え")):
        src = os.path.join(data, name)
        if os.path.exists(src) and not os.path.exists(os.path.join(dest, name)):
            transfer(src, os.path.join(dest, name), steps, report, label)
        elif os.path.exists(src):
            report.append(f"{label}: 統合版にすでにあるので、そのままにしました。")
    return {"kind": "mc", "from": folder, "steps": steps, "report": report, "backup": backup,
            "time": time.time()}


# ---------------- ARK の引き継ぎ ----------------

ARK_PROFILE_KEYS = ("map", "port", "query_port", "rcon_port", "mods")
ARK_COMMON_KEYS = ("extra_args", "auto_backup", "backup_minutes", "backup_keep", "auto_restart", "restart_times",
                   "restart_on_crash", "discord_webhook", "discord_events")
ARK_SHARED_KEYS = ("session_name", "server_password", "admin_password", "max_players", "battleye")


def convert_ark_settings(old: dict, game: str) -> dict:
    """古い ARK 版の settings.json から、統合版の ARK(asa / ase)1つ分の設定を作る。"""
    active = old.get("game", "asa")
    out = {}
    for k in ARK_COMMON_KEYS + ARK_SHARED_KEYS:
        if k in old:
            out[k] = old[k]
    prof = {k: old[k] for k in ARK_PROFILE_KEYS if k in old} if game == active else dict(
        (old.get("profiles") or {}).get(game) or {})
    out.update({k: v for k, v in prof.items() if k in ARK_PROFILE_KEYS})
    return out


def import_ark(folder: str) -> dict:
    data = os.path.join(folder, "data")
    steps, report = [], []
    backup = safety_backup("ark", folder)
    old = paths.load_json(os.path.join(data, "settings.json"), {})
    if not isinstance(old, dict):
        old = {}
    games = []
    for game, key, sdir, bdir in (("asa", "ark_asa", "server", "backups"), ("ase", "ark_ase", "server_ase", "backups_ase")):
        if os.path.isdir(os.path.join(data, sdir)) or old.get("game", "asa") == game or game in (old.get("profiles") or {}):
            games.append((game, key, sdir, bdir))
    for game, key, sdir, bdir in games:
        root = paths.game_dir(key)
        os.makedirs(root, exist_ok=True)
        sfile = os.path.join(root, "settings.json")
        if os.path.exists(sfile):
            report.append(f"{key}: 統合版にすでに設定があるので、設定は引き継ぎませんでした。")
        else:
            paths.save_json(sfile, convert_ark_settings(old, game))
            steps.append({"op": "write", "dst": sfile})
            report.append(f"{key}: 設定を引き継ぎました。")
        transfer(os.path.join(data, sdir), os.path.join(root, "server"), steps, report, f"{key} のサーバー本体")
        transfer(os.path.join(data, bdir), os.path.join(root, "backups"), steps, report, f"{key} のバックアップ")
    shared = paths.SHARED_DIR
    for name, label in (("steamcmd", "SteamCMD"), ("tools", "道具(playit)")):
        src = os.path.join(data, name)
        if os.path.exists(src) and not os.path.exists(os.path.join(shared, name)):
            transfer(src, os.path.join(shared, name), steps, report, label)
    return {"kind": "ark", "from": folder, "steps": steps, "report": report, "backup": backup, "time": time.time()}


def run(folder: str) -> dict:
    """古いソフトのフォルダを、統合版に引き継ぐ。"""
    folder = os.path.abspath(folder)
    kind = classify(folder)
    if not kind:
        raise MigrateError("このフォルダは、OneClickServerMC / OneClickServerARK のものではないようです(data フォルダが見つかりません)。")
    busy = _old_running(kind, folder)
    if busy:
        raise MigrateError(busy)
    paths.ensure_dirs()
    result = import_mc(folder) if kind == "mc" else import_ark(folder)
    j = Journal()
    j.add(result)
    return result


def undo_last() -> dict:
    j = Journal()
    run_ = j.last_undoable()
    if not run_:
        raise MigrateError("元に戻せる引き継ぎはありません。")
    report = []
    for st in reversed(run_["steps"]):
        op, dst, src = st["op"], st["dst"], st.get("src")
        try:
            if op == "move":
                if os.path.exists(dst) and not os.path.exists(src):
                    os.makedirs(os.path.dirname(src), exist_ok=True)
                    os.rename(dst, src)
                    report.append(f"戻しました: {os.path.basename(src)}")
                else:
                    report.append(f"戻せませんでした(場所が変わっています): {os.path.basename(src)}")
            elif op == "copy":
                if os.path.isdir(dst):
                    shutil.rmtree(dst)
                elif os.path.exists(dst):
                    os.remove(dst)
                report.append(f"コピーを消しました: {os.path.basename(dst)}")
            elif op == "write" and os.path.exists(dst):
                os.remove(dst)
                report.append(f"書いた設定を消しました: {os.path.basename(os.path.dirname(dst))}")
        except OSError as e:
            report.append(f"失敗しました({os.path.basename(dst)}): {e}")
    run_["undone"] = True
    j.save()
    return {"report": report}


def last_report() -> dict | None:
    r = Journal().last_undoable()
    if not r:
        return None
    return {"kind": r["kind"], "from": r["from"], "report": r["report"]}
