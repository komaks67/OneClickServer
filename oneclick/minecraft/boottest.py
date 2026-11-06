"""起動テスト：本物のワールドに触らずに「ちゃんと起動するか」を試す。

マイクラのサーバーには「--universe（ワールドを置く場所）」という起動のしかたがあるので、
テスト用の空のフォルダにワールドを作らせて、起動できたらすぐ止めて消す。

起動しなかったときは「原因のMODを自動でさがす」もできる：
MODを半分ずつOFFにして起動を試し、原因を1つにしぼっていく（二分探索）。
OFFにしたMODは別のフォルダに一時的に移すだけで、終わったら（失敗・中止でも）必ず元にもどす。
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
import time

from . import conflicts, javamgr, loaders
from .javamgr import NO_WINDOW
from .paths import load_json, save_json
from .server import DONE_RE

TEST_DIR = "_mcmh_test_world"
HOLD_DIR = "_mcmh_mods_hold"
JOURNAL = "_mcmh_hold.json"


class Cancelled(Exception):
    pass


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def run_once(prof, log=print, cancel: threading.Event | None = None, timeout=900) -> dict:
    """1回だけ起動を試す。"""
    cancel = cancel or threading.Event()
    need = prof.get("java_major") or loaders.guess_java(prof.get("mc", ""))
    java = javamgr.ensure_java(need, log)
    test_dir = os.path.join(prof.dir, TEST_DIR)
    shutil.rmtree(test_dir, ignore_errors=True)
    os.makedirs(test_dir, exist_ok=True)
    cmd = loaders.start_command(java, prof.get("start"), int(prof.get("memory_mb", 4096)), prof.dir)
    cmd += ["--universe", TEST_DIR, "--world", "test", "--port", str(_free_port())]
    t0 = time.time()
    lines: list[str] = []
    ok = False
    reason = None
    proc = subprocess.Popen(cmd, cwd=prof.dir, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1,
                            creationflags=NO_WINDOW)

    def watchdog():
        while proc.poll() is None:
            if cancel.is_set() or time.time() - t0 > timeout:
                proc.kill()
                return
            time.sleep(0.5)
    threading.Thread(target=watchdog, daemon=True).start()
    last_note = time.time()
    try:
        for line in proc.stdout:
            line = line.rstrip("\r\n")
            lines.append(line)
            if len(lines) > 3000:
                del lines[:1000]
            if DONE_RE.search(line):
                ok = True
                log(f"起動できました（{int(time.time() - t0)}秒）。テストを終わります…")
                try:
                    proc.stdin.write("stop\n")
                    proc.stdin.flush()
                except OSError:
                    pass
            elif time.time() - last_note > 15:
                last_note = time.time()
                log(f"起動中… {int(time.time() - t0)}秒")
        try:
            proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            proc.kill()
    finally:
        if proc.poll() is None:
            proc.kill()
        shutil.rmtree(test_dir, ignore_errors=True)
    if cancel.is_set():
        raise Cancelled()
    seconds = int(time.time() - t0)
    if not ok and seconds >= timeout:
        reason = f"{timeout // 60}分たっても起動が終わりませんでした"
    issues = [] if ok else conflicts.analyze_text("\n".join(lines))
    return {"ok": ok, "seconds": seconds, "issues": issues, "reason": reason, "tail": lines[-40:]}


# ---------------- MODを一時的にどける（必ず元にもどす） ----------------

def _hold(prof, files: set[str]):
    mods = prof.mods_dir
    hold = os.path.join(prof.dir, HOLD_DIR)
    os.makedirs(hold, exist_ok=True)
    journal = os.path.join(prof.dir, JOURNAL)
    moved = load_json(journal, [])
    for f in sorted(files):
        src = os.path.join(mods, f)
        if os.path.exists(src):
            os.replace(src, os.path.join(hold, f))
            moved.append(f)
            save_json(journal, moved)  # 1つ動かすたびに記録（途中で落ちてももどせる）


def restore(prof):
    """どけておいたMODを全部 mods にもどす。ソフトの起動時にも呼ぶ。"""
    hold = os.path.join(prof.dir, HOLD_DIR)
    journal = os.path.join(prof.dir, JOURNAL)
    if os.path.isdir(hold):
        for f in os.listdir(hold):
            dst = os.path.join(prof.mods_dir, f)
            if not os.path.exists(dst):
                os.replace(os.path.join(hold, f), dst)
        try:
            os.rmdir(hold)
        except OSError:
            pass
    if os.path.exists(journal):
        os.remove(journal)
    shutil.rmtree(os.path.join(prof.dir, TEST_DIR), ignore_errors=True)


# ---------------- 原因のMODをさがす ----------------

def _graph(prof):
    """MODファイルごとの「入っているID」と「必要なID」を調べる。"""
    files = sorted(f for f in os.listdir(prof.mods_dir) if f.endswith(".jar"))
    metas = {f: conflicts.read_jar(os.path.join(prof.mods_dir, f)) for f in files}
    provider: dict[str, str] = {}
    for f, m in metas.items():
        for i in m["ids"]:
            provider.setdefault(i, f)
    requires = {f: {provider[d] for d in m["depends"] if d in provider and provider[d] != f} for f, m in metas.items()}
    return files, metas, requires


def _with_dependents(group: set[str], requires: dict[str, set[str]]) -> set[str]:
    """group をOFFにするなら、group を必要としているMODも一緒にOFFにする（前提MODがなくて止まるのを防ぐ）。"""
    out = set(group)
    changed = True
    while changed:
        changed = False
        for f, req in requires.items():
            if f not in out and req & out:
                out.add(f)
                changed = True
    return out


def find_culprit(prof, log=print, cancel: threading.Event | None = None) -> dict:
    cancel = cancel or threading.Event()
    restore(prof)
    files, metas, requires = _graph(prof)
    if not files:
        return {"found": False, "message": "MODが入っていません。"}
    names = {f: (metas[f].get("name") or f) for f in files}
    log(f"MOD {len(files)} 個で、まず全部入れた状態で試します…")
    try:
        first = run_once(prof, log, cancel)
        if first["ok"]:
            return {"found": False, "message": "全部入れた状態で起動できました。MODの問題ではないかもしれません。", "first": first}
        suspects = list(files)
        rounds = 0
        max_rounds = max(1, len(files).bit_length() + 2)
        while len(suspects) > 1 and rounds < max_rounds + 4:
            rounds += 1
            half = set(suspects[: len(suspects) // 2])
            off = _with_dependents(half, requires)
            log(f"【{rounds}回目 / だいたい{max_rounds}回】{len(off)}個をOFFにして試します（残りの候補 {len(suspects)}個）")
            _hold(prof, off)
            try:
                r = run_once(prof, log, cancel)
            finally:
                restore(prof)
            if r["ok"]:
                suspects = [f for f in suspects if f in off]
                log("→ 起動できた！ 原因はOFFにした中にあります")
            else:
                suspects = [f for f in suspects if f not in off]
                log("→ まだ起動しない。原因は残りの中にあります")
            if not suspects:
                return {"found": False, "message": "1つにしぼれませんでした。2つ以上のMODの組み合わせが原因かもしれません。"}
        culprit = suspects[0]
        # 本当にそれが原因か、それ（と、それを必要とするMOD）だけOFFにして確かめる
        off = _with_dependents({culprit}, requires)
        log(f"確認: 「{names[culprit]}」をOFFにして試します…")
        _hold(prof, off)
        try:
            r = run_once(prof, log, cancel)
        finally:
            restore(prof)
        if not r["ok"]:
            return {"found": False, "message": f"「{names[culprit]}」が怪しいですが、OFFにしても起動しませんでした。原因が2つ以上あるかもしれません。",
                    "suspect": culprit}
        return {"found": True, "file": culprit, "name": names[culprit],
                "also": [names[f] for f in sorted(off - {culprit})], "path": f"{os.path.basename(prof.mods_dir)}/{culprit}",
                "paths": [f"{os.path.basename(prof.mods_dir)}/{f}" for f in sorted(off)],
                "message": f"原因は「{names[culprit]}」です。"}
    finally:
        restore(prof)
