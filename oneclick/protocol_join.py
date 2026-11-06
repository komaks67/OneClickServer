"""起動の引数から、招待リンク(mcmh://join/…)を取り出す。"""
from __future__ import annotations

import urllib.parse


def join_hash(argv: list) -> str:
    """"--join <リンク>" があれば、マイクラの画面に渡す "#join=…" を返す(なければ空)。"""
    if "--join" not in argv:
        return ""
    i = argv.index("--join")
    if i + 1 >= len(argv):
        return ""
    from .minecraft import protocol
    addr = protocol.parse(argv[i + 1])
    return "#join=" + urllib.parse.quote(addr) if addr else ""
