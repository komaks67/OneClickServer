"""画面（ブラウザ）から呼ばれる機能をまとめたところ。"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
import zipfile
import webbrowser

from . import (extras, items as items_mod, regions, presets, admin, boottest, client_install, compat, conflicts, gamerules_meta, gateway, loaders, mcping, metrics,
               autostart, curseforge, ddns, diagnose, features, support, updater, mod_ja, protocol, watch, modrinth, upgrade, mrpack, msmp, net, network, optimize, packupdate, pregen, props_meta, versions,
               server, translate)
from . import paths as _paths
from .modrinth import ModList
from .paths import BASE_DIR, CACHE_DIR, IS_WINDOWS, SERVERS_DIR, load_settings, save_settings

from .appinfo import VERSION as APP_VERSION  # noqa: E402
settings = load_settings()
settings.setdefault("translate_auto", True)
settings.setdefault("translate_email", "")
settings.setdefault("translate_fallback", True)
settings.setdefault("port_mode", "same")
settings.setdefault("ui_mode", "simple")
settings.setdefault("hide_next_step", False)
settings.setdefault("tour_done", False)
settings.setdefault("self_play", True)
settings.setdefault("theme", "auto")
settings.setdefault("style", "simple")
if not settings.get("style_default_v2"):
    # 見た目の「初期設定」を、シンプルに変えた。昔の初期値(mc)のままの人も、一度だけシンプルにする（あとで、設定から戻せる）
    settings["style"] = "simple"
    settings["style_default_v2"] = True
    try:
        save_settings(settings)
    except OSError:
        pass

# Discord に知らせられること
DISCORD_EVENTS = {"start": "サーバーが起動した", "stop": "サーバーが止まった", "crash": "サーバーが落ちた（トラブル）",
                  "join": "だれかが入った・出た", "intrusion": "ホワイトリストにない人が入ろうとした",
                  "lag": "サーバーが重くなった"}
DISCORD_DEFAULT = ["start", "stop", "crash", "intrusion"]
watch.settings_ref = settings


class UserError(Exception):
    """画面にそのまま見せるエラー。"""


# ============================================================ ジョブ（時間のかかる作業）

class Job:
    def __init__(self, title):
        self.id = uuid.uuid4().hex[:12]
        self.title = title
        self.log: list[str] = []
        self.status = "running"
        self.result = None
        self.error = None
        self.progress = None
        self.cancel = threading.Event()

    def write(self, msg):
        self.log.append(str(msg))


jobs: dict[str, Job] = {}


def start_job(title, func) -> dict:
    job = Job(title)
    jobs[job.id] = job

    def run():
        try:
            job.result = func(job)
            job.status = "done"
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            job.error = str(e)
            job.status = "error"
            job.write(f"失敗: {e}")
    threading.Thread(target=run, daemon=True).start()
    return {"job": job.id}


def job_status(id, since=0):
    job = jobs.get(id)
    if not job:
        raise UserError("作業が見つかりません")
    return {"status": job.status, "log": job.log[int(since):], "next": len(job.log),
            "result": job.result, "error": job.error, "title": job.title}


def job_cancel(id):
    job = jobs.get(id)
    if job:
        job.cancel.set()
        job.write("止めています…（もとにもどしています）")
    return {"ok": True}


def jobs_running() -> int:
    return sum(1 for j in jobs.values() if j.status == "running")


# ============================================================ サーバーと動作中の管理

class Console:
    def __init__(self):
        self.lines: list[str] = []
        self.base = 0
        self.lock = threading.Lock()

    def add(self, line):
        with self.lock:
            self.lines.append(line)
            if len(self.lines) > 4000:
                drop = 1000
                del self.lines[:drop]
                self.base += drop

    def since(self, n):
        with self.lock:
            start = max(0, int(n) - self.base)
            return self.lines[start:], self.base + len(self.lines)


runners: dict[str, server.Runner] = {}
consoles: dict[str, Console] = {}
crash_info: dict[str, dict] = {}


def _prof(id) -> server.Profile:
    if not id or "/" in id or "\\" in id or id in (".", ".."):
        raise UserError("サーバーが選ばれていません")
    d = os.path.join(SERVERS_DIR, id)
    if not os.path.isfile(os.path.join(d, server.PROFILE_FILE)):
        raise UserError("サーバーが見つかりません")
    return server.Profile(d)


def _runner(id) -> server.Runner:
    if id not in runners:
        prof = _prof(id)
        con = consoles.setdefault(id, Console())

        def on_state(state, id=id):
            r = runners.get(id)
            try:
                _state_notify(id, state, r)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            try:
                from .. import discordhub
                discordhub.mc_state_changed(id, state, r)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            if state == "stopped" and r and r.last_exit and r.last_exit.get("crashed"):
                def analyze():
                    try:
                        res = conflicts.analyze_logs(_prof(id), r.last_exit.get("started"),
                                                     con.since(0)[0])
                        res["during_start"] = r.last_exit.get("during_start")
                        res["time"] = time.time()
                        res["gave_up"] = bool(r.last_exit.get("gave_up"))
                        _memory_after_crash(id, res)
                        crash_info[id] = res
                        from .. import discordhub
                        discordhub.mc_crash(id, res)
                    except Exception:  # noqa: BLE001
                        traceback.print_exc()
                threading.Thread(target=analyze, daemon=True).start()
            elif state == "starting":
                crash_info.pop(id, None)
                monitors[id].start()
            elif state == "running" and r and _msmp_wanted(r.prof):
                links[id].start(lambda: r.running)
            if state == "stopped" and id in links:
                links[id].stop()
        r = server.Runner(prof, con.add, on_state)
        r.line_hooks.append(lambda line, id=id: _watch_line(id, line))
        r.on_giveup = lambda limit, id=id: _notify(id, "crash", "⛔ 自動の再起動をやめました",
                                                    f"30分のうちに {limit} 回、落ちたためです。原因を直してから起動してください。", desktop=True)
        captures[id] = admin.Capture(r)
        links[id] = msmp.Link(lambda id=id: _msmp_conf(id), on_notify=lambda m, p_, id=id: _on_notify(id, m, p_))
        monitors[id] = metrics.Monitor(r)
        trackers[id] = pregen.Tracker(r)
        r.before_start.append(lambda runner, id=id: _prepare_start(id, runner))
        runners[id] = r
    return runners[id]


monitors: dict[str, metrics.Monitor] = {}
captures: dict[str, admin.Capture] = {}
links: dict[str, msmp.Link] = {}
trackers: dict[str, pregen.Tracker] = {}
gateways: dict[str, gateway.Gateway] = {}
busy: set[str] = set()   # 起動テスト中のサーバー


def _prepare_start(id, runner):
    """起動の直前（再起動のときも）：友達用パックを最新にして、門番を用意する。"""
    p = runner.prof
    con = consoles.setdefault(id, Console())
    if p.get("memory_auto"):
        try:
            adv = memory_advice(id)
            cur = int(p.get("memory_mb", 4096) or 4096)
            if adv["recommended"] > cur:
                p.set(memory_mb=adv["recommended"])
                con.add(f"🧠 メモリを自動で調整しました: {cur // 1024 if cur % 1024 == 0 else cur / 1024:g}GB → {adv['recommended'] / 1024:g}GB（MODの数と人数に合わせて）")
        except Exception:  # noqa: BLE001
            traceback.print_exc()
    if p.get("backup_on_start", True) and os.path.isdir(p.world_dir()):
        last = next((server.backup_time(b) for b in server.list_backups(p)), None)
        if not last or time.time() - last > 10 * 60:
            try:
                con.add("💾 起動の前にワールドをバックアップしています…")
                server.make_backup(p, "start")
            except Exception as e:  # noqa: BLE001
                con.add(f"⚠ 起動前のバックアップに失敗しました: {e}")
    try:
        res = _export(id)
        if res.get("changed"):
            con.add(f"📦 MODが変わったので、友達用パックを {res['version']} に更新しました（友達は自動で更新できます）")
    except Exception as e:  # noqa: BLE001
        con.add(f"⚠ 友達用パックを作れませんでした: {e}")
    _write_msmp_props(p)
    port = int(p.props().get("server-port", "25565") or 25565)
    gw = gateways.get(id)
    if p.get("pack_server", True):
        if gw and gw.public_port != port:
            gw.close()
            gw = None
        if not gw:
            gw = gateway.Gateway(port, lambda: _pack_serve_info(id), blocked_func=lambda: _banned_ips(id),
                                 on_event=lambda kind, ip, info=None: _gateway_event(id, kind, ip, info))
            try:
                gw.start()
            except OSError as e:
                raise UserError(f"ポート {port} を使えませんでした（ほかのソフトが使っています）: {e}") from e
            gateways[id] = gw
        gw.internal_port = gateway.internal_port()
        runner.extra_args = ["--port", str(gw.internal_port)]
    else:
        if gw:
            gw.close()
            gateways.pop(id, None)
        runner.extra_args = []


# ============================================================ 管理用の窓口（1.21.9 以降）

def _msmp_wanted(p) -> bool:
    return versions.at_least(p.get("mc", ""), versions.MSMP) and p.get("msmp", True)


def _write_msmp_props(p):
    """窓口を「このPCの中だけ・合言葉つき」で開く設定を書く。"""
    if not versions.at_least(p.get("mc", ""), versions.MSMP):
        return
    props = p.props()
    if not p.get("msmp", True):
        if props.get("management-server-enabled") == "true":
            server.write_props(p.props_path, {"management-server-enabled": "false"})
        return
    port = p.get("msmp_port")
    if not port or not network.port_free(int(port)):
        port = gateway.internal_port()
        p.set(msmp_port=port)
    secret = props.get("management-server-secret") or ""
    if len(secret) != 40 or not secret.isalnum():
        secret = msmp.new_secret()
    server.write_props(p.props_path, {
        "management-server-enabled": "true", "management-server-host": "127.0.0.1",
        "management-server-port": str(port), "management-server-secret": secret,
        "management-server-tls-enabled": "false", "management-server-allowed-origins": "http://127.0.0.1"})


def _msmp_conf(id):
    try:
        p = _prof(id)
    except UserError:
        return None
    props = p.props()
    if props.get("management-server-enabled") != "true":
        return None
    try:
        return ("127.0.0.1", int(props.get("management-server-port") or 0), props.get("management-server-secret", ""))
    except ValueError:
        return None


def _on_notify(id, method, params):
    r = runners.get(id)
    con = consoles.get(id)
    p = params[0] if isinstance(params, list) and params else params
    name = (p or {}).get("name") if isinstance(p, dict) else None
    if method.endswith("players/joined") and name and r:
        r.players.add(name)
    elif method.endswith("players/left") and name and r:
        r.players.discard(name)
    elif method.endswith("gamerules/updated") and con and isinstance(p, dict):
        con.add(f"🔧 ゲームルール {p.get('key')} が {p.get('value')} になりました")


def _msmp(id):
    link = links.get(id)
    return link if link and link.connected else None


def any_server_running() -> bool:
    return any(r.running for r in runners.values())


def _server_summary(p: server.Profile) -> dict:
    id = os.path.basename(p.dir)
    r = runners.get(id)
    props = p.props()
    return {"id": id, "name": p.name, "mc": p.get("mc"), "loader": p.get("loader"),
            "loader_label": p.loader_label(), "status": r.state if r else "stopped",
            "players": sorted(r.players) if r else [], "address": p.get("public_address") or "",
            "port": props.get("server-port", "25565"), "memory_mb": p.get("memory_mb", 4096),
            "mods": len([m for m in ModList(p.dir).items if m.get("location") == "server"]),
            "crash": crash_info.get(id),
            "playit": bool(playit_proc and playit_proc.poll() is None),
            "pack_exported": bool(p.get("pack_fp")),
            "whitelist_on": props.get("white-list", "false") == "true",
            "members": len(server.read_list(p, "whitelist")),
            "tps": (monitors[id].tps if id in monitors else None),
            "progress": (r.progress if r and r.state == "starting" else None)}


def state():
    return {"version": APP_VERSION, "ram_mb": total_ram_mb(),
            "servers": [_server_summary(p) for p in server.list_profiles()],
            "settings": {**{k: settings.get(k) for k in ("translate_auto", "translate_email", "translate_fallback", "port_mode", "ui_mode", "hide_next_step", "tour_done", "self_play", "theme",
                                                         "style", "minecraft_dir", "auto_update_check")},
                         "cf_enabled": bool(settings.get("cf_key"))},
            "update": updater.cached(), "app_exe": updater.FROZEN,
            "jobs_running": jobs_running(), "windows": IS_WINDOWS, "foreign_data": _paths.FOREIGN_DATA}


def server_info(id):
    p = _prof(id)
    r = runners.get(id)
    info = _server_summary(p)
    info.update({"eula": server.eula_accepted(p), "props": p.props(), "auto_restart": p.get("auto_restart", True),
                 "backup_interval_min": p.get("backup_interval_min", 30), "backup_keep": p.get("backup_keep", 10),
                 "dir": p.dir, "last_exit": r.last_exit if r else None,
                 "memory": memory_advice(id),
                 "auto_clear_min": p.get("auto_clear_min", 0), "auto_stop_min": p.get("auto_stop_min", 0),
                 "backup_on_start": p.get("backup_on_start", True), "autostart": p.get("autostart", False),
                 "discord_events": p.get("discord_events", ["start", "stop", "crash", "intrusion"]),
                 "auto_restart_max": p.get("auto_restart_max", 3), "memory_auto": p.get("memory_auto", False)})
    return info


def server_start(id, accept_eula=False, skip_deps=False, skip_stale=False):
    p = _prof(id)
    if not server.eula_accepted(p):
        if not accept_eula:
            return {"need_eula": True}
        server.accept_eula(p)
    port = int(p.props().get("server-port", "25565") or 25565)
    r = _runner(id)
    if r.running:
        return {"ok": True}
    if not skip_deps:
        missing = dep_status(id)["missing"]
        if missing:
            return {"need_deps": missing}
    for other_id, other in runners.items():
        if other_id != id and other.running and int(other.prof.props().get("server-port", 25565)) == port:
            raise UserError(f"ポート {port} は、いま動いている「{other.prof.name}」が使っています。先に「{other.prof.name}」を止めてください（同時に遊ぶなら、設定で「サーバーごとに別の番号にする」を選びます）。")
    if id in busy:
        raise UserError("起動テスト中です。テストが終わるまで待ってください。")
    gw = gateways.get(id)
    own_gateway = bool(gw and gw.alive and gw.public_port == port)
    port_busy = (not own_gateway) and not network.port_free(port)
    if not skip_stale:
        # 前のサーバー（java）が裏で残っていると、ワールドの鍵やポートが使われていて起動できない
        locked = diagnose.session_lock_held(p.world_dir())
        if locked or port_busy:
            stale = diagnose.stale_java(p.dir, _tracked_pids())
            if stale:
                return {"stale": stale, "locked": locked, "port_busy": port_busy}
            if locked:
                raise UserError("ワールドの鍵（session.lock）を、ほかのものが使っています。バックアップ・ウイルス対策ソフト・"
                                "OneDriveなどが見ている可能性があります。数十秒待ってからもう一度ためすか、PCを再起動してください。")
    if port_busy:
        raise UserError(f"ポート {port} はほかのソフトが使っています。マイクラのサーバーがほかに動いていないか確認するか、"
                        "「設定」でポート番号を変えてください（例: 25566）。")
    threading.Thread(target=lambda: _safe(r.start, id), daemon=True).start()
    return {"ok": True}


def _tracked_pids() -> set:
    """いま、このソフトが動かしているサーバーの java の番号。"""
    out = set()
    for r in list(runners.values()):
        try:
            if r.proc is not None and r.proc.poll() is None:
                out.add(r.proc.pid)
        except Exception:  # noqa: BLE001
            pass
    return out


def diagnose_run(id):
    """「困ったとき診断」：起動できない原因になりやすいことをまとめて調べる。"""
    p = _prof(id)
    r = runners.get(id)
    running = bool(r and r.running)
    port = int(p.props().get("server-port", "25565") or 25565)
    gw = gateways.get(id)
    own_gateway = bool(gw and gw.alive and gw.public_port == port)
    busy = (not running) and (not own_gateway) and not network.port_free(port)
    con = consoles.get(id)
    res = diagnose.run(p, running, _tracked_pids(), busy, con.since(0)[0] if con else None)
    if len(res["issues"]) > 1:   # ほかに原因が見つかったら、「はっきりしません」は出さない
        res["issues"] = [i for i in res["issues"] if i["title"] != "はっきりした原因は見つかりませんでした"]
    res["running"] = running
    return res


def stale_java_list(id):
    return {"stale": diagnose.stale_java(_prof(id).dir, _tracked_pids())}


def stale_java_kill(id, pids):
    p = _prof(id)
    done = diagnose.kill_java([int(x) for x in (pids or [])], _tracked_pids())
    time.sleep(1.0)
    return {"killed": done, "locked": diagnose.session_lock_held(p.world_dir())}


def _safe(func, id):
    try:
        func()
    except Exception as e:  # noqa: BLE001
        consoles.setdefault(id, Console()).add(f"⚠ エラー: {e}")


def server_stop(id):
    r = _runner(id)
    threading.Thread(target=r.stop, daemon=True).start()
    return {"ok": True}


def server_kill(id):
    _runner(id).kill()
    return {"ok": True}


def server_restart(id):
    r = _runner(id)

    def do():
        r.stop()
        time.sleep(2)
        r.start()
    threading.Thread(target=lambda: _safe(do, id), daemon=True).start()
    return {"ok": True}


def server_command(id, command):
    r = _runner(id)
    if not r.send(command):
        raise UserError("サーバーが動いていません。先に「▶ 起動」を押してください。")
    consoles.setdefault(id, Console()).add(f"> {command}")
    return {"ok": True}


def console(id, since=0):
    con = consoles.setdefault(id, Console())
    lines, nxt = con.since(since)
    r = runners.get(id)
    mon = monitors.get(id)
    return {"lines": lines, "next": nxt, "status": r.state if r else "stopped",
            "players": sorted(r.players) if r else [], "crash": crash_info.get(id),
            "progress": dict(r.progress) if r and r.state == "starting" else None,
            "tps": mon.tps if mon and r and r.state == "running" else None}


def server_delete(id):
    p = _prof(id)
    r = runners.get(id)
    if r and r.running:
        raise UserError("先にサーバーを止めてください。")
    _rmtree_hard(p.dir)
    if os.path.exists(p.dir):
        raise UserError("フォルダを消しきれませんでした。マイクラやエクスプローラーでこのサーバーのフォルダを開いていたら閉じて、"
                        "もう一度ためしてください。（ウイルス対策ソフトが見ている場合も、少し待つと消せます）")
    runners.pop(id, None)
    consoles.pop(id, None)
    crash_info.pop(id, None)
    return {"ok": True}


def _rmtree_hard(path):
    """Windows で「読み取り専用」のファイルがあっても消せるようにする。少し待って再挑戦もする。"""
    import stat

    def onerr(func, p, exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    for i in range(3):
        shutil.rmtree(path, onerror=onerr)
        if not os.path.exists(path):
            return
        time.sleep(1.0)


def server_set(id, props=None, profile=None):
    p = _prof(id)
    result = {"ok": True, "live": [], "restart": []}
    if props:
        result.update(props_set(id, props))
    if profile:
        allowed = {"memory_mb": int, "auto_restart": bool, "backup_interval_min": int, "backup_keep": int,
                   "public_address": str, "name": str, "pack_server": bool, "msmp": bool,
                   "auto_clear_min": int, "auto_stop_min": int, "backup_on_start": bool, "autostart": bool,
                   "discord_events": list, "auto_restart_max": int, "memory_auto": bool, "pack_auto": bool}
        clean = {}
        for k, v in profile.items():
            if k not in allowed:
                continue
            try:
                v = allowed[k](v)
            except (TypeError, ValueError) as e:
                raise UserError(f"{k} の値がおかしいです") from e
            if k == "memory_mb" and not 512 <= v <= 65536:
                raise UserError("メモリの量がおかしいです")
            if k == "name":
                v = admin.text(v, 40) or p.name
            if k == "public_address":
                v = admin.text(v, 200)
            if k == "auto_restart_max" and not 1 <= v <= 20:
                raise UserError("再起動の回数がおかしいです（1〜20）")
            if k in ("auto_clear_min", "auto_stop_min") and not 0 <= v <= 1440:
                raise UserError("分の数がおかしいです（0〜1440）")
            if k == "discord_events":
                v = [x for x in v if x in DISCORD_EVENTS]
            clean[k] = v
        p.set(**clean)
        admin.audit(p.dir, "ソフトの設定を変更", ", ".join(f"{k}={v}" for k, v in clean.items() if k != "public_address"))
    return result


# ---------------- 全設定（server.properties） ----------------

def props_get(id):
    p = _prof(id)
    props = p.props()
    mc = p.get("mc", "")
    first_run = len(props) < 25 and not os.path.isdir(p.world_dir())
    items = props_meta.describe(props, mc, first_run)
    for it in items:
        if it.get("type") == "secret" and it.get("value"):
            it["value"] = "••••••••"   # 合言葉は画面に出さない
            it["masked"] = True
    return {"items": items, "groups": props_meta.GROUPS, "first_run": first_run, "mc": mc,
            "running": bool(runners.get(id) and runners[id].running), "msmp": bool(_msmp(id))}


def props_set(id, values: dict):
    p = _prof(id)
    mc = p.get("mc", "")
    clean = {}
    for k, v in (values or {}).items():
        m = props_meta.BY_KEY.get(k, {})
        if m.get("type") == "secret" and v == "••••••••":
            continue  # 変えていない
        if m.get("managed"):
            continue  # このソフトが管理する設定は「外から操作」の画面で変える
        try:
            clean[k] = props_meta.validate(k, v, mc)
        except ValueError as e:
            raise UserError(str(e)) from e
    if "white-list" in clean and "enforce-whitelist" not in clean:
        clean["enforce-whitelist"] = clean["white-list"]
    old = p.props()
    changed = {k: v for k, v in clean.items() if old.get(k) != v}
    if not changed:
        return {"live": [], "restart": []}
    server.write_props(p.props_path, changed)
    live, restart = [], []
    link = _msmp(id)
    for k, v in changed.items():
        spec = props_meta.BY_KEY.get(k, {}).get("live")
        if link and spec:
            val = v == "true" if props_meta.BY_KEY[k]["type"] == "bool" else (int(v) if props_meta.BY_KEY[k]["type"] == "int" else v)
            try:
                link.call(f"minecraft:serversettings/{spec[0]}/set", val)
                live.append(k)
                continue
            except msmp.MsmpError:
                pass
        restart.append(k)
    admin.audit(p.dir, "サーバーの設定を変更", ", ".join(f"{k}={'(合言葉)' if 'password' in k or 'secret' in k else v}"
                                                   for k, v in changed.items()))
    running = bool(runners.get(id) and runners[id].running)
    return {"live": live, "restart": restart if running else []}


# ---------------- ゲームルール ----------------

def gamerules_get(id):
    p = _prof(id)
    mc = p.get("mc", "")
    r = runners.get(id)
    if not r or r.state != "running":
        return {"running": False, "rules": [], "groups": gamerules_meta.GROUPS, "presets": _presets()}
    link = _msmp(id)
    rules, via = None, "console"
    if link:
        try:
            res = link.call("minecraft:gamerules")
            rules = res.get("gamerules", res) if isinstance(res, dict) else res
            via = "msmp"
        except msmp.MsmpError:
            rules = None
    if rules is None:
        rules = admin.console_gamerules(captures[id], mc)
    out = []
    for g in rules or []:
        d = gamerules_meta.describe(g["key"])
        out.append({**g, **d})
    order = {k: i for i, (k, _l) in enumerate(gamerules_meta.GROUPS)}
    out.sort(key=lambda x: (order.get(x["group"], 99), x["label"]))
    return {"running": True, "rules": out, "groups": gamerules_meta.GROUPS, "via": via, "presets": _presets()}


def _presets():
    return [{"i": i, "title": t, "desc": d} for i, (t, d, _r) in enumerate(gamerules_meta.PRESETS)]


def _gamerule_apply(id, key, value) -> str:
    p = _prof(id)
    if not re.fullmatch(r"[A-Za-z0-9_:./\-]{2,80}", key or ""):
        raise UserError("おかしなルール名です")
    if isinstance(value, bool):
        sval = "true" if value else "false"
    else:
        try:
            value = int(value)
        except (TypeError, ValueError) as e:
            raise UserError("数字を入れてください") from e
        if not -1_000_000 <= value <= 100_000_000:
            raise UserError("数字が大きすぎます")
        sval = str(value)
    link = _msmp(id)
    if link:
        try:
            link.call("minecraft:gamerules/update", {"key": key, "value": value})
            admin.audit(p.dir, "ゲームルールを変更", f"{key}={sval}")
            return "msmp"
        except msmp.MsmpError:
            pass
    r = runners.get(id)
    if not r or not r.running:
        raise UserError("サーバーを起動してから変えてください。")
    lines = captures[id].run(f"gamerule {key} {sval}", 1.0)
    if any("Incorrect" in line or "Unknown" in line for line in lines):
        raise UserError("変えられませんでした: " + " / ".join(lines[:2]))
    admin.audit(p.dir, "ゲームルールを変更", f"{key}={sval}")
    return "console"


def gamerule_set(id, key, value):
    return {"via": _gamerule_apply(id, key, value)}


def gamerule_preset(id, index):
    p = _prof(id)
    title, _d, rules = gamerules_meta.PRESETS[int(index)]
    use_old = not versions.at_least(p.get("mc", ""), versions.GAMERULE_REGISTRY)
    done, failed = [], []
    for k, v in gamerules_meta.preset_for(rules, use_old).items():
        try:
            _gamerule_apply(id, k, v)
            done.append(k)
        except UserError:
            failed.append(k)
    return {"title": title, "done": len(done), "failed": failed}


# ---------------- 管理コマンド ----------------

def op_catalog(id):
    p = _prof(id)
    r = runners.get(id)
    return {"actions": admin.catalog(), "players": sorted(r.players) if r else [],
            "running": bool(r and r.state == "running"), "mc": p.get("mc")}


def item_catalog(id):
    """/give などで使う、アイテムのID一覧（バニラ＋サーバーのMOD）。初回は少し時間がかかる。"""
    p = _prof(id)
    ml = ModList(p.dir)
    titles = {os.path.basename(m["path"]): m["title"] for m in ml.items if m.get("path") and m.get("title")}

    def work(job):
        return items_mod.catalog(p, titles, job.write)
    return start_job("アイテムの一覧を作っています", work)


def op_action(id, action, args=None):
    p = _prof(id)
    r = runners.get(id)
    if not r or r.state != "running":
        raise UserError("サーバーを起動してから使ってください。")
    if action in ("ban_ip", "pardon_ip") and gateways.get(id):
        # 門番を通るとサーバーからは全員「127.0.0.1」に見えるので、IP BAN は門番でおこなう
        target = (args or {}).get("target") or (args or {}).get("player")
        res = (ban_add if action == "ban_ip" else ban_remove)(id, "ip", target, *([(args or {}).get("reason", "")] if action == "ban_ip" else []))
        return {"command": f"（門番で{'IP BAN' if action == 'ban_ip' else 'IP BAN 解除'}）{target}", "output": ["門番で処理しました"], "bans": res}
    try:
        cmd = admin.build(action, args or {}, p.get("mc", ""))
    except admin.BadInput as e:
        raise UserError(str(e)) from e
    lines = captures[id].run(cmd)
    consoles.setdefault(id, Console()).add(f"> {cmd}")
    label = {"ban": "BAN", "pardon": "BAN 解除", "ban_ip": "IP BAN", "pardon_ip": "IP BAN 解除", "kick": "キック",
             "op": "OPにする", "deop": "OPをやめる", "whitelist_add": "ホワイトリストに追加",
             "whitelist_remove": "ホワイトリストから外す"}.get(action, "管理コマンド")
    admin.audit(p.dir, label, cmd)
    return {"command": cmd, "output": lines}


def audit_get(id):
    return {"items": admin.audit_read(_prof(id).dir)}


# ---------------- BAN ----------------

def bans_get(id):
    p = _prof(id)
    return {**admin.read_bans(p.dir), "running": bool(runners.get(id) and runners[id].running)}


LOOPBACK = ("127.0.0.1", "::1", "0:0:0:0:0:0:0:1", "localhost")


def _resolve_ip(id, target):
    """IP BAN の相手：IPアドレスそのものか、いま入っているプレイヤーの名前（本当のIPに直す）。"""
    t = (target or "").strip()
    if admin.IP_RE.match(t):
        ip = t
    else:
        name = admin.player(t)
        ip = player_ips.get(id, {}).get(name)
        if not ip:
            raise UserError(f"{name} のIPアドレスが分かりません（いまサーバーに入っている人だけ、名前で IP BAN できます）。")
    if ip in LOOPBACK or ip.startswith("127."):
        raise UserError("このPC自身（127.0.0.1）は BAN できません（みんな入れなくなります）。")
    return ip


def ban_add(id, kind, target, reason=""):
    p = _prof(id)
    reason = admin.text(reason, 200)
    r = runners.get(id)
    if kind != "player" and gateways.get(id):
        try:
            ip = _resolve_ip(id, target)
        except admin.BadInput as e:
            raise UserError(str(e)) from e
        admin.ipban_file_add(p.dir, ip, reason)
        _banned_cache.pop(id, None)
        admin.audit(p.dir, "IP BAN", ip)
        if r and r.state == "running":
            for name, pip in list(player_ips.get(id, {}).items()):
                if pip == ip and name in r.players:
                    r.send(f"kick {name} {reason or 'Banned'}".strip())
        return bans_get(id)
    try:
        if r and r.state == "running":
            op_action(id, "ban" if kind == "player" else "ban_ip", {"player": target, "target": target, "reason": reason})
            time.sleep(0.5)
        elif kind == "player":
            name, uuid = server.lookup_uuid(admin.player(target))
            admin.ban_file_add(p.dir, name, uuid, reason)
            admin.audit(p.dir, "BAN", name)
        else:
            admin.ipban_file_add(p.dir, admin._ip(target), reason)
            admin.audit(p.dir, "IP BAN", target)
    except (admin.BadInput, ValueError) as e:
        raise UserError(str(e)) from e
    except net.NetError as e:
        raise UserError("名前を確かめるためにインターネットにつながる必要があります。") from e
    return bans_get(id)


def ban_remove(id, kind, target):
    p = _prof(id)
    r = runners.get(id)
    if kind != "player" and gateways.get(id):
        try:
            admin.ipban_file_remove(p.dir, admin._ip(target))
        except admin.BadInput as e:
            raise UserError(str(e)) from e
        _banned_cache.pop(id, None)
        admin.audit(p.dir, "IP BAN 解除", target)
        return bans_get(id)
    try:
        if r and r.state == "running":
            op_action(id, "pardon" if kind == "player" else "pardon_ip", {"player": target, "target": target})
            time.sleep(0.5)
        elif kind == "player":
            admin.ban_file_remove(p.dir, admin.player(target))
            admin.audit(p.dir, "BAN 解除", target)
        else:
            admin.ipban_file_remove(p.dir, admin._ip(target))
            admin.audit(p.dir, "IP BAN 解除", target)
    except admin.BadInput as e:
        raise UserError(str(e)) from e
    return bans_get(id)


# ---------------- 外から操作（窓口・RCON・Query・JMX） ----------------

def remote_get(id):
    p = _prof(id)
    props = p.props()
    mc = p.get("mc", "")
    link = links.get(id)
    return {
        "msmp_supported": versions.at_least(mc, versions.MSMP), "msmp": p.get("msmp", True),
        "msmp_connected": bool(link and link.connected), "msmp_error": link.error if link else None,
        "msmp_port": props.get("management-server-port"),
        "rcon": props.get("enable-rcon") == "true", "rcon_port": props.get("rcon.port", "25575"),
        "rcon_password": props.get("rcon.password", "") if props.get("enable-rcon") == "true" else "",
        "query": props.get("enable-query") == "true", "query_port": props.get("query.port", props.get("server-port")),
        "jmx": props.get("enable-jmx-monitoring") == "true",
        "running": bool(runners.get(id) and runners[id].running)}


def remote_set(id, kind, on):
    p = _prof(id)
    on = bool(on)
    if kind == "msmp":
        p.set(msmp=on)
        if not on:
            server.write_props(p.props_path, {"management-server-enabled": "false"})
    elif kind == "rcon":
        upd = {"enable-rcon": "true" if on else "false"}
        if on:
            port = 25575 if network.port_free(25575) else gateway.internal_port()
            upd.update({"rcon.port": str(port), "rcon.password": msmp.new_secret()[:24]})
        server.write_props(p.props_path, upd)
    elif kind == "query":
        server.write_props(p.props_path, {"enable-query": "true" if on else "false",
                                          "query.port": p.props().get("server-port", "25565")})
    elif kind == "jmx":
        server.write_props(p.props_path, {"enable-jmx-monitoring": "true" if on else "false"})
    else:
        raise UserError("おかしな操作です")
    admin.audit(p.dir, "外から操作の設定", f"{kind}={'ON' if on else 'OFF'}")
    return remote_get(id)


# ============================================================ メモリ

def total_ram_mb() -> int:
    try:
        if IS_WINDOWS:
            import ctypes

            class MEM(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MEM()
            m.dwLength = ctypes.sizeof(MEM)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return int(m.ullTotalPhys // (1024 * 1024))
        with open("/proc/meminfo") as f:
            return int(f.readline().split()[1]) // 1024
    except Exception:  # noqa: BLE001
        return 8192


def advise(total, mods, players, loader, self_play) -> dict:
    """サーバーに何GBのメモリを使わせればいいかを計算する。"""
    need = 2048 if loader == "vanilla" else 3072
    need += (mods // 40) * 1024              # MOD 40個ごとに +1GB
    need += max(0, players - 4) // 4 * 512   # 5人以上なら 4人ごとに +0.5GB
    need = min(max(need, 2048), 12288)
    reserve = 3072                            # Windows とほかのソフトの分
    if self_play:
        reserve += 4096 if loader != "vanilla" else 2048  # 自分が遊ぶマイクラの分
    max_safe = max(1024, (total - reserve) // 512 * 512)
    rec = min(need, max_safe) // 512 * 512
    level = "ok"
    if need > max_safe:
        level = "short"
    return {"recommended": max(1024, rec), "need": need, "max_safe": max_safe, "total": total,
            "reserve": reserve, "level": level, "mods": mods, "players": players}


def memory_advice(id=None, mods=None, players=None, loader=None, self_play=None):
    if id:
        p = _prof(id)
        mods = len(ModList(p.dir).items) if mods is None else mods
        try:
            players = int(p.props().get("max-players", 10)) if players is None else players
        except ValueError:
            players = 10
        loader = loader or p.get("loader")
    sp = settings.get("self_play", True) if self_play is None else self_play
    return advise(total_ram_mb(), int(mods or 0), int(players or 10), loader or "fabric", sp)


def set_settings(**kw):
    for k in ("translate_auto", "translate_email", "translate_fallback", "self_play", "minecraft_dir"):
        if k in kw:
            settings[k] = kw[k]
    if "ui_mode" in kw:
        if kw["ui_mode"] not in ("simple", "detail"):
            raise UserError("表示は「かんたん」か「くわしい」を選んでください。")
        settings["ui_mode"] = kw["ui_mode"]
    for k in ("hide_next_step", "tour_done", "auto_update_check"):
        if k in kw:
            settings[k] = bool(kw[k])
    if "port_mode" in kw:
        if kw["port_mode"] not in ("same", "separate"):
            raise UserError("ポートは「そろえる」か「別々にする」を選んでください。")
        settings["port_mode"] = kw["port_mode"]
    if "cf_key" in kw:
        key = curseforge.clean_key(admin.text(kw["cf_key"] or "", 400))
        if key:
            ok, why = curseforge.check_key(key)
            if not ok:
                raise UserError(why)
        settings["cf_key"] = key
    if "theme" in kw:
        if kw["theme"] not in ("auto", "light", "dark"):
            raise ValueError("画面の明るさは「自動・明るい・暗い」から選んでください")
        settings["theme"] = kw["theme"]
    if "style" in kw:
        if kw["style"] not in ("mc", "simple"):
            raise ValueError("見た目は「マイクラ風・シンプル」から選んでください")
        settings["style"] = kw["style"]
    save_settings(settings)
    return {"ok": True}


# ============================================================ MOD

def _side_text(client, srv):
    if srv == "unsupported":
        return "遊ぶ人のPCだけ（サーバーには入れない）"
    if client == "unsupported":
        return "サーバーだけ（遊ぶ人はいらない）"
    if client == "optional" and srv in ("required", "optional"):
        return "サーバーに入れる・遊ぶ人はなくてもOK"
    if srv == "optional" and client == "required":
        return "遊ぶ人に必要・サーバーはなくてもOK"
    return "サーバーと遊ぶ人の両方に必要"


def _ja_for(slug, title, desc):
    b = mod_ja.builtin(slug, title)
    if b:
        return b, "builtin"
    t = translate.cached(desc or "")
    if t:
        return t, "auto"
    return None, None


def _card(h, mc=None, loader=None, have=frozenset()):
    pid = h.get("project_id") or h.get("id")
    cats = h.get("display_categories") or h.get("categories") or []
    all_cats = set(h.get("categories") or []) | set(cats)
    vers = h.get("versions") or h.get("game_versions") or []
    loaders_ = [l for l in ("fabric", "neoforge", "forge", "quilt", "paper", "purpur", "spigot", "bukkit") if l in all_cats]
    ja, src = _ja_for(h.get("slug"), h.get("title"), h.get("description"))
    compat_ = None
    if mc and not loader and vers:
        compat_ = ({"ok": True, "text": f"マイクラ {mc} で使えます"} if mc in vers
                   else {"ok": False, "text": f"マイクラ {mc} 用がありません（対応: {compat.summarize(vers)}）"})
    if mc and loader and loader != "vanilla":
        ok_loader = (loader in all_cats or (loader == "quilt" and "fabric" in all_cats)
                     or any(k in all_cats for k in modrinth.loader_keys(loader)))
        ok_ver = mc in vers
        if ok_loader and ok_ver:
            compat_ = {"ok": True, "text": f"このサーバー（{mc}・{loaders.LOADER_LABEL[loader]}）で使えます"}
        elif not ok_loader:
            names = "・".join(loaders.LOADER_LABEL.get(l, l.capitalize()) for l in loaders_) or "ほかの種類"
            compat_ = {"ok": False, "text": f"{loaders.LOADER_LABEL[loader]} 用がありません（{names} 用のみ）"}
        else:
            compat_ = {"ok": False, "text": f"マイクラ {mc} 用がありません（対応: {compat.summarize(vers)}）"}
    gallery = h.get("featured_gallery") or ((h.get("gallery") or [None])[0])
    if isinstance(gallery, dict):
        gallery = gallery.get("url")
    return {"id": pid, "slug": h.get("slug"), "title": h.get("title"), "author": h.get("author"),
            "icon": h.get("icon_url"), "downloads": h.get("downloads", 0), "follows": h.get("follows", 0),
            "desc": h.get("description", ""), "ja": ja, "ja_source": src,
            "genres": mod_ja.genres(cats), "library": mod_ja.is_library(all_cats),
            "side": _side_text(h.get("client_side"), h.get("server_side")),
            "client_side": h.get("client_side"), "server_side": h.get("server_side"),
            "loaders": [loaders.LOADER_LABEL.get(l, l.capitalize()) for l in loaders_],
            "versions_text": compat.summarize(vers) if vers else "", "compat": compat_,
            "image": gallery, "installed": pid in have,
            "url": f"https://modrinth.com/{h.get('project_type', 'mod')}/{h.get('slug') or pid}"}


def _cf_card(m, mc=None, loader=None, have=frozenset()):
    ci = curseforge.card_info(m, mc, loader)
    pid = f"cf:{m['id']}"
    ja = mod_ja.builtin(m.get("slug"), m.get("name"))
    compat_ = None
    if mc and loader and loader != "vanilla" and ci["ok"] is not None:
        compat_ = ({"ok": True, "text": f"このサーバー（{mc}・{loaders.LOADER_LABEL.get(loader, loader)}）で使えます"} if ci["ok"]
                   else {"ok": False, "text": f"このサーバー（{mc}・{loaders.LOADER_LABEL.get(loader, loader)}）用がありません"})
    shots = m.get("screenshots") or []
    return {"id": pid, "slug": m.get("slug"), "title": m.get("name"), "author": ((m.get("authors") or [{}])[0]).get("name"),
            "icon": (m.get("logo") or {}).get("thumbnailUrl"), "downloads": int(m.get("downloadCount") or 0), "follows": 0,
            "desc": m.get("summary", ""), "ja": ja, "ja_source": "builtin" if ja else None,
            "genres": [c.get("name") for c in (m.get("categories") or [])][:3], "library": False,
            "side": "", "client_side": None, "server_side": None,
            "loaders": [loaders.LOADER_LABEL.get(x, x) for x in ci["loaders"]],
            "versions_text": compat.summarize(ci["versions"]) if ci["versions"] else "", "compat": compat_,
            "image": shots[0].get("thumbnailUrl") if shots else None, "installed": pid in have,
            "url": (m.get("links") or {}).get("websiteUrl"), "source": "curseforge",
            "can_download": m.get("allowModDistribution") is not False}


# ---------------- お気に入りのMOD ----------------
_FAV_KEYS = ("id", "slug", "title", "author", "icon", "downloads", "desc", "ja", "ja_source", "genres", "library", "source", "can_download")


def favorites_get():
    return {"items": list(settings.get("favorite_mods") or [])}


def favorite_toggle(card):
    """お気に入りに入れる／はずす。card は検索結果のカード（画面から、そのまま渡される）。"""
    if not isinstance(card, dict) or not card.get("id"):
        raise UserError("お気に入りに入れられませんでした。")
    items = [x for x in (settings.get("favorite_mods") or []) if isinstance(x, dict)]
    cid = str(card["id"])
    if any(str(x.get("id")) == cid for x in items):
        items = [x for x in items if str(x.get("id")) != cid]
        on = False
    else:
        item = {k: card.get(k) for k in _FAV_KEYS if k in card}
        item["id"] = cid
        item["desc"] = (item.get("desc") or "")[:300]
        item["ja"] = (item.get("ja") or "")[:300] or None
        items.insert(0, item)
        items = items[:300]
        on = True
    settings["favorite_mods"] = items
    save_settings(settings)
    return {"on": on, "items": items}


def mods_search(q="", category="", sort="downloads", offset=0, server=None, only_ok=True,
                project_type="mod", mc=None, loader=None, source="modrinth"):
    have = set()
    if server:
        p = _prof(server)
        mc, loader = p.get("mc"), p.get("loader")
        have = {m.get("project_id") for m in ModList(p.dir).items}
    if source == "curseforge" and project_type == "mod":
        try:
            hits, total = curseforge.search(settings.get("cf_key"), q, mc if only_ok else "",
                                            loader if only_ok else None, sort=sort, offset=int(offset),
                                            plugins=loader in loaders.PLUGIN_LOADERS)
        except curseforge.KeyMissing as e:
            raise UserError(str(e)) from e
        return {"hits": [_cf_card(h, mc, loader, have) for h in hits], "total": min(total, 10000),
                "mc": mc, "loader": loader}
    use_mc = mc if (only_ok and mc) else ""
    use_loader = loader if (only_ok and loader and loader != "vanilla") else None
    hits, total = modrinth.search(q, use_mc, use_loader, project_type=project_type, category=category,
                                  sort=sort, offset=int(offset), limit=24)
    return {"hits": [_card(h, mc, loader, have) for h in hits], "total": total,
            "mc": mc, "loader": loader}


def _cf_detail(id, server=None):
    key = settings.get("cf_key")
    m = curseforge.mod(key, id.split(":", 1)[1])
    p = _prof(server) if server else None
    card = _cf_card(m, p.get("mc") if p else None, p.get("loader") if p else None,
                    {x.get("project_id") for x in ModList(p.dir).items} if p else set())
    body = curseforge.description(key, m["id"])
    deps = []
    if p:
        try:
            deps = [{"title": d["title"], "installed": False}
                    for d in curseforge.preview_deps(ModList(p.dir), key, m["id"], p.get("mc"), p.get("loader"))]
        except net.NetError:
            deps = []
    return {**card, "body": body, "body_html": True, "license": None,
            "gallery": [{"url": g.get("url"), "title": g.get("title"), "description": g.get("description")}
                        for g in (m.get("screenshots") or [])][:12],
            "links": {dst: (m.get("links") or {}).get(src) for src, dst in (("sourceUrl", "source_url"), ("issuesUrl", "issues_url"),
                                                                            ("wikiUrl", "wiki_url")) if (m.get("links") or {}).get(src)},
            "updated": (m.get("dateModified") or "")[:10], "matrix": [], "deps": deps, "conflicts": []}


def mod_detail(id, server=None):
    if str(id).startswith("cf:"):
        try:
            return _cf_detail(id, server)
        except curseforge.KeyMissing as e:
            raise UserError(str(e)) from e
    p = _prof(server) if server else None
    proj = modrinth.project(id)
    pid = proj["id"]
    card = _card({**proj, "project_id": pid, "versions": proj.get("game_versions"),
                  "display_categories": proj.get("categories"),
                  "categories": (proj.get("categories") or []) + (proj.get("loaders") or [])})
    out = {**card, "body": proj.get("body") or "", "license": (proj.get("license") or {}).get("name"),
           "gallery": [{"url": g.get("url"), "title": g.get("title"), "description": g.get("description")}
                       for g in proj.get("gallery", [])][:12],
           "links": {k: proj.get(k) for k in ("source_url", "issues_url", "wiki_url", "discord_url") if proj.get(k)},
           "updated": (proj.get("updated") or "")[:10]}
    try:
        out["matrix"] = compat.matrix(pid)
    except net.NetError:
        out["matrix"] = []
    deps, conflicts_ = [], []
    if p:
        mc, loader = p.get("mc"), p.get("loader")
        ml = ModList(p.dir)
        have = {m.get("project_id"): m for m in ml.items if m.get("project_id")}
        out["installed"] = pid in have
        try:
            ok = compat.works_with(pid, mc, loader)
        except net.NetError:
            ok = None
        out["compat"] = {"ok": ok, "text": (f"このサーバー（{mc}・{loaders.LOADER_LABEL.get(loader, loader)}）で使えます"
                                            if ok else f"このサーバー（{mc}・{loaders.LOADER_LABEL.get(loader, loader)}）"
                                            "では使えません")} if ok is not None else None
        if ok:
            ver = modrinth.best_version(modrinth.versions_for(pid, mc, loader))
            dep_ids = [d["project_id"] for d in (ver or {}).get("dependencies", []) if d.get("project_id")]
            titles = {}
            if dep_ids:
                try:
                    titles = {k: v["title"] for k, v in modrinth.projects(dep_ids).items()}
                except net.NetError:
                    pass
            for d in (ver or {}).get("dependencies", []):
                dp = d.get("project_id")
                if not dp:
                    continue
                kind = d.get("dependency_type")
                if kind == "required":
                    deps.append({"title": titles.get(dp, dp), "installed": dp in have})
                elif kind == "incompatible" and dp in have:
                    conflicts_.append({"level": "error", "text": f"「{titles.get(dp, dp)}」とは一緒に使えません"
                                                                 "（Modrinth に書かれています）"})
        keys = {conflicts._norm(proj.get("slug", "")), conflicts._norm(proj.get("title", ""))}
        others = {m["title"]: {conflicts._norm(m.get("slug") or ""), conflicts._norm(m["title"])}
                  for m in ml.items if m.get("project_id") != pid}
        for name, lv, msg in conflicts.known_conflicts_for(keys, others):
            conflicts_.append({"level": lv, "text": f"入っている「{name}」: {msg}"})
    else:
        ver = None
    out["deps"] = deps
    out["conflicts"] = conflicts_
    return out


def translate_text(text):
    try:
        return {"ja": translate.translate(text, settings.get("translate_email") or None,
                                          fallback=settings.get("translate_fallback", True))}
    except translate.TranslateLimit as e:
        raise UserError(str(e)) from e


def install_many(prof, ids, log):
    ml = ModList(prof.dir)
    result = modrinth.InstallResult()
    visiting: set = set()
    errors = []
    cf_seen: set = set()
    for pid in ids:
        try:
            if str(pid).startswith("cf:"):
                curseforge.install(ml, settings.get("cf_key"), pid.split(":", 1)[1], prof.get("mc"), prof.get("loader"),
                                   log, result, cf_seen)
            else:
                modrinth.install(ml, pid, prof.get("mc"), prof.get("loader"), log, result, visiting)
        except Exception as e:  # noqa: BLE001
            errors.append(str(e))
    return result, errors


def install_preview(server, ids):
    """入れる前に、一緒に入る前提MODを調べる（確認の窓に出す用）。"""
    p = _prof(server)
    ml = ModList(p.dir)
    deps, seen_titles = [], set()
    want = set(ids or [])
    for pid in ids or []:
        try:
            if str(pid).startswith("cf:"):
                lst = curseforge.preview_deps(ml, settings.get("cf_key"), pid.split(":", 1)[1], p.get("mc"), p.get("loader"))
            else:
                lst = modrinth.preview_deps(ml, pid, p.get("mc"), p.get("loader"))
        except (net.NetError, curseforge.KeyMissing):
            continue
        for d in lst:
            if d["id"] not in want and d["title"] not in seen_titles:
                seen_titles.add(d["title"])
                deps.append(d)
    # 説明書きに出ない前提MOD（例: Distant Horizons + Sodium → Indium）
    have = set()
    for m in ml.items:
        have |= {conflicts._norm(m.get("slug") or ""), conflicts._norm(m.get("title") or "")}
    for pid in ids or []:
        if str(pid).startswith("cf:"):
            continue
        try:
            pr = modrinth.project(pid)
            have |= {conflicts._norm(pr.get("slug") or ""), conflicts._norm(pr.get("title") or "")}
        except net.NetError:
            pass
    for n in conflicts.known_needs(have, p.get("loader")):
        if n["title"] not in seen_titles:
            deps.append({"id": n["need"], "title": n["title"]})
    return {"deps": deps}


def _result_dict(result, errors):
    return {"installed": result.installed, "client_only": result.client_only, "skipped": result.skipped,
            "warnings": result.warnings, "errors": errors}


def mods_install(server, ids):
    p = _prof(server)
    if p.get("loader") == "vanilla":
        raise UserError("このサーバーは「バニラ（MODなし）」なのでMODは入れられません。")
    word = "プラグイン" if p.uses_plugins else "MOD"

    def work(job):
        r_ = runners.get(server)
        if not (r_ and r_.running) and os.path.isdir(p.world_dir()):
            try:  # MODを足すとワールドが壊れることがあるので、先に自動でバックアップ
                job.write("念のため、ワールドのバックアップをとっています…")
                server.make_backup(p, "mods")
            except Exception:  # noqa: BLE001  とれなくてもMODは入れる
                pass
        r, e = install_many(p, ids, job.write)
        out = _result_dict(r, e)
        out["running"] = bool(runners.get(server) and runners[server].running)
        return out
    return start_job(f"{word}を {len(ids)} 個入れています", work)


def dep_status(server):
    """足りない前提MODの一覧（画面で「入れますか？」と聞く用）。ネットは使わず、MODの中身を読んで調べる。"""
    p = _prof(server)
    if p.get("loader") in (None, "vanilla"):
        return {"missing": []}
    try:
        issues = conflicts.check_server(p)["issues"]
    except Exception:  # noqa: BLE001  調べられなくても、MODを入れる作業は止めない
        return {"missing": []}
    seen, out = set(), []
    for i in issues:
        a = i.get("action") or {}
        if a.get("type") == "install_dep" and i["level"] == "error" and a["id"] not in seen:
            seen.add(a["id"])
            out.append({"id": a["id"], "title": i["title"], "detail": i.get("detail") or ""})
    return {"missing": out}


def quick_invite(id):
    """「友達を呼ぶ」をまとめてやる: サーバー起動 → playit 起動 → パックと入り方カードを作る。
    playit のアカウント登録（Claim）とアドレスを決めるところだけは、人の操作が必要。"""
    p = _prof(id)

    def work(job):
        global playit_proc
        done, todo = [], []
        r = runners.get(id)
        if not (r and r.running):
            job.write("サーバーを起動しています…")
            res = server_start(id)
            if res.get("need_eula"):
                raise UserError("先に、一度「▶ 起動」を押して、利用規約（EULA）に同意してください。")
            if res.get("need_deps"):
                raise UserError("前提MODが足りません。「▶ 起動」を押して、画面の案内に従ってください。")
            done.append("サーバーを起動しました（完了まで数分かかることがあります）")
        else:
            done.append("サーバーは、もう動いています")
        if playit_proc and playit_proc.poll() is None:
            done.append("playit は、もう動いています")
        else:
            job.write("playit を起動しています…")
            playit_proc = network.start_playit(job.write)
            done.append("playit を起動しました")
        addr = p.get("public_address") or ""
        if addr:
            job.write("友達用パックを作っています…")
            try:
                pack_export(id)
                done.append("友達用パックを作りました")
            except Exception as e:  # noqa: BLE001
                todo.append(f"パックを作れませんでした（{e}）。「友達用パック」タブで作ってください")
            try:
                guide_card(id)
                done.append("友達用の入り方カードを作りました（デスクトップ）")
            except Exception:  # noqa: BLE001
                pass
        else:
            todo.append("playit の黒い画面の手順で、アドレスを作って保存してください（初回だけ）")
        return {"done": done, "todo": todo, "address": addr}
    return start_job("友達を呼ぶ準備をしています", work)


def ignore_dep(server, id, ignore=True):
    """「この前提MODの警告は、もう出さない」を、サーバーごとに覚える。"""
    p = _prof(server)
    cur = list(p.get("ignored_deps") or [])
    if ignore and id not in cur:
        cur.append(id)
    if not ignore and id in cur:
        cur.remove(id)
    p.set(ignored_deps=cur)
    return {"ignored": cur}


def ignored_deps(server):
    return {"ignored": list(_prof(server).get("ignored_deps") or [])}


def adopt_data():
    """この data フォルダを、このPCのものとして使う（別のPCから来た data でも、持ち主を切りかえる）。"""
    _paths.adopt_data()
    _paths.FOREIGN_DATA = False
    return {"ok": True}


_JOIN_PATTERNS = [
    (re.compile(r"not.white-?listed|multiplayer\.disconnect\.not_whitelisted", re.I), "ホワイトリストに入っていません",
     "ホストが、この人の「マイクラの名前」をメンバーに追加すると入れます。"),
    (re.compile(r"banned|multiplayer\.disconnect\.banned", re.I), "BANされています", "メンバー・BANのタブで確認してください。"),
    (re.compile(r"Mod rejections|mismatch|Missing mods?|Registry|incompatible", re.I), "MODの構成が、サーバーと合っていません",
     "友達用パックを最新にして、入れ直してもらってください（MODの種類・バージョンがちがうときに出ます）。"),
    (re.compile(r"Outdated (?:client|server)|multiplayer\.disconnect\.outdated", re.I), "マイクラのバージョンがちがいます",
     "サーバーと同じバージョンの「（マルチ用）」で起動してもらってください。"),
    (re.compile(r"Timed out|timeout", re.I), "通信が途中で切れました（時間切れ）",
     "友達のネットが不安定か、サーバーが重いときに起きます。"),
    (re.compile(r"Connection reset|Internal Exception|ClosedChannel|Disconnected$", re.I), "通信が切れました",
     "友達の回線・Wi-Fi が不安定なことが多いです。何度も続くなら、有線や別の回線を試してもらってください。"),
    (re.compile(r"illegal characters|Invalid session|Failed to verify username|unverified", re.I), "ログインの確認に失敗しました",
     "友達がマイクラにログインできているか確認してもらってください（正規版が必要です）。"),
    (re.compile(r"server is full|multiplayer\.disconnect\.server_full", re.I), "満員です", "最大人数を増やすか、だれかに出てもらってください。"),
]


def join_problems(id):
    """サーバーの記録から「入れなかった・切れた人」を探して、理由と直し方を日本語で出す。"""
    p = _prof(id)
    latest = os.path.join(p.dir, "logs", "latest.log")
    text = conflicts._tail(latest, 400_000) if os.path.exists(latest) else ""
    out = []
    line_re = re.compile(r"^\[(?P<t>[^\]]*?(\d\d:\d\d:\d\d)[^\]]*)\].*?(?:(?P<who>\S+) lost connection: (?P<why>.+)|Disconnecting (?P<who2>\S+?) .*?: (?P<why2>.+)|(?P<who3>\S+) \(/[^)]*\) lost connection: (?P<why3>.+))$")
    for ln in text.splitlines():
        if "lost connection" not in ln and "Disconnecting" not in ln:
            continue
        m = line_re.match(ln)
        who = (m.group("who") or m.group("who2") or m.group("who3")) if m else None
        why = ((m.group("why") or m.group("why2") or m.group("why3")) if m else ln).strip()
        if why in ("Disconnected", "Quitting") or why.endswith("Quitting"):
            continue  # 普通に出ていっただけ
        if "(" in (who or "") and "/" in (who or ""):
            who = who.split("(")[0]
        for rx, title, fix in _JOIN_PATTERNS:
            if rx.search(why):
                break
        else:
            title, fix = "つながりませんでした", "理由の文字を、ホストに見せてもらうと原因がわかります。"
        t = re.search(r"(\d\d:\d\d:\d\d)", ln)
        out.append({"time": t.group(1) if t else "", "who": watch.mask_ips(who or "だれか"), "title": title, "fix": fix,
                    "raw": watch.mask_ips(why)[:200]})
    return {"items": out[-15:][::-1]}


def guide_card(id):
    """友達に渡す「入り方」を、1枚のページ（HTML）にしてデスクトップに保存する。印刷・スクショ・PDF化ができる。"""
    import html
    import datetime
    p = _prof(id)
    addr = p.get("public_address") or ""
    mc, ld = p.get("mc") or "", p.loader_label()
    esc = html.escape
    rows = [
        ("マイクラ Java版を用意する", f"公式ランチャーで、一度普通に起動して閉じます。使うバージョンは <b>{esc(mc)}</b>（{esc(ld)}）です。"),
        ("ホストに「マイクラの名前」を伝える", "名前が登録されていないと、サーバーに入れません。"),
        ("OneClickServerMC を開く", "ホストからもらった <b>OneClickServerMC.exe</b> をダブルクリックします（インストールは不要です）。"),
        ("「友達のサーバーに参加」を押す", f"アドレス <b>{esc(addr) or 'ホストに聞いてください'}</b> を入れるか、もらった .mrpack を選びます。"),
        ("青い「入れる」ボタンを押す", "数分かかります。「できました」と出れば完了です。"),
        ("公式ランチャーで「（マルチ用）」を選んで「プレイ」", "普通の「最新版」ではなく、名前の最後に「（マルチ用）」が付いたものを選びます。"),
        ("「マルチプレイ」からサーバーを選ぶ", f"一覧に出なければ「サーバーを追加」で、アドレスに <b>{esc(addr) or '（ホストに聞く）'}</b> を入れます。"),
    ]
    lis = "".join(f"<li><b>{esc(t)}</b><span>{d}</span></li>" for t, d in rows)
    rules_html = marks_html = ""
    if p.get("rules_text") and p.get("rules_in_card", True):
        rules_html = '<h2>みんなのルール</h2><ol class="rules">' + "".join(f"<li>{esc(l)}</li>" for l in p.get("rules_text").split("\n")) + "</ol>"
    marks = p.get("landmarks") or []
    if marks:
        marks_html = '<h2>場所の一覧</h2><table class="marks">' + "".join(
            f"<tr><td>{esc(m['name'])}</td><td>{DIMS.get(m.get('dim'), '')}</td><td>X {m['x']} / Y {m['y']} / Z {m['z']}</td><td>{esc(m.get('note') or '')}</td></tr>" for m in marks) + "</table>"
    page = f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(p.name)} への入り方</title><style>
body{{font-family:"Yu Gothic UI","Meiryo",sans-serif;max-width:720px;margin:24px auto;padding:0 16px;color:#222;line-height:1.7}}
h1{{font-size:26px;margin:0 0 4px}} .addr{{background:#eef6ee;border:2px solid #5a9a5a;border-radius:10px;padding:12px 16px;font-size:22px;margin:16px 0}}
ol{{padding-left:1.4em}} li{{margin:14px 0}} li span{{display:block;color:#444}} .note{{background:#fff7e0;border-radius:8px;padding:10px 14px;margin-top:20px;font-size:14px}}
.rules li{{margin:6px 0}} h2{{font-size:19px;margin-top:28px}} .marks{{border-collapse:collapse;width:100%;font-size:14px}} .marks td{{border-bottom:1px solid #ddd;padding:6px 8px}}
@media print{{body{{margin:0}}}}</style></head><body>
<h1>「{esc(p.name)}」への入り方</h1><div>マイクラ {esc(mc)} ／ {esc(ld)}</div>
<div class="addr">サーバーのアドレス：<b>{esc(addr) or "（ホストに聞いてください）"}</b></div>
<ol>{lis}</ol>
{rules_html}{marks_html}<div class="note"><b>2回目からは</b>、OneClickServerMC の「▶ 遊ぶ」を押すだけです。<br><b>入れないとき</b>：ホストに、画面に出た文字（エラー）を見せてください。</div>
<div class="note" style="background:#fdecec"><b>注意：</b>ホストの <b>data フォルダ</b>は、人に渡さないでください（鍵が入っています）。</div>
<p style="color:#888;font-size:12px">作成: {datetime.date.today():%Y-%m-%d} ／ OneClickServerMC（非公式のツールです）</p></body></html>"""
    home = os.path.expanduser("~")
    base = next((d for d in (os.path.join(home, "Desktop"), os.path.join(home, "デスクトップ"), home) if os.path.isdir(d)), home)
    out = os.path.join(base, f"{_paths_safe(p.name)}_友達用の入り方.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    return {"path": out}


def _paths_safe(name):
    from .paths import safe_name
    return safe_name(name) or "server"


# ---------------- みんなのルール ----------------
def rules_get(id):
    p = _prof(id)
    return {"text": p.get("rules_text") or "", "on_join": bool(p.get("rules_on_join", False)),
            "in_card": bool(p.get("rules_in_card", True))}


def rules_set(id, text="", on_join=False, in_card=True):
    p = _prof(id)
    text = "\n".join(l.strip()[:120] for l in (text or "").replace("\r", "").split("\n") if l.strip())[:1500]
    if len(text.split("\n")) > 12:
        raise UserError("ルールは12行までにしてください（ゲームの画面に出すと、長すぎて読めなくなります）。")
    p.set(rules_text=text, rules_on_join=bool(on_join), rules_in_card=bool(in_card))
    return rules_get(id)


def _send_rules(id, name):
    """入ってきた人に、ルールを、ゲームの画面で見せる（チャットに出る）。"""
    p = _prof(id)
    text = p.get("rules_text") or ""
    r = runners.get(id)
    if not (text and p.get("rules_on_join") and r and r.running and re.fullmatch(r"\w{2,16}", name or "")):
        return

    def go():
        r.send("tellraw " + name + " " + json.dumps({"text": f"━━ {p.name} のルール ━━", "color": "gold", "bold": True}, ensure_ascii=False))
        for i, line in enumerate(text.split("\n"), 1):
            r.send("tellraw " + name + " " + json.dumps({"text": f"{i}. {line}", "color": "yellow"}, ensure_ascii=False))
    threading.Timer(4.0, go).start()


# ---------------- ワールドの地図（目印） ----------------
DIMS = {"overworld": "地上", "nether": "ネザー", "end": "エンド"}


def landmarks_get(id):
    return {"items": list(_prof(id).get("landmarks") or []), "dims": DIMS}


def landmark_add(id, name, x, z, y=64, dim="overworld", note=""):
    p = _prof(id)
    name = (name or "").strip()[:30]
    if not name:
        raise UserError("場所の名前を入れてください。")
    if dim not in DIMS:
        dim = "overworld"
    try:
        x, y, z = int(float(x)), int(float(y)), int(float(z))
    except (TypeError, ValueError):
        raise UserError("座標は数字で入れてください（F3 キーで、いまの座標が見られます）。") from None
    if not all(-30_000_000 <= v <= 30_000_000 for v in (x, y, z)):
        raise UserError("座標が大きすぎます。")
    items = list(p.get("landmarks") or [])
    if len(items) >= 200:
        raise UserError("目印は200個までです。")
    items.append({"id": uuid.uuid4().hex[:8], "name": name, "x": x, "y": y, "z": z, "dim": dim,
                  "note": (note or "").strip()[:80]})
    p.set(landmarks=items)
    return landmarks_get(id)


def landmark_remove(id, lid):
    p = _prof(id)
    p.set(landmarks=[m for m in (p.get("landmarks") or []) if m.get("id") != lid])
    return landmarks_get(id)


# ---------------- 荒らしの巻き戻し（その場所だけ） ----------------
def grief_backups(id):
    p = _prof(id)
    out = []
    for b in server.list_backups(p):
        t = server.backup_time(b)
        out.append({"file": os.path.basename(b), "when": time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else "?",
                    "kind": os.path.basename(b).split("_", 1)[-1][:-4]})
    return {"backups": out, "dims": DIMS}


def _grief_args(id, file, dim, x, z, radius):
    p = _prof(id)
    try:
        x, z, radius = int(float(x)), int(float(z)), int(float(radius))
    except (TypeError, ValueError):
        raise UserError("座標と範囲は数字で入れてください。") from None
    if not 16 <= radius <= 1000:
        raise UserError("範囲（半径）は 16〜1000 ブロックにしてください。")
    path = os.path.join(server.backups_dir(p), os.path.basename(file or ""))
    if not os.path.isfile(path):
        raise UserError("そのバックアップが見つかりません。")
    return p, path, dim, x, z, radius


def grief_preview(id, file, dim, x, z, radius):
    p, path, dim, x, z, radius = _grief_args(id, file, dim, x, z, radius)
    r = regions.revert_area(p.dir, os.path.basename(p.world_dir()), path, dim, x, z, radius, dry_run=True)
    r["running"] = bool(runners.get(id) and runners[id].running)
    return r


def grief_revert(id, file, dim, x, z, radius):
    p, path, dim, x, z, radius = _grief_args(id, file, dim, x, z, radius)
    if runners.get(id) and runners[id].running:
        raise UserError("サーバーが動いています。先に「■ 停止」を押してから、やり直してください。")

    def work(job):
        job.write("念のため、今のワールドをバックアップしています…")
        server.make_backup(p, "before-rollback")
        job.write("その場所だけを、もどしています…")
        r = regions.revert_area(p.dir, os.path.basename(p.world_dir()), path, dim, x, z, radius)
        admin.audit(p.dir, "荒らしの巻き戻し（範囲）", f"{os.path.basename(path)} / {dim} / X{x} Z{z} 半径{radius}")
        return r
    return start_job("その場所だけを、もどしています", work)


# ---------------- おすすめMODの組み合わせ ----------------
def mod_presets(server):
    """おすすめセット一覧。このサーバー（マイクラのバージョン・種類）で使えるMODに、印をつける。"""
    p = _prof(server)
    mc, ld = p.get("mc"), p.get("loader")

    def work(job):
        from concurrent.futures import ThreadPoolExecutor
        slugs = sorted({m["slug"] for s in presets.PRESETS for m in s["mods"]})
        job.write("このサーバーで使えるか、調べています…")

        def ok(slug):
            try:
                return slug, bool(compat.works_with(slug, mc, ld))
            except Exception:  # noqa: BLE001
                return slug, None
        with ThreadPoolExecutor(6) as ex:
            res = dict(ex.map(ok, slugs))
        out = []
        for s in presets.PRESETS:
            mods = [{**m, "ok": res.get(m["slug"])} for m in s["mods"]]
            out.append({**s, "mods": mods, "usable": sum(1 for m in mods if m["ok"])})
        return {"presets": out, "mc": mc, "loader": ld}
    return start_job("おすすめセットを調べています", work)


# ---------------- メモリ診断 ----------------
def mem_diag(id):
    """今のメモリ設定で、あと何個くらいMODを入れても平気かを、見積もる。"""
    p = _prof(id)
    adv = memory_advice(id)
    alloc = int(p.get("memory_mb", 4096) or 4096)
    loader = p.get("loader") or "fabric"
    sp = settings.get("self_play", True)
    slots = 0
    n = adv["mods"]
    if advise(adv["total"], n, adv["players"], loader, sp)["need"] <= alloc:
        while slots < 300 and advise(adv["total"], n + slots + 1, adv["players"], loader, sp)["need"] <= alloc:
            slots += 1
    level = "ok"
    if alloc < adv["need"]:
        level = "short" if alloc < adv["need"] - 512 else "tight"
    elif slots < 8:
        level = "tight"
    can_raise = adv["max_safe"] > alloc
    return {"allocated": alloc, "need": adv["need"], "recommended": adv["recommended"], "max_safe": adv["max_safe"],
            "total": adv["total"], "mods": adv["mods"], "players": adv["players"], "slots": slots, "level": level,
            "can_raise": can_raise}


# ---------------- ワールドの点検 ----------------
def _save_world_check(id, r):
    p = _prof(id)
    p.set(world_check_last={"time": time.time(), "files": r["files"], "problems": r["problems"][:30]})


def world_check_last(id):
    last = _prof(id).get("world_check_last")
    if not last:
        return {"last": None}
    return {"last": {**last, "text": time.strftime("%m/%d %H:%M", time.localtime(last.get("time", 0)))}}


def world_check(id):
    p = _prof(id)
    if runners.get(id) and runners[id].running:
        raise UserError("サーバーが動いています。動いている間は、書きかけのデータで、まちがって「こわれている」と出ることがあります。先に停止してください。")

    def work(job):
        job.write("ワールドを点検しています…")
        r = extras.scan_world(p.world_dir(), job.write)
        _save_world_check(id, r)
        job.write(f"{r['files']} 個のファイルを調べました。問題: {len(r['problems'])} 件")
        return r
    return start_job("ワールドを点検しています", work)


def _daily_world_check():
    now = time.time()
    for p in server.list_profiles():
        id = os.path.basename(p.dir)
        r = runners.get(id)
        if (r and r.running) or id in busy or not p.get("world_check_auto", True):
            continue
        if now - (p.get("world_check_last") or {}).get("time", 0) < 86400 or not os.path.isdir(p.world_dir()):
            continue
        res = extras.scan_world(p.world_dir())
        _save_world_check(id, res)
        if res["problems"]:
            first = res["problems"][0]
            _notify(id, "crash", "⚠ ワールドに、おかしなところが見つかりました",
                    f"{len(res['problems'])}件: {first['file']} {first['msg']}", desktop=True)
        return  # 1回に1つのサーバーだけ（重くしないため）


# ---------------- サーバーの引っ越し ----------------
def _desktop_dir():
    home = os.path.expanduser("~")
    return next((d for d in (os.path.join(home, "Desktop"), os.path.join(home, "デスクトップ"), home) if os.path.isdir(d)), home)


def server_export(id):
    p = _prof(id)
    if runners.get(id) and runners[id].running:
        raise UserError("サーバーが動いています。先に停止してから、やり直してください。")
    out = os.path.join(_desktop_dir(), f"{_paths_safe(p.name)}_引っ越し_{time.strftime('%Y%m%d')}.zip")

    def work(job):
        r = extras.export_server(p.dir, out, job.write)
        admin.audit(p.dir, "引っ越しファイルを作成", out)
        return {"path": out, "mb": os.path.getsize(out) // (1024 * 1024), "files": r["files"]}
    return start_job("引っ越しファイルを作っています", work)


def server_import_inspect(path):
    path = _uploaded(path)
    try:
        return extras.inspect_export(path)
    except (ValueError, zipfile.BadZipFile, OSError) as e:
        raise UserError(str(e) if isinstance(e, ValueError) else "このファイルは読めません。引っ越しファイル（zip）を選んでください。") from e


def server_import(path, name=""):
    path = _uploaded(path)
    info = server_import_inspect(path)
    nm = (name or info["name"]).strip()[:40] or "サーバー"

    def work(job):
        folder = os.path.join(SERVERS_DIR, _paths_safe(nm))
        n = 2
        base = folder
        while os.path.exists(folder):
            folder = f"{base}_{n}"
            n += 1
        try:
            extras.import_server(path, folder, job.write)
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        prof = server.Profile(folder)
        prof.set(name=nm)
        port = free_port()
        server.write_props(prof.props_path, {"server-port": str(port)})
        job.write("できました。サーバーの一覧に出ます。")
        return {"server": os.path.basename(folder)}
    return start_job("引っ越しファイルをひらいています", work)


# ---------------- ひな形 ----------------
def templates_list():
    return {"items": [{k: v for k, v in t.items() if k != "props"} for t in extras.TEMPLATES]}


def template_apply(id, tid):
    t = next((x for x in extras.TEMPLATES if x["id"] == tid), None)
    if not t:
        raise UserError("そのひな形はありません。")
    done, skipped = [], []
    for k, v in t["props"].items():
        try:
            props_set(id, {k: v})
            done.append(k)
        except UserError:
            skipped.append(k)
    if "pvp" in skipped:
        skipped = [x for x in skipped if x != "pvp"] + ["pvp（このバージョンでは、ゲームルールで変えます。「かんたん設定」で、サーバーを起動して変えてください）"]
    return {"title": t["title"], "applied": done, "skipped": skipped, "mods": t["mods"]}


# ---------------- PCの重さ ----------------
_cpu_prev = {"t": None}


def pc_load():
    """PC全体のメモリと、CPUの使われ方。重いときの警告に使う。"""
    total = total_ram_mb()
    free = None
    cpu = None
    try:
        if IS_WINDOWS:
            import ctypes

            class MEM(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MEM()
            m.dwLength = ctypes.sizeof(MEM)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            free = int(m.ullAvailPhys // (1024 * 1024))

            class FT(ctypes.Structure):
                _fields_ = [("lo", ctypes.c_ulong), ("hi", ctypes.c_ulong)]
            idle, kern, user = FT(), FT(), FT()
            ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user))
            val = lambda f: (f.hi << 32) | f.lo  # noqa: E731
            now = (val(idle), val(kern) + val(user))
            prev = _cpu_prev["t"]
            _cpu_prev["t"] = now
            if prev and now[1] - prev[1] > 0:
                cpu = round(100 * (1 - (now[0] - prev[0]) / (now[1] - prev[1])))
        else:
            with open("/proc/meminfo") as f:
                info = {l.split(":")[0]: int(l.split()[1]) for l in f if ":" in l}
            free = info.get("MemAvailable", 0) // 1024
            with open("/proc/stat") as f:
                v = [int(x) for x in f.readline().split()[1:]]
            now = (v[3] + v[4], sum(v))
            prev = _cpu_prev["t"]
            _cpu_prev["t"] = now
            if prev and now[1] - prev[1] > 0:
                cpu = round(100 * (1 - (now[0] - prev[0]) / (now[1] - prev[1])))
    except Exception:  # noqa: BLE001
        pass
    used_pct = round(100 * (1 - free / total)) if free is not None and total else None
    level, msgs = "ok", []
    if free is not None and (free < 600 or (used_pct or 0) >= 93):
        level = "ng"
        msgs.append(f"PCのメモリが、ほとんど残っていません（空き {free}MB）。ゲームやサーバーが止まる・重くなるおそれがあります。")
    elif free is not None and (free < 1500 or (used_pct or 0) >= 85):
        level = "warn"
        msgs.append(f"PCのメモリが、少なくなっています（空き {free}MB）。")
    if cpu is not None and cpu >= 95:
        level = "ng"
        msgs.append(f"PCのCPUが、ほぼいっぱいです（{cpu}%）。")
    elif cpu is not None and cpu >= 85 and level == "ok":
        level = "warn"
        msgs.append(f"PCのCPUが、かなり使われています（{cpu}%）。")
    tips = []
    if level != "ok":
        tips = ["ブラウザのタブや、使っていないアプリを閉じてください", "サーバーのメモリを、少し減らしてください（「くわしい表示」→「設定」）",
                "重いMOD（影MODなど）を、一度OFFにしてください", "自分も遊ぶときは、ゲームの描画距離を下げてください"]
    return {"level": level, "free_mb": free, "total_mb": total, "used_pct": used_pct, "cpu": cpu, "messages": msgs, "tips": tips}


def install_dep(server, id):
    """相性チェックで見つかった「足りない前提MOD」をさがして入れる。"""
    p = _prof(server)

    def work(job):
        pid = None
        try:
            pid = modrinth.project(id)["id"]
        except net.NetError:
            hits, _ = modrinth.search(id, p.get("mc"), p.get("loader"), limit=5)
            if hits:
                pid = hits[0]["project_id"]
        if not pid:
            raise UserError(f"「{id}」が Modrinth で見つかりませんでした。MODの説明ページで入手先を確認してください。")
        r, e = install_many(p, [pid], job.write)
        return _result_dict(r, e)
    return start_job(f"前提MOD「{id}」を入れています", work)


def mods_list(server):
    p = _prof(server)
    ml = ModList(p.dir)
    ml.sync_with_folder(lambda *_: None)
    out = []
    for m in sorted(ml.items, key=lambda m: m["title"].lower()):
        where = {"server": "サーバー ＋ 遊ぶ人", "client_only": "遊ぶ人だけ"}.get(m.get("location"), "?")
        if m.get("location") == "server" and m.get("client_side") == "unsupported":
            where = "サーバーだけ"
        ja, _src = _ja_for(m.get("slug"), m.get("title"), m.get("description"))
        out.append({"path": m["path"], "title": m["title"], "version": m.get("version_number") or "-",
                    "where": where, "enabled": m.get("enabled", True), "icon": m.get("icon_url"),
                    "manual": bool(m.get("manual") and not m.get("project_id")), "ja": ja,
                    "desc": m.get("description") or "", "id": m.get("project_id"),
                    "genres": mod_ja.genres(m.get("categories")),
                    "mark": m.get("mark") or "", "can_optional": mrpack.can_be_optional(m),
                    "url": f"https://modrinth.com/mod/{m.get('slug') or m.get('project_id')}" if m.get("project_id") else None})
    return {"mods": out, "mc": p.get("mc"), "loader": p.get("loader"), "loader_label": p.loader_label()}


def mods_enable(server, paths, enabled):
    p = _prof(server)
    ml = ModList(p.dir)
    for m in ml.items:
        if m["path"] in paths:
            ml.set_enabled(m, bool(enabled))
    return {"ok": True}


CONFIG_DIRS = ("config", "defaultconfigs", "kubejs/config")


def _mod_keys(p, m) -> set[str]:
    keys = {conflicts._norm(m.get("slug") or ""), conflicts._norm(m.get("title") or "")}
    fp = os.path.join(p.dir, m["path"]) if m.get("enabled", True) else os.path.join(p.dir, m["path"] + ".disabled")
    if os.path.exists(fp):
        try:
            meta = conflicts.read_jar(fp)
            keys |= {conflicts._norm(x) for x in meta.get("ids", set())}
            if meta.get("main_id"):
                keys.add(conflicts._norm(meta["main_id"]))
        except Exception:  # noqa: BLE001
            pass
    keys -= {"", "minecraft", "forge", "fabric", "neoforge", "config", "common", "client", "server"}
    return {k for k in keys if len(k) >= 3}


def mods_remove_preview(server, paths):
    """外すMODといっしょに消せる設定ファイル・フォルダの候補。"""
    p = _prof(server)
    ml = ModList(p.dir)
    out = []
    for m in [m for m in ml.items if m["path"] in set(paths or [])]:
        keys = _mod_keys(p, m)
        found = []
        roots = list(CONFIG_DIRS)
        if p.uses_plugins:
            roots.append("plugins")
        for root in roots:
            d = os.path.join(p.dir, root.replace("/", os.sep))
            if not os.path.isdir(d):
                continue
            for name in sorted(os.listdir(d)):
                if name.lower().endswith((".jar", ".disabled")):
                    continue
                base = conflicts._norm(os.path.splitext(name)[0].split("-")[0])
                if base and any(base == k or (len(k) >= 5 and base.startswith(k)) for k in keys):
                    rel = f"{root}/{name}"
                    full = os.path.join(d, name)
                    size = sum(os.path.getsize(os.path.join(a, f)) for a, _d, fs in os.walk(full) for f in fs) \
                        if os.path.isdir(full) else os.path.getsize(full)
                    found.append({"path": rel, "dir": os.path.isdir(full), "kb": max(1, size // 1024)})
        out.append({"mod": m["path"], "title": m["title"], "configs": found})
    return {"items": out}


def mods_remove(server, paths, configs=None):
    p = _prof(server)
    ml = ModList(p.dir)
    allowed = set()
    if configs:
        for it in mods_remove_preview(server, paths)["items"]:
            allowed |= {c["path"] for c in it["configs"]}
    for m in [m for m in ml.items if m["path"] in paths]:
        ml.remove(m)
    removed = []
    for rel in configs or []:
        if rel not in allowed:
            continue  # 候補に出したもの以外は消さない
        full = os.path.join(p.dir, rel.replace("/", os.sep))
        if not _inside(full, p.dir):
            continue
        if os.path.isdir(full):
            shutil.rmtree(full, ignore_errors=True)
        elif os.path.exists(full):
            os.remove(full)
        removed.append(rel)
    if paths:
        admin.audit(p.dir, "MODを外す", "、".join(os.path.basename(x) for x in paths) + (f"（設定 {len(removed)}個も）" if removed else ""))
    return {"ok": True, "configs_removed": removed}


def mods_upload_jar(server, path, filename):
    p = _prof(server)
    path = _uploaded(path)
    if not filename.lower().endswith(".jar"):
        raise UserError(".jar ファイルを選んでください。")
    shutil.move(path, os.path.join(p.mods_dir, os.path.basename(filename)))
    return mods_list(server)


def mods_updates(server):
    p = _prof(server)
    ml = ModList(p.dir)
    ups = modrinth.check_updates(ml, p.get("mc"), p.get("loader"))
    if settings.get("cf_key") and any(m.get("source") == "curseforge" for m in ml.items):
        ups += [(m, {"cf": f, "version_number": f.get("displayName") or f.get("fileName")})
                for m, f in curseforge.check_updates(ml, settings["cf_key"], p.get("mc"), p.get("loader"))]
    _update_cache[server] = ups
    return {"updates": [{"sha1": m["sha1"], "title": m["title"], "old": m.get("version_number"),
                         "new": v.get("version_number"), "icon": m.get("icon_url"),
                         "source": m.get("source", "modrinth")} for m, v in ups]}


_update_cache: dict[str, list] = {}


def mods_apply_updates(server, sha1s):
    p = _prof(server)
    ups = [(m, v) for m, v in _update_cache.get(server, []) if m["sha1"] in set(sha1s)]

    def work(job):
        ml = ModList(p.dir)
        n = 0
        for m, v in ups:
            rec = next((x for x in ml.items if x.get("sha1") == m.get("sha1")), None)
            if rec and "cf" in v:
                enabled = rec.get("enabled", True)
                curseforge.install(ml, settings.get("cf_key"), rec["cf_id"], p.get("mc"), p.get("loader"), job.write)
                new = ml.by_project(rec["project_id"])
                if new and not enabled:
                    ml.set_enabled(new, False)
                n += 1
            elif rec:
                modrinth.apply_update(ml, rec, v, job.write)
                n += 1
        return {"updated": n}
    return start_job("MODを更新しています", work)


def check_conflicts(server):
    return conflicts.check_server(_prof(server))


def analyze_logs(server):
    return conflicts.analyze_logs(_prof(server))


def plan(ids, titles=None):
    return compat.plan(ids, titles or {})


def mc_versions():
    return {"versions": loaders.release_versions()[:80]}


def supported_loaders(mc):
    return {"loaders": loaders.supported_loaders(mc), "java": loaders.guess_java(mc)}


# ============================================================ サーバーを作る

def free_port():
    """新しいサーバーのポート番号。
    「そろえる」(same): いつも 25565です。同時に動かさないなら、これが一番かんたんです。
    「別々」(separate): 25565 から順に、使われていない番号を選びます。"""
    if settings.get("port_mode", "same") != "separate":
        return 25565
    used = set()
    for p in server.list_profiles():
        try:
            used.add(int(p.props().get("server-port", 25565)))
        except ValueError:
            pass
    port = 25565
    while port in used:
        port += 1
    return port


def apply_port_mode(mode, apply_existing=False):
    """ポートの決め方を保存する。apply_existing=True なら、今あるサーバーの番号も変える。
    動いているサーバーは変えない（止めてからもう一度やってもらう）。"""
    if mode not in ("same", "separate"):
        raise UserError("「そろえる」か「別々にする」を選んでください。")
    settings["port_mode"] = mode
    save_settings(settings)
    changed, skipped = [], []
    if apply_existing:
        n = 0
        for p in server.list_profiles():
            id = os.path.basename(p.dir)
            r = runners.get(id)
            if r and r.running:
                skipped.append(p.name)
                n += 1 if mode == "separate" else 0
                continue
            want = 25565 if mode == "same" else 25565 + n
            n += 1
            if str(p.props().get("server-port", "25565")) != str(want):
                server.write_props(p.props_path, {"server-port": str(want)})
                changed.append(p.name)
    return {"mode": mode, "changed": changed, "skipped": skipped}


def port_check(port):
    port = int(port)
    used_by = next((p.name for p in server.list_profiles()
                    if str(p.props().get("server-port")) == str(port)), None)
    return {"free": network.port_free(port), "used_by": used_by}


def create_defaults():
    return {"port": free_port(), "memory": memory_advice(mods=0, players=10, loader="fabric"),
            "ram_mb": total_ram_mb()}


def create_server(name, mc, loader, memory_mb, port, mods=None, titles=None):
    mods = mods or []
    titles = titles or {}
    if not mc:
        raise UserError("マイクラのバージョンを選んでください。")
    port = int(port)

    def work(job):
        ld = loader
        skipped = []
        if ld in (None, "", "auto"):
            sup = loaders.supported_loaders(mc)
            cands = [s for s in sup if s in loaders.MOD_LOADERS]
            if mods and cands:
                job.write("選んだMODに合う種類を調べています…")
                rows = compat.plan(mods, titles)["rows"]
                rows = [r for r in rows if r["mc"] == mc and r["loader"] in cands]
                ld = rows[0]["loader"] if rows else cands[0]
                skipped = rows[0]["ng_titles"] if rows else []
            else:
                ld = next((x for x in ("fabric", "neoforge", "forge") if x in cands), "vanilla")
            job.write(f"→ {loaders.LOADER_LABEL[ld]} にします")
        prof = server.create_server(name or "マイクラサーバー", mc, ld, int(memory_mb), port, job.write)
        res = None
        if mods and ld in loaders.PLUGIN_LOADERS:
            skipped = list(titles.get(m, m) for m in mods)
            job.write("Paper / Purpur ではMODは使えないので、選んだMODは入れません（プラグインはあとから入れられます）")
        elif mods and ld != "vanilla":
            job.write(f"MODを {len(mods)} 個入れています…")
            r, e = install_many(prof, mods, job.write)
            res = _result_dict(r, e)
        return {"server": os.path.basename(prof.dir), "mods": res, "skipped": skipped, "loader": ld}
    return start_job("サーバーを作っています", work)


def create_from_pack(memory_mb, name="", pack_id=None, upload=None):
    port = free_port()
    if upload:
        upload = _uploaded(upload)

    def work(job):
        path = upload
        if pack_id:
            path = modrinth.download_modpack(pack_id, CACHE_DIR, log=job.write)
        if not path or not os.path.exists(path):
            raise UserError("MODパックのファイルが見つかりません。")
        prof = server.create_from_pack(name, path, int(memory_mb), port, job.write)
        return {"server": os.path.basename(prof.dir)}
    return start_job("MODパックからサーバーを作っています", work)


# ============================================================ 友達用パック・参加

def pack_tab(server):
    p = _prof(server)
    excluded = set(p.get("pack_exclude", []))
    items = []
    for m in mrpack.pack_candidates(p):
        need = mrpack.friend_need(m)
        ja, _ = _ja_for(m.get("slug"), m.get("title"), m.get("description"))
        items.append({"path": m["path"], "title": m["title"], "need": need, "icon": m.get("icon_url"), "ja": ja,
                      "included": need == "required" or m["path"] not in excluded})
    items.sort(key=lambda x: (x["need"] != "required", x["title"].lower()))
    return {"items": items, "address": p.get("public_address") or "", "name": p.name, "mc": p.get("mc"),
            "loader_label": p.loader_label(), "version": p.get("pack_version_out", "1.0.0")}


def pack_set_exclude(server, exclude):
    _prof(server).set(pack_exclude=sorted(set(exclude)))
    return {"ok": True}


_export_lock = threading.Lock()


def _export(id, version=None, configs=True, force=False) -> dict:
    """友達用パックを作る。中身が変わっていなければ作り直さない。変わっていたらバージョンを1つ上げる。"""
    import hashlib
    from .paths import safe_name
    with _export_lock:
        p = _prof(id)
        exclude = set(p.get("pack_exclude", []))
        addr = p.get("public_address") or ""
        fp = mrpack.fingerprint(p, addr, configs, exclude)
        exp_dir = os.path.join(p.dir, "exports")
        latest = os.path.join(exp_dir, "latest.mrpack")
        cur = p.get("pack_version_out") or "1.0.0"
        same = fp == p.get("pack_fp") and os.path.exists(latest)
        if same and not force and (not version or version == cur):
            return {"changed": False, "version": cur, "path": _versioned(p, cur), "latest": latest}
        if version and version != cur:
            new_ver = version
        elif p.get("pack_fp") and not same:
            new_ver = mrpack.bump(cur)
        else:
            new_ver = cur
        pack_id = p.get("pack_id") or uuid.uuid4().hex
        os.makedirs(exp_dir, exist_ok=True)
        info = mrpack.export_pack(p, latest, addr or None, new_ver, configs, exclude=exclude,
                                  update_info={"pack_id": pack_id, "version": new_ver, "address": addr, "name": p.name})
        out = os.path.join(exp_dir, f"{safe_name(p.name)}-{safe_name(new_ver)}.mrpack")
        shutil.copyfile(latest, out)
        with open(latest, "rb") as f:
            sha = hashlib.sha256(f.read()).hexdigest()
        snap = _pack_snapshot(p, exclude)
        extra = {}
        old_snap = p.get("pack_mods")
        if isinstance(old_snap, dict) and new_ver != cur:
            ch = _pack_diff(old_snap, snap)
            ch["other"] = not (ch["added"] or ch["removed"] or ch["updated"])
            log = [e for e in (p.get("pack_log") or []) if isinstance(e, dict)]
            log.append({"version": new_ver, "time": time.time(), **ch})
            extra["pack_log"] = log[-15:]
            res_changes = ch
        else:
            res_changes = None
        p.set(pack_fp=fp, pack_version_out=new_ver, pack_id=pack_id, pack_sha256=sha,
              pack_size=os.path.getsize(latest), pack_time=time.time(), pack_mods=snap, **extra)
        return {"changed": True, "changes": res_changes, "version": new_ver, "path": out, "latest": latest, **info}


def _pack_snapshot(p, exclude) -> dict:
    """いま友達用パックに入るMODの一覧（変更点を計算するため）。"""
    snap = {}
    for m in mrpack.pack_candidates(p):
        need = mrpack.friend_need(m)
        if need == "optional" and m["path"] in exclude:
            continue
        key = str(m.get("project_id") or m.get("slug") or m["path"])
        snap[key] = {"title": m.get("title") or os.path.basename(m["path"]),
                     "ver": str(m.get("version_number") or ""), "sha1": str(m.get("sha1") or "")}
    return snap


def _pack_diff(old: dict, new: dict) -> dict:
    added = sorted(v["title"] for k, v in new.items() if k not in old)
    removed = sorted(v["title"] for k, v in old.items() if k not in new)
    updated = [{"title": v["title"], "from": old[k]["ver"], "to": v["ver"]}
               for k, v in sorted(new.items(), key=lambda kv: kv[1]["title"].lower())
               if k in old and (old[k]["sha1"] != v["sha1"] or old[k]["ver"] != v["ver"])]
    return {"added": added, "removed": removed, "updated": updated}


def merge_pack_changes(log, from_ver, to_ver) -> dict:
    """友達の持っているバージョンから最新までの変更点を、ひとまとめにする。"""
    def vt(v):
        return tuple(int(x) for x in re.findall(r"\d+", str(v or ""))) or (0,)
    added, removed, updated, other = [], [], {}, False
    lo, hi = vt(from_ver), vt(to_ver)
    n = 0
    for e in sorted((x for x in (log or []) if isinstance(x, dict)), key=lambda x: vt(x.get("version"))):
        if not (lo < vt(e.get("version")) <= hi):
            continue
        n += 1
        other = other or bool(e.get("other"))
        for a in e.get("added", []):
            if a in removed:
                removed.remove(a)
            elif a not in added:
                added.append(a)
        for r in e.get("removed", []):
            updated.pop(r, None)
            if r in added:
                added.remove(r)
            elif r not in removed:
                removed.append(r)
        for u in e.get("updated", []):
            if u.get("title") in added:
                continue
            old = updated.get(u["title"])
            updated[u["title"]] = {"title": u["title"], "from": old["from"] if old else u.get("from", ""), "to": u.get("to", "")}
    return {"added": sorted(added), "removed": sorted(removed), "updated": list(updated.values()),
            "other": other, "known": n > 0}


def _versioned(p, version):
    from .paths import safe_name
    return os.path.join(p.dir, "exports", f"{safe_name(p.name)}-{safe_name(version)}.mrpack")


def _pack_serve_info(id):
    try:
        p = _prof(id)
    except UserError:
        return None
    latest = os.path.join(p.dir, "exports", "latest.mrpack")
    if not os.path.exists(latest) or not p.get("pack_id"):
        return None
    return ({"pack_id": p.get("pack_id"), "version": p.get("pack_version_out"), "sha256": p.get("pack_sha256"),
             "size": p.get("pack_size"), "name": p.name, "mc": p.get("mc"), "loader": p.get("loader"),
             "changes_log": [e for e in (p.get("pack_log") or []) if isinstance(e, dict)][-15:]}, latest)


def pack_export(server, version=None, configs=True):
    r = _export(server, version or None, configs, force=True)
    if not os.path.exists(r["path"]):
        shutil.copyfile(r["latest"], r["path"])
    info = mrpack.pack_info(r["path"])
    manual = 0
    import zipfile
    with zipfile.ZipFile(r["path"]) as z:
        manual = len([n for n in z.namelist() if n.startswith("overrides/mods/")])
    return {"path": r["path"], "file": os.path.basename(r["path"]), "files": len(info["files"]),
            "manual": manual, "version": r["version"]}


def pack_status(server):
    p = _prof(server)
    gw = gateways.get(server)
    addr = p.get("public_address") or ""
    fp_now = mrpack.fingerprint(p, addr, True, set(p.get("pack_exclude", [])))
    return {"enabled": p.get("pack_server", True), "version": p.get("pack_version_out"),
            "exported": bool(p.get("pack_fp")), "outdated": bool(p.get("pack_fp")) and fp_now != p.get("pack_fp"),
            "auto": bool(p.get("pack_auto", True)), "auto_time": p.get("pack_auto_time") or 0,
            "auto_text": p.get("pack_auto_text") or "",
            "gateway": bool(gw and gw.alive), "downloads": gw.pack_downloads if gw else 0,
            "connections": gw.connections if gw else 0, "address": addr,
            "running": bool(runners.get(server) and runners[server].running)}


def join_info(path):
    path = _uploaded(path)
    info = mrpack.pack_info(path)
    files = [f for f in info["files"] if f.get("env", {}).get("client") != "unsupported"]
    names = {}
    hashes = {f["hashes"].get("sha1"): f["path"] for f in files if f.get("hashes", {}).get("sha1")}
    icons = {}
    if hashes:
        try:
            found = net.post_json(f"{modrinth.API}/version_files", {"hashes": list(hashes), "algorithm": "sha1"})
            projs = modrinth.projects([v["project_id"] for v in found.values()]) if found else {}
            for h, v in found.items():
                pr = projs.get(v["project_id"])
                if pr:
                    names[hashes[h]] = pr["title"]
                    icons[hashes[h]] = pr.get("icon_url")
        except net.NetError:
            pass
    return {"name": info["name"], "mc": info["mc"], "loader": info["loader"],
            "loader_label": loaders.LOADER_LABEL.get(info["loader"], info["loader"]), "path": path,
            "files": [{"path": f["path"], "title": names.get(f["path"], os.path.basename(f["path"])),
                       "icon": icons.get(f["path"]),
                       "need": "optional" if f.get("env", {}).get("client") == "optional" else "required"}
                      for f in files],
            "risks": mrpack.pack_risks(path),
            "minecraft_dir": settings.get("minecraft_dir"),
            "minecraft_dir_exists": os.path.isdir(settings.get("minecraft_dir") or ""),
            "mem_gb": max(4, min(8, total_ram_mb() // 1024 // 3))}


def _save_joined(res, mc_dir, mem_gb, skip, local_server=None):
    up = res.get("update") or {}
    joined = settings.setdefault("joined", {})
    joined[res["profile_key"]] = {
        "name": res["name"], "mc": res.get("mc"), "loader": res.get("loader"), "game_dir": res["game_dir"],
        "mc_dir": mc_dir, "mem_gb": int(mem_gb), "skip": sorted(skip or []), "version": res.get("pack_version"),
        "pack_id": up.get("pack_id"), "address": up.get("address") or "", "local_server": local_server,
        "updated": time.time()}
    save_settings(settings)


def join(path, skip, mem_gb, minecraft_dir=None, local_server=None):
    path = _uploaded(path)
    mc_dir = minecraft_dir or settings.get("minecraft_dir")
    if local_server:
        _prof(local_server)
    settings["minecraft_dir"] = mc_dir
    save_settings(settings)

    def work(job):
        res = client_install.join_with_pack(path, mc_dir, int(mem_gb), os.path.join(CACHE_DIR, "installers"),
                                            job.write, skip=set(skip or []))
        _save_joined(res, mc_dir, mem_gb, skip, local_server)
        return res
    return start_job("マイクラにMODセットを入れています", work)


def pack_install_self(server):
    r = _export(server)
    info = join_info(r["latest"])
    info["local_server"] = server
    return info


# ---------------- 参加しているサーバー（友達側の自動更新） ----------------

def joined_list():
    return {"joined": [{"key": k, **v} for k, v in sorted(settings.get("joined", {}).items(),
                                                          key=lambda kv: -kv[1].get("updated", 0))]}


def _remote_info(rec):
    if rec.get("local_server"):
        try:
            _export(rec["local_server"])
        except UserError:
            pass
        got = _pack_serve_info(rec["local_server"])
        if not got:
            raise packupdate.UpdateError("サーバーが見つかりません")
        return {**got[0], "_local": got[1]}
    if not rec.get("address"):
        raise packupdate.UpdateError("アドレスがわかりません")
    return packupdate.check(rec["address"])


def joined_check(key=None):
    out = {}
    for k, rec in settings.get("joined", {}).items():
        if key and k != key:
            continue
        if not rec.get("pack_id"):
            out[k] = {"status": "unsupported", "text": "自動更新に対応していないパックです（友達に新しいパックをもらってください）"}
            continue
        try:
            info = _remote_info(rec)
        except (packupdate.UpdateError, net.NetError, OSError) as e:
            out[k] = {"status": "offline", "text": "サーバーにつながりませんでした（サーバーが止まっているかも）", "detail": str(e)}
            continue
        if info.get("pack_id") != rec.get("pack_id"):
            out[k] = {"status": "other", "text": "このアドレスは別のサーバーになっています"}
        elif str(info.get("version")) != str(rec.get("version")):
            out[k] = {"status": "update", "text": f"新しいパックがあります（{rec.get('version')} → {info.get('version')}）",
                      "new": info.get("version"),
                      "changes": merge_pack_changes(info.get("changes_log"), rec.get("version"), info.get("version"))}
        else:
            out[k] = {"status": "latest", "text": f"最新です（{rec.get('version')}）"}
    return {"results": out}


def _do_update(key, job):
    rec = settings.get("joined", {}).get(key)
    if not rec:
        raise UserError("見つかりません")
    info = _remote_info(rec)
    if info.get("pack_id") != rec.get("pack_id"):
        raise UserError("このアドレスは別のサーバーになっています。")
    if str(info.get("version")) == str(rec.get("version")):
        job.write("最新です。")
        return False
    job.write(f"新しいパック {info.get('version')} をダウンロードしています…")
    path = info.get("_local") or packupdate.download(info, os.path.join(CACHE_DIR, "packs"))
    res = client_install.join_with_pack(path, rec["mc_dir"], rec.get("mem_gb", 4), os.path.join(CACHE_DIR, "installers"),
                                        job.write, skip=set(rec.get("skip", [])))
    _save_joined(res, rec["mc_dir"], rec.get("mem_gb", 4), rec.get("skip", []), rec.get("local_server"))
    return True


def joined_update(key):
    return start_job("新しいパックを入れています", lambda job: {"updated": _do_update(key, job)})


def joined_play(key):
    def work(job):
        updated = False
        try:
            job.write("新しいパックがないか確かめています…")
            updated = _do_update(key, job)
        except (packupdate.UpdateError, net.NetError, OSError) as e:
            job.write(f"確かめられませんでした（{e}）。今のまま遊びます。")
        opened = client_install.open_launcher()
        return {"updated": updated, "opened": opened, "name": settings["joined"][key]["name"]}
    return start_job("遊ぶ準備をしています", work)


def joined_remove(key):
    settings.get("joined", {}).pop(key, None)
    save_settings(settings)
    return joined_list()


# ---------------- 起動テスト ----------------

def boot_test(id, accept_eula=False):
    p = _prof(id)
    r = runners.get(id)
    if r and r.running:
        raise UserError("サーバーが動いている間はテストできません。先に止めてください。")
    if id in busy:
        raise UserError("もうテスト中です。")
    if not server.eula_accepted(p):
        if not accept_eula:
            return {"need_eula": True}
        server.accept_eula(p)

    def work(job):
        busy.add(id)
        try:
            job.write("本物のワールドには触らず、テスト用のワールドで起動してみます…")
            res = boottest.run_once(p, job.write, job.cancel)
            if not res["ok"]:
                res["check"] = conflicts.check_server(p)["issues"]
            return res
        except boottest.Cancelled:
            return {"cancelled": True}
        finally:
            busy.discard(id)
    return start_job("起動テストをしています", work)


def find_culprit(id):
    p = _prof(id)
    r = runners.get(id)
    if r and r.running:
        raise UserError("サーバーが動いている間はできません。先に止めてください。")
    if id in busy:
        raise UserError("もうテスト中です。")

    def work(job):
        busy.add(id)
        try:
            return boottest.find_culprit(p, job.write, job.cancel)
        except boottest.Cancelled:
            return {"cancelled": True, "found": False, "message": "途中で止めました。MODは元にもどしました。"}
        finally:
            busy.discard(id)
    return start_job("原因のMODをさがしています", work)


# ---------------- 重さ・事前生成 ----------------

def perf(id):
    p = _prof(id)
    _runner(id)
    slugs = {m.get("slug") for m in ModList(p.dir).items if m.get("enabled", True)}
    out = metrics.summary(monitors.get(id), p, slugs)
    r = runners.get(id)
    out["running"] = bool(r and r.state == "running")
    out["vanilla"] = p.get("loader") == "vanilla"
    return out


def pregen_status(id):
    p = _prof(id)
    _runner(id)
    r = runners[id]
    t = trackers.get(id)
    return {"supported": p.get("loader") != "vanilla", "installed": pregen.installed(p),
            "running": r.state == "running", "needs_restart": pregen.installed(p) and r.running and not _chunky_loaded(id),
            "state": t.state if t else {}, "dims": pregen.DIMENSIONS,
            "estimates": {r_: pregen.estimate(r_) for r_ in (500, 1000, 2000, 3000, 5000)}}


def _chunky_loaded(id):
    con = consoles.get(id)
    if not con:
        return False
    lines, _ = con.since(0)
    started = max((i for i, l in enumerate(lines) if l.startswith("▶")), default=0)
    return any("chunky" in l.lower() for l in lines[started:]) or bool(trackers.get(id) and trackers[id].state)


def pregen_install(id):
    p = _prof(id)

    def work(job):
        r_, e = install_many(p, ["chunky"], job.write)
        if e:
            raise UserError("Chunky を入れられませんでした: " + "、".join(e))
        return _result_dict(r_, e)
    return start_job("Chunky を入れています", work)


def pregen_start(id, radius, dim="minecraft:overworld", center="spawn"):
    r = _runner(id)
    if r.state != "running":
        raise UserError("サーバーを起動してから始めてください。")
    radius = int(radius)
    if not (100 <= radius <= 30000):
        raise UserError("範囲は 100〜30000 ブロックにしてください。")
    trackers[id].state[dim] = {"status": "started", "percent": 0.0, "updated": time.time(), "radius": radius}
    target = dim
    p = _prof(id)
    if p.uses_plugins:
        target = pregen.bukkit_world(dim, p.props().get("level-name", "world"))
        trackers[id].aliases[target] = dim
    for c in pregen.commands(target, radius, center):
        r.send(c)
        time.sleep(0.4)
    return {"ok": True}


def pregen_cmd(id, action):
    if action not in ("pause", "continue", "cancel"):
        raise UserError("おかしな操作です")
    r = _runner(id)
    if not r.send(f"chunky {action}"):
        raise UserError("サーバーが動いていません。")
    if action == "cancel":
        r.send("chunky confirm")
    return {"ok": True}


# ============================================================ ワンクリック最適化・重さの原因さがし

def _mods_count(p):
    return len([m for m in ModList(p.dir).items if m.get("enabled", True) and m.get("location") == "server"])


def optimize_plan(id):
    p = _prof(id)
    mods = _mods_count(p)
    rec = memory_advice(id)["recommended"]
    items = optimize.plan(p, total_ram_mb(), os.cpu_count() or 4, mods, rec)
    slugs = {m.get("slug") for m in ModList(p.dir).items if m.get("enabled", True)}
    perf_mods = [{"id": s_, "title": n, "desc": d} for s_, n, d in metrics.PERF_MODS.get(p.get("loader"), [])
                 if s_ not in slugs]
    return {"items": items, "perf_mods": perf_mods, "cpus": os.cpu_count() or 4, "ram_mb": total_ram_mb(),
            "mods": mods, "undo_at": optimize.has_undo(p), "running": bool(runners.get(id) and runners[id].running),
            "jvm": "Aikar's Flags（" + ("12GB 以上用" if int(p.get("memory_mb", 4096)) >= 12288 else "普通") + "）を自動で使っています"}


def optimize_apply(id, ids):
    p = _prof(id)
    want = set(ids or [])
    items = [it for it in optimize_plan(id)["items"] if it["id"] in want and it["kind"] != "info"]
    if not items:
        raise UserError("変える項目を選んでください。")
    done = optimize.apply(p, items, server.write_props, lambda mb: p.set(memory_mb=mb))
    admin.audit(p.dir, "ワンクリック最適化", "、".join(done))
    r = runners.get(id)
    return {"done": done, "restart": bool(r and r.running)}


def optimize_undo(id):
    p = _prof(id)
    n = optimize.undo(p, server.write_props, lambda mb: p.set(memory_mb=mb))
    if not n:
        raise UserError("元にもどす記録がありません。")
    admin.audit(p.dir, "最適化を元にもどす", f"{n}個")
    return {"restored": n}


def lag_scan(id):
    r = _runner(id)
    if r.state != "running":
        raise UserError("サーバーを起動してから調べてください。")
    players = sorted(r.players)

    def work(job):
        res = optimize.scan(captures[id], players, job.write, cancel=job.cancel.is_set)
        last_scan[id] = res
        return res
    return start_job("重さの原因をさがしています", work)


last_scan: dict[str, dict] = {}


def lag_clear(id, types, warn_seconds=0):
    r = _runner(id)
    if r.state != "running":
        raise UserError("サーバーが動いていません。")
    cmds = optimize.clear_commands(types or [])
    if not cmds:
        raise UserError("消すものを選んでください。")
    names = "・".join(optimize.CLEAR_TYPES[t] for t in types if t in optimize.CLEAR_TYPES)
    warn_seconds = max(0, min(120, int(warn_seconds or 0)))

    def go():
        if warn_seconds:
            r.send(f"say [お掃除] {warn_seconds}秒後に、{names}を消します。大事なものは拾ってください")
            time.sleep(warn_seconds)
        for c in cmds:
            captures[id].run_until(c, r"Killed|No entity|entities", 2.0, hide=False)
        r.send(f"say [お掃除] {names}を消しました")
    if warn_seconds:
        threading.Thread(target=go, daemon=True).start()
    else:
        go()
    admin.audit(_prof(id).dir, "お掃除", names)
    return {"ok": True, "later": bool(warn_seconds)}


def startup():
    """ソフトを起動したとき：前回の起動テストで一時的にどけたMODがあれば、元にもどす。見張り係を動かす。"""
    for p in server.list_profiles():
        try:
            boottest.restore(p)
        except OSError:
            pass
    try:
        updater.cleanup()
    except Exception:  # noqa: BLE001
        pass
    start_scheduler()
    threading.Thread(target=_auto_update_check, daemon=True).start()


def _auto_update_check():
    """起動の少しあとに、新しい版がないか、こっそり調べる（設定でオフにできる）。"""
    time.sleep(8)
    if settings.get("auto_update_check", True):
        try:
            updater.check()
        except Exception:  # noqa: BLE001
            pass


# ============================================================ 見張り係（自動お掃除・自動停止など）

_sched = {"thread": None}
sched_state: dict[str, dict] = {}
SCHED_SEC = 5


def start_scheduler():
    if _sched["thread"] and _sched["thread"].is_alive():
        return
    t = threading.Thread(target=_scheduler_loop, daemon=True)
    _sched["thread"] = t
    t.start()


_tick_n = [0]


def _scheduler_loop():
    while True:
        time.sleep(SCHED_SEC)
        try:
            scheduler_tick()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        _tick_n[0] += 1
        if _tick_n[0] % 120 == 0:  # 約10分ごとに、1日1回の点検が必要か見る
            try:
                _daily_world_check()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            try:   # 新しい版の確認は、半日に1回
                if settings.get("auto_update_check", True) and time.time() - updater.last_checked() > 12 * 3600:
                    updater.check()
            except Exception:  # noqa: BLE001
                pass


_pack_watch: dict[str, dict] = {}


def _pack_auto_tick(now: float):
    """MODが変わったら、少し待ってから、友達用パックを自動で作り直す（友達側に「更新があります」が出る）。"""
    for p in server.list_profiles():
        id = os.path.basename(p.dir)
        st = _pack_watch.setdefault(id, {"next": 0.0, "fp": None, "since": 0.0})
        if now < st["next"]:
            continue
        st["next"] = now + 15
        if not p.get("pack_fp") or not p.get("pack_auto", True) or not p.get("pack_server", True) or id in busy:
            st["fp"] = None
            continue
        try:
            fp = mrpack.fingerprint(p, p.get("public_address") or "", True, set(p.get("pack_exclude", [])))
        except Exception:  # noqa: BLE001
            continue
        if fp == p.get("pack_fp"):
            st["fp"] = None
            continue
        if st["fp"] != fp:           # 変わった直後。落ちつくまで（15秒）待つ
            st["fp"], st["since"] = fp, now
            continue
        if now - st["since"] < 15:
            continue
        try:
            res = _export(id)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            p.set(pack_auto_text=f"自動で作り直せませんでした（{e}）", pack_auto_time=time.time())
            st["next"] = now + 120
            continue
        st["fp"] = None
        if res.get("changed"):
            ch = res.get("changes") or {}
            bits = ([f"＋{x}" for x in ch.get("added", [])] + [f"↑{u['title']}" for u in ch.get("updated", [])]
                    + [f"－{x}" for x in ch.get("removed", [])])
            msg = f"MODの変更に合わせて、友達用パックを {res['version']} に自動で作り直しました" + (f"（{'、'.join(bits[:6])}）" if bits else "")
            p.set(pack_auto_text=msg, pack_auto_time=time.time())
            consoles.setdefault(id, Console()).add("📦 " + msg + "。友達のソフトに「更新があります」と出ます")


def scheduler_tick(now: float | None = None):
    now = now or time.time()
    try:
        _schedule_tick(now)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    try:
        _pack_auto_tick(now)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    for id, r in list(runners.items()):
        st = sched_state.setdefault(id, {})
        if r.state != "running" or id in busy:
            st.clear()
            continue
        p = r.prof
        con = consoles.setdefault(id, Console())
        st.setdefault("since", now)
        # --- 自動お掃除（落ちているアイテム） ---
        every = int(p.get("auto_clear_min", 0) or 0)
        if every > 0:
            nxt = st.setdefault("clear_at", now + every * 60)
            if not st.get("warned") and now >= nxt - 30:
                r.send("say [お掃除] 30秒後に、落ちているアイテムを消します。大事なものは拾ってください")
                st["warned"] = True
            if now >= nxt:
                captures[id].run_until("kill @e[type=minecraft:item]", r"Killed|No entity|entities", 2.0, hide=False)
                r.send("say [お掃除] 落ちているアイテムを消しました")
                st["clear_at"] = now + every * 60
                st["warned"] = False
        else:
            st.pop("clear_at", None)
        # --- だれもいなくなったら自動で止める ---
        stop_min = int(p.get("auto_stop_min", 0) or 0)
        if stop_min > 0:
            t = trackers.get(id)
            if r.players or (t and t.active):
                st.pop("empty_since", None)
            else:
                st.setdefault("empty_since", max(now, st["since"]))
                left = st["empty_since"] + stop_min * 60 - now
                if left <= 0:
                    con.add(f"⏻ だれもいない時間が {stop_min}分 続いたので、サーバーを自動で止めます")
                    admin.audit(p.dir, "自動停止", f"{stop_min}分だれもいなかった")
                    st.clear()
                    threading.Thread(target=r.stop, daemon=True).start()
        else:
            st.pop("empty_since", None)
        for hook in scheduler_hooks:
            try:
                hook(id, r, now)
            except Exception:  # noqa: BLE001
                traceback.print_exc()


scheduler_hooks: list = []


def auto_stop_left(id):
    st = sched_state.get(id) or {}
    p = _prof(id)
    m = int(p.get("auto_stop_min", 0) or 0)
    if not m or "empty_since" not in st:
        return None
    return max(0, int(st["empty_since"] + m * 60 - time.time()))


# ============================================================ バージョン変更（アップデート）

def version_options(id):
    p = _prof(id)
    cands = upgrade.candidates(p)
    loader = p.get("loader")
    newer_loader = None
    if loader not in ("vanilla",):
        try:
            lv = loaders.loader_version(loader, p.get("mc"))
            if lv and lv != "-" and str(lv) != str(p.get("loader_version")):
                newer_loader = lv
        except net.NetError:
            pass
    return {"mc": p.get("mc"), "loader": loader, "loader_label": p.loader_label(),
            "loader_version": p.get("loader_version"), "newer_loader": newer_loader,
            "candidates": cands[:12], "upgraded_from": p.get("upgraded_from")}


def version_plan(id, mc):
    p = _prof(id)
    return upgrade.plan(p, mc)


def version_change(id, mc):
    p = _prof(id)
    r = runners.get(id)
    if r and r.running:
        raise UserError("先にサーバーを止めてください。")
    if id in busy:
        raise UserError("起動テスト中です。")

    def work(job):
        busy.add(id)
        try:
            res = upgrade.run(p, mc, server.make_backup, job.write)
        finally:
            busy.discard(id)
        admin.audit(p.dir, "バージョンを変更", f"{res['from']} → {res['mc']}")
        return res
    return start_job(f"マイクラ {mc} に変えています", work)



# ============================================================ 見張り（通知・荒らし検知・プレイ時間）

intrusions: dict[str, watch.Intrusions] = {}
playtimes: dict[str, watch.Playtime] = {}
player_ips: dict[str, dict[str, str]] = {}
_banned_cache: dict[str, tuple] = {}
_lag_state: dict[str, dict] = {}


def _intr(id) -> watch.Intrusions:
    if id not in intrusions:
        intrusions[id] = watch.Intrusions(_prof(id).dir)
    return intrusions[id]


def _play(id) -> watch.Playtime:
    if id not in playtimes:
        playtimes[id] = watch.Playtime(_prof(id).dir)
    return playtimes[id]


def _notify(id, kind, title, text="", desktop=False):
    p = _prof(id)
    url = settings.get("discord_webhook")
    events = p.get("discord_events", DISCORD_DEFAULT)
    if url and kind in events:
        watch.discord_send(url, title, text, kind, p.name)
    if desktop and settings.get("desktop_notify", True):
        watch.desktop_notify(f"{p.name}: {title}", text)


def _state_notify(id, state, r):
    p = _prof(id)
    if state == "running":
        _notify(id, "start", "🟢 サーバーが起動しました", f"マイクラ {p.get('mc')}・{p.loader_label()}\n"
                + (f"アドレス: {p.get('public_address')}" if p.get("public_address") else ""))
    elif state == "stopped":
        _play(id).close_all()
        if r and r.last_exit and r.last_exit.get("crashed"):
            _notify(id, "crash", "🔴 サーバーが落ちました", "原因はソフトの画面で確認できます。"
                    + ("自動で再起動します。" if p.get("auto_restart", True) and not r.last_exit.get("during_start") else ""),
                    desktop=True)
        elif r and r.last_exit is not None:
            _notify(id, "stop", "⚪ サーバーが止まりました")


def _watch_line(id, line):
    if "logged in with entity id" in line:
        m = watch.LOGIN_RE.search(line)
        if m:
            ip = m.group(2).strip("[]")
            gw = gateways.get(id)
            if gw and ip in ("127.0.0.1", "::1", "0:0:0:0:0:0:0:1"):
                ip = gw.real_ip(int(m.group(3))) or ip
            player_ips.setdefault(id, {})[m.group(1)] = ip
        return False
    if " joined the game" in line:
        m = watch.JOIN_RE.search(line)
        if m:
            _play(id).join(m.group(1))
            r = runners.get(id)
            n = len(r.players) + (0 if r and m.group(1) in r.players else 1) if r else 1
            _notify(id, "join", f"👋 {m.group(1)} が入りました", f"今 {n}人")
            _send_rules(id, m.group(1))
        return False
    if " left the game" in line:
        m = watch.LEFT_RE.search(line)
        if m:
            _play(id).leave(m.group(1))
            _notify(id, "join", f"🚪 {m.group(1)} が出ました")
        return False
    ev = watch.parse_reject(line)
    if ev:
        gw = gateways.get(id)
        if gw and ev.get("port") and ev.get("ip") in ("127.0.0.1", "::1", None):
            ev["ip"] = gw.real_ip(ev["port"]) or ev["ip"]
        ev["kind"] = "rejected"
        if _intr(id).add(ev):
            who = ev.get("name") or "だれか"
            msg = f"{who}（{ev.get('ip') or 'IP不明'}）が入ろうとしました（{ev['reason']}ので、ことわりました）"
            consoles.setdefault(id, Console()).add("🚨 " + msg)
            _notify(id, "intrusion", "🚨 知らない人が入ろうとしました", msg, desktop=True)
    return False


def _gateway_event(id, kind, ip, info=None):
    ev = {"kind": kind, "ip": ip, "name": None, "time": time.time(),
          "reason": {"blocked": "IP BAN している", "flood": "短い時間に何度もつないできた"}.get(kind, kind)}
    if _intr(id).add(ev):
        msg = (f"BAN している IP（{ip}）からつなごうとしたので、ことわりました" if kind == "blocked"
               else f"{ip} から1分に {info.get('count') if info else '?'}回以上つないできたので、5分間ことわります（いたずら・攻撃かもしれません）")
        consoles.setdefault(id, Console()).add("🚨 " + msg)
        _notify(id, "intrusion", "🚨 あやしいアクセス", msg, desktop=kind == "flood")


def _banned_ips(id) -> set:
    path = os.path.join(_prof(id).dir, "banned-ips.json")
    try:
        st = os.stat(path)
    except OSError:
        return set()
    key = (st.st_mtime_ns, st.st_size)
    cached = _banned_cache.get(id)
    if cached and cached[0] == key:
        return cached[1]
    ips = {b.get("ip") for b in (server.load_json(path, []) or []) if b.get("ip")}
    _banned_cache[id] = (key, ips)
    return ips


def _lag_hook(id, r, now):
    mon = monitors.get(id)
    if not mon or mon.tps is None:
        return
    st = _lag_state.setdefault(id, {"low_since": None, "notified": 0})
    if mon.tps < 12:
        st["low_since"] = st["low_since"] or now
        if now - st["low_since"] >= 120 and now - st["notified"] > 1800:
            st["notified"] = now
            consoles.setdefault(id, Console()).add(f"🐢 サーバーが重くなっています（TPS {mon.tps:.1f}）。「重さ・最適化」タブで原因をさがせます")
            _notify(id, "lag", "🐢 サーバーが重くなっています", f"TPS {mon.tps:.1f}（20 が快適）が2分以上続いています。"
                    "「重さ・最適化」タブで原因をさがせます。")
    else:
        st["low_since"] = None


scheduler_hooks.append(_lag_hook)


def notify_get(id=None):
    url = settings.get("discord_webhook") or ""
    out = {"webhook_set": bool(url), "webhook_hint": (url[:40] + "…") if url else "", "name": watch.sender_name(),
           "desktop": settings.get("desktop_notify", True), "events": DISCORD_EVENTS,
           "error": watch.last_error["text"]}
    if id:
        out["on"] = _prof(id).get("discord_events", DISCORD_DEFAULT)
    return out


def notify_set(id=None, webhook=None, events=None, desktop=None, name=None):
    if name is not None:
        n = admin.text(name, 80).strip()
        if not n:
            raise UserError("名前を入れてください。")
        if re.search(r"discord|clyde|@|#|:|```", n, re.I):
            raise UserError("Discord の決まりで、名前に「discord」「clyde」や @ # : は使えません。")
        settings["discord_name"] = n
    if webhook is not None:
        webhook = webhook.strip()
        if webhook and not watch.valid_webhook(webhook):
            raise UserError("Discord の Webhook URL の形がおかしいです（https://discord.com/api/webhooks/… をそのまま貼ってください）")
        settings["discord_webhook"] = webhook
    if desktop is not None:
        settings["desktop_notify"] = bool(desktop)
    save_settings(settings)
    if id and events is not None:
        _prof(id).set(discord_events=[e for e in events if e in DISCORD_EVENTS])
    return notify_get(id)


def notify_test(webhook=None):
    url = (webhook or "").strip() or settings.get("discord_webhook") or ""
    if not url:
        raise UserError("先に Webhook URL を入れてください。")
    try:
        watch.discord_send_now(url, "✅ テストです", "管理者からの通知が、ここに届きます。")
    except ValueError as e:
        raise UserError(str(e)) from e
    return {"ok": True}


def intrusions_get(id):
    items = _intr(id).items
    return {"items": [{**x, "first_text": time.strftime("%m/%d %H:%M", time.localtime(x.get("first", 0))),
                       "last_text": time.strftime("%m/%d %H:%M", time.localtime(x.get("last", 0)))} for x in items],
            "banned_ips": sorted(_banned_ips(id))}


def applicants(id):
    """「入りたい」と来た人（ホワイトリストにいなくて、ことわられた人）の一覧。承認すると名簿に入る。"""
    p = _prof(id)
    have = {str(e.get("name", "")).lower() for e in server.read_list(p, "whitelist")}
    gone = {str(n).lower() for n in (p.get("applicant_dismissed") or [])}
    out = []
    for x in _intr(id).items:
        n = x.get("name")
        if x.get("kind") != "rejected" or not n or "ホワイトリスト" not in (x.get("reason") or ""):
            continue
        if n.lower() in have or n.lower() in gone or any(o["name"].lower() == n.lower() for o in out):
            continue
        out.append({"name": n, "count": x.get("count", 1), "last_text": time.strftime("%m/%d %H:%M", time.localtime(x.get("last", 0)))})
    return {"items": out, "enabled": p.props().get("white-list", "false") == "true"}


def applicant_dismiss(id, name):
    p = _prof(id)
    cur = list(p.get("applicant_dismissed") or [])
    if name and name not in cur:
        cur.append(name)
    p.set(applicant_dismissed=cur[-200:])
    return applicants(id)


def intrusions_clear(id):
    _intr(id).clear()
    return {"ok": True}


def playtime(id, days=30):
    days = max(1, min(365, int(days or 30)))
    out = _play(id).summary(days)
    for p_ in out["ranking"]:
        p_["text"] = watch.fmt_minutes(p_["minutes"])
        p_["last_text"] = time.strftime("%m/%d %H:%M", time.localtime(p_["last"]))
    out["total_text"] = watch.fmt_minutes(out["total_minutes"])
    return out

# ============================================================ PC起動時に自動で起動

def autostart_get():
    return {"supported": autostart.supported(), "enabled": autostart.is_enabled(),
            "servers": [{"id": os.path.basename(p.dir), "name": p.name, "on": bool(p.get("autostart"))}
                        for p in server.list_profiles()]}


def autostart_set(enabled=None, server_id=None, on=None):
    if server_id is not None:
        p = _prof(server_id)
        p.set(autostart=bool(on))
    if enabled is not None:
        try:
            autostart.set_enabled(bool(enabled))
        except (RuntimeError, OSError) as e:
            raise UserError(str(e)) from e
    return autostart_get()


def autostart_servers() -> list[str]:
    """PC起動時：「自動で起動」をONにしたサーバーを動かす（EULAに同意ずみのものだけ）。"""
    started = []
    for p in server.list_profiles():
        id = os.path.basename(p.dir)
        if p.get("autostart") and server.eula_accepted(p):
            try:
                server_start(id, skip_deps=True)  # 人がいない起動では、確認の画面を出せない
                started.append(id)
            except Exception as e:  # noqa: BLE001
                consoles.setdefault(id, Console()).add(f"⚠ 自動起動できませんでした: {e}")
    return started


def open_launcher():
    if not client_install.open_launcher():
        raise UserError("ランチャーが見つかりませんでした。ふだん通りマイクラのランチャーを開いてください。")
    return {"ok": True}


# ============================================================ 友達を呼ぶ（公開・ポート）

playit_proc = None


def public_info(id):
    p = _prof(id)
    port = int(p.props().get("server-port", "25565") or 25565)
    r = runners.get(id)
    running = bool(r and r.running)
    lan = network.local_ip()
    return {"address": p.get("public_address") or "", "port": port, "local_ip": lan,
            "lan_address": lan if port == 25565 else f"{lan}:{port}",
            "gateway": network.gateway_ip(),
            "port_free": True if running or (gateways.get(id) and gateways[id].alive and gateways[id].public_port == port)
            else network.port_free(port),
            "pack_server": p.get("pack_server", True),
            "port_mismatch": _port_mismatch(p.get("public_address") or "", port),
            "running": running, "playit": bool(playit_proc and playit_proc.poll() is None)}


def _port_mismatch(address, port) -> bool:
    """アドレスに書いたポート番号が、サーバーのポート番号とちがう（ポート開放・同じWi-Fiのとき）。"""
    try:
        host, aport, explicit = mcping.parse_address(address)
    except ValueError:
        return False
    if not address or not re.fullmatch(r"[\d.]+", host):
        return False  # playit などのドメインは、ポートがちがって普通
    return (aport if explicit else 25565) != port


def set_address(id, address):
    _prof(id).set(public_address=(address or "").strip())
    return {"ok": True}


def playit_toggle():
    global playit_proc
    if playit_proc and playit_proc.poll() is None:
        playit_proc.terminate()
        playit_proc = None
        return {"playit": False}

    def work(job):
        global playit_proc
        playit_proc = network.start_playit(job.write)
        return {"playit": True}
    return start_job("playit を準備しています", work)


def upnp_open(id):
    port = int(_prof(id).props().get("server-port", "25565") or 25565)
    try:
        addr = network.upnp_open(port, lambda *_: None)
    except network.UpnpError as e:
        raise UserError(str(e)) from e
    _prof(id).set(public_address=addr)
    return {"address": addr}


def upnp_close(id):
    port = int(_prof(id).props().get("server-port", "25565") or 25565)
    try:
        network.upnp_close(port)
    except network.UpnpError as e:
        raise UserError(str(e)) from e
    return {"ok": True}


def firewall(id):
    port = int(_prof(id).props().get("server-port", "25565") or 25565)
    try:
        network.firewall_allow(port)
    except RuntimeError as e:
        raise UserError(str(e)) from e
    return {"ok": True}


def server_ping(address):
    if not address:
        raise UserError("アドレスを入れてください。")
    import socket
    try:
        return mcping.ping(address)
    except socket.gaierror:
        msg = "アドレスが見つかりません（打ちまちがいがないか確認してください）"
    except ConnectionRefusedError:
        msg = "相手は見つかりましたが、サーバーが動いていないか、ポート番号がちがいます"
    except (TimeoutError, socket.timeout):
        msg = "時間切れです（外から届いていません。playit が動いているか、ポート開放ができているか確認してください）"
    except ValueError:
        msg = "アドレスの書き方がおかしいです（例: xxxx.joinmc.link や 1.2.3.4:25565）"
    except (OSError, ConnectionError) as e:
        msg = f"つながりませんでした（{e}）"
    return {"ok": False, "error": msg}


def global_ip():
    return {"ip": network.global_ip()}


def reach_test(id, address=None):
    """外から届くか（外のサービスに試してもらう）＋届かないときの原因さがし。"""
    p = _prof(id)
    port = int(p.props().get("server-port", "25565") or 25565)
    address = (address or p.get("public_address") or "").strip()
    if not address:
        ip = network.global_ip()
        if not ip:
            raise UserError("インターネットの住所（グローバルIP）が分かりませんでした。")
        address = ip if port == 25565 else f"{ip}:{port}"
    r = runners.get(id)
    running = bool(r and r.state == "running")
    hints = []
    if not running:
        hints.append("サーバーが動いていません。先に「▶ 起動」を押してください。")
    try:
        res = network.reach_test(address)
    except (net.NetError, ValueError) as e:
        raise UserError(f"テスト用のサービスにつながりませんでした: {e}") from e
    res["address"] = address
    if res["online"]:
        res["message"] = "外から届いています！友達はこのアドレスで入れます。"
    else:
        if running:
            if "joinmc.link" in address or "playit" in address:
                hints.append("playit.gg の黒い画面が動いているか、トンネルの Local Port がサーバーのポート（"
                             f"{port}）と同じか確認してください。")
            else:
                hints.append(f"ルーターのポート開放（{port}番・TCP）ができていない可能性が高いです。「UPnPで自動で開放」を試すか、playit.gg を使ってください。")
                hints.append("Windows のファイアウォールが止めているかもしれません。「ファイアウォールで許可」を押してください。")
                gip = network.global_ip()
                if gip and network.is_not_public(gip):
                    hints.append("この回線は、外から直接入れない回線（マンションの共有回線・CGNAT など）かもしれません。その場合は playit.gg を使ってください。")
        res["message"] = "外から届いていません。"
    if res.get("cached"):
        hints.append("※ テスト用のサービスは結果を数分おぼえています。設定を変えた直後は、少し待ってからもう一度試してください。")
    res["hints"] = hints
    return res


# ---------------- DuckDNS（変わらないアドレス） ----------------

def ddns_get(id=None):
    d = settings.get("duckdns") or {}
    out = {"domain": d.get("domain", ""), "set": bool(d.get("token")), "ok": ddns.state["ok"],
           "message": ddns.state["message"], "ip": ddns.state["ip"],
           "last": time.strftime("%H:%M", time.localtime(ddns.state["last"])) if ddns.state["last"] else None}
    if id and d.get("domain"):
        port = int(_prof(id).props().get("server-port", "25565") or 25565)
        host = f"{d['domain']}.duckdns.org"
        out["address"] = host if port == 25565 else f"{host}:{port}"
    return out


def ddns_set(domain="", token="", id=None):
    if not domain and not token:
        settings.pop("duckdns", None)
        save_settings(settings)
        return ddns_get(id)
    old = settings.get("duckdns") or {}
    token = token or old.get("token", "")
    try:
        sub, tok = ddns.validate(domain, token)
    except ValueError as e:
        raise UserError(str(e)) from e
    if not ddns.update(sub, tok):
        raise UserError(ddns.state["message"])
    settings["duckdns"] = {"domain": sub, "token": tok}
    save_settings(settings)
    return ddns_get(id)


def _ddns_hook(id, r, now):
    d = settings.get("duckdns") or {}
    if d.get("token") and ddns.due(now):
        ddns.update(d["domain"], d["token"])


scheduler_hooks.append(_ddns_hook)


# ---------------- 招待リンク・アドレスから参加 ----------------

def invite(id):
    p = _prof(id)
    addr = p.get("public_address") or ""
    return {"address": addr, "link": protocol.link_for(addr) if addr else "", "name": p.name}


def join_from_address(address):
    """友達のサーバーのアドレスから、友達用パックをダウンロードして「参加」の準備をする。"""
    address = (address or "").strip()
    if not protocol.ADDR_RE.match(address):
        raise UserError("アドレスの形がおかしいです（例: example.joinmc.link や 203.0.113.5:25565）")

    def work(job):
        job.write(f"{address} に友達用パックがあるか確かめています…")
        try:
            info_ = packupdate.check(address)
            path = packupdate.download(info_, os.path.join(CACHE_DIR, "packs"))
        except packupdate.UpdateError as e:
            raise RuntimeError("このアドレスから友達用パックをもらえませんでした。サーバーが動いているか、"
                               "アドレスが正しいか確かめてください。ファイル（.mrpack）をもらって入れることもできます。"
                               f"（{e}）") from e
        job.write("ダウンロードしました。中身を調べています…")
        out = join_info(path)
        out["address"] = address
        return out
    return start_job("友達用パックをもらっています", work)


# ============================================================ メンバー

def members(id):
    p = _prof(id)
    return {"whitelist": [e.get("name") for e in server.read_list(p, "whitelist")],
            "ops": [e.get("name") for e in server.read_list(p, "ops")],
            "enabled": p.props().get("white-list", "false") == "true",
            "running": bool(runners.get(id) and runners[id].running)}


def member_add(id, kind, name):
    p = _prof(id)
    name = (name or "").strip()
    if not name:
        raise UserError("名前を入れてください。")
    try:
        admin.player(name)
    except admin.BadInput as e:
        raise UserError(str(e)) from e
    admin.audit(p.dir, "ホワイトリストに追加" if kind == "whitelist" else "OPにする", name)
    r = runners.get(id)
    if r and r.running:
        try:
            name = server.lookup_uuid(name)[0]  # 名前がまちがっていないか先に確かめる
        except ValueError as e:
            raise UserError(str(e)) from e
        except net.NetError:
            pass
        r.send(f"whitelist add {name}" if kind == "whitelist" else f"op {name}")
        time.sleep(1.2)
        return members(id)
    try:
        server.add_to_list(p, kind, name)
    except ValueError as e:
        raise UserError(str(e)) from e
    except net.NetError as e:
        raise UserError("名前を確かめるためにインターネットにつながる必要があります。つながっているか確認してください。") from e
    return members(id)


def member_remove(id, kind, name):
    p = _prof(id)
    try:
        admin.player(name)
    except admin.BadInput as e:
        raise UserError(str(e)) from e
    admin.audit(p.dir, "ホワイトリストから外す" if kind == "whitelist" else "OPをやめる", name)
    r = runners.get(id)
    if r and r.running:
        r.send(f"whitelist remove {name}" if kind == "whitelist" else f"deop {name}")
        time.sleep(1.2)
    else:
        server.remove_from_list(p, kind, name)
    return members(id)


def whitelist_toggle(id, on):
    p = _prof(id)
    v = "true" if on else "false"
    server.write_props(p.props_path, {"white-list": v, "enforce-whitelist": v})
    r = runners.get(id)
    if r and r.running:
        r.send("whitelist on" if on else "whitelist off")
    return members(id)


# ============================================================ バックアップ

def backups(id):
    p = _prof(id)
    out = []
    for path in server.list_backups(p):
        fn = os.path.basename(path)
        stamp, kind = fn[:-4].split("_", 1) if "_" in fn else (fn, "")
        when = f"{stamp[0:4]}/{stamp[4:6]}/{stamp[6:8]} {stamp[9:11]}:{stamp[11:13]}"
        out.append({"file": fn, "when": when, "kind": {"auto": "自動", "manual": "手動", "start": "起動前",
                                                       "before-rollback": "巻き戻し前"}.get(kind, kind),
                    "size_mb": round(os.path.getsize(path) / 1024 / 1024, 1), "t": server.backup_time(path)})
    return {"backups": out, "interval": p.get("backup_interval_min", 30), "keep": p.get("backup_keep", 10),
            "backup_on_start": p.get("backup_on_start", True), "rollback": rollback_targets(id)["targets"]}


ROLLBACK_POINTS = [(15, "15分前"), (60, "1時間前"), (180, "3時間前"), (24 * 60, "昨日（24時間前）")]


def rollback_targets(id):
    """「◯分前」に一番近い（それより前の）バックアップを選ぶ。"""
    p = _prof(id)
    lst = [(server.backup_time(b), b) for b in server.list_backups(p)]
    lst = [(t, b) for t, b in lst if t and not b.endswith("_before-rollback.zip")]
    now = time.time()
    out = []
    for minutes, label in ROLLBACK_POINTS:
        cands = [(t, b) for t, b in lst if t <= now - minutes * 60]
        if cands:
            t, b = max(cands)
            ago = int((now - t) // 60)
            out.append({"minutes": minutes, "label": label, "file": os.path.basename(b),
                        "when": time.strftime("%m/%d %H:%M", time.localtime(t)),
                        "ago": f"{ago // 60}時間{ago % 60}分前" if ago >= 60 else f"{ago}分前"})
        else:
            out.append({"minutes": minutes, "label": label, "file": None})
    return {"targets": out}


def rollback(id, file):
    """サーバーを止めて → 今のワールドを念のため保存 → 選んだバックアップにもどす → 動いていたら起動し直す。"""
    p = _prof(id)
    path = os.path.join(server.backups_dir(p), os.path.basename(file or ""))
    if not file or not os.path.exists(path):
        raise UserError("バックアップが見つかりません。")
    r = runners.get(id)
    was_running = bool(r and r.running)

    def work(job):
        if was_running:
            job.write("サーバーに知らせて、止めています…")
            r.send("say [巻き戻し] 10秒後にワールドを巻き戻します。いったんサーバーから出されます")
            for _ in range(20):
                if job.cancel.is_set():
                    return {"cancelled": True}
                time.sleep(0.5)
            r.stop()
            for _ in range(240):
                if not r.running:
                    break
                time.sleep(0.5)
            if r.running:
                raise RuntimeError("サーバーが止まりませんでした")
        if os.path.isdir(p.world_dir()):
            job.write("念のため、今のワールドを保存しています（巻き戻し前）…")
            server.make_backup(p, "before-rollback")
        job.write(f"{os.path.basename(path)} にもどしています…")
        server.restore_backup(p, path)
        admin.audit(p.dir, "ワールドを巻き戻し", os.path.basename(path))
        if was_running:
            job.write("サーバーを起動しています…")
            server_start(id, accept_eula=True, skip_deps=True)
        return {"ok": True, "restarted": was_running}
    return start_job("ワールドを巻き戻しています", work)


def backup_now(id):
    p = _prof(id)
    r = runners.get(id)

    def work(job):
        job.write("ワールドを保存しています…")
        path = r.backup() if r else server.make_backup(p, "manual")
        return {"file": os.path.basename(path)}
    return start_job("バックアップしています", work)


def backup_restore(id, file):
    p = _prof(id)
    r = runners.get(id)
    if r and r.running:
        raise UserError("先にサーバーを止めてください。")
    path = os.path.join(server.backups_dir(p), os.path.basename(file))
    if not os.path.exists(path):
        raise UserError("バックアップが見つかりません。")
    server.restore_backup(p, path)
    return {"ok": True}


# ============================================================ その他

def dist_export():
    """友達に配る用の zip を作る。data フォルダ（設定・鍵・ワールド）は入れない。"""
    import zipfile
    from .runtime import APP_DIR
    skip_dirs = {"data", "__pycache__", "build", "dist", ".git"}
    desk = os.path.join(os.path.expanduser("~"), "Desktop")
    out_dir = desk if os.path.isdir(desk) else os.path.expanduser("~")
    out = os.path.join(out_dir, "OneClickServerMC_配布用.zip")
    root_name = "OneClickServerMC"
    n = 0
    with zipfile.ZipFile(out + ".tmp", "w", zipfile.ZIP_DEFLATED) as z:
        for r, dirs, files in os.walk(APP_DIR):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for f in files:
                if f.endswith((".zip", ".tmp", ".log")) or f in ("settings.json", "instance.json"):
                    continue
                full = os.path.join(r, f)
                z.write(full, os.path.join(root_name, os.path.relpath(full, APP_DIR)))
                n += 1
    with zipfile.ZipFile(out + ".tmp") as z:  # 念のため、中に data や設定が入っていないか確かめる
        bad = [x for x in z.namelist() if "/data/" in x or x.endswith(("settings.json", "instance.json"))]
    if bad:
        os.remove(out + ".tmp")
        raise UserError("書き出しを中止しました（設定ファイルが入りそうだったため）。")
    os.replace(out + ".tmp", out)
    return {"path": out, "files": n}


def open_folder(id=None, which="server", path=None):
    if path:
        target = os.path.dirname(path) if os.path.isfile(path) else path
        if not _inside(target, BASE_DIR):
            raise UserError("このソフトのフォルダの外は開けません。")
    else:
        p = _prof(id)
        target = {"server": p.dir, "mods": p.mods_dir, "backups": server.backups_dir(p),
                  "exports": os.path.join(p.dir, "exports"), "logs": os.path.join(p.dir, "logs"),
                  "crash": os.path.join(p.dir, "crash-reports")}.get(which, p.dir)
    os.makedirs(target, exist_ok=True)
    if IS_WINDOWS:
        os.startfile(target)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])
    return {"ok": True}


def _inside(path, root) -> bool:
    try:
        return os.path.commonpath([os.path.realpath(path), os.path.realpath(root)]) == os.path.realpath(root)
    except ValueError:
        return False


def _uploaded(path) -> str:
    """画面から渡されたファイルの場所が、アップロードやこのソフトの書き出し先の中かを確かめる。"""
    if not path or not (_inside(path, os.path.join(CACHE_DIR, "uploads")) or _inside(path, SERVERS_DIR)
                        or _inside(path, os.path.join(CACHE_DIR, "packs"))):
        raise UserError("そのファイルは使えません。もう一度ファイルを選んでください。")
    if not os.path.isfile(path):
        raise UserError("ファイルが見つかりません。もう一度選んでください。")
    return path


def open_url(url):
    if not str(url).startswith(("https://", "http://")):
        raise UserError("開けないアドレスです。")
    webbrowser.open(url)
    return {"ok": True}


def modpacks_search(q="", offset=0):
    hits, total = modrinth.search(q, "", None, project_type="modpack", sort="downloads" if not q else "relevance",
                                  offset=int(offset), limit=24)
    return {"hits": [_card({**h, "project_type": "modpack"}) for h in hits], "total": total}


shutdown_hook = None


def shutdown(stop_servers=True):
    def later():
        time.sleep(0.4)
        for j in jobs.values():
            j.cancel.set()
        if stop_servers:
            for r in list(runners.values()):
                if r.running:
                    r.stop()
        if playit_proc and playit_proc.poll() is None:
            playit_proc.terminate()
        for gw in gateways.values():
            gw.close()
        if shutdown_hook:
            shutdown_hook()
        os._exit(0)
    threading.Thread(target=later, daemon=True).start()
    return {"ok": True}


# ============================================================ ソフトの更新・診断情報

def update_check():
    """新しい版があるか調べる（画面の「更新を確認する」）。"""
    return updater.check()


def update_status():
    """画面を開いたときに、前回の更新に失敗していないか見る（1回だけ知らせる）。"""
    return {"failed": updater.pop_failure()}


def update_apply():
    """新しい版をダウンロードして、入れ替える。終わると、ソフトは自動で開き直す。"""
    if not updater.FROZEN:
        raise UserError("いまは「Python」で動かしているので、自動では更新できません。リリースのページから、新しい版を入れてください。")
    if any_server_running():
        raise UserError("サーバーが動いています。先に「■ 停止」で止めてから、更新してください。")

    def work(job):
        try:
            return updater.apply(job, any_server_running, shutdown)
        except updater.UpdateError as e:
            raise UserError(str(e)) from e
    return start_job("ソフトを更新しています", work)


def open_releases_page():
    from .appinfo import RELEASES_PAGE
    webbrowser.open(RELEASES_PAGE)
    return {"ok": True}


def support_bundle(id=None):
    """困ったときの診断情報を、秘密を消したzipにまとめて、デスクトップに作る。"""
    prof = _prof(id) if id else None
    issues = None
    lines = None
    if prof is not None:
        try:
            issues = diagnose_run(id)["issues"]
        except Exception:  # noqa: BLE001
            issues = None
        con = consoles.get(id)
        lines = con.since(0)[0] if con else None
    nm = _paths_safe(prof.name) if prof else "ソフト"
    out = os.path.join(_desktop_dir(), f"OneClickServerMC_診断情報_{nm}_{time.strftime('%Y%m%d_%H%M%S')}.zip")
    r = support.build(out, prof, issues, lines)
    _reveal(out)
    return {"path": out, "files": r["files"], "kb": max(1, r["bytes"] // 1024)}


def _reveal(path):
    """ファイルのあるフォルダを開く（Windows では、そのファイルを選んだ状態で）。"""
    try:
        if IS_WINDOWS:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
    except OSError:
        pass


# ============================================================ 追加機能（2.2）

def _memory_after_crash(id, res):
    """メモリ不足で落ちたとき、画面に「増やす」案を出す（自動調整がONなら、自分で増やす）。"""
    if not any(i.get("title") == "メモリが足りません" for i in res.get("issues", [])):
        return
    p = _prof(id)
    cur = int(p.get("memory_mb", 4096) or 4096)
    adv = memory_advice(id)
    ceiling = max(adv["max_safe"], cur)
    new = min(cur + 1024, ceiling) // 512 * 512
    if new <= cur:
        res["memory_suggest"] = {"current": cur, "suggested": None, "max_safe": adv["max_safe"]}
        return
    if p.get("memory_auto"):
        p.set(memory_mb=new)
        consoles.setdefault(id, Console()).add(f"🧠 メモリ不足で落ちたので、メモリを自動で {cur // 1024}GB → {new / 1024:g}GB に増やしました")
        res["memory_raised"] = new
    else:
        res["memory_suggest"] = {"current": cur, "suggested": new, "max_safe": adv["max_safe"]}


def preflight(id):
    """起動の前に、つまずきやすいところを まとめて点検する。"""
    p = _prof(id)
    r = runners.get(id)
    running = bool(r and r.running)
    items = []
    F = features.item
    if running:
        return {"ok": True, "items": [F("ok", "サーバーは、もう動いています")], "running": True}
    # EULA
    if not server.eula_accepted(p):
        items.append(F("info", "最初の起動で、利用規約（EULA）に同意する画面が出ます", "Minecraftの決まりです。「同意して起動」を押せば進みます。"))
    # Java
    need = p.get("java_major") or loaders.guess_java(p.get("mc", ""))
    try:
        jp = javamgr_find(need)
    except Exception:  # noqa: BLE001
        jp = None
    items.append(F("ok", f"Java {need}: 用意ずみ") if jp else
                 F("info", f"Java {need} は、起動のときに自動で用意します", "初回だけ数分かかります（インターネットが必要です）。"))
    # メモリ
    adv = memory_advice(id)
    items.append(features.memory_item(int(p.get("memory_mb", 4096) or 4096), adv["total"], adv["need"], adv["max_safe"]))
    # ポート・前のjava・鍵
    port = int(p.props().get("server-port", "25565") or 25565)
    gw = gateways.get(id)
    own = bool(gw and gw.alive and gw.public_port == port)
    port_busy = (not own) and not network.port_free(port)
    locked = diagnose.session_lock_held(p.world_dir())
    stale = diagnose.stale_java(p.dir, _tracked_pids()) if (port_busy or locked) else []
    if stale:
        items.append(F("error", "前のサーバー（java）が、裏で動いたままです", f"残っている java: {len(stale)}個。ワールドの鍵やポートを使っています。",
                       "起動を押すと、終了するか聞きます。"))
    elif locked:
        items.append(F("error", "ワールドの鍵（session.lock）が使われています", "バックアップ・ウイルス対策・OneDrive などが見ているかもしれません。",
                       "数十秒待って、もう一度ためしてください。"))
    elif port_busy:
        items.append(F("error", f"ポート {port} が、ほかで使われています", "別のサーバーや、別のソフトが使っています。", "「設定」でポート番号を変えてください（例: 25566）。"))
    else:
        items.append(F("ok", f"ポート {port}: 空いています"))
    for oid, o in runners.items():
        if oid != id and o.running and int(o.prof.props().get("server-port", 25565)) == port:
            items.append(F("error", f"「{o.prof.name}」が同じポートで動いています", "同じポートでは、同時に動かせません。", f"先に「{o.prof.name}」を止めてください。"))
    # ディスク
    d = features.disk_item(p.dir)
    if d:
        items.append(d)
    # MODの足りない部品
    try:
        miss = dep_status(id)["missing"]
    except Exception:  # noqa: BLE001
        miss = []
    if miss:
        items.append(F("error", f"必要な部品（MOD）が足りません（{len(miss)}個）", "、".join(m.get("title") or m.get("name") or str(m) if isinstance(m, dict) else str(m) for m in miss[:6]),
                       "起動を押すと、自動で入れるか聞きます。"))
    else:
        items.append(F("ok", "MODの必要な部品: そろっています"))
    # 安全
    if p.props().get("white-list", "false") != "true":
        items.append(F("warn", "ホワイトリストがOFFです", "アドレスを知っている人は、だれでも入れます。", "「メンバー・BAN」タブでONにできます。"))
    if not p.get("public_address"):
        items.append(F("info", "友達に伝えるアドレスが、まだ決まっていません", "このPCの中だけで遊ぶなら、そのままで大丈夫です。", "「友達を呼ぶ」タブで準備できます。"))
    last = next((server.backup_time(b) for b in server.list_backups(p)), None)
    if os.path.isdir(p.world_dir()) and (not last or time.time() - last > 7 * 86400):
        items.append(F("info", "最近のバックアップがありません", "起動の前に、自動でバックアップします（設定でOFFにしていなければ）。"))
    order = {"error": 0, "warn": 1, "info": 2, "ok": 3}
    items.sort(key=lambda i: order.get(i["level"], 4))
    return {"ok": not any(i["level"] == "error" for i in items), "items": items, "running": False,
            "stale": [s_["pid"] for s_ in stale]}


def javamgr_find(need):
    from . import javamgr
    return javamgr.find_java(int(need))


def player_ids(id):
    """参加者の名前とUUID（IDをコピーするため）。"""
    p = _prof(id)
    r = runners.get(id)
    return {"players": features.player_ids(p.dir, sorted(r.players) if r else [])}


# ---------------- 予約（曜日と時刻で起動・停止） ----------------

_sched_fired: dict = {}


def schedule_get(id):
    p = _prof(id)
    rules = p.get("schedule", []) or []
    return {"rules": rules, "texts": [features.schedule_text(r) for r in rules]}


def schedule_set(id, rules):
    p = _prof(id)
    try:
        clean = features.clean_schedule(rules)
    except ValueError as e:
        raise UserError(str(e)) from e
    p.set(schedule=clean)
    admin.audit(p.dir, "予約を変更", "; ".join(features.schedule_text(r) for r in clean) or "なし")
    return {"ok": True, "rules": clean}


def _schedule_tick(now):
    import datetime
    dt = datetime.datetime.fromtimestamp(now)
    key = dt.strftime("%Y%m%d%H%M")
    if _sched_fired.get("_min") == key:
        return
    _sched_fired["_min"] = key
    for p in server.list_profiles():
        rules = p.get("schedule") or []
        if not rules:
            continue
        id = os.path.basename(p.dir)
        con = consoles.setdefault(id, Console())
        for idx, kind in features.schedule_events(rules, dt):
            fk = (id, idx, kind, key)
            if fk in _sched_fired:
                continue
            _sched_fired[fk] = 1
            r = runners.get(id)
            running = bool(r and r.running)
            if kind == "start" and not running:
                con.add("⏰ 予約の時刻になったので、サーバーを起動します")
                admin.audit(p.dir, "予約で起動", features.schedule_text(rules[idx]))
                try:
                    res = server_start(id, accept_eula=server.eula_accepted(p), skip_deps=False, skip_stale=False)
                    if not res.get("ok"):
                        con.add("⚠ 予約の起動ができませんでした（確認が必要です: " + ", ".join(k for k in res) + "）。手動で起動してください")
                        _notify(id, "crash", "⚠ 予約の起動ができませんでした", "ソフトを開いて、手動で起動してください。", desktop=True)
                except UserError as e:
                    con.add(f"⚠ 予約の起動ができませんでした: {e}")
                    _notify(id, "crash", "⚠ 予約の起動ができませんでした", str(e), desktop=True)
            elif kind in ("warn5", "warn1") and running and r.state == "running":
                r.send("say [予約] あと{}分でサーバーを止めます。保存して、ひと区切りつけてください".format(5 if kind == "warn5" else 1))
            elif kind == "stop" and running:
                con.add("⏰ 予約の時刻になったので、サーバーを止めます")
                admin.audit(p.dir, "予約で停止", features.schedule_text(rules[idx]))
                threading.Thread(target=r.stop, daemon=True).start()
    for k in [k for k in _sched_fired if k != "_min" and k[3] != key][:200]:
        _sched_fired.pop(k, None)


# ---------------- よく使うコマンドのボタン ----------------

def quick_cmds_get(id):
    return {"items": _prof(id).get("quick_cmds", []) or []}


def quick_cmds_set(id, items):
    if not isinstance(items, list):
        raise UserError("形がおかしいです")
    clean = []
    for it in items[:24]:
        if not isinstance(it, dict):
            continue
        label = admin.text(it.get("label", ""), 24).strip()
        cmd = admin.text(it.get("cmd", ""), 200).strip().lstrip("/")
        if label and cmd:
            clean.append({"label": label, "cmd": cmd})
    _prof(id).set(quick_cmds=clean)
    return {"ok": True, "items": clean}


# ---------------- 招待カード ----------------

def invite_card(id):
    p = _prof(id)
    addr = p.get("public_address") or ""
    req, opt = [], []
    try:
        for m in mrpack.pack_candidates(p):
            (req if mrpack.friend_need(m) == "required" else opt).append(m.get("title") or m.get("path"))
    except Exception:  # noqa: BLE001
        pass
    return {"name": p.name, "mc": p.get("mc"), "loader_label": p.loader_label(), "address": addr,
            "link": protocol.link_for(addr) if addr else "", "required": sorted(req, key=str.lower)[:60],
            "optional": sorted(opt, key=str.lower)[:30], "whitelist": p.props().get("white-list", "false") == "true",
            "max_players": p.props().get("max-players", "10")}


# ---------------- MODの印（必須 / あると便利） ----------------

def mod_mark(server, path, mark):
    """友達用パックでの扱い。「あると便利」＝友達が入れなくても入れる（クライアントで任意のMODだけ）。"""
    p = _prof(server)
    ml = ModList(p.dir)
    m = next((x for x in ml.items if x["path"] == path), None)
    if not m:
        raise UserError("そのMODが見つかりません。")
    if mark not in ("", "required", "optional"):
        raise UserError("印の指定がおかしいです。")
    if mark == "optional" and not mrpack.can_be_optional(m):
        raise UserError("このMODは、ないと入れなくなるおそれがあるので「あると便利」にはできません。")
    if mark in ("", "required"):
        m.pop("mark", None)
    else:
        m["mark"] = "optional"
    ml.save()
    return {"ok": True}


ACTIONS = {name: obj for name, obj in dict(globals()).items()
           if callable(obj) and not name.startswith("_") and name not in {
               "start_job", "jobs_running", "any_server_running", "total_ram_mb", "advise", "install_many",
               "Job", "Console", "UserError", "ACTIONS", "load_settings", "save_settings", "startup"}
           and getattr(obj, "__module__", None) == __name__}
