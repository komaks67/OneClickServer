"""「困ったとき診断」：サーバーが起動しない原因になりやすいことを、まとめて調べる。

- 前のサーバー（java）が裏で残っていて、ワールドの鍵（session.lock）を持ったままになっていないか
- ポート番号が使われていないか
- ディスクの空き
- ログ（記録）から、原因を日本語で推定（conflicts.py のルールを使う）
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time

from . import conflicts
from .paths import CACHE_DIR, IS_WINDOWS

try:
    from .javamgr import NO_WINDOW
except Exception:  # noqa: BLE001
    NO_WINDOW = 0

PID_DIR = os.path.join(CACHE_DIR, "pids")


# ---------------- 起動した java の番号（PID）を覚えておく ----------------

def _pid_file(server_dir: str) -> str:
    return os.path.join(PID_DIR, os.path.basename(os.path.abspath(server_dir)) + ".json")


def remember_pid(server_dir: str, pid: int) -> None:
    try:
        os.makedirs(PID_DIR, exist_ok=True)
        with open(_pid_file(server_dir), "w", encoding="utf-8") as f:
            json.dump({"pid": int(pid), "dir": os.path.abspath(server_dir), "time": time.time()}, f)
    except OSError:
        pass


def forget_pid(server_dir: str, pid: int | None = None) -> None:
    p = _pid_file(server_dir)
    try:
        if pid is not None:
            with open(p, encoding="utf-8") as f:
                if int(json.load(f).get("pid", -1)) != int(pid):
                    return
        os.remove(p)
    except (OSError, ValueError):
        pass


def remembered_pid(server_dir: str) -> int | None:
    try:
        with open(_pid_file(server_dir), encoding="utf-8") as f:
            return int(json.load(f).get("pid"))
    except (OSError, ValueError, TypeError):
        return None


# ---------------- ワールドの鍵 ----------------

def session_lock_held(world_dir: str) -> bool:
    """ワールドの鍵（session.lock）を、ほかのプロセスが握っていれば True。
    Windows では、握られているファイルの読み取りが「別のプロセスがロックしています」で失敗する。"""
    path = os.path.join(world_dir, "session.lock")
    if not os.path.exists(path):
        return False
    try:
        with open(path, "rb") as f:
            f.read(1)
        return False
    except PermissionError:
        return True
    except OSError as e:
        return getattr(e, "winerror", None) in (32, 33)


# ---------------- 動いている java の一覧 ----------------

_SERVER_WORDS = ("nogui", "fabric-server-launch", "win_args.txt", "unix_args.txt", "server.jar", "paper", "purpur")


def _short(cmd: str, n=140) -> str:
    cmd = re.sub(r"\s+", " ", cmd or "").strip()
    return cmd if len(cmd) <= n else cmd[:n - 1] + "…"


def list_java() -> list[dict]:
    """いま動いている java のうち、マイクラのサーバーらしいもの。"""
    out: list[dict] = []
    if IS_WINDOWS:
        ps = ("Get-CimInstance Win32_Process -Filter \"Name='java.exe' OR Name='javaw.exe'\" | "
              "Select-Object ProcessId,CommandLine,CreationDate | ConvertTo-Json -Compress")
        try:
            r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=20, creationflags=NO_WINDOW)
            data = json.loads(r.stdout) if r.stdout.strip() else []
        except (OSError, ValueError, subprocess.SubprocessError):
            data = []
        if isinstance(data, dict):
            data = [data]
        for d in data:
            cmd = d.get("CommandLine") or ""
            if any(w in cmd for w in _SERVER_WORDS):
                out.append({"pid": int(d.get("ProcessId")), "cmd": cmd, "started": _ps_date(d.get("CreationDate"))})
    else:
        try:
            r = subprocess.run(["ps", "-eo", "pid=,lstart=,args="], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return out
        for line in r.stdout.splitlines():
            m = re.match(r"\s*(\d+)\s+(\w{3} \w{3}\s+\d+ [\d:]+ \d{4})\s+(.*)", line)
            if not m or "java" not in m.group(3).split(" ", 1)[0]:
                continue
            if any(w in m.group(3) for w in _SERVER_WORDS):
                out.append({"pid": int(m.group(1)), "cmd": m.group(3), "started": m.group(2)})
    return out


def _ps_date(v) -> str:
    """PowerShell の日付（/Date(1730000000000)/ など）を読みやすい文字にする。"""
    if isinstance(v, str):
        m = re.search(r"/Date\((\d+)\)/", v)
        if m:
            return time.strftime("%m/%d %H:%M", time.localtime(int(m.group(1)) / 1000))
        return v[:16]
    return ""


def stale_java(server_dir: str, tracked_pids: set[int]) -> list[dict]:
    """ソフトが管理していない（＝前から残っている）サーバー用の java を返す。
    certain=True は、このサーバーのものだと分かっているもの。"""
    mine = remembered_pid(server_dir)
    out = []
    for j in list_java():
        if j["pid"] in tracked_pids or j["pid"] == os.getpid():
            continue
        certain = (mine is not None and j["pid"] == mine) or os.path.abspath(server_dir).lower() in j["cmd"].lower()
        out.append({"pid": j["pid"], "started": j["started"], "cmd": _short(j["cmd"]), "certain": certain})
    out.sort(key=lambda x: (not x["certain"], x["pid"]))
    return out


def kill_java(pids: list[int], tracked_pids: set[int]) -> list[int]:
    """いま「サーバーらしい java」の一覧にある番号だけ、終了する（関係ないソフトは止めない）。"""
    allowed = {j["pid"] for j in list_java()} - set(tracked_pids)
    done = []
    for pid in pids:
        pid = int(pid)
        if pid not in allowed:
            continue
        try:
            if IS_WINDOWS:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                               timeout=20, creationflags=NO_WINDOW)
            else:
                os.kill(pid, 9)
            done.append(pid)
        except (OSError, subprocess.SubprocessError):
            pass
    return done


# ---------------- 診断 ----------------

def _issue(level, title, detail, fix=None, action=None):
    return conflicts._issue(level, title, detail, fix, action)


def lock_issue(stale: list[dict]) -> dict:
    if stale:
        return _issue("error", "前のサーバーが、裏で動いたままです",
                      "ワールドの鍵（session.lock）を、残っている java が持っているので、新しく起動できません。"
                      f"（残っている java: {len(stale)}個）",
                      "「残っているjavaを終了する」を押してから、もう一度起動してください。",
                      {"type": "kill_java", "pids": [s["pid"] for s in stale]})
    return _issue("error", "ワールドの鍵を、ほかのものが使っています",
                  "javaは見つかりませんでしたが、ワールドのファイル（session.lock）が使用中です。"
                  "バックアップ・ウイルス対策ソフト・OneDriveなどの同期が、ファイルを見ている可能性があります。",
                  "数十秒待ってからもう一度ためしてください。だめならPCを再起動するか、"
                  "このソフトのフォルダを「ダウンロード」や「OneDrive」の外（例: C:\\mcmh）に移してください。")


def run(prof, running: bool, tracked_pids: set[int], port_busy: bool, console_lines: list[str] | None = None) -> dict:
    """サーバー1つの診断。issues は画面にそのまま出せる形。"""
    issues: list[dict] = []
    server_dir = prof.dir
    stale = [] if running else stale_java(server_dir, tracked_pids)
    locked = (not running) and session_lock_held(prof.world_dir())
    if locked:
        issues.append(lock_issue([s for s in stale if s["certain"]] or stale))
    elif stale and any(s["certain"] for s in stale):
        issues.append(_issue("warn", "前のサーバーが、裏で動いたままかもしれません",
                             "このサーバーの java が、ソフトの外で動いています。",
                             "不要なら「残っているjavaを終了する」を押してください。",
                             {"type": "kill_java", "pids": [s["pid"] for s in stale if s["certain"]]}))
    if port_busy and not running:
        issues.append(_issue("error", "ポート番号がほかで使われています",
                             "サーバーが使うポート番号を、ほかのサーバーやソフトが使っています。",
                             "動いている別のサーバーを止めるか、「設定」でポート番号を変えてください（例: 25566）。",
                             {"type": "kill_java", "pids": [s["pid"] for s in stale]} if stale else None))
    try:
        free_gb = shutil.disk_usage(server_dir).free / 1024 ** 3
        if free_gb < 1:
            issues.append(_issue("error", f"ディスクの空きが {free_gb:.1f}GB しかありません",
                                 "空きが少ないと、ワールドの保存やバックアップに失敗します。",
                                 "いらないバックアップやファイルを消して、空きを作ってください。"))
        elif free_gb < 5:
            issues.append(_issue("warn", f"ディスクの空きが少なめです（{free_gb:.1f}GB）",
                                 "バックアップがたまると、足りなくなることがあります。",
                                 "古いバックアップを減らすか、不要なファイルを消してください。"))
    except OSError:
        pass
    low = (server_dir or "").lower()
    if IS_WINDOWS and ("\\onedrive\\" in low or "\\downloads\\" in low):
        issues.append(_issue("info", "ソフトのフォルダが「ダウンロード」か「OneDrive」の中にあります",
                             "ファイルを同期・検査するソフトがワールドに触れて、起動に失敗することがあります。",
                             "困ったときは、ソフトのフォルダを C:\\mcmh のような場所に移してください（dataフォルダごと）。"))
    log = conflicts.analyze_logs(prof, None, console_lines)
    have = {i["title"] for i in issues}
    for i in log.get("issues", []):
        if i["title"] in have or (locked and "鍵" in i["title"]):
            continue
        if "鍵" in i["title"] and not locked and not stale:
            # 前の記録に出ていただけで、いまは鍵は使われていない（直ったあと）
            i = {**i, "level": "info", "action": None,
                 "title": "前の記録に、ワールドの鍵のエラーがありました",
                 "detail": "いまは、鍵は使われていないようです（前のサーバーは、もう残っていません）。",
                 "fix": "もう一度、サーバーを起動してみてください。"}
        issues.append(i)
    order = {"error": 0, "warn": 1, "info": 2}
    issues.sort(key=lambda i: order.get(i["level"], 3))
    return {"issues": issues, "stale": stale, "locked": locked,
            "crash_report": log.get("crash_report"), "description": log.get("description")}
