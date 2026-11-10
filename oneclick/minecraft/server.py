"""サーバーの作成・設定・起動/停止・コンソール・参加者・バックアップ。"""
from __future__ import annotations

import datetime
import os
import re
import shutil
import subprocess
import threading
import time
import zipfile

from . import javamgr, loaders, net, versions
from .javamgr import NO_WINDOW
from .paths import SERVERS_DIR, load_json, safe_name, save_json

PROFILE_FILE = "mcmh.json"

DEFAULT_PROPS = {
    "motd": "みんなのMODサーバー",
    "max-players": "10",
    "difficulty": "normal",
    "gamemode": "survival",
    "pvp": "true",
    "white-list": "true",
    "enforce-whitelist": "true",
    "online-mode": "true",
    "view-distance": "10",
    "simulation-distance": "10",
    "spawn-protection": "0",
    "allow-flight": "true",   # MODの乗り物などで誤ってキックされないように
    "server-port": "25565",
    "level-name": "world",
}

PROP_LABELS = [
    ("motd", "サーバーの説明文"),
    ("max-players", "最大人数"),
    ("difficulty", "難しさ (peaceful/easy/normal/hard)"),
    ("gamemode", "ゲームモード (survival/creative/adventure)"),
    ("pvp", "プレイヤー同士の攻撃 (true/false)"),
    ("white-list", "ホワイトリスト（許可した人だけ入れる）"),
    ("view-distance", "見える距離（チャンク）"),
    ("simulation-distance", "動く範囲（チャンク）"),
    ("allow-flight", "飛行を許す（MOD用に true 推奨）"),
    ("server-port", "ポート番号"),
    ("level-name", "ワールドのフォルダ名"),
    ("level-seed", "シード値（空でランダム）"),
]


# ---------------- server.properties ----------------

def read_props(path: str) -> dict:
    props = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line or line.lstrip().startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                props[k.strip()] = v
    return props


def write_props(path: str, updates: dict):
    """既存の行の順番やコメントを残したまま値を書きかえる。"""
    lines = []
    done = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip("\n")
                if line and not line.lstrip().startswith("#") and "=" in line:
                    k = line.split("=", 1)[0].strip()
                    if k in updates:
                        line = f"{k}={updates[k]}"
                        done.add(k)
                lines.append(line)
    for k, v in updates.items():
        if k not in done:
            lines.append(f"{k}={v}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ---------------- サーバーのプロフィール ----------------

class Profile:
    """サーバーごとの設定（mcmh.json）。ほかの場所で書きかえられたら、自動で読み直す。"""

    def __init__(self, folder: str):
        self.dir = folder
        self._path = os.path.join(folder, PROFILE_FILE)
        self._mtime = None
        self._data = {}
        self._reload()

    def _stamp(self):
        try:
            st = os.stat(self._path)
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _reload(self):
        self._mtime = self._stamp()
        self._data = load_json(self._path, {})

    @property
    def data(self):
        if self._stamp() != self._mtime:
            self._reload()
        return self._data

    @data.setter
    def data(self, value):
        self._data = value

    @property
    def name(self):
        return self.data.get("name") or os.path.basename(self.dir)

    def get(self, k, default=None):
        return self.data.get(k, default)

    def set(self, **kw):
        self._reload()  # ほかで書きかえた分を消さないように、読み直してから書く
        self._data.update(kw)
        self.save()

    def save(self):
        save_json(self._path, self._data)
        self._mtime = self._stamp()

    @property
    def mods_dir(self):
        return os.path.join(self.dir, loaders.content_folder(self.get("loader")))

    @property
    def uses_plugins(self):
        return loaders.uses_plugins(self.get("loader"))

    @property
    def props_path(self):
        return os.path.join(self.dir, "server.properties")

    def props(self):
        return read_props(self.props_path)

    def world_dir(self):
        return os.path.join(self.dir, self.props().get("level-name", "world") or "world")

    def loader_label(self):
        lv = self.get("loader_version")
        base = loaders.LOADER_LABEL.get(self.get("loader"), "?")
        return f"{base} {lv}" if lv and self.get("loader") != "vanilla" else base


def list_profiles() -> list[Profile]:
    os.makedirs(SERVERS_DIR, exist_ok=True)
    out = []
    for name in sorted(os.listdir(SERVERS_DIR)):
        d = os.path.join(SERVERS_DIR, name)
        if os.path.isfile(os.path.join(d, PROFILE_FILE)):
            out.append(Profile(d))
    return out


def create_server(name: str, mc: str, loader: str, memory_mb: int, port: int,
                  log=print, loader_ver: str | None = None) -> Profile:
    folder = os.path.join(SERVERS_DIR, safe_name(name))
    n = 2
    while os.path.exists(folder):
        folder = os.path.join(SERVERS_DIR, f"{safe_name(name)}_{n}")
        n += 1
    os.makedirs(folder)
    try:
        need = loaders.required_java(mc)
        log(f"マイクラ {mc} には Java {need} が必要です。確認中…")
        java = javamgr.ensure_java(need, log)
        start = loaders.install_server(folder, loader, mc, loader_ver, java, log)
        prof = Profile(folder)
        prof.data = {
            "name": name, "mc": mc, "loader": loader,
            "loader_version": start.pop("loader_version", None),
            "start": start, "java_major": need, "memory_mb": memory_mb,
            "auto_restart": True, "backup_interval_min": 30, "backup_keep": 10,
            "created": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        prof.save()
        props = dict(DEFAULT_PROPS)
        if versions.at_least(mc, versions.PROPS_TO_GAMERULES):
            props.pop("pvp", None)  # 1.21.9 以降は pvp はゲームルール
        props["server-port"] = str(port)
        props["motd"] = name
        write_props(prof.props_path, props)
        os.makedirs(prof.mods_dir, exist_ok=True)
        log("サーバーの準備ができました！")
        return prof
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def create_from_pack(name: str, pack_path: str, memory_mb: int, port: int, log=print) -> Profile:
    """MODパック（.mrpack）からサーバーを作る。"""
    from . import mrpack  # 循環 import を避ける
    from .modrinth import ModList
    info = mrpack.pack_info(pack_path)
    if info["loader"] not in ("fabric", "neoforge", "forge", "vanilla"):
        raise RuntimeError(f"{info['loader']} のパックには対応していません")
    prof = create_server(name or info["name"], info["mc"], info["loader"], memory_mb, port,
                         log, loader_ver=info["loader_version"])
    try:
        shutil.copy(pack_path, os.path.join(prof.dir, "source.mrpack"))
        log(f"MODパックの中身を入れています（{len(info['files'])} 個）…")
        ml = ModList(prof.dir)
        mrpack.install_pack_files(pack_path, prof.dir, "server", log, mod_list=ml)
        # パックの server.properties が上書きした場合でもポートはこちらの設定にする
        write_props(prof.props_path, {"server-port": str(port)})
        prof.set(pack_name=info["name"], pack_version=info.get("version"))
        log("MODパックのサーバーができました！")
        return prof
    except Exception:
        shutil.rmtree(prof.dir, ignore_errors=True)
        raise


def eula_accepted(prof: Profile) -> bool:
    p = os.path.join(prof.dir, "eula.txt")
    if not os.path.exists(p):
        return False
    with open(p, encoding="utf-8", errors="replace") as f:
        return "eula=true" in f.read()


def accept_eula(prof: Profile):
    with open(os.path.join(prof.dir, "eula.txt"), "w", encoding="utf-8") as f:
        f.write("# https://aka.ms/MinecraftEULA に同意しました（OneClickServerMCで確認）\n"
                f"# {datetime.datetime.now().isoformat(timespec='seconds')}\neula=true\n")


# ---------------- 起動中のサーバー ----------------

JOIN_RE = re.compile(r"\]: (\w{2,16}) joined the game")
LEFT_RE = re.compile(r"\]: (\w{2,16}) left the game")
DONE_RE = re.compile(r"\]: Done \(")
SAVED_RE = re.compile(r"Saved the game|Saved the world", re.I)


PREP_RE = re.compile(r"Preparing spawn area:\s*(\d+)\s*%")
PROGRESS_STEPS = [
    (re.compile(r"Starting minecraft server|Starting net\.minecraft\.server", re.I), 8, "サーバーを始めています"),
    (re.compile(r"Loading (?:libraries|mods?)|Loading \d+ mods|ModLauncher|Loading Minecraft|Mod discovery|Found \d+ mod", re.I), 18, "MODを読み込んでいます"),
    (re.compile(r"Loading properties|Default game type|Generating keypair", re.I), 30, "設定を読み込んでいます"),
    (re.compile(r"Starting Minecraft server on|Preparing level|Loading (?:world|level)", re.I), 45, "ワールドを開いています"),
    (re.compile(r"Preparing start region|Preparing spawn", re.I), 60, "スポーン地点を準備中"),
    (re.compile(r"RCON running|Running delayed init|Done preparing", re.I), 96, "もうすぐ完了です"),
]


class Runner:
    """1つのサーバーを動かす係。画面とは on_line/on_state で連絡する。"""

    def __init__(self, prof: Profile, on_line, on_state):
        self.prof = prof
        self.on_line = on_line
        self.on_state = on_state
        self.proc: subprocess.Popen | None = None
        self.players: set[str] = set()
        self.history: list[str] = []
        self.last_exit: dict | None = None
        self._reached_running = False
        self._started_at = 0.0
        self.state = "stopped"   # stopped / starting / running / stopping
        self._stop_requested = False
        self._saved = threading.Event()
        self._backup_thread = None
        self._lock = threading.Lock()
        self.extra_args: list[str] = []      # 起動するときに足す引数（門番を使うときの --port など）
        self.line_hooks: list = []           # ログ1行ごとに呼ぶ関数（True を返すと画面に出さない）
        self.before_start: list = []         # 起動の直前に呼ぶ関数（再起動のときも）
        self.progress: dict = {"pct": 0, "stage": ""}   # 起動の進み具合（画面の進行バー用）
        self._restart_times: list[float] = []            # 自動再起動した時刻（くり返し防止）
        self._auto_start = False                         # 自動の再起動による start か

    # --- 起動・停止 ---
    def start(self):
        if self.proc and self.proc.poll() is None:
            return
        if not self._auto_start:
            self._restart_times.clear()     # 人が起動したら、再起動の回数は数えなおす
        self._auto_start = False
        need = self.prof.get("java_major") or loaders.guess_java(self.prof.get("mc", ""))
        java = javamgr.ensure_java(need, self.on_line)
        for f in self.before_start:
            f(self)
        cmd = loaders.start_command(java, self.prof.get("start"), int(self.prof.get("memory_mb", 4096)),
                                    self.prof.dir) + list(self.extra_args)
        self._stop_requested = False
        self._reached_running = False
        self._started_at = time.time()
        self.players.clear()
        self.progress = {"pct": 2, "stage": "起動しています"}
        self._set_state("starting")
        self.on_line("▶ サーバーを起動します…")
        self.proc = subprocess.Popen(
            cmd, cwd=self.prof.dir, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=NO_WINDOW)
        try:
            from . import diagnose
            diagnose.remember_pid(self.prof.dir, self.proc.pid)   # ソフトが落ちても「前のjava」を見つけられるように
        except Exception:  # noqa: BLE001
            pass
        threading.Thread(target=self._reader, daemon=True).start()
        self._start_backup_timer()

    def _reader(self):
        proc = self.proc
        for line in proc.stdout:
            line = line.rstrip("\r\n")
            self._parse(line)
            hide = False
            for h in self.line_hooks:
                try:
                    hide = bool(h(line)) or hide
                except Exception:  # noqa: BLE001
                    pass
            if not hide:
                self.on_line(line)
        code = proc.wait()
        try:
            from . import diagnose
            diagnose.forget_pid(self.prof.dir, proc.pid)
        except Exception:  # noqa: BLE001
            pass
        self.players.clear()
        was_running = self._reached_running
        # 止めていないのに止まった ＝ 何かトラブル（起動の途中で止まった場合も）
        crashed = not self._stop_requested and (code != 0 or not was_running)
        self.last_exit = {"code": code, "crashed": crashed, "during_start": not was_running,
                          "time": time.time(), "started": self._started_at}
        self._set_state("stopped")
        self.on_line(f"■ サーバーが止まりました（終了コード {code}）")
        # 起動の途中で止まった時は、設定やMODの問題なので自動再起動しない（くり返しになるため）
        if crashed and was_running and self.prof.get("auto_restart", True):
            limit = max(1, int(self.prof.get("auto_restart_max", 3) or 3))
            now = time.time()
            self._restart_times = [t for t in self._restart_times if now - t < 30 * 60]
            if len(self._restart_times) >= limit:
                self.last_exit["gave_up"] = True
                self.on_line(f"⛔ 30分の間に {limit} 回、自動で再起動しましたが、また止まりました。"
                             "くり返しになるので、自動の再起動をやめます。原因を直してから、手動で起動してください。")
                try:
                    self.on_giveup(limit)
                except Exception:  # noqa: BLE001
                    pass
                return
            self._restart_times.append(now)
            self.on_line(f"⚠ 予想外に止まったので 10 秒後に自動で再起動します…（{len(self._restart_times)}/{limit}回目）")
            time.sleep(10)
            if not self._stop_requested:
                try:
                    self._auto_start = True
                    self.start()
                except Exception as e:  # noqa: BLE001
                    self._auto_start = False
                    self.on_line(f"再起動に失敗: {e}")

    def on_giveup(self, limit: int):
        """自動再起動をあきらめたとき（api 側で通知を足す）。"""

    def _progress(self, line: str):
        """ログの1行から、起動の進み具合（％と段階）を読み取る。"""
        if self.state != "starting":
            return
        pr = self.progress
        m = PREP_RE.search(line)
        if m:
            pr.update(pct=max(pr["pct"], 60 + int(int(m.group(1)) * 0.38)), stage=f"スポーン地点を準備中（{m.group(1)}%）")
            return
        hit = [(pct, label) for pat, pct, label in PROGRESS_STEPS if pat.search(line)]
        if hit:
            pct, label = max(hit)
            if pct > pr["pct"]:
                pr.update(pct=pct, stage=label)

    def _parse(self, line: str):
        if self.state == "starting":
            self._progress(line)
        if m := JOIN_RE.search(line):
            self.players.add(m.group(1))
            self.on_state(self.state)
        elif m := LEFT_RE.search(line):
            self.players.discard(m.group(1))
            self.on_state(self.state)
        elif DONE_RE.search(line):
            self._reached_running = True
            self.progress = {"pct": 100, "stage": "起動完了"}
            self._set_state("running")
            self.on_line("✅ 起動完了！ 友達が入れる状態です。")
        if SAVED_RE.search(line):
            self._saved.set()

    def _set_state(self, s):
        self.state = s
        self.on_state(s)

    def send(self, command: str) -> bool:
        if not self.proc or self.proc.poll() is not None:
            return False
        try:
            # 1回に送れるのは1行だけ（改行をまぜて、別のコマンドをこっそり実行させないため）
            line = re.sub(r"[\r\n\x00]+", " ", str(command)).strip().lstrip("/")
            if not line:
                return False
            self.proc.stdin.write(line[:2000] + "\n")
            self.proc.stdin.flush()
            return True
        except OSError:
            return False

    def stop(self, wait=True):
        self._stop_requested = True
        if not self.proc or self.proc.poll() is not None:
            return
        self._set_state("stopping")
        self.send("stop")
        if wait:
            try:
                self.proc.wait(timeout=90)
            except subprocess.TimeoutExpired:
                self.on_line("止まらないので強制終了します…")
                self.proc.kill()

    def kill(self):
        self._stop_requested = True
        if self.proc and self.proc.poll() is None:
            self.proc.kill()

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    # --- バックアップ ---
    def _start_backup_timer(self):
        if self._backup_thread and self._backup_thread.is_alive():
            return

        def loop():
            last = time.time()
            while self.running:
                time.sleep(5)
                minutes = int(self.prof.get("backup_interval_min", 30) or 0)
                if minutes > 0 and self.state == "running" and time.time() - last >= minutes * 60:
                    last = time.time()
                    try:
                        path = self.backup(auto=True)
                        self.on_line(f"💾 自動バックアップしました: {os.path.basename(path)}")
                    except Exception as e:  # noqa: BLE001
                        self.on_line(f"自動バックアップ失敗: {e}")
        self._backup_thread = threading.Thread(target=loop, daemon=True)
        self._backup_thread.start()

    def backup(self, auto=False) -> str:
        with self._lock:
            live = self.running and self.state == "running"
            if live:
                self._saved.clear()
                self.send("save-off")
                self.send("save-all flush")
                self._saved.wait(timeout=60)
                time.sleep(1)
            try:
                return make_backup(self.prof, "auto" if auto else "manual")
            finally:
                if live:
                    self.send("save-on")


# ---------------- バックアップ（サーバー停止中でも使える） ----------------

def backups_dir(prof: Profile) -> str:
    return os.path.join(prof.dir, "backups")


def make_backup(prof: Profile, kind="manual") -> str:
    world = prof.world_dir()
    if not os.path.isdir(world):
        raise FileNotFoundError("ワールドがまだありません（一度サーバーを起動すると作られます）")
    os.makedirs(backups_dir(prof), exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = os.path.join(backups_dir(prof), f"{stamp}_{kind}.zip")
    base = os.path.basename(world)
    roots = [world] + [world + s for s in ("_nether", "_the_end") if os.path.isdir(world + s)]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for root_dir in roots:
            for root, _dirs, files in os.walk(root_dir):
                for fn in files:
                    if fn == "session.lock":
                        continue
                    full = os.path.join(root, fn)
                    try:
                        z.write(full, os.path.relpath(full, prof.dir))
                    except (PermissionError, OSError):
                        pass
        z.comment = f"world={base}".encode()
    prune_backups(prof)
    return out


def list_backups(prof: Profile) -> list[str]:
    d = backups_dir(prof)
    if not os.path.isdir(d):
        return []
    return sorted((os.path.join(d, f) for f in os.listdir(d) if f.endswith(".zip")), reverse=True)


def backup_time(path: str) -> float | None:
    """ファイル名（20260101-120000_auto.zip）から作った時刻を読む。"""
    m = re.match(r"(\d{8}-\d{6})_", os.path.basename(path))
    if not m:
        return None
    try:
        return datetime.datetime.strptime(m.group(1), "%Y%m%d-%H%M%S").timestamp()
    except ValueError:
        return None


AUTO_KINDS = ("_auto.zip", "_start.zip", "_before-rollback.zip")


def prune_backups(prof: Profile, now: float | None = None):
    """自動のバックアップを整理する。
    新しい順に「残す数」だけ残し、さらに「1時間ごとに1つ（24時間ぶん）」「1日ごとに1つ（7日ぶん）」も残す。
    （「1時間前」「昨日」に巻き戻せるように）"""
    keep = int(prof.get("backup_keep", 10) or 10)
    now = now or time.time()
    autos = [b for b in list_backups(prof) if b.endswith(AUTO_KINDS)]
    keepers = set(autos[:keep])
    hours, days = set(), set()
    for b in autos:  # 新しい順
        t = backup_time(b)
        if t is None:
            keepers.add(b)
            continue
        age = now - t
        if age <= 24 * 3600:
            h = int(age // 3600)
            if h not in hours:
                hours.add(h)
                keepers.add(b)
        elif age <= 8 * 24 * 3600:
            d = int(age // 86400)
            if d not in days:
                days.add(d)
                keepers.add(b)
    for old in autos:
        if old not in keepers:
            try:
                os.remove(old)
            except OSError:
                pass
    # 巻き戻しのときによけた古いワールドは、新しい2つだけ残す
    d = backups_dir(prof)
    if os.path.isdir(d):
        rep = sorted({re.sub(r"(_nether|_the_end)$", "", f) for f in os.listdir(d) if f.startswith("replaced_")}, reverse=True)
        for old in rep[2:]:
            for suffix in ("", "_nether", "_the_end"):
                shutil.rmtree(os.path.join(d, old + suffix), ignore_errors=True)


def restore_backup(prof: Profile, zip_path: str):
    """今のワールドを念のため退避してから、バックアップを戻す。"""
    world = prof.world_dir()
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    for suffix in ("", "_nether", "_the_end"):
        if os.path.isdir(world + suffix):
            shutil.move(world + suffix, os.path.join(backups_dir(prof), f"replaced_{stamp}{suffix}"))
    with zipfile.ZipFile(zip_path) as z:
        javamgr.safe_extract_zip(z, prof.dir)


# ---------------- ホワイトリスト・OP（停止中に編集する用） ----------------

def lookup_uuid(name: str) -> tuple[str, str]:
    """プレイヤー名から UUID を調べる（Mojang の公式窓口）。"""
    if not re.fullmatch(r"\w{2,16}", name or ""):
        raise ValueError(f"「{name}」はマイクラの名前として使えない形です（英数字と _ の 3〜16 文字）")
    try:
        data = net.get_json(f"https://api.mojang.com/users/profiles/minecraft/{name}")
    except net.NetError as e:
        if "404" in str(e) or "400" in str(e):
            raise ValueError(f"「{name}」というプレイヤーが見つかりません（つづりを確認してください）") from e
        raise  # ネットにつながらないなど
    except ValueError as e:  # 204（中身なし）＝見つからない
        raise ValueError(f"「{name}」というプレイヤーが見つかりません（つづりを確認してください）") from e
    raw = data["id"]
    uuid = f"{raw[0:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"
    return data["name"], uuid


def read_list(prof: Profile, kind: str) -> list[dict]:
    return load_json(os.path.join(prof.dir, f"{kind}.json"), [])


def add_to_list(prof: Profile, kind: str, player: str):
    name, uuid = lookup_uuid(player)
    entries = [e for e in read_list(prof, kind) if e.get("uuid") != uuid]
    entry = {"uuid": uuid, "name": name}
    if kind == "ops":
        entry.update({"level": 4, "bypassesPlayerLimit": False})
    entries.append(entry)
    save_json(os.path.join(prof.dir, f"{kind}.json"), entries)
    return name


def remove_from_list(prof: Profile, kind: str, player: str):
    entries = [e for e in read_list(prof, kind) if e.get("name", "").lower() != player.lower()]
    save_json(os.path.join(prof.dir, f"{kind}.json"), entries)
