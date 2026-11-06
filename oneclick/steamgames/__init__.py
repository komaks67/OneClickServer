"""「サーバー1つを、このPCで動かす」タイプのゲーム(ARK・Palworld・Terraria)。"""
from __future__ import annotations

from .ark import ArkAsa, ArkAse
from .palworld import Palworld
from .terraria import Terraria

# 画面に並べる順番
CLASSES = [ArkAsa, ArkAse, Palworld, Terraria]
BY_KEY = {c.KEY: c for c in CLASSES}

_instances: dict = {}


def get(key: str):
    """ゲームの管理係を返す(初めて呼ばれたときに作る)。"""
    if key not in BY_KEY:
        raise KeyError(key)
    if key not in _instances:
        _instances[key] = BY_KEY[key]()
    return _instances[key]


def existing() -> list:
    return list(_instances.values())
