"""重さの見える化：TPS（1秒あたりの処理回数）・メモリ・CPU・人数をはかって記録する。

TPS は、サーバーに裏でコマンドを送ってはかる（その返事は画面のログに出さない）。
  - マイクラ 1.20.3 以降 … tick query
  - それより前の Forge / NeoForge … forge tps / neoforge tps
  - それより前の Fabric / バニラ … はかれない（「処理が追いつかない」記録だけ数える）
"""
from __future__ import annotations

import collections
import os
import re
import threading
import time

from .paths import IS_WINDOWS

SAMPLE_SEC = 5
PROBE_SEC = 15
KEEP = 720  # 5秒ごと × 720 ＝ 1時間

LAG_RE = re.compile(r"Can't keep up! .*?Running (\d+)ms or (\d+) ticks behind", re.I)
PROBE_LINE_RE = re.compile(
    r"The game is (?:running|frozen|sprinting)|Target tick rate|Average time per tick|Percentiles|"
    r"Mean tick time|Mean TPS|ms/tick|TPS\s*\(|^\s*Dim\b|Overall|\b\d+(?:\.\d+)?\s*TPS\b|TPS from last", re.I)
TICK_QUERY_RE = re.compile(r"Average time per tick:\s*([\d.]+)\s*ms", re.I)
FORGE_RE = re.compile(r"Overall\s*:?\s*Mean tick time:\s*([\d.]+)\s*ms\.?\s*Mean TPS:\s*([\d.]+)", re.I)
NEO_RE = re.compile(r"Overall\s*:?\s*([\d.]+)\s*TPS\s*\(\s*([\d.]+)\s*ms/tick", re.I)
GENERIC_TPS_RE = re.compile(r"Mean TPS:\s*([\d.]+)", re.I)
# Paper / Purpur の /tps:「TPS from last 1m, 5m, 15m: *20.0, 20.0, 20.0」
PAPER_TPS_RE = re.compile(r"TPS from last 1m, 5m, 15m:\s*\*?([\d.]+)", re.I)


def _ver_tuple(mc: str):
    m = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", mc or "")
    if not m:
        return (99, 0, 0)
    return tuple(int(x or 0) for x in m.groups())


def probe_command(mc: str, loader: str) -> str | None:
    v = _ver_tuple(mc)
    if v[0] > 1 or v >= (1, 20, 3):
        return "tick query"
    if loader == "forge":
        return "forge tps"
    if loader == "neoforge":
        return "neoforge tps"
    if loader in ("paper", "purpur"):
        return "tps"
    return None


# ---------------- プロセスのメモリ・CPU ----------------

def _proc_stats(pid: int):
    """(使っているメモリMB, これまでのCPU時間 秒) を返す。"""
    try:
        if IS_WINDOWS:
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            k32 = ctypes.windll.kernel32
            h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return None
            try:
                pmc = PMC()
                pmc.cb = ctypes.sizeof(PMC)
                k32.K32GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb)
                ft = [wintypes.FILETIME() for _ in range(4)]
                k32.GetProcessTimes(h, *[ctypes.byref(f) for f in ft])
                cpu = sum(((f.dwHighDateTime << 32) | f.dwLowDateTime) for f in ft[2:]) / 1e7
                return pmc.WorkingSetSize / 1024 / 1024, cpu
            finally:
                k32.CloseHandle(h)
        with open(f"/proc/{pid}/status") as f:
            rss = next((int(line.split()[1]) / 1024 for line in f if line.startswith("VmRSS:")), 0)
        with open(f"/proc/{pid}/stat") as f:
            parts = f.read().rsplit(")", 1)[1].split()
        tck = os.sysconf("SC_CLK_TCK")
        return rss, (int(parts[11]) + int(parts[12])) / tck
    except (OSError, ValueError, StopIteration, AttributeError):
        return None


# ---------------- はかる係 ----------------

class Monitor:
    def __init__(self, runner):
        self.r = runner
        self.samples: collections.deque = collections.deque(maxlen=KEEP)
        self.lags: collections.deque = collections.deque(maxlen=500)   # (時刻, 遅れたミリ秒)
        self.tps = None
        self.mspt = None
        self.tps_at = 0.0
        self.probe = None
        self.probe_supported = True
        self._hide_until = 0.0
        self._thread = None
        self._last_cpu = None
        self._probe_sent = 0
        self._probe_answers = 0
        runner.line_hooks.append(self.on_line)

    def on_line(self, line: str) -> bool:
        if m := LAG_RE.search(line):
            self.lags.append((time.time(), int(m.group(1))))
        hiding = time.time() < self._hide_until
        got = None
        if m := TICK_QUERY_RE.search(line):
            mspt = float(m.group(1))
            got = (min(20.0, 1000.0 / mspt) if mspt > 0 else 20.0, mspt)
        elif m := FORGE_RE.search(line):
            got = (min(20.0, float(m.group(2))), float(m.group(1)))
        elif m := NEO_RE.search(line):
            got = (min(20.0, float(m.group(1))), float(m.group(2)))
        elif m := PAPER_TPS_RE.search(line):
            got = (min(20.0, float(m.group(1))), None)
        elif hiding and (m := GENERIC_TPS_RE.search(line)):
            got = (min(20.0, float(m.group(1))), None)
        if got and hiding:
            self.tps, self.mspt = got
            self.tps_at = time.time()
            self._probe_answers += 1
        # 裏で送ったコマンドの返事は、画面のログに出さない
        return hiding and bool(PROBE_LINE_RE.search(line) or "Unknown or incomplete command" in line
                               or "Unknown command" in line or "<--[HERE]" in line)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        prof = self.r.prof
        self.probe = probe_command(prof.get("mc", ""), prof.get("loader", ""))
        self.probe_supported = self.probe is not None
        self._probe_sent = self._probe_answers = 0
        self.tps = self.mspt = None
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        last_probe = 0.0
        for _ in range(600):  # 起動の合図はプロセスができる少し前に来るので、できるまで待つ
            if self.r.running:
                break
            time.sleep(0.1)
        while self.r.running:
            now = time.time()
            if self.r.state == "running" and self.probe and now - last_probe >= PROBE_SEC:
                last_probe = now
                self._hide_until = now + 3
                self.r.send(self.probe)
                self._probe_sent += 1
                if self._probe_sent >= 4 and self._probe_answers == 0:
                    self.probe = None          # 返事がないので、このサーバーでははかれない
                    self.probe_supported = False
            self._sample()
            time.sleep(SAMPLE_SEC)
        self._last_cpu = None

    def _sample(self):
        proc = self.r.proc
        if not proc:
            return
        now = time.time()
        st = _proc_stats(proc.pid)
        mem = cpu_pct = None
        if st:
            mem, cpu_t = st
            if self._last_cpu:
                dt = now - self._last_cpu[0]
                if dt > 0:
                    cpu_pct = max(0.0, min(100.0, (cpu_t - self._last_cpu[1]) / dt / (os.cpu_count() or 1) * 100))
            self._last_cpu = (now, cpu_t)
        fresh = self.tps if now - self.tps_at < PROBE_SEC * 2.5 else None
        lag = sum(1 for t, _ in self.lags if t > now - SAMPLE_SEC)
        self.samples.append({"t": now, "tps": fresh, "mspt": self.mspt if fresh is not None else None,
                             "mem": round(mem) if mem else None, "cpu": round(cpu_pct, 1) if cpu_pct is not None else None,
                             "players": len(self.r.players), "lag": lag})


PERF_MODS = {
    "fabric": [("lithium", "Lithium", "内部の計算を軽くする"), ("ferrite-core", "FerriteCore", "メモリを減らす"),
               ("modernfix", "ModernFix", "メモリと起動時間を減らす"), ("krypton", "Krypton", "通信を軽くする"),
               ("c2me-fabric", "C2ME", "地形の生成を速くする")],
    "neoforge": [("ferrite-core", "FerriteCore", "メモリを減らす"), ("modernfix", "ModernFix", "メモリと起動時間を減らす")],
    "forge": [("ferrite-core", "FerriteCore", "メモリを減らす"), ("modernfix", "ModernFix", "メモリと起動時間を減らす")],
    "paper": [("spark", "spark", "どこが重いかを調べる")],
    "purpur": [("spark", "spark", "どこが重いかを調べる")],
}


def summary(mon: Monitor | None, prof, installed_slugs: set[str]) -> dict:
    samples = list(mon.samples) if mon else []
    now = time.time()
    recent = [s for s in samples if s["t"] > now - 120]
    tps_vals = [s["tps"] for s in recent if s["tps"] is not None]
    mem_vals = [s["mem"] for s in recent if s["mem"]]
    cpu_vals = [s["cpu"] for s in recent if s["cpu"] is not None]
    lags10 = [ms for t, ms in (mon.lags if mon else []) if t > now - 600]
    alloc = int(prof.get("memory_mb", 4096))
    tps = round(sum(tps_vals) / len(tps_vals), 1) if tps_vals else None
    mem = max(mem_vals) if mem_vals else None
    cpu = round(sum(cpu_vals) / len(cpu_vals)) if cpu_vals else None
    props = prof.props()
    advice = []

    def add(level, title, text, action=None):
        advice.append({"level": level, "title": title, "text": text, "action": action})

    if tps is not None and tps < 15:
        add("error", "とても重い（TPS が低い）", f"いまの TPS は {tps}（普通は 20）。ブロックの動きや生き物がカクカクします。")
    elif tps is not None and tps < 19:
        add("warn", "少し重い", f"いまの TPS は {tps}（普通は 20）。")
    if len(lags10) >= 3:
        add("warn", "処理が追いつかないことがあります",
            f"この10分で {len(lags10)} 回、サーバーの処理が遅れました（一番長くて {max(lags10) / 1000:.1f}秒）。")
    heavy = (tps is not None and tps < 19) or len(lags10) >= 3
    if mem and alloc and mem >= alloc:  # Java は割り当て＋αまで使う。割り当てを超えたら、ほぼいっぱい
        add("warn", "メモリがいっぱいに近いです",
            f"{mem / 1024:.1f}GB 使っています（割り当て {alloc / 1024:.1f}GB）。「設定」でメモリを 1〜2GB 増やすと楽になります。",
            {"type": "goto", "tab": "settings"})
    if cpu is not None and cpu > 85:
        add("warn", "CPU（PCの頭脳）がフル回転しています", "ほかの重いソフトを閉じる、見える距離を下げる、が効きます。")
    if heavy:
        try:
            vd = int(props.get("view-distance", 10))
            sd = int(props.get("simulation-distance", 10))
        except ValueError:
            vd = sd = 10
        if vd > 8 or sd > 8:
            add("info", "見える距離・動く範囲を下げる",
                f"いまは 見える距離 {vd}・動く範囲 {sd}。どちらも 6〜8 にすると、かなり軽くなります。", {"type": "goto", "tab": "settings"})
        missing = [m for m in PERF_MODS.get(prof.get("loader"), []) if m[0] not in installed_slugs]
        if missing:
            add("info", "軽量化MODを入れる",
                "まだ入っていない軽量化MOD: " + "、".join(f"{n}（{d}）" for _s, n, d in missing),
                {"type": "install", "ids": [s for s, _n, _d in missing]})
        add("info", "ワールドを事前生成する", "新しい場所を歩くたびに地形を作るのが重さの原因になりがちです。下の「事前生成」で、先に作っておけます。")
    if not advice and samples:
        add("ok", "軽く動いています", "いまのところ、重さの問題は見使っていません。")
    return {"tps": tps, "mem": mem, "alloc": alloc, "cpu": cpu, "lags10": len(lags10),
            "players": samples[-1]["players"] if samples else 0, "advice": advice,
            "probe_supported": bool(mon and mon.probe_supported), "samples": samples[-360:]}
