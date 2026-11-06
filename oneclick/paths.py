"""保存場所。ソフトのフォルダの中の data/ にすべて保存します。

data/
  minecraft/   マイクラ(従来の OneClickServerMC の data と同じ中身)
  ark_asa/     ARK: Survival Ascended
  ark_ase/     ARK: Survival Evolved
  palworld/    Palworld
  terraria/    Terraria
  shared/      SteamCMD・playit など、ゲームをまたいで使うもの
  migrate/     引き継ぎ前の自動バックアップ置き場
更新しても、data フォルダには一切さわりません。
"""
from __future__ import annotations

import json
import os

from .runtime import APP_DIR

DATA_DIR = os.environ.get("ONECLICK_DATA") or os.path.join(APP_DIR, "data")
SHARED_DIR = os.path.join(DATA_DIR, "shared")
MIGRATE_DIR = os.path.join(DATA_DIR, "migrate")
APP_SETTINGS = os.path.join(DATA_DIR, "app_settings.json")
INSTANCE_FILE = os.path.join(DATA_DIR, "instance.json")


def game_dir(key: str) -> str:
    return os.path.join(DATA_DIR, key)


def ensure_dirs():
    for d in (DATA_DIR, SHARED_DIR, MIGRATE_DIR):
        os.makedirs(d, exist_ok=True)


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
