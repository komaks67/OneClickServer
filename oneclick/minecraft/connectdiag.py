"""「友達が入れないとき」の診断。ボタン1つで、原因になりやすいところを順に調べて、直し方を日本語で出す。

調べること(上から順に):
 1. サーバーが動いているか / このPCの中で応答しているか / 人数がいっぱいでないか
 2. 友達に伝えるアドレスが決まっているか・古くなっていないか(回線のアドレスが変わった)
 3. インターネットの外から、本当にそのアドレスにつながるか(外のサービスに試してもらう)
    つながらないときは、原因を絞る(playit が止まっている / 共有アドレスの回線 / ファイアウォール・ポート)
 4. ホワイトリスト・マイクラのバージョン・MODの Java
 5. 最近のログに残っている「入れなかった理由」(conflicts のルールを使う)

ネットを使う部分は、テストで差しかえられるように、引数(deps)で受け取る。
"""
from __future__ import annotations

import ipaddress
import json
import os
import re

from . import conflicts, features, javareq

LEVEL_ORDER = {"error": 0, "warn": 1, "info": 2, "ok": 3}


def _item(level, title, detail="", fix="", action=None):
    d = features.item(level, title, detail, fix)
    if action:
        d["action"] = action
    return d


def _is_private(host: str) -> bool:
    try:
        a = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(a.is_private or a.is_loopback or a.is_link_local)


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _split(address: str):
    """(ホスト, ポート or None)。"""
    a = (address or "").strip()
    m = re.match(r"^\[([^\]]+)\](?::(\d+))?$", a)
    if m:
        return m.group(1), (int(m.group(2)) if m.group(2) else None)
    if a.count(":") == 1:
        h, p = a.split(":")
        return h, (int(p) if p.isdigit() else None)
    return a, None


def whitelist_count(server_dir: str) -> int:
    try:
        with open(os.path.join(server_dir, "whitelist.json"), encoding="utf-8") as f:
            return len(json.load(f) or [])
    except (OSError, ValueError):
        return 0


def _log_issues(prof, console_lines) -> list[dict]:
    text = []
    latest = os.path.join(prof.dir, "logs", "latest.log")
    try:
        text.append(conflicts._tail(latest, 250_000))
    except Exception:  # noqa: BLE001
        pass
    if console_lines:
        text.append("\n".join(console_lines[-600:]))
    out = []
    for i in conflicts.analyze_text("\n".join(text)):
        if i["level"] in ("error", "warn") and i.get("title") != "はっきりした原因は見つかりませんでした":
            out.append(i)
    return out[:5]


def run(prof, ctx: dict, deps: dict | None = None) -> dict:
    """ctx: running, port, playit_alive, own_gateway, console_lines, port_mismatch(bool)
    deps: ping(address)->dict, reach_test(address)->dict, global_ip()->str|None, shared_v4()->bool|None"""
    deps = deps or {}
    items: list[dict] = []
    port = int(ctx.get("port") or 25565)
    running = bool(ctx.get("running"))
    address = (prof.get("public_address") or "").strip()
    local_ok = False

    # ---- 1. サーバー本体 ----
    if not running:
        items.append(_item("error", "サーバーが動いていません",
                           "動いていないサーバーには、誰も入れません。", "「▶ 起動」を押して、「サーバーが起動しました」と出るまで待ってください。"))
    else:
        try:
            r = deps["ping"](f"127.0.0.1:{port}")
            local_ok = True
            items.append(_item("ok", "サーバーは、このPCの中では応答しています",
                               f"マイクラ {r.get('version', '?')}・参加 {r.get('online', 0)}/{r.get('max', 0)}人"))
            if r.get("max") and r.get("online", 0) >= r["max"]:
                items.append(_item("warn", "人数がいっぱいです", f"いま {r['online']}/{r['max']}人 なので、新しく入れません。",
                                   "「設定」タブの「最大人数」(max-players)を増やして、サーバーを再起動してください。"))
        except Exception:  # noqa: BLE001
            items.append(_item("error", "サーバーは動いているのに、応答がありません",
                               "起動の途中か、固まっている可能性があります。",
                               "1〜2分待って、もう一度診断してください。それでもだめなら「困ったとき診断」を使ってください。"))

    # ---- 2. アドレス ----
    host, aport = _split(address) if address else ("", None)
    if not address:
        items.append(_item("warn", "友達に伝えるアドレスが、まだ決まっていません",
                           "アドレスがないと、友達は入り先が分かりません。",
                           "「友達を呼ぶ」タブで、playit.gg(おすすめ)などでアドレスを作ってください。",
                           {"type": "tab", "tab": "public", "label": "「友達を呼ぶ」を開く"}))
    elif _is_private(host):
        items.append(_item("info", "同じ家（同じ Wi-Fi）の人だけ入れるアドレスです",
                           f"いまのアドレス {address} は、家の中だけで使えます。",
                           "遠くの友達を呼ぶときは、「友達を呼ぶ」タブで playit.gg などを使ってください。",
                           {"type": "tab", "tab": "public", "label": "「友達を呼ぶ」を開く"}))
    else:
        if ctx.get("port_mismatch"):
            items.append(_item("error", "アドレスのポート番号が、サーバーと違います",
                               f"サーバーは {port} 番で動いていますが、友達に伝えるアドレスは別の番号です。",
                               f"アドレスの後ろを「:{port}」にして保存し直してください（25565 のときは何もつけません）。",
                               {"type": "tab", "tab": "public", "label": "「友達を呼ぶ」を開く"}))
        if _is_ip(host) and host != "::1":
            now = None
            try:
                now = deps["global_ip"]() if "global_ip" in deps else None
            except Exception:  # noqa: BLE001
                now = None
            if now and ":" not in host and host != now:
                items.append(_item("error", "アドレスの数字が、いまのインターネット上の住所と違います",
                                   f"伝えているのは {host} ですが、いまこの家の住所は {now} です。回線をつなぎ直したり、時間がたつと変わることがあります。",
                                   "「友達を呼ぶ」タブでアドレスを作り直して、友達に新しいアドレスを伝えてください。playit.gg なら、アドレスは変わりません。",
                                   {"type": "tab", "tab": "public", "label": "「友達を呼ぶ」を開く"}))

        # ---- 3. 外から届くか ----
        if running and local_ok:
            try:
                rt = deps["reach_test"](address)
            except Exception:  # noqa: BLE001
                rt = None
                items.append(_item("info", "外からのテストができませんでした",
                                   "外のサービス(mcsrvstat.us)につながらなかったか、混み合っています。",
                                   "少し待ってから、もう一度診断してください。"))
            if rt is not None:
                if rt.get("online"):
                    items.append(_item("ok", "インターネットの外から、つながります",
                                       f"外のサービスから {address} を試して、サーバーが見えました。入れないのは、別の原因かもしれません（下を見てください）。"))
                else:
                    items.extend(_unreachable(host, port, ctx, deps))

    # ---- 4. 入れる人・バージョン・MOD ----
    props = prof.props() if hasattr(prof, "props") else {}
    if str(props.get("white-list", "false")).lower() == "true":
        n = whitelist_count(prof.dir)
        items.append(_item("warn" if n == 0 else "info", "ホワイトリストが ON です",
                           f"リストにある人（いま {n}人）だけが入れます。ない人は「You are not whitelisted」と出て、入れません。",
                           "友達の名前を、「メンバー・BAN」タブのホワイトリストに入れてください。",
                           {"type": "tab", "tab": "members", "label": "「メンバー・BAN」を開く"}))
    mc, loader = prof.get("mc") or "?", prof.get("loader") or ""
    modded = loader in ("fabric", "forge", "neoforge", "quilt")
    items.append(_item("info", f"友達は、マイクラ {mc}" + (f"（{_loader_label(loader)}）" if modded else "") + " で入ります",
                       "バージョンが違うと「Outdated client」や「Outdated server」と出て入れません。"
                       + ("MODも同じセットが必要です。" if modded else ""),
                       ("友達には、友達用パックの「参加する」ボタンを使ってもらうのが一番かんたんです。" if modded else "")))
    try:
        ji = javareq.issue(prof)
        if ji:
            items.append(_item("warn", ji["title"], ji["detail"], ji["fix"]))
    except Exception:  # noqa: BLE001
        pass

    # ---- 5. ログに残っている理由 ----
    try:
        for i in _log_issues(prof, ctx.get("console_lines")):
            items.append(_item(i["level"], "ログ: " + i["title"], i.get("detail") or "", i.get("fix") or ""))
    except Exception:  # noqa: BLE001
        pass

    items.sort(key=lambda i: LEVEL_ORDER.get(i["level"], 4))
    bad = [i for i in items if i["level"] == "error"]
    warn = [i for i in items if i["level"] == "warn"]
    if bad:
        head = f"原因らしいところが {len(bad)}つ 見つかりました"
    elif warn:
        head = f"気をつけたいところが {len(warn)}つ あります"
    else:
        head = "サーバー側に、はっきりした問題は見つかりませんでした"
    return {"items": items, "headline": head, "errors": len(bad), "warnings": len(warn), "running": running}


def _loader_label(loader: str) -> str:
    return {"fabric": "Fabric", "forge": "Forge", "neoforge": "NeoForge", "quilt": "Quilt"}.get(loader, loader)


def _unreachable(host: str, port: int, ctx: dict, deps: dict) -> list[dict]:
    """外からつながらなかったとき、原因を絞る。"""
    out = []
    domain = bool(host) and not _is_ip(host)
    playit_like = domain and bool(re.search(r"(joinmc\.link|playit\.|ply\.gg)", host, re.I))
    if playit_like:
        if not ctx.get("playit_alive"):
            out.append(_item("error", "playit が動いていません",
                             "アドレスは playit.gg のものですが、playit が止まっているので、外からつながりません。",
                             "「友達を呼ぶ」タブで「▶ playit を起動」を押してください。黒い画面は、遊んでいる間は閉じないでください。",
                             {"type": "tab", "tab": "public", "label": "「友達を呼ぶ」を開く"}))
            return out
        out.append(_item("error", "外から、つながりません（playit は動いています）",
                         "playit のトンネルの行き先ポートが、サーバーのポート番号と違う可能性が高いです。",
                         f"playit.gg のサイトで、トンネルの Local Port を {port} にしてください。",
                         {"type": "tab", "tab": "public", "label": "「友達を呼ぶ」を開く"}))
        return out
    shared = None
    try:
        shared = deps["shared_v4"]() if "shared_v4" in deps else None
    except Exception:  # noqa: BLE001
        shared = None
    if shared:
        out.append(_item("error", "この回線は、ポートを開けても外から入れない種類です",
                         "マンションの回線や、v6プラスなどの回線は、1つの住所を何軒かで使っているため、ポートを開けても外から届きません。",
                         "playit.gg を使ってください。ルーターの設定はいりません。",
                         {"type": "tab", "tab": "public", "label": "「友達を呼ぶ」を開く"}))
        return out
    out.append(_item("error", "外から、サーバーにつながりません",
                     f"サーバーはこのPCの中では動いています。外から届かないのは、ポート {port} が開いていないか、ファイアウォールが止めているのが原因のことが多いです。",
                     "①「ファイアウォールを許可」を押す ②「友達を呼ぶ」タブの「ポートを開ける」を押す ③だめなら playit.gg を使う、の順に試してください。",
                     {"type": "firewall", "label": "ファイアウォールを許可"}))
    out.append(_item("info", "うまくいかない回線のときは", "ルーターが自動のポート開放に対応していないと、外からは入れません。",
                     "playit.gg なら、ルーターの設定なしで、ほとんどの回線で使えます。",
                     {"type": "tab", "tab": "public", "label": "「友達を呼ぶ」を開く"}))
    return out
