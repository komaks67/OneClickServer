"""ワールドの事前生成：遊ぶ前に地形を作っておいて、遊んでいる間のカクつきを減らす。

定番のMOD「Chunky」を使う（サーバーだけに入れればよく、友達は入れなくていい）。
"""
from __future__ import annotations

import math
import re
import time

from .modrinth import ModList

PROGRESS_RE = re.compile(
    r"\[Chunky\]\s*Task (running|finished|stopped|paused|cancelled|started) for ([\w:./-]+)\.?"
    r"(?:\s*Processed:\s*(\d+)\s*chunks\s*\(([\d.]+)%\))?(?:,\s*ETA:\s*([\d:]+))?(?:,\s*Rate:\s*([\d.]+)\s*cps)?"
    r"(?:.*?Total time:\s*([\d:]+))?", re.I)

DIMENSIONS = [("minecraft:overworld", "地上（オーバーワールド）"), ("minecraft:the_nether", "ネザー"),
              ("minecraft:the_end", "エンド")]


def installed(prof) -> bool:
    ml = ModList(prof.dir)
    return any((m.get("slug") == "chunky" or m["title"].lower().startswith("chunky")) and m.get("enabled", True)
               for m in ml.items)


def estimate(radius: int) -> dict:
    side = math.ceil(radius * 2 / 16)
    chunks = side * side
    return {"chunks": chunks, "mb_min": round(chunks * 0.006), "mb_max": round(chunks * 0.02),
            "min_fast": max(1, round(chunks / 250 / 60)), "min_slow": max(1, round(chunks / 60 / 60))}


class Tracker:
    """ログに出る Chunky の進み具合を読む。"""

    def __init__(self, runner):
        self.state = {}
        self.active = False
        self.aliases = {}  # Paper のワールド名（world_nether など）→ minecraft:the_nether
        runner.line_hooks.append(self.on_line)

    def on_line(self, line: str) -> bool:
        if "[Chunky]" not in line and "chunky" not in line.lower():
            return False
        m = PROGRESS_RE.search(line)
        if not m:
            return False
        status, dim, chunks, pct, eta, rate, total = m.groups()
        dim = dim.rstrip(".")
        dim = self.aliases.get(dim, dim)
        st = self.state.setdefault(dim, {})
        st.update({"status": status.lower(), "updated": time.time()})
        if chunks:
            st["chunks"] = int(chunks)
        if pct:
            st["percent"] = float(pct)
        if eta:
            st["eta"] = eta
        if rate:
            st["rate"] = float(rate)
        if total:
            st["total_time"] = total
        if st["status"] == "finished":
            st["percent"] = 100.0
        self.active = any(s.get("status") in ("running", "started") for s in self.state.values())
        # 「〇〇%」の行は何度も出るので、画面のログには出さない（終わった・止めたは出す）
        return status.lower() == "running"


def bukkit_world(dim: str, level: str = "world") -> str:
    """Paper / Purpur では、ワールドは名前で指定する。"""
    return {"minecraft:overworld": level, "minecraft:the_nether": f"{level}_nether",
            "minecraft:the_end": f"{level}_the_end"}.get(dim, dim)


def commands(dim: str, radius: int, center: str = "spawn") -> list[str]:
    cmds = [f"chunky world {dim}", "chunky shape square"]
    cmds.append("chunky spawn" if center == "spawn" else "chunky center 0 0")
    cmds += [f"chunky radius {int(radius)}", "chunky start", "chunky confirm"]
    return cmds
