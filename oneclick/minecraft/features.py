"""新しい機能の、画面とは関係ない部分（判定・検査・予約のしくみ）。api.py から呼ぶ。"""
from __future__ import annotations

import datetime
import json
import os
import re
import shutil

DAY_NAMES = "月火水木金土日"
_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


# ---------------- 予約（曜日と時刻で、起動・停止する） ----------------

def clean_schedule(rules) -> list[dict]:
    """画面から来た予約を、安全な形にそろえる（おかしいものはエラー）。"""
    if not isinstance(rules, list):
        raise ValueError("予約の形がおかしいです")
    out = []
    for r in rules[:12]:
        if not isinstance(r, dict):
            continue
        days = sorted({int(d) for d in (r.get("days") or []) if str(d).isdigit() and 0 <= int(d) <= 6})
        start = str(r.get("start") or "").strip()
        stop = str(r.get("stop") or "").strip()
        for t in (start, stop):
            if t and not _HHMM.match(t):
                raise ValueError(f"時刻「{t}」がおかしいです（例: 19:00）")
        if not start and not stop:
            continue
        if not days:
            raise ValueError("曜日を1つ以上選んでください")
        norm = lambda t: f"{int(t.split(':')[0]):02d}:{t.split(':')[1]}" if t else ""  # noqa: E731
        out.append({"days": days, "start": norm(start), "stop": norm(stop)})
    return out


def schedule_text(r: dict) -> str:
    days = "".join(DAY_NAMES[d] for d in r["days"]) if len(r["days"]) < 7 else "毎日"
    parts = []
    if r.get("start"):
        parts.append(f"{r['start']} に起動")
    if r.get("stop"):
        parts.append(f"{r['stop']} に停止")
    return f"{days} " + "、".join(parts)


def schedule_events(rules: list[dict], now: datetime.datetime) -> list[tuple]:
    """いま（この「分」）にやることの一覧: [(番号, 'start'|'warn5'|'warn1'|'stop')]。"""
    out = []
    hm = now.hour * 60 + now.minute
    for i, r in enumerate(rules or []):
        if now.weekday() not in r.get("days", []):
            continue
        for kind, t in (("start", r.get("start")), ("stop", r.get("stop"))):
            if not t:
                continue
            h, m = map(int, t.split(":"))
            at = h * 60 + m
            if kind == "start" and hm == at:
                out.append((i, "start"))
            if kind == "stop":
                if hm == at:
                    out.append((i, "stop"))
                elif hm == at - 5:
                    out.append((i, "warn5"))
                elif hm == at - 1:
                    out.append((i, "warn1"))
    return out


# ---------------- 参加者のID（UUID） ----------------

def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def player_ids(server_dir: str, online: list[str]) -> list[dict]:
    """名前とUUIDの一覧（サーバーの記録ファイルから）。"""
    people: dict[str, dict] = {}

    def add(name, uuid=None, **flags):
        if not name:
            return
        d = people.setdefault(name.lower(), {"name": name, "uuid": "", "online": False, "op": False, "whitelisted": False,
                                              "banned": False})
        if uuid and not d["uuid"]:
            d["uuid"] = uuid
        d.update({k: v or d[k] for k, v in flags.items()})

    for e in read_json(os.path.join(server_dir, "usercache.json"), []) or []:
        add(e.get("name"), e.get("uuid"))
    for e in read_json(os.path.join(server_dir, "whitelist.json"), []) or []:
        add(e.get("name"), e.get("uuid"), whitelisted=True)
    for e in read_json(os.path.join(server_dir, "ops.json"), []) or []:
        add(e.get("name"), e.get("uuid"), op=True)
    for e in read_json(os.path.join(server_dir, "banned-players.json"), []) or []:
        add(e.get("name"), e.get("uuid"), banned=True)
    for n in online:
        add(n, online=True)
    return sorted(people.values(), key=lambda d: (not d["online"], d["name"].lower()))


# ---------------- 起動前チェック ----------------

def item(level, title, detail="", fix=""):
    return {"level": level, "title": title, "detail": detail, "fix": fix}


def disk_item(path: str):
    try:
        free = shutil.disk_usage(path).free / 1024 ** 3
    except OSError:
        return None
    if free < 1:
        return item("error", f"ディスクの空きが {free:.1f}GB しかありません", "ワールドの保存に失敗するおそれがあります。",
                    "いらないファイルやバックアップを消して、空きを作ってください。")
    if free < 5:
        return item("warn", f"ディスクの空きが少なめです（{free:.1f}GB）", "バックアップがたまると足りなくなります。",
                    "古いバックアップを減らしてください。")
    return item("ok", f"ディスクの空き: {free:.0f}GB")


def memory_item(alloc: int, total: int, need: int, max_safe: int):
    gb = lambda mb: f"{mb / 1024:.1f}GB"  # noqa: E731
    if alloc > total * 0.85:
        return item("error", f"メモリを使いすぎです（{gb(alloc)} / PC全体 {gb(total)}）",
                    "PCのメモリをほとんど使い切るので、PCが固まったり、サーバーが落ちたりします。",
                    f"「設定」でメモリを {gb(max_safe)} 以下にしてください。")
    if alloc < need - 512:
        return item("warn", f"メモリが少なめです（{gb(alloc)}。おすすめは {gb(need)}）",
                    "MODの数や人数のわりに少ないので、途中で落ちたり、重くなったりするかもしれません。",
                    "「設定」のメモリを増やしてください。")
    if alloc > max_safe:
        return item("warn", f"メモリが、安全な量（{gb(max_safe)}）より多めです",
                    "自分も同じPCでマイクラを遊ぶと、足りなくなるかもしれません。", "")
    return item("ok", f"メモリ: {gb(alloc)}（PC全体 {gb(total)}）")
