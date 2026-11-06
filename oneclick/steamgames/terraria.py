"""Terraria の専用サーバー(公式サイト terraria.org で配布されている zip を使う)。

サーバーは「コンソール」で動くので、命令は標準入力に書き込んで送り、出力はログに映す。
設定は serverconfig.txt に書いて、起動の引数 -config で渡す。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from .manager import BaseManager, field

NAMES_URL = "https://terraria.org/api/get/dedicated-servers-names"
DOWNLOAD_URL = "https://terraria.org/api/download/pc-dedicated-server/{zipname}"
FALLBACK_ZIP = "terraria-server-1458.zip"   # 最新の名前が調べられなかったときに使う(2026年11月時点の最新)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) OneClickServer"
BAD_FILE_CHARS = '\\/:*<>|'


def latest_zip_name(fetch=None) -> str:
    """terraria.org に、いちばん新しいサーバーの zip の名前を聞く。分からなければ既定の名前。"""
    try:
        raw = (fetch or _fetch_text)(NAMES_URL)
        names = [n for n in json.loads(raw) if re.fullmatch(r"terraria-server-\d+\.zip", str(n))]
        if names:
            return max(names, key=lambda n: int(re.search(r"\d+", n).group()))
    except Exception:  # noqa: BLE001
        pass
    return FALLBACK_ZIP


def _fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")


def normalize_zip_name(v: str) -> str:
    """「1458」「terraria-server-1458」「terraria-server-1458.zip」のどれでも、zip の名前にする。"""
    v = str(v or "").strip()
    m = re.fullmatch(r"(?:terraria-server-)?(\d{3,5})(?:\.zip)?", v)
    return f"terraria-server-{m.group(1)}.zip" if m else ""


def safe_extract(zip_path: Path, dest: Path):
    dest = dest.resolve()
    with zipfile.ZipFile(zip_path) as z:
        for m in z.namelist():
            target = (dest / m).resolve()
            if not target.is_relative_to(dest):
                raise RuntimeError(f"危険なパスが入っているので、中止しました: {m}")
        z.extractall(dest)


def find_windows_server_dir(root: Path) -> Path | None:
    for base, _dirs, files in os.walk(root):
        if "TerrariaServer.exe" in files:
            return Path(base)
    return None


def render_config(s: dict, worlds_dir: Path) -> str:
    wd = str(worlds_dir)
    name = str(s["world_name"])
    lines = [
        f"world={wd}\\{name}.wld" if os.name == "nt" else f"world={wd}/{name}.wld",
        f"autocreate={int(s['world_size'])}",
        f"worldname={name}",
        f"difficulty={int(s['difficulty'])}",
        f"maxplayers={int(s['max_players'])}",
        f"port={int(s['port'])}",
        f"password={s['password']}",
        f"motd={s['motd']}",
        f"worldpath={wd}",
        f"secure={1 if s['secure'] else 0}",
        "upnp=0",
        "npcstream=1",
        "priority=1",
    ]
    if str(s.get("seed", "")).strip():
        lines.insert(2, f"seed={str(s['seed']).strip()}")
    return "\n".join(lines) + "\n"


class Terraria(BaseManager):
    KEY = "terraria"
    NAME = "Terraria"
    SHORT = "Terraria"
    APPID = None
    PROCS = ("TerrariaServer.exe",)
    EXE_REL = "TerrariaServer.exe"
    SAVE_DIRS = ("worlds", "serverconfig.txt")
    PROTO = "TCP"
    DISK_GB = 2
    RAM_GB = 4
    NAME_KEY = "world_name"
    CHAT_COMMAND = "say {text}"
    ADMIN_BUTTONS = [
        {"id": "playing", "label": "いま入っている人を見る(結果はホームのログに出ます)", "cmd": "playing"},
        {"id": "save", "label": "ワールドを保存", "cmd": "save"},
    ]
    SIZES = {1: "小さい", 2: "普通", 3: "大きい"}
    DIFFS = {0: "クラシック(普通)", 1: "エキスパート(むずかしい)", 2: "マスター(とてもむずかしい)", 3: "ジャーニー(クリエイティブ)"}
    DEFAULTS = {
        "world_name": "TerrariaWorld", "world_size": 2, "difficulty": 0, "seed": "", "max_players": 8,
        "port": 7777, "password": "", "motd": "ようこそ!", "secure": True, "server_version": "",
    }
    FIELDS = [
        field("world_name", "ワールドの名前", group="basic",
              help="ワールドの<b>名前</b>です。同じ名前のワールドがすでにあるときは、それを使います。なければ新しく作ります。日本語も使えますが、<b>\\ / : * ? \" < > |</b> は使えません。"),
        field("world_size", "ワールドの大きさ(新しく作るとき)", "select", options=SIZES,
              help="新しいワールドを作るときだけ使います。すでにあるワールドには関係ありません。"),
        field("difficulty", "むずかしさ(新しく作るとき)", "select", options=DIFFS,
              help="新しいワールドを作るときだけ使います。あとから変えることはできません。初めてなら<b>クラシック</b>がおすすめです。"),
        field("seed", "シード(空でOK・新しく作るとき)", group="basic",
              help="同じ<b>シード</b>を入れると、同じ地形のワールドができます。空なら、ランダムです。"),
        field("password", "参加パスワード(空なら誰でも入れます)", "password",
              help="入るときに必要な<b>合言葉</b>です。空なら、アドレスを知っている人は<b>誰でも</b>入れます。外に公開するときは、付けるのがおすすめです。"),
        field("max_players", "最大人数", "number", min=1, max=255,
              help="同時に入れる人数の上限です。友達と遊ぶなら<b>8人</b>くらいで十分です。"),
        field("motd", "入ったときのメッセージ", group="basic", help="サーバーに入ったときに、画面に出るメッセージです。"),
        field("port", "ゲーム用(TCP)", "number", group="port", port=True,
              help="ゲームの通信に使う番号(<b>TCP</b>)です。普通は<b>7777のまま</b>で大丈夫です。"),
        field("secure", "不正対策を有効にする", "bool", group="adv",
              help="不正なプログラムを防ぐ仕組みです。普通は<b>オン</b>で大丈夫です。"),
        field("server_version", "サーバーの版(空なら最新)", group="adv",
              help="上級者向けです。「1458」のように書くと、その版のサーバーを入れます。ゲームの版と合わないときだけ使ってください。空なら、いちばん新しい版を入れます。"),
        field("extra_args", "追加の起動オプション(空白区切り)", group="adv",
              help="上級者向けです。分からなければ<b>空のまま</b>で大丈夫です。"),
    ]
    LOG_RULES = [
        (("only one usage of each socket address", "address already in use"),
         "ポート番号が他のソフトに使われています。前のサーバーが残っていないか確認するか、「設定」でポート番号を変えてください。"),
        (("out of memory", "outofmemory"), "メモリが足りません。他のソフトを閉じてください。"),
        (("unhandled exception", "exception"), "サーバーでエラーが起きています。ワールドのファイルが壊れていないか、バックアップから戻して確認してください。"),
    ]

    def __init__(self, *a, **kw):
        self._lines: list[str] = []
        super().__init__(*a, **kw)

    # ---------- 設定 ----------
    def _validate_game(self, merged, new):
        name = str(merged["world_name"]).strip()
        if not name:
            return "ワールドの名前が空です。"
        if any(c in name for c in BAD_FILE_CHARS) or name in (".", "..") or name.endswith("."):
            return "ワールドの名前に、\\ / : * ? \" < > | は使えません。"
        if len(name) > 40:
            return "ワールドの名前は 40 文字までにしてください。"
        if "server_version" in new and str(new["server_version"]).strip() and not normalize_zip_name(new["server_version"]):
            return "サーバーの版は、「1458」のような数字で入れてください(空なら最新です)。"
        if "difficulty" in new or "world_size" in new:
            if int(merged["difficulty"]) not in self.DIFFS or int(merged["world_size"]) not in self.SIZES:
                return "むずかしさかワールドの大きさが正しくありません。"
        return ""

    def check_before_start(self):
        if not str(self.settings["world_name"]).strip():
            return "ワールドの名前が空です。"
        return super().check_before_start()

    # ---------- 導入(zip をダウンロードして展開) ----------
    def install_note(self):
        return "約 30MB のダウンロードで、すぐ終わります。"

    def check_game_update(self):
        """Terraria は、公式の最新の zip 名と、入れたときの zip 名を比べる(版を固定しているときは調べない)。"""
        if not self.installed() or normalize_zip_name(self.settings.get("server_version", "")):
            return None
        try:
            inst = (self.root / "installed_zip.txt").read_text(encoding="utf-8").strip()
        except OSError:
            return None
        latest = latest_zip_name()
        def num(z):
            m = re.search(r"(\d+)", z or "")
            return int(m.group(1)) if m else 0
        if inst and num(latest) > num(inst):
            return {"installed": inst, "latest": latest}
        return None

    def _do_install(self):
        want = normalize_zip_name(self.settings.get("server_version", "")) or latest_zip_name()
        url = DOWNLOAD_URL.format(zipname=want)
        tmp = self.root / "download"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)
        zpath = tmp / want
        self.log(f"{want} をダウンロードしています…")
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r, open(zpath, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total:
                    self.progress = f"ダウンロード中 {done * 100 // total}%"
        self.progress = "展開しています…"
        ex = tmp / "x"
        safe_extract(zpath, ex)
        src = find_windows_server_dir(ex)
        if not src:
            raise RuntimeError("zip の中に TerrariaServer.exe が見つかりませんでした。")
        self.server_dir.mkdir(parents=True, exist_ok=True)
        # ワールドと設定は残して、サーバー本体のファイルだけ入れ替える
        for item in src.iterdir():
            dest = self.server_dir / item.name
            if item.name.lower() in ("worlds", "serverconfig.txt"):
                continue
            if item.is_dir():
                shutil.rmtree(dest, ignore_errors=True)
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)
        (self.server_dir / "worlds").mkdir(exist_ok=True)
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            (self.root / "installed_zip.txt").write_text(want, encoding="utf-8")
        except OSError:
            pass
        self.log(f"Terraria のサーバー({want})を入れました。")

    # ---------- 起動 ----------
    @property
    def worlds_dir(self) -> Path:
        return self.server_dir / "worlds"

    @property
    def config_path(self) -> Path:
        return self.server_dir / "serverconfig.txt"

    def prepare_start(self):
        self.worlds_dir.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(render_config(self.settings, self.worlds_dir), encoding="utf-8")

    def build_command(self):
        cmd = [str(self.server_exe()), "-config", str(self.config_path)]
        if str(self.settings["extra_args"]).strip():
            cmd += str(self.settings["extra_args"]).split()
        return cmd

    def _popen_kwargs(self):
        return dict(cwd=str(self.server_exe().parent), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def _after_start(self):
        proc = self.proc
        self._lines = []

        def pump():
            try:
                for raw in iter(proc.stdout.readline, b""):
                    line = raw.decode("utf-8", "replace").rstrip()
                    if line:
                        self._lines.append(line)
                        self._lines = self._lines[-400:]
                        self.log(line)
            except Exception:  # noqa: BLE001
                pass
        threading.Thread(target=pump, daemon=True).start()

    # ---------- 命令(標準入力に書く) ----------
    def send_command(self, cmd):
        if self.proc is None or self.proc.poll() is not None or self.proc.stdin is None:
            raise RuntimeError("このソフトを開き直したあとは、命令を送れません(いったん停止して、起動し直してください)")
        self.proc.stdin.write((cmd.replace("\n", " ") + "\n").encode("utf-8"))
        self.proc.stdin.flush()
        return ""

    def save_world(self):
        try:
            self.send_command("save")
            time.sleep(2)
            return True
        except Exception as e:  # noqa: BLE001
            self.log(f"セーブできませんでした: {e}")
            return False

    def graceful_exit(self):
        self.send_command("exit")

    def log_text(self):
        return "\n".join(self._lines).lower() if self._lines else None

    def diag_ports(self):
        return [("ゲーム用", self.settings["port"], "TCP")]
