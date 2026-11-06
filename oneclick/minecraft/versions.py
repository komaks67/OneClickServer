"""マイクラのバージョン番号をくらべるための道具。

1.21.11 までは「1.x.y」、そのあとは「26.1」のような番号（年.回数）になった。
数字の大きさでそのままくらべられる（26.1 > 1.21.11）。
"""
from __future__ import annotations

import re

MSMP = (1, 21, 9)            # 管理用の窓口（Server Management Protocol）ができた
GAMERULE_REGISTRY = (1, 21, 11)  # ゲームルールの名前が新しくなった（keepInventory → minecraft:keep_inventory）
PROPS_TO_GAMERULES = (1, 21, 9)  # pvp などが設定ファイルからゲームルールに移った
TICK_COMMAND = (1, 20, 3)


def parse(mc: str) -> tuple[int, int, int]:
    m = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", mc or "")
    if not m:
        return (999, 0, 0)  # 分からないときは「新しい」とみなす
    return tuple(int(x or 0) for x in m.groups())  # type: ignore[return-value]


def at_least(mc: str, v: tuple) -> bool:
    return parse(mc) >= tuple(v)


def in_range(mc: str, since: tuple | None = None, until: tuple | None = None) -> bool:
    """since 以上、until 未満なら True。"""
    p = parse(mc)
    if since and p < tuple(since):
        return False
    if until and p >= tuple(until):
        return False
    return True
