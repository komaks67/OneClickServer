"""いま動いているサーバーや、ホームに並べるカードをまとめて見る係。

マイクラ(複数のサーバーを持てる)と、ARK・Palworld・Terraria(ゲームごとに1つ)を、
同じ「サーバー一覧」として扱えるようにする。
"""
from __future__ import annotations

import os

from . import paths, steamgames

# 「新しく作る」に並べるゲーム(表示順)
GAME_CHOICES = [
    {"key": "minecraft", "name": "Minecraft", "badge": "MC"},
    {"key": "ark_asa", "name": "ARK: Survival Ascended", "badge": "ASA"},
    {"key": "ark_ase", "name": "ARK: Survival Evolved", "badge": "ASE"},
    {"key": "palworld", "name": "Palworld", "badge": "PAL"},
    {"key": "terraria", "name": "Terraria", "badge": "TER"},
]
NAMES = {g["key"]: g["name"] for g in GAME_CHOICES}
BADGES = {g["key"]: g["badge"] for g in GAME_CHOICES}


def mc_api():
    """マイクラ部分(初めて呼ばれたときに読み込む)。"""
    from .minecraft import api
    return api


def steam_running() -> bool:
    return any(m.running() for m in steamgames.existing())


def steam_busy() -> bool:
    return any(m.busy for m in steamgames.existing())


def any_running() -> bool:
    try:
        mc = bool(mc_api().any_server_running()) or bool(mc_api().jobs_running())
    except Exception:  # noqa: BLE001
        mc = False
    return mc or steam_running() or steam_busy()


def steam_exists(key: str) -> bool:
    """そのゲームのサーバーを、もう作ってあるか(設定ファイルがあるか)。"""
    root = paths.game_dir(key)
    return os.path.exists(os.path.join(root, "settings.json")) or os.path.exists(os.path.join(root, "server"))


def steam_cards() -> list:
    cards = []
    for cls in steamgames.CLASSES:
        if not steam_exists(cls.KEY):
            continue
        m = steamgames.get(cls.KEY)
        st = "busy" if m.busy else ("running" if m.running() else "stopped")
        if st == "stopped" and not m.installed():
            detail = "まだ導入されていません"
        else:
            detail = m.NAME
        cards.append({
            "game": cls.KEY, "id": cls.KEY, "badge": cls.SHORT[:3].upper(), "name": m.display_name(),
            "detail": detail, "status": st, "busy": m.busy, "players": m.cached_players(),
            "update": bool(m.upd.get("game") or m.upd.get("mods")),
            "url": f"/g/{cls.KEY}/",
        })
    return cards


def mc_cards() -> list:
    cards = []
    try:
        servers = mc_api().state()["servers"]
    except Exception:  # noqa: BLE001
        return cards
    for s in servers:
        st = {"running": "running", "starting": "busy", "stopping": "busy"}.get(s.get("status"), "stopped")
        cards.append({
            "game": "minecraft", "id": s["id"], "badge": "MC", "name": s["name"],
            "detail": f"Minecraft {s.get('mc')} / {s.get('loader_label')}・MOD {s.get('mods', 0)}個",
            "status": st, "busy": "", "players": len(s.get("players") or []) if st == "running" else None,
            "url": "/go/minecraft",
        })
    return cards


def home_cards() -> list:
    return mc_cards() + steam_cards()


def check_all_updates():
    """ソフトを開いたとき1回だけ、導入済みの各ゲームの更新を調べる(1つずつ、順番に)。"""
    for cls in steamgames.CLASSES:
        try:
            if not steam_exists(cls.KEY):
                continue
            m = steamgames.get(cls.KEY)
            if m.busy or not m.installed():
                continue
            m.run_update_check()
        except Exception:  # noqa: BLE001
            continue
