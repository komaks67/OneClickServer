"""DuckDNS（無料の「変わらないアドレス」サービス）と連携する。

家のインターネットの住所（グローバルIP）は、ときどき変わる。DuckDNS で
「◯◯.duckdns.org」という名前を作っておき、このソフトが10分ごとに今の住所を知らせることで、
友達はいつも同じアドレス（◯◯.duckdns.org:25565）で入れる。
※ ポート開放（または UPnP）は別に必要。playit.gg を使うなら DuckDNS はいらない。
"""
from __future__ import annotations

import re
import time

from . import net

UPDATE_URL = "https://www.duckdns.org/update"
SUB_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,61}[a-z0-9]?$")
TOKEN_RE = re.compile(r"^[0-9a-fA-F-]{36}$")
INTERVAL = 10 * 60

state = {"last": 0.0, "ok": None, "message": "", "ip": None}


def normalize(domain: str) -> str:
    d = (domain or "").strip().lower()
    d = re.sub(r"^https?://", "", d).split("/")[0]
    if d.endswith(".duckdns.org"):
        d = d[: -len(".duckdns.org")]
    return d


def validate(domain: str, token: str) -> tuple[str, str]:
    sub = normalize(domain)
    if not SUB_RE.match(sub):
        raise ValueError("DuckDNS の名前がおかしいです（英小文字・数字・- だけ。例: mymcserver）")
    tok = (token or "").strip()
    if not TOKEN_RE.match(tok):
        raise ValueError("DuckDNS の token がおかしいです（DuckDNS のページに出ている 36文字の英数字）")
    return sub, tok


def update(domain: str, token: str) -> bool:
    """今の住所を DuckDNS に知らせる。OK なら True。"""
    sub, tok = validate(domain, token)
    try:
        body = net.request(f"{UPDATE_URL}?domains={sub}&token={tok}&ip=&verbose=true", timeout=15, retries=1)
    except net.NetError as e:
        state.update(last=time.time(), ok=False, message=f"DuckDNS につながりませんでした（{e.__class__.__name__}）")
        return False
    text = body.decode("utf-8", "replace").strip()
    ok = text.startswith("OK")
    lines = text.splitlines()
    state.update(last=time.time(), ok=ok, ip=lines[1] if ok and len(lines) > 1 else None,
                 message="今の住所を知らせました" if ok else "DuckDNS にことわられました（名前か token がまちがっています）")
    return ok


def due(now: float | None = None) -> bool:
    return (now or time.time()) - state["last"] >= INTERVAL
