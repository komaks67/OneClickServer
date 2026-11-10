"""Discord のボット(4ゲーム共通)。ホーム画面から1回設定すれば、マイクラ・ARK・Palworld・Terraria を同じボットで操作できる。

- チャンネルに「▶ 起動」「■ 停止」「📊 状態」のボタンのパネルを、サーバーごとに置く
- 起動・停止・落ちたときのお知らせは、パネルのスレッドに「カード」で出る
- 設定(トークン・チャンネル・操作できる人・パネルの場所)は、マイクラ側の settings.json の
  `discord_bot` に入る(トークンは Windows の DPAPI で暗号化される)
- サーバーの目印: マイクラは `mc:<フォルダ名>`、ほかは ゲームの key(ark_asa など)
"""
from __future__ import annotations

import os
import re
import threading

from . import discordbot, registry, steamgames

_bot: discordbot.Bot | None = None
_lock = threading.Lock()
_last: dict = {}
_ID = re.compile(r"^\d{15,22}$")


class HubError(Exception):
    """画面にそのまま見せる、分かりやすいエラー。"""


# ============================================================ 設定の読み書き
def _store() -> dict:
    return registry.mc_api().settings


def cfg() -> dict:
    c = _store().get("discord_bot")
    return c if isinstance(c, dict) else {}


def _save_cfg(c: dict):
    api = registry.mc_api()
    api.settings["discord_bot"] = c
    api.save_settings(api.settings)


def _panels(c=None) -> dict:
    p = (c if c is not None else cfg()).get("panels")
    return p if isinstance(p, dict) else {}


# ============================================================ サーバー一覧と、ゲームごとのつなぎ口
def servers() -> list:
    """操作できるサーバー [{sid, game, name}]。"""
    out = []
    try:
        api = registry.mc_api()
        for p in api.server.list_profiles():
            out.append({"sid": "mc:" + os.path.basename(p.dir), "game": "minecraft", "name": p.name})
    except Exception:  # noqa: BLE001
        pass
    for cls in steamgames.CLASSES:
        if registry.steam_exists(cls.KEY):
            out.append({"sid": cls.KEY, "game": cls.KEY, "name": steamgames.get(cls.KEY).display_name()})
    return out


def _find(key: str):
    for s in servers():
        if discordbot.server_key(s["sid"]) == key:
            return s["sid"]
    return None


def _name(sid: str) -> str:
    for s in servers():
        if s["sid"] == sid:
            return s["name"]
    return sid


def _is_mc(sid: str) -> bool:
    return sid.startswith("mc:")


def _mc_id(sid: str) -> str:
    return sid[3:]


# ============================================================ ボットの起動・つなぎ直し
def restart():
    """設定が変わったとき・ソフトを開いたときに、ボットをつなぎ直す。"""
    global _bot
    with _lock:
        if _bot:
            _bot.stop()
            _bot = None
        tok = (cfg().get("token") or "").strip()
        if tok:
            _bot = discordbot.Bot(tok, _interaction).start()


def stop():
    global _bot
    with _lock:
        if _bot:
            _bot.stop()
            _bot = None


def _allowed(inter: dict) -> bool:
    u = (inter.get("member") or {}).get("user") or inter.get("user") or {}
    allow = {str(x).strip().lower() for x in (cfg().get("users") or []) if str(x).strip()}
    return bool(allow) and (str(u.get("id") or "") in allow or str(u.get("username") or "").lower() in allow)


# ============================================================ お知らせ(カード)
def post(sid: str, title: str, desc: str = "", kind: str = "info", fields=None):
    """そのサーバーのパネルのスレッドに、カードを出す(パネルがなければ何もしない)。"""
    b = _bot
    try:
        panel = _panels().get(sid) or {}
    except Exception:  # noqa: BLE001
        return
    if not (b and panel.get("message")):
        return
    embed = discordbot.card(title, desc, kind, fields, footer=_name(sid))

    def work():
        try:
            b.send(panel.get("thread") or panel["channel"], embed=embed)
        except discordbot.DiscordError as e:
            _console(sid, f"⚠ Discord に書き込めませんでした: {e}")
    threading.Thread(target=work, daemon=True).start()


def post_alert(title: str, desc: str = "", kind: str = "bad"):
    """パネルを置いたチャンネルに、PC 全体のお知らせ(ディスク・重さ)を出す。"""
    b, ch = _bot, cfg().get("channel")
    if not (b and ch):
        return
    embed = discordbot.card(title, desc, kind, None, footer="OneClickServer")

    def work():
        try:
            b.send(ch, embed=embed)
        except discordbot.DiscordError as e:
            print(f"⚠ Discord に警告を書けませんでした: {e}", flush=True)
    threading.Thread(target=work, daemon=True).start()


# ---- 「いま誰がいるか」を、1枚のカードで書きかえる(入った・出たのたびに通知を増やさない)
_presence: dict = {}          # sid -> {"names": set, "recent": [(時刻, 名前, 入/出)], "timer": Timer}
_PRESENCE_WAIT = 3.0          # 続けて入退出しても、3秒まとめて1回だけ書きかえる


def presence_enabled(sid: str) -> bool:
    return bool(_bot and (_panels().get(sid) or {}).get("message"))


def presence_card(sid: str, names, recent) -> dict:
    names = sorted(set(names), key=str.lower)
    desc = ("、".join(names) + f"\n**いま {len(names)} 人**") if names else "**いま 0 人**(だれもいません)"
    fields = []
    if recent:
        fields.append(("さいきん", "\n".join(f"`{t}` {n} が{'入りました' if k == 'in' else '出ました'}"
                                           for t, n, k in recent[-6:][::-1]), False))
    return discordbot.card("👥 いま遊んでいる人", desc, "ok" if names else "stop", fields, footer=_name(sid))


def presence_event(sid: str, name: str | None, joined: bool | None, names=None):
    """入った(True)・出た(False)・全員いなくなった(None) を受け取り、カードを書きかえる予約をする。"""
    if not presence_enabled(sid):
        return
    import time as _t
    with _lock:
        st = _presence.setdefault(sid, {"names": set(), "recent": [], "timer": None})
        if names is not None:
            st["names"] = set(names)
        if joined is True and name:
            st["names"].add(name)
            st["recent"].append((_t.strftime("%H:%M"), name, "in"))
        elif joined is False and name:
            st["names"].discard(name)
            st["recent"].append((_t.strftime("%H:%M"), name, "out"))
        elif joined is None:
            st["names"].clear()
        st["recent"] = st["recent"][-20:]
        if st["timer"]:
            st["timer"].cancel()
        st["timer"] = threading.Timer(_PRESENCE_WAIT, _presence_flush, args=(sid,))
        st["timer"].daemon = True
        st["timer"].start()


def _presence_flush(sid: str):
    b = _bot
    panel = _panels().get(sid) or {}
    st = _presence.get(sid)
    if not (b and st and panel.get("message")):
        return
    ch = panel.get("thread") or panel["channel"]
    embed = presence_card(sid, st["names"], st["recent"])
    try:
        mid = st.get("message")
        if mid:
            try:
                b.edit_message(ch, mid, embed=embed)
                return
            except discordbot.DiscordError:
                pass                          # 消されていたら、作りなおす
        st["message"] = b.send(ch, embed=embed).get("id")
    except discordbot.DiscordError as e:
        _console(sid, f"⚠ Discord に書き込めませんでした: {e}")


def _console(sid: str, text: str):
    """そのサーバーのログ画面に一行出す。"""
    try:
        if _is_mc(sid):
            api = registry.mc_api()
            api.consoles.setdefault(_mc_id(sid), api.Console()).add(text)
        else:
            steamgames.get(sid).log(text)
    except Exception:  # noqa: BLE001
        pass


def mc_state_changed(id: str, state: str, r):
    """マイクラの状態が変わったとき(minecraft/api.py から呼ばれる)。"""
    sid = "mc:" + id
    if state in ("stopped", "starting"):
        presence_event(sid, None, None)
    if _last.get(id) == state:        # 人が入った・出たときにも呼ばれるので、本当に変わったときだけ知らせる
        return
    _last[id] = state
    if not _bot:
        return
    api = registry.mc_api()
    p = api._prof(id)
    if state == "starting":
        post(sid, "⏳ 起動しています", "「起動完了」まで、数分かかることがあります。", "wait")
    elif state == "running":
        post(sid, "🟢 起動しました", "友達が入れる状態です。", "ok",
             [("バージョン", f"マイクラ {p.get('mc')}・{p.loader_label()}", True)]
             + ([("アドレス", f"`{p.get('public_address')}`", True)] if p.get("public_address") else []))
    elif state == "stopped":
        if r and r.last_exit and r.last_exit.get("crashed"):
            post(sid, "🔴 起動できませんでした" if r.last_exit.get("during_start") else "🔴 サーバーが落ちました",
                 "原因を調べています…", "bad")
        elif r and r.last_exit is not None:
            post(sid, "⚪ 止まりました", "ワールドは保存されています。", "stop")


def mc_crash(id: str, res: dict):
    """マイクラが落ちた原因が分かったとき。"""
    issues = [i for i in (res.get("issues") or []) if isinstance(i, dict)][:3]
    fields = [(f"{n}. {i.get('title') or '原因'}", i.get("fix") or "ソフトの画面の「困ったとき診断」で調べてください。", False)
              for n, i in enumerate(issues, 1)]
    desc = "" if issues else "原因は、記録からはわかりませんでした。ソフトの画面の「困ったとき診断」で調べてください。"
    if res.get("memory_raised"):
        desc += ("\n" if desc else "") + "メモリを自動で増やしました。もう一度「▶ 起動」を押してください。"
    if res.get("gave_up"):
        desc += ("\n" if desc else "") + "何度も落ちたので、自動の再起動は止めました。"
    post("mc:" + id, "🔎 原因の見当", desc, "bad", fields)


def steam_event(key: str, event: str, text: str):
    """ARK・Palworld・Terraria の起動・停止・落ちたとき(steamgames/manager.py の notify から呼ばれる)。"""
    if not _bot:
        return
    if event == "start":
        if "テスト" in text:
            return
        post(key, "⏳ 起動しています", "ワールドの読み込みに、数分かかることがあります。", "wait")
    elif event == "stop":
        post(key, "⚪ 止まりました", "ワールドは保存されています。", "stop")
    elif event == "crash":
        post(key, "🔴 サーバーが落ちました", text, "bad")


# ============================================================ 状態カード・起動・停止
def status_card(sid: str) -> dict:
    return _mc_status(sid) if _is_mc(sid) else _steam_status(sid)


def _head(state: str):
    return {"running": ("🟢 動いています（遊べます）", "ok"), "starting": ("⏳ 起動しています…", "wait"),
            "stopping": ("⏳ 止めています…", "wait"), "busy": ("⏳ 作業中です…", "wait")}.get(state, ("⚪ 止まっています", "stop"))


def _names_field(names):
    names = sorted(set(names), key=str.lower)
    return ("今いる人", ("、".join(names) + f"（{len(names)}人）") if names else "誰もいません（0人）", False)


def _mc_status(sid):
    api = registry.mc_api()
    id = _mc_id(sid)
    p = api._prof(id)
    r = api.runners.get(id)
    state = r.state if r else "stopped"
    head, kind = _head(state)
    fields = [("バージョン", f"マイクラ {p.get('mc')}・{p.loader_label()}", True),
              ("playit", "動いています" if api.playit_proc and api.playit_proc.poll() is None else "止まっています", True)]
    if state == "running":
        fields.append(_names_field(r.players if r else []))
    if p.get("public_address"):
        fields.append(("アドレス", f"`{p.get('public_address')}`", False))
    return discordbot.card(f"{p.name}　{head}", "", kind, fields, footer=p.name)


def _steam_status(sid):
    m = steamgames.get(sid)
    if m.busy:
        state = "busy"
    else:
        state = "running" if m.running() else "stopped"
    head, kind = _head(state)
    fields = [("ゲーム", m.NAME, True), ("playit", "動いています" if m.playit_running() else "止まっています", True)]
    if state == "running":
        try:
            n = m.player_count()
        except Exception:  # noqa: BLE001
            n = None
        fields.append(("今いる人", f"{n}人" if n is not None else "人数は、このゲームでは調べられません", False))
    lan = m.state().get("lan")
    if lan:
        fields.append(("同じWi-Fiのアドレス", f"`{lan}`", False))
    return discordbot.card(f"{m.display_name()}　{head}", "", kind, fields, footer=m.display_name())


def start(sid: str) -> str:
    """ボタンで起動。起動できないときは、理由を文章で返す。"""
    return _mc_start(sid) if _is_mc(sid) else _steam_start(sid)


def stop_server(sid: str) -> str:
    return _mc_stop(sid) if _is_mc(sid) else _steam_stop(sid)


def _mc_start(sid):
    api = registry.mc_api()
    id = _mc_id(sid)
    r = api.runners.get(id)
    if r and r.running:
        return "もう動いています（" + ("起動完了" if r.state == "running" else "起動の途中") + "）。"
    try:
        errs = [i for i in api.preflight(id).get("items", []) if i.get("level") == "error"]
    except Exception:  # noqa: BLE001
        errs = []
    if errs:
        return "⚠ **起動できません。** 理由:\n" + "\n".join(
            f"・{i.get('title')}" + (f"\n　直し方: {i.get('fix')}" if i.get("fix") else "") for i in errs[:4])
    try:
        res = api.server_start(id)
    except api.UserError as e:
        return f"⚠ **起動できません。** 理由: {e}"
    if res.get("need_eula"):
        return "⚠ **起動できません。** 理由: まだ利用規約（EULA）に同意していません。ソフトの画面で、一度「▶ 起動」を押してください。"
    if res.get("need_deps"):
        names = "、".join(str(d.get("title") or d.get("id") or "?") if isinstance(d, dict) else str(d) for d in res["need_deps"][:5])
        return f"⚠ **起動できません。** 理由: 前提MODが足りません（{names}）。ソフトの画面で「▶ 起動」を押して、案内に従ってください。"
    if res.get("stale"):
        return ("⚠ **起動できません。** 理由: 前のサーバー（java）が、裏で残っています。"
                "ソフトの画面で「▶ 起動」を押すと、終了させてから起動できます（またはタスクマネージャーで java を終了します）。")
    out = "▶ **起動を始めました。**「起動完了」になったら、スレッドでお知らせします。"
    if api.playit_proc and api.playit_proc.poll() is None:
        out += "\nplayit は、もう動いています。"
    else:
        try:
            api.playit_proc = api.network.start_playit(lambda *_: None)
            out += "\nplayit も起動しました。"
        except Exception as e:  # noqa: BLE001
            out += f"\n⚠ playit は起動できませんでした（{str(e)[:120]}）。"
    return out


def _mc_stop(sid):
    api = registry.mc_api()
    id = _mc_id(sid)
    r = api.runners.get(id)
    if not (r and r.running):
        return "サーバーは、もう止まっています。"
    api.server_stop(id)
    return "■ **停止を始めました。** ワールドを保存してから止まります。止まったら、スレッドでお知らせします。"


def _steam_start(sid):
    m = steamgames.get(sid)
    res = m.start()
    if not res.get("ok"):
        return f"⚠ **起動できません。** 理由: {res.get('message') or '不明'}"
    out = "▶ **起動を始めました。** 止まったときなどは、スレッドでお知らせします。"
    if m.playit_running():
        out += "\nplayit は、もう動いています。"
    elif m.playit_path().exists():
        r2 = m.playit_toggle()
        out += "\nplayit も起動しました。" if r2.get("ok") else f"\n⚠ playit は起動できませんでした（{str(r2.get('message'))[:100]}）。"
    return out


def _steam_stop(sid):
    m = steamgames.get(sid)
    res = m.stop()
    if not res.get("ok"):
        return res.get("message") or "止められませんでした。"
    return "■ **停止を始めました。** ワールドを保存してから止まります。止まったら、スレッドでお知らせします。"


# ============================================================ ボタンが押されたとき
def _interaction(inter: dict):
    b = _bot
    if not b or inter.get("type") != 3:
        return
    parts = str((inter.get("data") or {}).get("custom_id") or "").split(":")
    if len(parts) != 3 or parts[0] != "ocs":
        return                                  # ほかのプログラムのボタン。さわらない
    act, sid = parts[1], _find(parts[2])
    if not _allowed(inter):
        return b.reply(inter, "このボタンを押せるのは、決められた人だけです。")
    if not sid:
        return b.reply(inter, "このサーバーは、もうありません（名前を変えたか、消したかもしれません）。ソフトの設定から、パネルを置き直してください。")
    who = ((inter.get("member") or {}).get("user") or inter.get("user") or {}).get("username") or "?"
    if act == "status":
        try:
            return b.reply(inter, embed=status_card(sid))
        except Exception as e:  # noqa: BLE001
            return b.reply(inter, f"⚠ 状態を調べられませんでした: {str(e)[:200]}")
    b.reply(inter, "", defer=True)
    try:
        if act == "start":
            text = start(sid)
            _console(sid, f"🎮 Discord から {who} が「起動」を押しました")
        elif act == "stop":
            text = stop_server(sid)
            _console(sid, f"🎮 Discord から {who} が「停止」を押しました")
        else:
            text = "わからないボタンです。"
    except Exception as e:  # noqa: BLE001
        text = f"⚠ うまくいきませんでした: {str(e)[:300]}"
    b.edit(inter, embed=discordbot.card(text.split("\n", 1)[0].replace("**", "")[:240],
                                        (text.split("\n", 1) + [""])[1],
                                        "bad" if text.startswith("⚠") else "ok"))


# ============================================================ 画面から呼ばれる機能
def bot_get() -> dict:
    c = cfg()
    pan = _panels(c)
    return {"token_set": bool(c.get("token")), "channel": c.get("channel") or "", "users": list(c.get("users") or []),
            "status": _bot.status if _bot else ("止まっています（トークンが入っていません）" if not c.get("token") else "止まっています"),
            "ok": bool(_bot and _bot.ok), "name": _bot.user if _bot else "",
            "alerts": __import__("oneclick.sysmon", fromlist=["settings"]).settings(),
            "servers": [{**s, "panel": bool((pan.get(s["sid"]) or {}).get("message"))} for s in servers()]}


def bot_set(token=None, channel=None, users=None, alerts=None) -> dict:
    c = dict(cfg())
    if token is not None:
        token = str(token).strip()
        if token and not re.fullmatch(r"[A-Za-z0-9_\-.]{50,120}", token):
            raise HubError("トークンの形がおかしいです。Discord の開発者ページの「Bot」→「Token」を、そのまま貼ってください。")
        c["token"] = token
        if not token:
            c["panels"] = {}
    if channel is not None:
        channel = str(channel).strip()
        if channel and not _ID.match(channel):
            raise HubError("チャンネルIDは、数字だけです（例: 1234567890123456789）。")
        c["channel"] = channel
    if users is not None:
        if isinstance(users, str):
            users = re.split(r"[\s,、]+", users)
        clean = []
        for u in users or []:
            u = str(u).strip().lstrip("@")
            if not u:
                continue
            if not (_ID.match(u) or re.fullmatch(r"[A-Za-z0-9_.]{2,32}", u)):
                raise HubError(f"「{u}」は、ユーザーID（数字）にも、ユーザー名にも見えません。")
            clean.append(u if _ID.match(u) else u.lower())
        c["users"] = clean[:30]
    if alerts is not None:
        if not isinstance(alerts, dict):
            raise HubError("警告の設定がおかしいです。")
        try:
            c["alerts"] = {"on": bool(alerts.get("on", True)),
                           "disk_gb": max(1, min(2000, int(float(alerts.get("disk_gb", 10))))),
                           "cpu": max(50, min(100, int(float(alerts.get("cpu", 90))))),
                           "mem": max(50, min(100, int(float(alerts.get("mem", 90)))))}
        except (TypeError, ValueError):
            raise HubError("警告の数字は、数字で入れてください。")
    _save_cfg(c)
    restart()
    return bot_get()


def bot_panel(sid: str) -> dict:
    """操作パネル(起動・停止・状態のボタン)を、決めたチャンネルに置く。スレッドも作る。"""
    if sid not in {s["sid"] for s in servers()}:
        raise HubError("そのサーバーが見つかりません。")
    c = cfg()
    b = _bot
    if not (b and c.get("token")):
        raise HubError("先に、ボットのトークンを保存してください。")
    if not c.get("channel"):
        raise HubError("先に、チャンネルを選んでください。")
    if not c.get("users"):
        raise HubError("先に、操作できる人（ユーザーID）を1人以上、保存してください。")
    name = _name(sid)
    try:
        msg = b.send(c["channel"], components=discordbot.panel_components(sid),
                     embed=discordbot.card(f"🎮 {name} の操作パネル",
                                           "ボタンを押すと、サーバーを操作できます。\n押せるのは、決められた人だけです。\n"
                                           "起動・停止などのお知らせは、このメッセージの**スレッド**に出ます。", "info",
                                           [("▶ 起動", "サーバーと playit を起動", True), ("■ 停止", "保存して止める", True),
                                            ("📊 状態", "今の様子を見る", True)], footer=name))
    except discordbot.DiscordError as e:
        raise HubError(str(e)) from None
    panel = {"channel": c["channel"], "message": str(msg.get("id") or ""), "thread": ""}
    note = ""
    try:
        th = b.make_thread(c["channel"], panel["message"], f"{name} のお知らせ")
        panel["thread"] = str(th.get("id") or "")
    except discordbot.DiscordError:
        note = "スレッドは作れませんでした（権限がありません）。お知らせは、同じチャンネルに出ます。"
    c = dict(cfg())
    c["panels"] = {**_panels(c), sid: panel}
    _save_cfg(c)
    if panel["thread"]:
        post(sid, "📌 ここにお知らせが出ます", "起動・停止・落ちたときなどを、カードでお知らせします。", "info")
    return {"ok": True, "note": note}


def bot_invite() -> dict:
    c = cfg()
    if not c.get("token"):
        raise HubError("先に、ボットのトークンを保存してください。")
    url = discordbot.invite_url(c["token"])
    if not url:
        raise HubError("トークンの形がおかしいようです。開発者ページの「Bot」→「Token」を、そのまま貼り直してください。")
    return {"url": url}


def bot_channels() -> dict:
    c = cfg()
    if not c.get("token"):
        raise HubError("先に、ボットのトークンを保存してください。")
    try:
        guilds = discordbot.rest("GET", "/users/@me/guilds", c["token"])
    except discordbot.DiscordError as e:
        raise HubError(str(e)) from None
    if not isinstance(guilds, list) or not guilds:
        return {"guilds": [], "note": "ボットが、まだ どのサーバーにも入っていません。「ボットをサーバーに入れる」のボタンで、入れてください。"}
    out = []
    for g in guilds[:10]:
        try:
            chs = discordbot.rest("GET", f"/guilds/{g['id']}/channels", c["token"])
        except discordbot.DiscordError:
            continue
        text = [{"id": str(x["id"]), "name": x.get("name") or ""} for x in chs if isinstance(x, dict) and x.get("type") in (0, 5)]
        out.append({"id": str(g["id"]), "name": g.get("name") or "", "channels": text[:100]})
    return {"guilds": out, "note": ""}


def bot_test() -> dict:
    c = cfg()
    if not c.get("token"):
        raise HubError("先に、ボットのトークンを保存してください。")
    try:
        me = discordbot.rest("GET", "/users/@me", c["token"])
    except discordbot.DiscordError as e:
        raise HubError(str(e)) from None
    checks = [{"ok": True, "text": f"ボットにつながりました（名前: {me.get('username') or ''}）"}]
    try:
        guilds = discordbot.rest("GET", "/users/@me/guilds", c["token"])
    except discordbot.DiscordError:
        guilds = None
    if guilds is not None:
        checks.append({"ok": bool(guilds), "text": f"サーバーに入っています（{len(guilds)}個）" if guilds
                       else "ボットが、まだサーバーに入っていません → 「ボットをサーバーに入れる」を押してください"})
    ch = c.get("channel")
    if not ch:
        checks.append({"ok": False, "text": "ボタンを置くチャンネルを、まだ選んでいません"})
    else:
        try:
            info = discordbot.rest("GET", f"/channels/{ch}", c["token"])
            checks.append({"ok": True, "text": f"チャンネルが見えます（#{info.get('name') or ch}）"})
        except discordbot.DiscordError as e:
            checks.append({"ok": False, "text": f"そのチャンネルが見えません → ボットがそのサーバーに入っているか、チャンネルの権限を確認してください（{str(e)[:80]}）"})
    checks.append({"ok": bool(c.get("users")), "text": "操作できる人が入っています" if c.get("users")
                   else "操作できる人（ユーザーID）が、まだ入っていません"})
    return {"name": me.get("username") or "", "status": _bot.status if _bot else "", "checks": checks,
            "all_ok": all(x["ok"] for x in checks)}
