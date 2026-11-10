"""PC の「ディスクの空き」と「重さ(CPU・メモリ)」を見張って、危ないときだけ Discord に知らせる。

- 1分ごとに調べる。同じ警告は、1時間に1回まで
- 直ったときは「もとに戻りました」を1回だけ出す
- 数字は Discord の設定(discord_bot.alerts)で変えられる。追加の部品は使わない
"""
from __future__ import annotations

import os
import shutil
import sys
import threading
import time

COOLDOWN = 3600
DEFAULTS = {"on": True, "disk_gb": 10, "cpu": 90, "mem": 90}
CPU_MINUTES = 5          # CPU がこの分数ずっと高かったときだけ知らせる(一瞬の高さでは鳴らさない)

_state: dict = {}        # 警告の種類 -> {"since": 最初に高くなった時刻, "sent": 最後に知らせた時刻, "active": bool}
_stop = threading.Event()
_prev_cpu = None


def settings() -> dict:
    from . import discordhub
    a = discordhub.cfg().get("alerts")
    out = dict(DEFAULTS)
    if isinstance(a, dict):
        out.update({k: a[k] for k in DEFAULTS if k in a})
    return out


# ------------------------------------------------------------ 測る
def disk_free_gb(path: str) -> float:
    return shutil.disk_usage(path).free / 1073741824


def mem_percent() -> float | None:
    try:
        if sys.platform == "win32":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong)] + \
                           [(n, ctypes.c_ulonglong) for n in ("t", "a", "pt", "pa", "vt", "va", "ae")]
            m = MS()
            m.l = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return float(m.load)
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                info[k] = int(v.split()[0])
        return 100.0 * (1 - info["MemAvailable"] / info["MemTotal"])
    except Exception:  # noqa: BLE001
        return None


def cpu_percent() -> float | None:
    """前回から今までの CPU 使用率(%)。最初の1回は None。"""
    global _prev_cpu
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            idle, kern, user = (wintypes.FILETIME() for _ in range(3))
            ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user))
            ft = lambda f: (f.dwHighDateTime << 32) | f.dwLowDateTime  # noqa: E731
            cur = (ft(idle), ft(kern) + ft(user))          # kernel には idle も含まれる
        else:
            with open("/proc/stat") as f:
                v = [int(x) for x in f.readline().split()[1:]]
            cur = (v[3] + (v[4] if len(v) > 4 else 0), sum(v))
        prev, _prev_cpu = _prev_cpu, cur
        if not prev or cur[1] == prev[1]:
            return None
        return max(0.0, min(100.0, 100.0 * (1 - (cur[0] - prev[0]) / (cur[1] - prev[1]))))
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------ 判定(テストしやすいように、時刻と値を受け取る)
def evaluate(values: dict, cfg: dict, now: float) -> list:
    """[(種類, "warn"|"recover", タイトル, 説明)] を返す。同じ警告の連投・復旧の1回だけを守る。"""
    out = []

    def judge(kind, bad, title, desc, ok_title, hold=0):
        st = _state.setdefault(kind, {"since": None, "sent": 0, "active": False})
        if bad:
            st["since"] = now if st["since"] is None else st["since"]
            if now - st["since"] >= hold and (not st["active"] or now - st["sent"] >= COOLDOWN):
                st["active"], st["sent"] = True, now
                out.append((kind, "warn", title, desc))
        else:
            st["since"] = None
            if st["active"]:
                st["active"] = False
                out.append((kind, "recover", ok_title, "もう心配いりません。"))

    d = values.get("disk_gb")
    if d is not None:
        judge("disk", d < cfg["disk_gb"], "🟠 ディスクの空きが少なくなっています",
              f"空きは **{d:.1f} GB** です(目安: {cfg['disk_gb']} GB 未満で知らせています)。\n"
              "古いバックアップは、ソフトの「バックアップ」画面の「最新の1つ以外を消す」で減らせます。",
              "🟢 ディスクの空きが、もとに戻りました")
    c = values.get("cpu")
    if c is not None:
        judge("cpu", c >= cfg["cpu"], "🟠 PC が重くなっています(CPU)",
              f"CPU が **{c:.0f}%** です({CPU_MINUTES}分以上続いています)。ほかのソフトを閉じると軽くなります。",
              "🟢 PC の重さ(CPU)が、もとに戻りました", hold=CPU_MINUTES * 60)
    m = values.get("mem")
    if m is not None:
        judge("mem", m >= cfg["mem"], "🟠 メモリが足りなくなっています",
              f"メモリを **{m:.0f}%** 使っています。サーバーのメモリ設定を下げるか、ほかのソフトを閉じてください。",
              "🟢 メモリが、もとに戻りました", hold=60)
    return out


def _data_dir() -> str:
    from . import paths
    os.makedirs(paths.DATA_DIR, exist_ok=True)
    return paths.DATA_DIR


def tick(now: float | None = None):
    from . import discordhub
    cfg = settings()
    if not cfg["on"]:
        return
    values = {"disk_gb": disk_free_gb(_data_dir()), "cpu": cpu_percent(), "mem": mem_percent()}
    for kind, what, title, desc in evaluate(values, cfg, now if now is not None else time.time()):
        discordhub.post_alert(title, desc, "bad" if what == "warn" else "ok")


def _loop():
    time.sleep(20)
    while not _stop.wait(60):
        try:
            tick()
        except Exception as e:  # noqa: BLE001
            print(f"PC の見張りでエラー: {e}", flush=True)


def start():
    threading.Thread(target=_loop, daemon=True).start()


def stop():
    _stop.set()
