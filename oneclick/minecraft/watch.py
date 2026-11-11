"""見張り：Discord への通知・デスクトップ通知・荒らし（ホワイトリスト外）の検知・プレイ時間の記録。"""
from __future__ import annotations

import datetime
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time

from . import net
from .paths import load_json, save_json

# ============================================================ Discord（Webhook）

WEBHOOK_RE = re.compile(r"^https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api/webhooks/\d{5,30}/[\w\-]{20,100}$")
COLORS = {"start": 0x5FA83E, "stop": 0x8B8B8B, "crash": 0xD9443A, "join": 0x4FD6E0, "leave": 0x6B6F78,
          "intrusion": 0xF2C94C, "lag": 0xE0B23A, "backup": 0x5B86E5, "test": 0x4FD6E0}
DEFAULT_NAME = "Minecraft_Server【管理者】"
settings_ref: dict = {}   # api が settings を入れる（送り主の名前を読むため）
_q: queue.Queue = queue.Queue(maxsize=200)


_IPV4_HIDE = re.compile(r"(^|[^\w.-])((?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d))(?::\d+)?(?![\w-]|\.\d)")


def hide_ips(text: str) -> str:
    """通知(Discord・デスクトップ)に、IPアドレスが入らないようにする。127.x(自分のPC)はそのまま。"""
    return _IPV4_HIDE.sub(lambda m: m.group(0) if m.group(2).startswith("127.") else m.group(1) + "(IPは非表示)", text or "")


def sender_name() -> str:
    """Discord に出る送り主の名前（1〜80文字。Discord の決まりで「discord」などは使えない）。"""
    n = (settings_ref.get("discord_name") or DEFAULT_NAME).strip()[:80]
    return n or DEFAULT_NAME
_sender = {"thread": None}
last_error = {"text": None, "at": 0.0}


def valid_webhook(url: str) -> bool:
    return bool(WEBHOOK_RE.match((url or "").strip()))


_IPV4 = re.compile(r"(?<![\d.])(\d{1,3})\.(\d{1,3})\.\d{1,3}\.\d{1,3}(?![\d.])")
_IPV6 = re.compile(r"(?<![\w:])([0-9a-fA-F]{1,4}):([0-9a-fA-F]{0,4})(?::[0-9a-fA-F]{0,4}){2,6}(?![\w:])")


def mask_ips(text: str) -> str:
    """Discord に送る文の中の IP アドレスを、後ろの数字をかくして送る（他人の情報を、広く見せないため）。
    完全な IP は、ソフトの画面（自分のPCの中）だけに残る。"""
    text = _IPV4.sub(lambda m: f"{m.group(1)}.{m.group(2)}.xxx.xxx", text or "")
    return _IPV6.sub(lambda m: f"{m.group(1)}:{m.group(2)}:xxxx:…", text)


def discord_send(url: str, title: str, text: str = "", kind: str = "test", server: str = "") -> None:
    """あとで順番に送る（Discord の回数制限にかからないように、1秒に1回まで）。"""
    if not valid_webhook(url):
        return
    title, text = mask_ips(title), mask_ips(text)
    payload = {"username": sender_name(),
               "embeds": [{"title": title[:250], "description": text[:3500], "color": COLORS.get(kind, 0x888888),
                           "footer": {"text": server[:100]} if server else None,
                           "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()}],
               "allowed_mentions": {"parse": []}}  # @everyone などで人を呼ばない
    if not payload["embeds"][0]["footer"]:
        del payload["embeds"][0]["footer"]
    try:
        _q.put_nowait((url, payload))
    except queue.Full:
        return
    if not (_sender["thread"] and _sender["thread"].is_alive()):
        t = threading.Thread(target=_send_loop, daemon=True)
        _sender["thread"] = t
        t.start()


def discord_send_now(url: str, title: str, text: str = "") -> None:
    """テスト用：すぐ送って、失敗したらエラーにする。"""
    if not valid_webhook(url):
        raise ValueError("Discord の Webhook URL の形がおかしいです（https://discord.com/api/webhooks/… をそのまま貼ってください）")
    payload = {"username": sender_name(), "allowed_mentions": {"parse": []},
               "embeds": [{"title": title, "description": text, "color": COLORS["test"]}]}
    try:
        net.request(url, data=payload, method="POST", timeout=15, retries=0)
    except net.NetError as e:
        msg = str(e).replace(url, "（Webhook）")
        raise ValueError(f"Discord に送れませんでした: {msg}") from e


def _send_loop():
    while True:
        try:
            url, payload = _q.get(timeout=60)
        except queue.Empty:
            return
        try:
            net.request(url, data=payload, method="POST", timeout=15, retries=1)
            last_error["text"] = None
        except net.NetError as e:
            last_error.update(text=str(e).replace(url, "（Webhook）"), at=time.time())
        time.sleep(1.1)


# ============================================================ デスクトップ通知（Windows の右下に出るお知らせ）

def desktop_notify(title: str, text: str) -> None:
    if not sys.platform.startswith("win"):
        return
    t = title.replace("'", "’")[:60]
    b = text.replace("'", "’")[:200]
    ps = ("Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; "
          "$n = New-Object System.Windows.Forms.NotifyIcon; $n.Icon = [System.Drawing.SystemIcons]::Information; "
          f"$n.Visible = $true; $n.ShowBalloonTip(8000, '{t}', '{b}', 'Info'); Start-Sleep -Seconds 9; $n.Dispose()")
    try:
        subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError:
        pass


# ============================================================ ホワイトリスト外・BAN された人のアクセス

REJECT_RE = re.compile(r"(You are not white-?listed on this server|You are banned from this server|"
                       r"Your IP address is banned)", re.I)
NAME_RES = [re.compile(r"name=([A-Za-z0-9_]{2,16})"),
            re.compile(r"Disconnecting\s+([A-Za-z0-9_]{2,16})\s*\("),
            re.compile(r"\]:\s*([A-Za-z0-9_]{2,16})\s*\(/[^)]*\)\s*lost connection"),
            re.compile(r"\]:\s*([A-Za-z0-9_]{2,16})\s+lost connection")]
ADDR_RE = re.compile(r"/(\d{1,3}(?:\.\d{1,3}){3}|\[?[0-9a-f:]+\]?):(\d{1,5})", re.I)
LOGIN_RE = re.compile(r"\]:\s*([A-Za-z0-9_]{2,16})\[/(\d{1,3}(?:\.\d{1,3}){3}|[0-9a-f:\[\]]+):(\d{1,5})\] logged in", re.I)


def parse_reject(line: str) -> dict | None:
    m = REJECT_RE.search(line)
    if not m:
        return None
    name = None
    for rx in NAME_RES:
        nm = rx.search(line)
        if nm:
            name = nm.group(1)
            break
    am = ADDR_RE.search(line)
    reason = "ホワイトリストにない" if "white" in m.group(1).lower() else "BAN されている"
    return {"name": name, "ip": am.group(1) if am else None, "port": int(am.group(2)) if am else None,
            "reason": reason, "time": time.time()}


class Intrusions:
    """入ろうとして断られた人の記録（同じ人の通知は10分に1回まで）。"""

    FILE = "mcmh-intrusions.json"

    def __init__(self, server_dir: str):
        self.path = os.path.join(server_dir, self.FILE)
        self.items: list[dict] = load_json(self.path, [])
        self._last_notice: dict[str, float] = {}
        self.lock = threading.Lock()

    def add(self, ev: dict) -> bool:
        """記録する。通知したほうがいいとき True。"""
        key = ev.get("name") or ev.get("ip") or "?"
        with self.lock:
            same = next((x for x in self.items if (x.get("name") or x.get("ip")) == key and x.get("kind") == ev.get("kind")), None)
            if same:
                same["count"] = same.get("count", 1) + 1
                same["last"] = ev["time"]
                if ev.get("ip"):
                    same["ip"] = ev["ip"]
            else:
                self.items.insert(0, {**ev, "count": 1, "first": ev["time"], "last": ev["time"]})
                self.items = self.items[:100]
            save_json(self.path, self.items)
            notify = ev["time"] - self._last_notice.get(key, 0) > 600
            if notify:
                self._last_notice[key] = ev["time"]
            return notify

    def clear(self):
        with self.lock:
            self.items = []
            save_json(self.path, self.items)


# ============================================================ プレイ時間

JOIN_RE = re.compile(r"\]:\s*([A-Za-z0-9_]{2,16}) joined the game")
LEFT_RE = re.compile(r"\]:\s*([A-Za-z0-9_]{2,16}) left the game")


class Playtime:
    """誰が・いつ・何分遊んだか（サーバーごと）。"""

    FILE = "mcmh-playtime.json"

    def __init__(self, server_dir: str):
        self.path = os.path.join(server_dir, self.FILE)
        self.data = load_json(self.path, {"sessions": [], "open": {}})
        self.lock = threading.Lock()

    def join(self, name: str, t: float | None = None):
        with self.lock:
            self.data["open"][name] = t or time.time()
            self._save()

    def leave(self, name: str, t: float | None = None):
        with self.lock:
            start = self.data["open"].pop(name, None)
            if start:
                end = t or time.time()
                if end > start:
                    self.data["sessions"].append([name, round(start), round(end)])
                    self.data["sessions"] = self.data["sessions"][-5000:]
            self._save()

    def close_all(self, t: float | None = None):
        for name in list(self.data.get("open", {})):
            self.leave(name, t)

    def _save(self):
        save_json(self.path, self.data)

    def summary(self, days: int = 30, now: float | None = None) -> dict:
        now = now or time.time()
        since = now - days * 86400
        with self.lock:
            sessions = [s for s in self.data["sessions"] if s[2] >= since]
            sessions += [[n, st, now] for n, st in self.data.get("open", {}).items()]
        per: dict[str, dict] = {}
        daily: dict[str, float] = {}
        hours = [0.0] * 24
        for name, st, en in sessions:
            st = max(st, since)
            p = per.setdefault(name, {"name": name, "minutes": 0.0, "sessions": 0, "last": 0, "days": set()})
            p["minutes"] += (en - st) / 60
            p["sessions"] += 1
            p["last"] = max(p["last"], en)
            t = st
            while t < en:  # 日ごと・時間ごとに分ける
                lt = time.localtime(t)
                nxt = min(en, t + 3600 - (lt.tm_min * 60 + lt.tm_sec))
                day = time.strftime("%m/%d", lt)
                daily[day] = daily.get(day, 0) + (nxt - t) / 60
                hours[lt.tm_hour] += (nxt - t) / 60
                p["days"].add(day)
                t = nxt
        ranking = sorted(per.values(), key=lambda p: -p["minutes"])
        for p in ranking:
            p["minutes"] = round(p["minutes"])
            p["days"] = len(p["days"])
            p["online"] = p["name"] in self.data.get("open", {})
        days_list = []
        for i in range(min(days, 14) - 1, -1, -1):
            d = time.strftime("%m/%d", time.localtime(now - i * 86400))
            days_list.append({"day": d, "minutes": round(daily.get(d, 0))})
        return {"ranking": ranking, "daily": days_list, "hours": [round(h) for h in hours],
                "total_minutes": round(sum(p["minutes"] for p in ranking)), "days": days}


def fmt_minutes(m: float) -> str:
    m = int(m)
    return f"{m // 60}時間{m % 60}分" if m >= 60 else f"{m}分"


def dumps(o) -> str:
    return json.dumps(o, ensure_ascii=False)
