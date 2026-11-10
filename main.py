"""OneClickServer を起動する。

画面は、このソフト自身の窓(pywebview / WebView2)で開く。
画面を閉じても、動いているサーバーは止まらない(もう一度起動すると画面が戻る)。
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from oneclick import paths  # noqa: E402


def _setup_logging():
    """画面なし(windowed)の exe では、エラーの表示先がないので、data\\error.log に残す。"""
    if sys.stderr is None or sys.stdout is None or os.path.basename(sys.executable).lower().startswith("pythonw"):
        os.makedirs(paths.DATA_DIR, exist_ok=True)
        sys.stderr = open(os.path.join(paths.DATA_DIR, "error.log"), "a", encoding="utf-8")
        sys.stdout = sys.stderr


def _no_proxy_open(req, timeout=3):
    return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=timeout)


def _ping(info) -> bool:
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{info['port']}/api/ping", data=b"{}", method="POST",
                                     headers={"X-Token": info["token"], "Content-Type": "application/json"})
        _no_proxy_open(req).read()
        return True
    except Exception:  # noqa: BLE001
        return False


def _existing():
    try:
        with open(paths.INSTANCE_FILE, encoding="utf-8") as f:
            info = json.load(f)
        return info if _ping(info) else None
    except (OSError, ValueError, KeyError):
        return None


def open_window(url) -> bool:
    """このソフト自身の窓で開く。窓が閉じられるまで待つ。使えなければ False(ブラウザで開く)。"""
    if os.environ.get("ONECLICK_BROWSER") == "1" or not sys.platform.startswith("win"):
        return False
    try:
        import webview
    except Exception:  # noqa: BLE001
        return False
    try:
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.create_window("OneClickServer", url, width=1360, height=880, min_size=(940, 620))
        storage = os.path.join(paths.DATA_DIR, "webview")
        os.makedirs(storage, exist_ok=True)
        webview.start(gui="edgechromium", private_mode=False, storage_path=storage)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"専用の窓が開けませんでした(ブラウザで開きます): {e}", flush=True)
        return False


def main():
    _setup_logging()
    if sys.version_info < (3, 10):
        raise SystemExit("Python 3.10 以上が必要です")
    paths.ensure_dirs()
    from oneclick import protocol_join
    auto = "--autostart" in sys.argv
    join = protocol_join.join_hash(sys.argv)
    existing = _existing()
    if existing:
        if auto:
            return      # もう動いている
        open_window(f"http://127.0.0.1:{existing['port']}/" + ("go/minecraft" + join if join else ""))
        return

    from oneclick import registry, selfupdate, shell as shell_mod, steamgames
    from oneclick.minecraft import api as mc_api, protocol as mc_protocol, webserver as mc_web

    mc_protocol.register()
    mc_api.startup()
    mc_httpd = mc_web.start(0)
    try:
        from oneclick import discordhub
        discordhub.restart()          # Discord のボット(ホーム画面で設定した人だけ)
    except Exception as e:  # noqa: BLE001
        print(f"Discord のボットを始められませんでした: {e}", flush=True)

    sh = shell_mod.Shell()
    sh.mc_port = mc_httpd.server_address[1]
    sh.notice = selfupdate.pop_failure() or ""
    sh.start(0)
    mc_web.HOME_URL = sh.home_url()

    def cleanup():
        try:
            os.remove(paths.INSTANCE_FILE)
        except OSError:
            pass
        from oneclick.steamgames import manager as sm
        for p in (sm._playit["proc"], getattr(mc_api, "playit_proc", None)):
            try:
                if p and p.poll() is None:
                    p.terminate()
            except Exception:  # noqa: BLE001
                pass
    sh.on_shutdown = cleanup
    mc_api.shutdown_hook = cleanup

    paths.save_json(paths.INSTANCE_FILE, {"port": sh.port, "token": sh.token, "pid": os.getpid()})
    print(f"画面: {sh.home_url()}", flush=True)

    def startup_update_check():
        time.sleep(2)
        try:
            selfupdate.check()
        except Exception as e:  # noqa: BLE001
            print(f"更新の確認に失敗: {e}", flush=True)
        try:
            registry.check_all_updates()      # ゲーム本体・MODの更新(通知だけ。勝手には入れない)
        except Exception as e:  # noqa: BLE001
            print(f"ゲームの更新の確認に失敗: {e}", flush=True)
    import threading
    threading.Thread(target=startup_update_check, daemon=True).start()

    started = time.time()
    if auto:
        ids = mc_api.autostart_servers()
        print(f"PC起動時の自動起動: {ids}", flush=True)
    elif not os.environ.get("ONECLICK_NO_WINDOW"):
        url = sh.home_url() + ("go/minecraft" + join if join else "")
        if not open_window(url):
            webbrowser.open(url)

    # 画面が閉じられて、サーバーも作業も動いていなければ、自動で終わる
    while True:
        time.sleep(5)
        last = max(sh.last_ping, mc_web.last_ping)
        idle = time.time() - (last or started)
        if registry.any_running():
            continue
        if last and idle > 60:
            break
        if not last and time.time() - started > 600:
            break
    cleanup()


if __name__ == "__main__":
    main()
