"""「サーバー1つを、このPCで動かす」ゲーム(ARK・Palworld・Terraria)の共通の管理係。

ゲームごとの違い(起動の引数・設定ファイル・安全な止め方・バックアップの場所)は、
このクラスを受け継いだ ark.py / palworld.py / terraria.py に書きます。
ここには、全ゲームで同じ処理(導入・起動・停止・バックアップ・自動化・診断・Discord通知)を置きます。
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

from .. import appinfo, net, paths, selfupdate
from . import extras

IS_WIN = os.name == "nt"
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
STEAMCMD_URL = "https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip"
PLAYIT_VERSION = "0.17.1"
PLAYIT_URL = ("https://github.com/playit-cloud/playit-agent/releases/download/"
              f"v{PLAYIT_VERSION}/playit-windows-x86_64-signed.exe")
# 配布元(GitHub Releases)が公表している SHA-256。ちがうファイルは使わず捨てる
PLAYIT_SHA256 = "9b00d6ff7d37d1052e5ae097e1348e11deae8617cd7a8ba39d1777f2006316a3"
BAD_CHARS = ('?', '"', "\n", "\r")

DISCORD_EVENTS = {"start": "サーバーが起動した", "stop": "サーバーが止まった", "crash": "サーバーが落ちた(トラブル)"}

# どのゲームにも共通の設定
COMMON_DEFAULTS = {
    "extra_args": "",
    "auto_backup": True,
    "backup_minutes": 60,
    "backup_keep": 10,
    "auto_restart": False,
    "restart_times": ["05:00"],
    "restart_on_crash": True,
    "discord_webhook": "",
    "discord_events": ["start", "stop", "crash"],
}

# 全ゲーム共通の playit(1つだけ動かす)
_playit = {"proc": None}
_playit_lock = threading.Lock()


class BaseManager:
    # ---------- ゲームごとに決める情報(受け継いだクラスで上書き) ----------
    KEY = ""
    NAME = ""
    SHORT = ""
    APPID: str | None = None          # Steam のアプリID(Steam 以外の配布なら None)
    PROCS: tuple = ()                 # このゲームのサーバーの exe 名
    EXE_REL = ""                      # サーバー本体の場所(サーバーのフォルダから)
    SAVE_DIRS: tuple = ()             # バックアップに入れる場所(サーバーのフォルダから)
    LOG_REL = ""                      # ログファイルの場所
    PROTO = "UDP"                     # ゲーム用ポートの種類
    DISK_GB = 20                      # 必要な空き容量の目安
    RAM_GB = 8                        # メモリの目安
    DEFAULTS: dict = {}
    FIELDS: list = []                 # 設定の画面の定義(下の field() を参照)
    ADMIN_BUTTONS: list = []          # 管理コマンドのボタン
    CHAT_COMMAND = ""                 # 全員へのメッセージ(空なら使えない)
    LOG_RULES: list = []
    MOD_UPDATE_NOTE = ""              # MODの更新通知について画面に出す一言
    MODS: dict | None = None          # MOD に対応するなら {"label":…, "note":…, "key":…}

    # ---------- 作り ----------
    def __init__(self, root: str | None = None, start_threads: bool = True):
        self.root = Path(root or paths.game_dir(self.KEY))
        self.root.mkdir(parents=True, exist_ok=True)
        self.settings_file = self.root / "settings.json"
        self.pid_file = self.root / "server.pid"
        self.steamcmd_dir = Path(paths.SHARED_DIR) / "steamcmd"
        self.tools_dir = Path(paths.SHARED_DIR) / "tools"
        self.settings = self._load_settings()
        self.proc = None
        self.pid = None
        self.busy = ""
        self.progress = ""
        self.logs: list[str] = []
        self.log_lock = threading.Lock()
        self.user_stopped = True
        self.started_at = None
        self._crashes: list[float] = []
        self._last_backup = time.time()
        self._last_restart_key = ""
        self._lock = threading.RLock()
        self._st = None                # 状態表示の記憶(参加人数など)と、取った時刻
        self._st_at = 0.0
        self.upd = {"checking": False, "checked": 0, "game": None, "mods": [], "error": ""}
        self.on_quit = None            # 更新のあと、ソフトを終了するための関数(shell が入れる)
        self._adopt_old_server()
        if start_threads:
            threading.Thread(target=self._scheduler, daemon=True).start()

    # ---------- 場所 ----------
    @property
    def server_dir(self) -> Path:
        return self.root / "server"

    @property
    def backup_dir(self) -> Path:
        d = self.root / "backups"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def server_exe(self) -> Path:
        return self.server_dir / self.EXE_REL

    def installed(self) -> bool:
        return self.server_exe().exists()

    # ---------- 設定 ----------
    @classmethod
    def all_defaults(cls) -> dict:
        d = copy.deepcopy(COMMON_DEFAULTS)
        d.update(copy.deepcopy(cls.DEFAULTS))
        return d

    def _load_settings(self) -> dict:
        s = self.all_defaults()
        if self.settings_file.exists():
            try:
                loaded = json.loads(self.settings_file.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    s.update({k: v for k, v in loaded.items() if k in s})
            except (OSError, ValueError):
                pass
        return self._after_load(s)

    def _after_load(self, s: dict) -> dict:
        """読み込んだ設定の、ゲームごとの手直し(上書きして使う)。"""
        return s

    def _write_settings(self):
        tmp = self.settings_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.settings_file)

    def display_name(self) -> str:
        return str(self.settings.get(self.NAME_KEY, self.NAME))

    NAME_KEY = "session_name"

    def _validate(self, new: dict) -> str:
        """おかしな値があれば、日本語の注意を返す。なければ空。"""
        merged = {**self.settings, **new}
        port_values = []
        for f in self.FIELDS:
            k = f["key"]
            if k not in new:
                continue
            v = merged[k]
            t = f.get("type", "text")
            label = f["label"]
            if t in ("text", "password") and any(c in str(v) for c in BAD_CHARS):
                return f"{label}に「?」「\"」や改行は使えません。"
            if t == "select" and str(v) not in {str(o) for o in f.get("options", {})}:
                return f"{label}の値が正しくありません。"
            if t in ("number", "float"):
                try:
                    n = float(v) if t == "float" else int(v)
                except (TypeError, ValueError):
                    return f"{label}が数字ではありません。"
                if "min" in f and n < f["min"] or "max" in f and n > f["max"]:
                    return f"{label}は {f.get('min', '')}〜{f.get('max', '')} の範囲にしてください。"
        for f in self.FIELDS:
            if f.get("port"):
                try:
                    p = int(merged[f["key"]])
                except (TypeError, ValueError):
                    return f"{f['label']}が数字ではありません。"
                if not 1024 <= p <= 65535:
                    return f"{f['label']}は 1024〜65535 にしてください。"
                port_values.append(p)
        if len(set(port_values)) != len(port_values):
            return "ポート番号が重なっています。全て違う番号にしてください。"
        for t in new.get("restart_times", self.settings["restart_times"]):
            if not re.fullmatch(r"\d{1,2}:\d{2}", str(t)) or int(str(t).split(":")[0]) > 23 \
                    or int(str(t).split(":")[1]) > 59:
                return f"再起動の時刻「{t}」が正しくありません。05:00 のように入力してください。"
        for k in ("backup_minutes", "backup_keep"):
            if k in new:
                try:
                    if int(new[k]) < 1:
                        raise ValueError
                except (TypeError, ValueError):
                    return "バックアップの間隔と残す数は、1 以上の数字にしてください。"
        return self._validate_game(merged, new)

    def _validate_game(self, merged: dict, new: dict) -> str:
        """ゲームごとの追加のチェック(上書きして使う)。"""
        return ""

    def save_settings(self, new: dict):
        with self._lock:
            defaults = self.all_defaults()
            new = {k: v for k, v in (new or {}).items() if k in defaults}
            if "restart_times" in new:
                new["restart_times"] = [("%02d:%s" % (int(str(t).split(":")[0]), str(t).split(":")[1]))
                                        if re.fullmatch(r"\d{1,2}:\d{2}", str(t)) else str(t)
                                        for t in new["restart_times"]]
            problem = self._validate(new)
            if problem:
                return {"ok": False, "message": problem}
            for k, v in new.items():
                default = defaults[k]
                try:
                    if isinstance(default, bool):
                        v = bool(v)
                    elif isinstance(default, int):
                        v = int(v)
                    elif isinstance(default, float):
                        v = float(v)
                    elif isinstance(default, list):
                        v = list(v)
                    else:
                        v = str(v)
                except (TypeError, ValueError):
                    v = default
                self.settings[k] = v
            self._write_settings()
            return {"ok": True, "message": "保存しました。"}

    # ---------- ログ ----------
    def log(self, text):
        stamp = datetime.now().strftime("%H:%M:%S")
        with self.log_lock:
            for line in str(text).splitlines() or [""]:
                self.logs.append(f"[{stamp}] {line}")
            if len(self.logs) > 600:
                self.logs = self.logs[-400:]

    # ---------- 状態 ----------
    def running(self) -> bool:
        if self.proc is not None:
            return self.proc.poll() is None
        return self.pid is not None and self._pid_alive(self.pid)

    def _pid_alive(self, pid) -> bool:
        if not IS_WIN:
            return False
        try:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True, creationflags=NOWIN).stdout
            return any(p.lower() in out.lower() for p in self.PROCS)
        except Exception:  # noqa: BLE001
            return False

    def _adopt_old_server(self):
        """画面を閉じて開き直したとき、動いているサーバーを引き継ぐ。"""
        try:
            pid = int(self.pid_file.read_text().strip())
        except (OSError, ValueError):
            return
        if self._pid_alive(pid):
            self.pid = pid
            self.user_stopped = False
            self.log("動いているサーバーを見つけました。引き続き管理します。")
        else:
            self.pid_file.unlink(missing_ok=True)

    def _kill(self):
        if self.proc is not None and self.proc.poll() is None:
            if IS_WIN:
                subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                               capture_output=True, creationflags=NOWIN)
            else:
                self.proc.kill()
        elif self.pid and IS_WIN:
            subprocess.run(["taskkill", "/PID", str(self.pid), "/T", "/F"], capture_output=True, creationflags=NOWIN)

    def _find_running_server(self) -> bool:
        """このソフトが管理していない、前のサーバーが残っていないか。"""
        if not IS_WIN:
            return False
        try:
            out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                                 creationflags=NOWIN).stdout.lower()
            return any(p.lower() in out for p in self.PROCS) and not self.running()
        except Exception:  # noqa: BLE001
            return False

    def local_ip(self) -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except OSError:
            return ""

    def game_port(self) -> int:
        return int(self.settings.get("port", 0) or 0)

    def state(self) -> dict:
        with self.log_lock:
            logs = list(self.logs[-200:])
        up = int(time.time() - self.started_at) if self.running() and self.started_at else 0
        return {
            "installed": self.installed(),
            "running": self.running(),
            "busy": self.busy,
            "progress": self.progress,
            "uptime": up,
            "logs": logs,
            "is_windows": IS_WIN,
            "playit": self.playit_running(),
            "version": appinfo.VERSION,
            "update": selfupdate.cached(),
            "upd": dict(self.upd),
            "backups": self.list_backups(),
            "lan": f"{self.local_ip()}:{self.game_port()}" if self.local_ip() and self.game_port() else "",
        }

    # ---------- 導入 ----------
    def install_or_update(self):
        with self._lock:
            if self.busy:
                return {"ok": False, "message": f"「{self.busy}」の作業中です。終わるまでお待ちください。"}
            if self.running():
                return {"ok": False, "message": "サーバーが動いています。先に停止してください。"}
            if not IS_WIN:
                return {"ok": False, "message": "サーバーの導入は Windows のみ対応しています。"}
            problem = self.preflight_install()
            if problem:
                return {"ok": False, "message": problem}
            self.busy = "サーバーの導入・更新"
        threading.Thread(target=self._install_worker, daemon=True).start()
        return {"ok": True, "message": f"導入を始めました。{self.DISK_GB}GB ほどの空きが必要です。時間がかかる場合があります。"}

    def _install_worker(self):
        try:
            self._do_install()
            if self.installed():
                self.log("導入・更新が完了しました。")
            else:
                self.log("導入に失敗した可能性があります。空き容量とネット接続を確認して、もう一度お試しください。")
        except Exception as e:  # noqa: BLE001
            self.log(f"導入でエラーが起きました: {e}")
        finally:
            self.progress = ""
            self.busy = ""

    def _do_install(self):
        """既定は SteamCMD で入れる。Steam 以外の配布のゲームは上書きする。"""
        exe = self.steamcmd_dir / "steamcmd.exe"
        if not exe.exists():
            self.log("SteamCMD をダウンロードしています…")
            self.steamcmd_dir.mkdir(parents=True, exist_ok=True)
            zpath = self.steamcmd_dir / "steamcmd.zip"
            net.download(STEAMCMD_URL, str(zpath), timeout=120)
            with zipfile.ZipFile(zpath) as z:
                root = os.path.abspath(self.steamcmd_dir)
                for n in z.namelist():            # zip の外に書き込もうとするファイルは受け付けない
                    if not os.path.abspath(os.path.join(root, n)).startswith(root + os.sep):
                        raise RuntimeError("SteamCMD の zip が正しくありません。")
                z.extractall(self.steamcmd_dir)
            if not exe.exists():
                raise RuntimeError("SteamCMD の zip に steamcmd.exe が入っていませんでした。")
            zpath.unlink(missing_ok=True)
            self.log("SteamCMD の準備ができました。")
        self.server_dir.mkdir(parents=True, exist_ok=True)
        cmd = [str(exe), "+force_install_dir", str(self.server_dir), "+login", "anonymous",
               "+app_update", str(self.APPID), "validate", "+quit"]
        self.log(f"{self.NAME} のサーバーをダウンロードしています。しばらくお待ちください…")
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=NOWIN)
        buf = b""
        while True:
            chunk = p.stdout.read1(512)
            if not chunk:
                break
            buf += chunk
            parts = re.split(rb"[\r\n]+", buf)
            buf = parts.pop()
            for raw in parts:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                m = re.search(r"progress:\s*([\d.]+)", line)
                if "Update state" in line and m:
                    self.progress = f"ダウンロード・確認中 {float(m.group(1)):.1f}%"
                else:
                    self.log(line)
        p.wait()

    # ---------- 起動・停止 ----------
    def build_command(self) -> list:
        raise NotImplementedError

    def prepare_start(self):
        """起動の直前に、設定ファイルなどを書く(上書きして使う)。"""

    def check_before_start(self) -> str:
        if self._find_running_server():
            return (f"前のサーバー({' か '.join(self.PROCS)})が残っています。"
                    "タスクマネージャーで終了してください。")
        return ""

    def _popen_kwargs(self) -> dict:
        return dict(cwd=str(self.server_exe().parent), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))

    def start(self, auto=False):
        with self._lock:
            if not auto:
                self._crashes = []
            if self.busy:
                return {"ok": False, "message": f"「{self.busy}」の作業中です。"}
            if self.running():
                return {"ok": False, "message": "すでに動いています。"}
            if not self.installed():
                return {"ok": False, "message": "先に「サーバーを導入」してください。"}
            problem = self.check_before_start()
            if problem:
                return {"ok": False, "message": problem}
            block, warn = self.preflight_start()
            if block:
                return {"ok": False, "message": block}
            try:
                self.prepare_start()
            except OSError as e:
                self.log(f"設定ファイルを書けませんでした: {e}")
                return {"ok": False, "message": f"設定ファイルを書けませんでした: {e}"}
            cmd = self.build_command()
            self.log("サーバーを起動します…")
            self.proc = subprocess.Popen(cmd, **self._popen_kwargs())
            self.pid = self.proc.pid
            try:
                self.pid_file.write_text(str(self.pid))
            except OSError:
                pass
            self.user_stopped = False
            self.started_at = time.time()
            self._last_backup = time.time()
            self._after_start()
            self.notify("start", "サーバーを起動しました。")
            if warn:
                self.log("注意: " + warn)
            return {"ok": True, "message": "起動しました。ワールドの読み込みに少し時間がかかります。" + (" " + warn if warn else "")}

    def _after_start(self):
        """起動した直後の追加の処理(上書きして使う)。"""

    # 以下、ゲームごとに「どう伝えるか」が違う3つ
    def send_command(self, cmd: str) -> str:
        """サーバーに命令を送って、返事を返す。"""
        raise NotImplementedError

    def save_world(self) -> bool:
        raise NotImplementedError

    def graceful_exit(self):
        """サーバーに「終了して」と頼む。"""
        raise NotImplementedError

    def stop(self):
        with self._lock:
            if self.busy:
                return {"ok": False, "message": f"「{self.busy}」の作業中です。"}
            if not self.running():
                return {"ok": False, "message": "サーバーは動いていません。"}
            self.user_stopped = True
            self.busy = "サーバーの停止"
        threading.Thread(target=self._stop_worker, daemon=True).start()
        return {"ok": True, "message": "停止しています。セーブしてから終了します。"}

    def _stop_worker(self, keep_busy=False):
        """busy は呼び出し側で先に立てておく。"""
        try:
            self.log("ワールドを保存しています…")
            self.save_world()
            try:
                self.graceful_exit()
            except Exception:  # noqa: BLE001
                pass
            for _ in range(60):
                if not self.running():
                    break
                time.sleep(1)
            if self.running():
                self.log("終了しないため、強制終了します。")
                self._kill()
            self.proc = None
            self.pid = None
            self.pid_file.unlink(missing_ok=True)
            self.log("サーバーを停止しました。")
            self.notify("stop", "サーバーを停止しました。")
        finally:
            if not keep_busy:
                self.busy = ""

    def restart(self, warn=False):
        with self._lock:
            if self.busy:
                return {"ok": False, "message": f"「{self.busy}」の作業中です。"}
            if not self.running():
                return {"ok": False, "message": "サーバーは動いていません。"}
            self.busy = "サーバーの再起動"
        threading.Thread(target=self._restart_worker, args=(warn,), daemon=True).start()
        return {"ok": True, "message": "再起動します。"}

    def _restart_worker(self, warn):
        try:
            if warn and self.CHAT_COMMAND:
                try:
                    self.send_command(self.CHAT_COMMAND.format(text="1分後にサーバーを再起動します"))
                    self.log("再起動の予告を送りました。1分待ちます。")
                    time.sleep(60)
                except Exception:  # noqa: BLE001
                    pass
            self.user_stopped = True
            self._stop_worker(keep_busy=True)
            time.sleep(2)
        finally:
            self.busy = ""
        self.log(self.start(auto=True)["message"])

    # ---------- バックアップ ----------
    def list_backups(self) -> list:
        items = []
        for f in sorted(self.backup_dir.glob("backup_*.zip"), reverse=True):
            items.append({"name": f.name, "size_mb": round(f.stat().st_size / 1048576, 1)})
        return items

    def backup(self):
        with self._lock:
            if self.busy:
                return {"ok": False, "message": f"「{self.busy}」の作業中です。"}
            if not any((self.server_dir / d).exists() for d in self.SAVE_DIRS):
                return {"ok": False, "message": "セーブデータがまだありません。"}
            self.busy = "バックアップ"
        threading.Thread(target=self._backup_worker, daemon=True).start()
        return {"ok": True, "message": "バックアップを始めました。"}

    def make_backup_file(self) -> Path:
        name = datetime.now().strftime("backup_%Y%m%d_%H%M%S.zip")
        target = self.backup_dir / name
        base = self.server_dir
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
            for sub in self.SAVE_DIRS:
                root = base / sub
                if root.is_file():
                    z.write(root, root.relative_to(base))
                    continue
                if not root.exists():
                    continue
                for f in root.rglob("*"):
                    if f.is_file():
                        try:
                            z.write(f, f.relative_to(base))
                        except OSError:
                            pass
        return target

    def _backup_worker(self):
        try:
            if self.running():
                self.save_world()
            target = self.make_backup_file()
            self.log(f"バックアップを作りました: {target.name}")
            keep = max(1, int(self.settings["backup_keep"]))
            for old in sorted(self.backup_dir.glob("backup_*.zip"), reverse=True)[keep:]:
                old.unlink(missing_ok=True)
        except Exception as e:  # noqa: BLE001
            self.log(f"バックアップに失敗しました: {e}")
        finally:
            self.busy = ""

    def prune_backups(self):
        """いちばん新しいバックアップ1つだけ残して、あとは消す。"""
        with self._lock:
            if self.busy:
                return {"ok": False, "message": f"「{self.busy}」の作業中です。"}
            files = sorted(self.backup_dir.glob("backup_*.zip"), key=lambda f: f.name, reverse=True)
            if len(files) <= 1:
                return {"ok": True, "message": "消すバックアップはありません。"}
            removed, freed = 0, 0
            for f in files[1:]:
                try:
                    size = f.stat().st_size
                    f.unlink()
                    removed += 1
                    freed += size
                except OSError:
                    pass
        self.log(f"古いバックアップを{removed}個消しました(最新の{files[0].name}は残しました)。")
        return {"ok": True, "message": f"{removed}個を消しました({round(freed / 1048576, 1)} MB あきました)。"}

    def _restore_target(self, member: str) -> str:
        """zip の中の名前を、サーバーのフォルダの中のどこに戻すか(古い形式の対応用に上書きできる)。"""
        return member

    def restore(self, name):
        if self.running() or self.busy:
            return {"ok": False, "message": "サーバーを停止し、作業が終わってから実行してください。"}
        target = self.backup_dir / os.path.basename(name)
        if not target.exists():
            return {"ok": False, "message": "バックアップが見つかりません。"}
        base = self.server_dir.resolve()
        base.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(target) as z:
            plan = []
            for m in z.namelist():
                if m.endswith("/"):
                    continue
                dest = (base / self._restore_target(m)).resolve()
                if not dest.is_relative_to(base):   # 外に出る名前は受け付けない
                    return {"ok": False, "message": "不正なバックアップです。"}
                plan.append((m, dest))
            for m, dest in plan:
                dest.parent.mkdir(parents=True, exist_ok=True)
                with z.open(m) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
        self.log(f"{target.name} から復元しました。")
        return {"ok": True, "message": "復元しました。"}

    def open_folder(self, which):
        path = {"data": self.root, "backups": self.backup_dir, "server": self.server_dir}.get(which, self.root)
        path.mkdir(parents=True, exist_ok=True)
        if IS_WIN:
            os.startfile(str(path))  # noqa
        return {"ok": True, "message": str(path)}

    # ---------- 公開の補助 ----------
    def firewall_rules(self) -> list:
        """[(名前, プロトコル, ポート)]。ゲームごとに上書きできる。"""
        return [(f"{appinfo.APP_NAME} {self.SHORT} Game", self.PROTO, self.settings["port"])]

    def add_firewall(self):
        if not IS_WIN:
            return {"ok": False, "message": "Windows のみ対応です。"}
        ng = False
        for name, proto, port in self.firewall_rules():
            # 同じ名前の古い許可を消してから足す(何度押しても増えない)
            subprocess.run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={name}"],
                           capture_output=True, text=True, creationflags=NOWIN)
            r = subprocess.run(
                ["netsh", "advfirewall", "firewall", "add", "rule", f"name={name}", "dir=in",
                 "action=allow", f"protocol={proto}", f"localport={port}"],
                capture_output=True, text=True, creationflags=NOWIN)
            if r.returncode != 0:
                ng = True
        if ng:
            return {"ok": False, "message": "失敗しました。このソフトを「管理者として実行」して、もう一度お試しください。"}
        return {"ok": True, "message": "Windows ファイアウォールの許可を追加しました。"}

    def tunnel_text(self) -> str:
        return f"種類は <b>Game Server ({self.PROTO})</b>、Local Port は <b>{self.settings.get('port')}</b> です。"

    def router_ports_text(self) -> str:
        return f"{self.PROTO} の {self.settings.get('port')}"

    # ---------- 見張り番 ----------
    def _scheduler(self):
        while True:
            time.sleep(15)
            try:
                self.scheduler_tick()
            except Exception as e:  # noqa: BLE001
                self.log(f"見張り番でエラー: {e}")

    def scheduler_tick(self, now: datetime | None = None):
        """15秒ごとの見張り(テストしやすいように、1回分を取り出してある)。"""
        s = self.settings
        now = now or datetime.now()
        # 落ちたら自動で再起動
        if self.pid is not None and not self.running() and not self.user_stopped and not self.busy:
            self.pid = None
            self.proc = None
            self.pid_file.unlink(missing_ok=True)
            self.notify("crash", "サーバーが予期せず止まりました。" +
                        ("自動で再起動します。" if s["restart_on_crash"] else ""))
            if not s["restart_on_crash"]:
                self.user_stopped = True
                return
            t = time.time()
            self._crashes = [x for x in self._crashes if t - x < 600] + [t]
            if len(self._crashes) >= 3:
                self.user_stopped = True
                msg = "10分の間に3回落ちたため、自動の再起動をやめました。「困ったとき診断」を押して、原因を確認してください。"
                self.log(msg)
                self.notify("crash", msg)
                return
            self.log("サーバーが予期せず止まりました。自動で再起動します。")
            self.user_stopped = True
            time.sleep(5)
            self.log(self.start(auto=True)["message"])
            return
        if not self.running() or self.busy:
            return
        # 定期バックアップ
        if s["auto_backup"] and time.time() - self._last_backup >= max(5, int(s["backup_minutes"])) * 60:
            self._last_backup = time.time()
            self.backup()
        # 定時再起動
        key = now.strftime("%Y%m%d%H%M")
        if s["auto_restart"] and now.strftime("%H:%M") in s["restart_times"] and key != self._last_restart_key:
            self._last_restart_key = key
            self.log("定時の再起動を始めます。")
            self.restart(warn=True)

    # ---------- Discord 通知 ----------
    def notify(self, event, text):
        try:
            from .. import discordhub
            discordhub.steam_event(self.KEY, event, text)       # Discord のボット(ホーム画面で設定)
        except Exception:  # noqa: BLE001
            pass
        url = self.settings.get("discord_webhook", "").strip()
        if not url or event not in self.settings.get("discord_events", []):
            return

        def send():
            try:
                body = json.dumps({"content": f"[{self.display_name()}] {text}"}).encode("utf-8")
                req = urllib.request.Request(url, data=body, method="POST", headers={
                    "Content-Type": "application/json", "User-Agent": f"{appinfo.APP_NAME}/{appinfo.VERSION}"})
                urllib.request.urlopen(req, timeout=10).read()
            except Exception as e:  # noqa: BLE001
                self.log(f"Discord への通知に失敗しました: {e}")
        threading.Thread(target=send, daemon=True).start()

    def test_discord(self):
        if not self.settings.get("discord_webhook", "").strip():
            return {"ok": False, "message": "Webhook の URL が空です。入力して「保存する」を押してから試してください。"}
        saved = list(self.settings.get("discord_events", []))
        self.settings["discord_events"] = list(DISCORD_EVENTS)
        self.notify("start", "テスト通知です。このメッセージが見えれば成功です。")
        self.settings["discord_events"] = saved
        return {"ok": True, "message": "テスト通知を送りました。Discord を確認してください。"}

    # ---------- playit.gg(ポート開放なしで公開。全ゲームで共通の1つ) ----------
    def playit_path(self) -> Path:
        return self.tools_dir / "playit.exe"

    def playit_running(self) -> bool:
        p = _playit["proc"]
        return p is not None and p.poll() is None

    def playit_toggle(self):
        with _playit_lock:
            if self.playit_running():
                _playit["proc"].terminate()
                _playit["proc"] = None
                return {"ok": True, "message": "playit を止めました。"}
        if not IS_WIN:
            return {"ok": False, "message": "Windows のみ対応です。"}
        with self._lock:
            if self.busy:
                return {"ok": False, "message": f"「{self.busy}」の作業中です。"}
            self.busy = "playit の準備"
        threading.Thread(target=self._playit_worker, daemon=True).start()
        return {"ok": True, "message": "playit を準備しています。黒い画面が開いたら、画面の案内に従ってください。"}

    def _playit_worker(self):
        try:
            exe = self.playit_path()
            if not exe.exists():
                self.tools_dir.mkdir(parents=True, exist_ok=True)
                self.log("playit をダウンロードしています…")
                net.download(PLAYIT_URL, str(exe), sha256=PLAYIT_SHA256, timeout=120)
            _playit["proc"] = subprocess.Popen(
                [str(exe)], cwd=str(self.tools_dir), creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
            self.log("playit を起動しました。黒い画面の案内に従ってください(初回のみ)。")
        except Exception as e:  # noqa: BLE001
            self.log(f"playit の準備に失敗しました: {e}")
        finally:
            self.busy = ""

    # ---------- 管理コマンド ----------
    def admin_command(self, cmd):
        allowed = {b["cmd"] for b in self.ADMIN_BUTTONS}
        chat_prefix = self.CHAT_COMMAND.split("{text}")[0] if self.CHAT_COMMAND else None
        if chat_prefix and cmd.startswith(chat_prefix) and chat_prefix:
            text = cmd[len(chat_prefix):].replace("\n", " ").replace("\r", " ")[:200]
            cmd = self.CHAT_COMMAND.format(text=text)
        elif cmd not in allowed:
            return {"ok": False, "message": "このコマンドは使えません。"}
        if not self.running():
            return {"ok": False, "message": "サーバーが動いていません。"}
        try:
            out = self.send_command(cmd).strip()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "message": f"つながりませんでした(起動直後は少し待ってください): {e}"}
        self.log(f"> {cmd}")
        if out:
            self.log(out)
        return {"ok": True, "message": out or "実行しました。", "output": out}

    # ---------- 困ったとき診断 ----------
    def _ram_gb(self):
        return extras.memory_gb()[0]

    def _port_free(self, port, proto=None) -> bool:
        proto = proto or self.PROTO
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM if proto == "UDP" else socket.SOCK_STREAM)
        try:
            s.bind(("0.0.0.0", int(port)))
            return True
        except OSError:
            return False
        finally:
            s.close()

    def diag_ports(self) -> list:
        """[(ラベル, ポート, プロトコル)] 起動前に空いているべきポート(上書きして使う)。"""
        return [("ゲーム用", self.settings.get("port"), self.PROTO)]

    def extra_diagnose(self) -> list:
        """ゲームごとの追加の診断 [(level, text)](上書きして使う)。"""
        return []

    def log_text(self) -> str | None:
        if not self.LOG_REL:
            return None
        p = self.server_dir / self.LOG_REL
        if not p.exists():
            return None
        try:
            with open(p, "rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - 400_000))
                return f.read().decode("utf-8", "replace").lower()
        except OSError:
            return None

    def diagnose(self):
        res = []
        res.append(("ok" if self.installed() else "ng",
                    "サーバーは導入済みです。" if self.installed() else "サーバーが未導入です。「ホーム」で導入してください。"))
        if self._find_running_server():
            res.append(("ng", f"前のサーバー({' か '.join(self.PROCS)})が残っています。タスクマネージャーで終了してください。"))
        else:
            res.append(("ok", "前のサーバーは残っていません。"))
        res += self.extra_diagnose()
        if not self.running():
            for label, port, proto in self.diag_ports():
                if port and not self._port_free(port, proto):
                    res.append(("ng", f"{label}のポート {port} ({proto}) が、他のソフトに使われています。"))
        ram = self._ram_gb()
        if ram is not None:
            ok = ram >= self.RAM_GB
            res.append(("ok" if ok else "warn",
                        f"メモリは約 {ram:.0f}GB です。" + ("" if ok else f"{self.NAME} のサーバーには {self.RAM_GB}GB 以上をおすすめします。")))
        try:
            free = shutil.disk_usage(self.root).free / 1073741824
            ok = free >= self.DISK_GB
            res.append(("ok" if ok else "warn",
                        f"ディスクの空きは約 {free:.0f}GB です。" + ("" if ok else f"{self.DISK_GB}GB 以上の空きをおすすめします。")))
        except OSError:
            pass
        text = self.log_text()
        if text is None:
            res.append(("warn", "ログがまだありません(一度も起動していない可能性があります)。"))
        else:
            found = False
            for keys, msg in self.LOG_RULES:
                if any(k in text for k in keys):
                    res.append(("ng", "ログから分かったこと: " + msg))
                    found = True
            if not found:
                res.append(("ok", "ログに、よくある失敗の跡は見つかりませんでした。"))
        bad = [m for lv, m in res if lv == "ng"]
        return {"ok": not bad, "items": [{"level": lv, "text": m} for lv, m in res],
                "message": "問題は見つかりませんでした。" if not bad else f"{len(bad)} 件の問題が見つかりました。"}

    # ---------- ソフトの更新(共通部品へ) ----------
    # ---------- 事前チェック(空き容量・メモリ) ----------
    def preflight_install(self) -> str:
        """導入の前に、ディスクの空きを調べる。足りなければ理由の文を返す(問題なければ空)。"""
        free = extras.disk_free_gb(self.root)
        if free is None:
            return ""
        need = 5 if self.installed() else self.DISK_GB       # すでに入っているなら、差分だけ
        if free < need:
            return (f"ディスクの空きが足りません。いまの空きは約 {free:.1f}GB で、{need}GB 以上が必要です。"
                    "いらないファイルを消すか、空きの多いドライブにこのソフトを置いて、もう一度お試しください。")
        return ""

    def preflight_start(self) -> tuple[str, str]:
        """起動の前に、メモリを調べる。(止める理由, 注意の文)。どちらも空なら問題なし。"""
        total, avail = extras.memory_gb()
        if avail is not None and avail < 1.5:
            return (f"メモリの空きが約 {avail:.1f}GB しかありません。このまま起動すると、パソコンが止まるおそれがあります。"
                    "他のソフトを閉じてから、もう一度お試しください。"), ""
        warn = ""
        if total is not None and total < self.RAM_GB:
            warn = f"このパソコンのメモリは約 {total:.0f}GB で、{self.NAME} のサーバーには {self.RAM_GB}GB 以上がおすすめです。重くなるかもしれません。"
        elif avail is not None and avail < self.RAM_GB * 0.5:
            warn = f"いまメモリの空きが約 {avail:.1f}GB です。他のソフトを閉じると、動きが安定します。"
        return "", warn

    # ---------- サーバーの状態表示(稼働時間・メモリ・参加人数) ----------
    def player_count(self):
        """いま入っている人数。調べられないゲームは None(上書きして使う)。"""
        return None

    def status_detail(self) -> dict:
        now = time.time()
        if self._st is not None and now - self._st_at < 8:
            return self._st
        running = self.running()
        d = {"running": running, "uptime": 0, "mem_mb": None, "players": None,
             "max_players": self.settings.get("max_players"), "at": now}
        if running:
            if self.started_at:
                d["uptime"] = int(now - self.started_at)
            d["mem_mb"] = extras.process_memory_mb(self.PROCS, self.pid)
            try:
                d["players"] = self.player_count()
            except Exception:  # noqa: BLE001
                d["players"] = None
        self._st, self._st_at = d, now
        return d

    def cached_players(self):
        """ホーム画面のカード用。通信はせず、記憶してある値だけを返す。"""
        st = self._st
        if st and st.get("running") and time.time() - self._st_at < 60:
            return st.get("players")
        return None

    # ---------- ゲーム本体・MOD の更新確認 ----------
    def check_game_update(self):
        """本体に新しい版があれば {"installed","latest"} を返す(上書きして使う)。既定は SteamCMD で比較。"""
        if not (self.APPID and IS_WIN and self.installed()):
            return None
        inst = extras.installed_buildid(self.server_dir, self.APPID)
        latest = extras.steam_latest_build(self.steamcmd_dir / "steamcmd.exe", self.APPID)
        if inst and latest and inst != latest:
            return {"installed": inst, "latest": latest}
        return None

    def check_mod_updates(self) -> list:
        """更新されたMODの一覧 [{"id","title","ago"}](対応するゲームだけ上書きして使う)。"""
        return []

    def check_updates(self):
        """画面のボタンから。裏で確認して、結果は state の upd に出る。"""
        with self._lock:
            if self.upd["checking"]:
                return {"ok": True, "message": "確認しています…"}
            if self.busy:
                return {"ok": False, "message": f"「{self.busy}」の作業中です。終わってからお試しください。"}
            self.upd["checking"] = True
        threading.Thread(target=self.run_update_check, daemon=True).start()
        return {"ok": True, "message": "新しい版があるか、確認しています…"}

    def run_update_check(self):
        err = ""
        game, mods = None, []
        try:
            game = self.check_game_update()
        except Exception as e:  # noqa: BLE001
            err = f"本体の確認に失敗しました: {e}"
        try:
            mods = self.check_mod_updates()
        except Exception as e:  # noqa: BLE001
            err = err or f"MODの確認に失敗しました: {e}"
        self.upd = {"checking": False, "checked": int(time.time()), "game": game, "mods": mods, "error": err}
        if game or mods:
            self.log("更新の確認: " + ("ゲーム本体に新しい版があります。" if game else "") +
                     (f" 更新されたMODが {len(mods)} 個あります。" if mods else ""))

    def update_check(self):
        try:
            return selfupdate.check()
        except Exception as e:  # noqa: BLE001
            return {"current": appinfo.VERSION, "latest": None, "newer": False, "can_apply": False, "safe": False,
                    "page": appinfo.RELEASES_PAGE, "notes": "", "message": f"更新を調べられませんでした: {e}"}

    # ---------- 画面に渡す情報 ----------
    def info(self) -> dict:
        """画面を組み立てるための、このゲームの情報。"""
        return {
            "key": self.KEY, "name": self.NAME, "short": self.SHORT, "proto": self.PROTO,
            "fields": self.FIELDS, "admin_buttons": self.ADMIN_BUTTONS, "chat": bool(self.CHAT_COMMAND),
            "mods": self.MODS, "disk_gb": self.DISK_GB, "ram_gb": self.RAM_GB, "mod_update_note": self.MOD_UPDATE_NOTE,
            "tunnel": self.tunnel_text(), "router_ports": self.router_ports_text(),
            "install_note": self.install_note(),
        }

    def install_note(self) -> str:
        return f"初回の導入には約 {self.DISK_GB}GB の空きが必要です。時間がかかりますので、そのままお待ちください。"

    def get_all(self) -> dict:
        return {"settings": self.settings, "info": self.info(), "events": DISCORD_EVENTS, "state": self.state()}


def field(key, label, type="text", group="basic", help="", **kw) -> dict:
    """設定の画面の部品1つ分の定義を作る。"""
    d = {"key": key, "label": label, "type": type, "group": group, "help": help}
    d.update(kw)
    return d
