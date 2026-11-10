"""管理の道具：管理コマンド（安全なひな形）、返事の取得、ゲームルール（コンソール版）、BAN、操作の記録。"""
from __future__ import annotations

import datetime
import json
import os
import re
import threading
import time

from . import gamerules_meta, versions
from .paths import load_json, save_json

NAME_RE = re.compile(r"^[A-Za-z0-9_]{2,16}$")
SELECTORS = {"@a", "@p", "@r", "@s"}
ITEM_RE = re.compile(r"^(?:[a-z0-9_.\-]+:)?[a-z0-9_/.\-]+$")
IP_RE = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$|^[0-9a-fA-F:]{2,39}$")


class BadInput(ValueError):
    pass


# ---------------- 入力のチェック ----------------

def player(v, allow_selector=False) -> str:
    v = (v or "").strip()
    if allow_selector and v in SELECTORS:
        return v
    if not NAME_RE.match(v):
        raise BadInput(f"「{v}」はマイクラの名前として使えない形です（英数字と _ の 3〜16 文字）")
    return v


def text(v, max_len=200) -> str:
    v = re.sub(r"[\x00-\x1f\x7f]", " ", str(v or "")).strip()
    if len(v) > max_len:
        raise BadInput(f"{max_len}文字までにしてください")
    return v


def integer(v, lo, hi, label="数") -> int:
    try:
        n = int(str(v).strip())
    except ValueError as e:
        raise BadInput(f"{label}には数字を入れてください") from e
    if not lo <= n <= hi:
        raise BadInput(f"{label}は {lo}〜{hi} にしてください")
    return n


def choice(v, options, label="選択") -> str:
    if v not in options:
        raise BadInput(f"{label}がおかしいです")
    return v


# ---------------- 管理コマンドのひな形 ----------------
# 名前: (グループ, 日本語の名前, 作り方)  作り方は 引数dict と mc を受け取ってコマンド文字列を返す

def _need_tick(mc):
    if not versions.at_least(mc, versions.TICK_COMMAND):
        raise BadInput("このコマンドは 1.20.3 以降で使えます")


ACTIONS = {
    "time": ("world", "時間を変える", lambda a, mc: f"time set {choice(a.get('value'), ['day', 'noon', 'night', 'midnight'], '時間')}"),
    "weather": ("world", "天気を変える", lambda a, mc: f"weather {choice(a.get('value'), ['clear', 'rain', 'thunder'], '天気')}"),
    "difficulty": ("world", "難しさを変える",
                   lambda a, mc: f"difficulty {choice(a.get('value'), ['peaceful', 'easy', 'normal', 'hard'], '難しさ')}"),
    "save": ("world", "ワールドを保存する", lambda a, mc: "save-all flush"),
    "seed": ("world", "シード値を見る", lambda a, mc: "seed"),
    "setworldspawn": ("world", "スポーン地点を決める",
                      lambda a, mc: "setworldspawn {} {} {}".format(*(integer(a.get(k), -30000000, 30000000, k.upper())
                                                                       for k in ("x", "y", "z")))),
    "worldborder": ("world", "世界のはし（ワールドボーダー）を決める",
                    lambda a, mc: f"worldborder set {integer(a.get('size'), 16, 59999968, '大きさ')}"),
    "kill_items": ("world", "落ちているアイテムを全部消す（軽くする）", lambda a, mc: "kill @e[type=item]"),
    "tick_freeze": ("world", "時間を止める（tick freeze）", lambda a, mc: (_need_tick(mc), "tick freeze")[1]),
    "tick_unfreeze": ("world", "時間を動かす（tick unfreeze）", lambda a, mc: (_need_tick(mc), "tick unfreeze")[1]),
    "tick_query": ("world", "処理の速さを見る（tick query）", lambda a, mc: (_need_tick(mc), "tick query")[1]),
    "list": ("people", "参加している人を見る", lambda a, mc: "list"),
    "say": ("people", "全員にメッセージ", lambda a, mc: f"say {text(a.get('message')) or _raise('メッセージを入れてください')}"),
    "gamemode": ("people", "ゲームモードを変える",
                 lambda a, mc: f"gamemode {choice(a.get('mode'), ['survival', 'creative', 'adventure', 'spectator'], 'モード')} "
                               f"{player(a.get('player'), True)}"),
    "tp": ("people", "テレポート（人のところへ）",
           lambda a, mc: f"tp {player(a.get('player'), True)} {player(a.get('target'))}"),
    "give": ("people", "アイテムを渡す",
             lambda a, mc: f"give {player(a.get('player'), True)} {_item(a.get('item'))} {integer(a.get('count', 1), 1, 6400, '個数')}"),
    "xp": ("people", "経験値（レベル）をあげる",
           lambda a, mc: f"xp add {player(a.get('player'), True)} {integer(a.get('amount'), -10000, 10000, 'レベル')} levels"),
    "heal": ("people", "体力と空腹を回復する",
             lambda a, mc: f"effect give {player(a.get('player'), True)} minecraft:instant_health 1 10 true"),
    "clear_effects": ("people", "効果を消す", lambda a, mc: f"effect clear {player(a.get('player'), True)}"),
    "spawnpoint": ("people", "その人の復活地点を今いる場所にする", lambda a, mc: f"spawnpoint {player(a.get('player'))}"),
    "kick": ("manage", "キックする", lambda a, mc: f"kick {player(a.get('player'))} {text(a.get('reason'))}".strip()),
    "ban": ("manage", "BAN する（ずっと入れなくする）", lambda a, mc: f"ban {player(a.get('player'))} {text(a.get('reason'))}".strip()),
    "pardon": ("manage", "BAN を解除する", lambda a, mc: f"pardon {player(a.get('player'))}"),
    "ban_ip": ("manage", "IPアドレスで BAN する", lambda a, mc: f"ban-ip {_ip_or_player(a.get('target'))} {text(a.get('reason'))}".strip()),
    "pardon_ip": ("manage", "IPアドレスの BAN を解除する", lambda a, mc: f"pardon-ip {_ip(a.get('target'))}"),
    "op": ("manage", "OP（管理者）にする", lambda a, mc: f"op {player(a.get('player'))}"),
    "deop": ("manage", "OP をやめさせる", lambda a, mc: f"deop {player(a.get('player'))}"),
    "whitelist_add": ("manage", "ホワイトリストに追加", lambda a, mc: f"whitelist add {player(a.get('player'))}"),
    "whitelist_remove": ("manage", "ホワイトリストから外す", lambda a, mc: f"whitelist remove {player(a.get('player'))}"),
    "reload": ("manage", "データパックを読み込み直す", lambda a, mc: "reload"),
    "datapacks": ("manage", "データパックの一覧", lambda a, mc: "datapack list"),
}


def _raise(msg):
    raise BadInput(msg)


def _item(v):
    v = (v or "").strip().lower()
    if not ITEM_RE.match(v):
        raise BadInput("アイテムのIDがおかしいです（例: minecraft:diamond）")
    return v


def _ip(v):
    v = (v or "").strip()
    if not IP_RE.match(v):
        raise BadInput("IPアドレスの形がおかしいです")
    return v


def _ip_or_player(v):
    v = (v or "").strip()
    return v if IP_RE.match(v) else player(v)


def build(action: str, args: dict, mc: str) -> str:
    if action not in ACTIONS:
        raise BadInput("知らない操作です")
    return ACTIONS[action][2](args or {}, mc)


def catalog():
    return [{"id": k, "group": g, "label": label} for k, (g, label, _f) in ACTIONS.items()]


# ---------------- コマンドの返事をつかまえる ----------------

class Capture:
    """コマンドを送ってから少しの間に出たログを集める。"""

    def __init__(self, runner):
        self.r = runner
        self.lines: list[str] = []
        self.active = False
        self.hide = False      # True の間は、集めた行を画面のログに出さない（ゲームルールを読むときなど）
        self.lock = threading.Lock()
        runner.line_hooks.append(self._hook)

    def _hook(self, line):
        if self.active:
            self.lines.append(re.sub(r"^\[[^\]]*\](?:\s*\[[^\]]*\])?:?\s*", "", line))
            return self.hide
        return False

    def run(self, command: str, wait=1.2) -> list[str]:
        with self.lock:
            self.lines = []
            self.active = True
            try:
                if not self.r.send(command):
                    raise BadInput("サーバーが動いていません")
                end = time.time() + wait
                while time.time() < end:
                    time.sleep(0.1)
            finally:
                self.active = False
            return [line for line in self.lines if line.strip()][:60]

    def run_until(self, command: str, pattern: str, timeout=2.0, hide=True) -> list[str]:
        """返事に pattern が出たらすぐ終わる（たくさんのコマンドを速く送るとき用）。"""
        rx = re.compile(pattern, re.I)
        with self.lock:
            self.lines = []
            self.active = True
            old_hide, self.hide = self.hide, hide
            try:
                if not self.r.send(command):
                    raise BadInput("サーバーが動いていません")
                end = time.time() + timeout
                while time.time() < end:
                    if any(rx.search(x) for x in self.lines):
                        break
                    time.sleep(0.03)
            finally:
                self.active = False
                self.hide = old_hide
            return [line for line in self.lines if line.strip()][:60]


# ---------------- ゲームルール（窓口が使えないとき・コンソールで） ----------------

GR_RE = re.compile(r"(?:Gamerule|Game rule)\s+([\w:.\-/]+)\s+is (?:currently|now) set to:?\s*(\S+)", re.I)


def console_gamerules(cap: Capture, mc: str) -> list[dict]:
    new = versions.at_least(mc, versions.GAMERULE_REGISTRY)
    names = list(gamerules_meta.NEW) if new else list(gamerules_meta.OLD)
    with cap.lock:
        cap.lines = []
        cap.hide = True
        cap.active = True
        try:
            for n in names:
                cap.r.send(f"gamerule {n}")
                time.sleep(0.03)
            time.sleep(1.5)
        finally:
            cap.active = False
            cap.hide = False
        lines = list(cap.lines)
    found = {}
    for line in lines:
        m = GR_RE.search(line)
        if m:
            found[m.group(1)] = m.group(2)
    out = []
    for n in names:
        if n not in found:
            continue
        v = found[n]
        typ = "boolean" if v in ("true", "false") else "integer"
        out.append({"key": n, "type": typ, "value": (v == "true") if typ == "boolean" else int(re.sub(r"[^\d-]", "", v) or 0)})
    return out


# ---------------- BAN（サーバーが止まっているとき・ファイルで） ----------------

def _now():
    return datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


def read_bans(server_dir: str) -> dict:
    players = load_json(os.path.join(server_dir, "banned-players.json"), [])
    ips = load_json(os.path.join(server_dir, "banned-ips.json"), [])
    return {"players": [{"name": b.get("name"), "reason": b.get("reason", ""), "created": b.get("created", ""),
                         "expires": b.get("expires", "forever")} for b in players],
            "ips": [{"ip": b.get("ip"), "reason": b.get("reason", ""), "created": b.get("created", "")} for b in ips]}


def ban_file_add(server_dir, name, uuid, reason=""):
    path = os.path.join(server_dir, "banned-players.json")
    lst = [b for b in load_json(path, []) if b.get("uuid") != uuid]
    lst.append({"uuid": uuid, "name": name, "created": _now(), "source": "OneClickServerMC",
                "expires": "forever", "reason": reason or "Banned by an operator."})
    save_json(path, lst)


def ban_file_remove(server_dir, name):
    path = os.path.join(server_dir, "banned-players.json")
    save_json(path, [b for b in load_json(path, []) if (b.get("name") or "").lower() != name.lower()])


def ipban_file_add(server_dir, ip, reason=""):
    path = os.path.join(server_dir, "banned-ips.json")
    lst = [b for b in load_json(path, []) if b.get("ip") != ip]
    lst.append({"ip": ip, "created": _now(), "source": "OneClickServerMC", "expires": "forever",
                "reason": reason or "Banned by an operator."})
    save_json(path, lst)


def ipban_file_remove(server_dir, ip):
    path = os.path.join(server_dir, "banned-ips.json")
    save_json(path, [b for b in load_json(path, []) if b.get("ip") != ip])


# ---------------- 操作の記録 ----------------

AUDIT = "mcmh-audit.jsonl"
_audit_lock = threading.Lock()


def audit(server_dir: str, action: str, detail: str = ""):
    rec = {"t": time.time(), "action": action, "detail": detail[:500]}
    path = os.path.join(server_dir, AUDIT)
    with _audit_lock:
        try:
            if os.path.exists(path) and os.path.getsize(path) > 2_000_000:  # 大きくなったら古い半分を消す
                with open(path, encoding="utf-8") as f:
                    lines = f.readlines()
                with open(path, "w", encoding="utf-8") as f:
                    f.writelines(lines[len(lines) // 2:])
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass


def audit_read(server_dir: str, limit=300) -> list[dict]:
    path = os.path.join(server_dir, AUDIT)
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        r["when"] = datetime.datetime.fromtimestamp(r["t"]).strftime("%Y/%m/%d %H:%M:%S")
        out.append(r)
    return out
