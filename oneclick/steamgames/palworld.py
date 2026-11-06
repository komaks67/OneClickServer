"""Palworld の専用サーバー(Steam のアプリID 2394010)。

設定は Pal/Saved/Config/WindowsServer/PalWorldSettings.ini の「OptionSettings=(…)」1行に入る。
このソフトは、起動の直前にその1行を書き換える(他の項目は元のまま残す)。
サーバーへの命令は、新しい REST API(8212)を先に使い、だめなら RCON(25575)に切り替える。
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
import time
import urllib.error
import urllib.request

from .manager import IS_WIN, NOWIN, BaseManager, field
from .rcon import Rcon

INI_SECTION = "[/Script/Pal.PalGameWorldSettings]"


# ---------------- PalWorldSettings.ini の読み書き(テストできるように、関数に分けてある) ----------------

def split_top_level(body: str) -> list[str]:
    """かっこの外・引用符の外にあるカンマだけで区切る。"""
    parts, buf, depth, quote = [], [], 0, False
    for ch in body:
        if ch == '"':
            quote = not quote
        elif not quote:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                parts.append("".join(buf))
                buf = []
                continue
        buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return [p for p in parts if p.strip()]


def parse_option_settings(text: str) -> dict:
    """ini の文字列から、OptionSettings の中身を {名前: 文字列の値} にして返す(順番は元のまま)。"""
    m = re.search(r"OptionSettings\s*=\s*\((.*)\)\s*$", text, re.S | re.M)
    if not m:
        return {}
    items = {}
    for part in split_top_level(m.group(1).strip()):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        items[k.strip()] = v.strip()
    return items


def format_value(value) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, float):
        return f"{value:.6f}"
    if isinstance(value, int):
        return str(value)
    return '"' + str(value).replace('"', "") + '"'


def render_ini(items: dict) -> str:
    body = ",".join(f"{k}={v}" for k, v in items.items())
    return f"{INI_SECTION}\nOptionSettings=({body})\n"


# 設定の名前 → (ini の名前, 種類)。種類: str / bool / float / int / enum(文字だが引用符なし)
INI_MAP = {
    "server_name": ("ServerName", "str"),
    "server_description": ("ServerDescription", "str"),
    "server_password": ("ServerPassword", "str"),
    "admin_password": ("AdminPassword", "str"),
    "max_players": ("ServerPlayerMaxNum", "int"),
    "port": ("PublicPort", "int"),
    "rcon_port": ("RCONPort", "int"),
    "rest_port": ("RESTAPIPort", "int"),
    "exp_rate": ("ExpRate", "float"),
    "capture_rate": ("PalCaptureRate", "float"),
    "spawn_rate": ("PalSpawnNumRate", "float"),
    "day_speed": ("DayTimeSpeedRate", "float"),
    "night_speed": ("NightTimeSpeedRate", "float"),
    "egg_hours": ("PalEggDefaultHatchingTime", "float"),
    "death_penalty": ("DeathPenalty", "enum"),
    "pvp": ("bIsPvP", "bool"),
    "friendly_fire": ("bEnableFriendlyFire", "bool"),
    "invader": ("bEnableInvaderEnemy", "bool"),
}


def apply_settings_to_ini(items: dict, settings: dict) -> dict:
    """元の項目を残したまま、このソフトで決めた項目と、遠隔操作用の項目を書き込む。"""
    out = dict(items)
    for key, (ini_key, kind) in INI_MAP.items():
        v = settings[key]
        if kind == "enum":
            out[ini_key] = str(v)
        elif kind == "bool":
            out[ini_key] = format_value(bool(v))
        elif kind == "float":
            out[ini_key] = format_value(float(v))
        elif kind == "int":
            out[ini_key] = format_value(int(v))
        else:
            out[ini_key] = format_value(str(v))
    out["RCONEnabled"] = "True"
    out["RESTAPIEnabled"] = "True"
    return out


class Palworld(BaseManager):
    KEY = "palworld"
    NAME = "Palworld"
    SHORT = "Palworld"
    APPID = "2394010"
    PROCS = ("PalServer.exe", "PalServer-Win64-Shipping-Cmd.exe")
    EXE_REL = "PalServer.exe"
    SAVE_DIRS = ("Pal/Saved/SaveGames", "Pal/Saved/Config")
    LOG_REL = "Pal/Saved/Logs/Pal.log"
    PROTO = "UDP"
    DISK_GB = 15
    RAM_GB = 16
    NAME_KEY = "server_name"
    CHAT_COMMAND = "announce {text}"
    ADMIN_BUTTONS = [
        {"id": "players", "label": "いま入っている人を見る", "cmd": "showplayers"},
        {"id": "save", "label": "ワールドを保存", "cmd": "save"},
        {"id": "info", "label": "サーバーの情報を見る", "cmd": "info"},
    ]
    DEFAULTS = {
        "server_name": "OneClickServer Palworld", "server_description": "",
        "server_password": "", "admin_password": "", "max_players": 16,
        "port": 8211, "rcon_port": 25575, "rest_port": 8212,
        "exp_rate": 1.0, "capture_rate": 1.0, "spawn_rate": 1.0,
        "day_speed": 1.0, "night_speed": 1.0, "egg_hours": 72.0,
        "death_penalty": "All", "pvp": False, "friendly_fire": False, "invader": True,
        "public_lobby": False, "multithread": True,
    }
    DEATH = {"None": "何も落とさない", "Item": "アイテムだけ落とす", "ItemAndEquipment": "アイテムと装備を落とす",
             "All": "アイテム・装備・仲間(パル)を落とす"}
    FIELDS = [
        field("server_name", "サーバー名", group="basic", help="ゲームの接続画面に出る<b>名前</b>です。「?」と「\"」は使えません。"),
        field("server_description", "サーバーの説明", group="basic", help="サーバーの<b>ひとこと説明</b>です。空でも大丈夫です。"),
        field("server_password", "参加パスワード(空なら誰でも入れます)", "password",
              help="入るときに必要な<b>合言葉</b>です。空なら、アドレスを知っている人は<b>誰でも</b>入れます。外に公開するときは、付けるのがおすすめです。"),
        field("admin_password", "管理者パスワード(必須)", "password",
              help="サーバーを<b>管理するための合言葉</b>です(必須)。このソフトが安全に止めたり保存したりするときにも使います。ゲームの中でチャットに <b>/AdminPassword パスワード</b> と打つと、管理者になれます。"),
        field("max_players", "最大人数", "number", min=1, max=32,
              help="同時に入れる人数の上限です(最大32)。多いほど、PCのメモリと回線が必要です。友達と遊ぶなら<b>4〜16人</b>くらいがおすすめです。"),
        field("exp_rate", "経験値の倍率", "float", group="game", min=0.1, max=20,
              help="<b>1</b>が基本です。2にすると、経験値が2倍もらえてレベルが上がりやすくなります。"),
        field("capture_rate", "パルの捕まえやすさ", "float", group="game", min=0.5, max=2,
              help="<b>1</b>が基本です。大きくすると、パルを捕まえやすくなります(最大2)。"),
        field("spawn_rate", "パルの出る数", "float", group="game", min=0.5, max=3,
              help="<b>1</b>が基本です。大きくすると、野生のパルがたくさん出ます。多すぎるとPCが重くなります。"),
        field("day_speed", "昼の進む速さ", "float", group="game", min=0.1, max=5,
              help="<b>1</b>が基本です。大きくすると、昼が早く終わります。"),
        field("night_speed", "夜の進む速さ", "float", group="game", min=0.1, max=5,
              help="<b>1</b>が基本です。大きくすると、夜が早く終わります。"),
        field("egg_hours", "卵がかえる時間(時間)", "float", group="game", min=0, max=240,
              help="大きい卵がかえるまでの時間です。基本は<b>72</b>時間です。<b>0</b>にすると、すぐにかえります。"),
        field("death_penalty", "やられたときに落とすもの", "select", group="game", options=DEATH,
              help="プレイヤーがやられたときの<b>ペナルティ</b>です。「何も落とさない」にすると、気軽に遊べます。"),
        field("pvp", "プレイヤー同士で戦えるようにする(PvP)", "bool", group="game", help="オンにすると、プレイヤー同士で攻撃できます。友達と協力して遊ぶなら<b>オフ</b>がおすすめです。"),
        field("friendly_fire", "味方への攻撃を有効にする", "bool", group="game", help="オンにすると、味方の攻撃が仲間にも当たります。普通は<b>オフ</b>です。"),
        field("invader", "拠点への襲撃イベントを起こす", "bool", group="game", help="オフにすると、拠点が野生のパルや人間に襲われなくなります。のんびり遊びたいときに。"),
        field("port", "ゲーム用(UDP)", "number", group="port", port=True,
              help="ゲームの通信に使う番号(UDP)です。普通は<b>8211のまま</b>で大丈夫です。"),
        field("rcon_port", "遠隔操作用 RCON(TCP・このPCの中だけ)", "number", group="port", port=True,
              help="このソフトが予備で使う番号です。<b>外には開けないでください</b>。普通は 25575 のままで大丈夫です。"),
        field("rest_port", "遠隔操作用 REST API(TCP・このPCの中だけ)", "number", group="port", port=True,
              help="このソフトが、サーバーに命令を送るのに使う番号です。<b>外には開けないでください</b>。普通は 8212 のままで大丈夫です。"),
        field("public_lobby", "コミュニティサーバー一覧に載せる(-publiclobby)", "bool", group="adv",
              help="オンにすると、ゲームの「コミュニティサーバー」の一覧にサーバーが出ます。家庭用ゲーム機の人と遊ぶときにも必要です。友達とだけなら<b>オフ</b>(アドレスを直接入力)で大丈夫です。"),
        field("multithread", "処理を速くするオプションを使う", "bool", group="adv",
              help="複数のCPUコアを使って、サーバーを軽くするオプションです。普通は<b>オン</b>で大丈夫です。不安定なときだけオフにしてください。"),
        field("extra_args", "追加の起動オプション(空白区切り)", group="adv",
              help="上級者向けです。起動のときに足したいオプションを、空白で区切って書きます。分からなければ<b>空のまま</b>で大丈夫です。"),
    ]
    LOG_RULES = [
        (("address already in use", "failed to bind", "bind failed"),
         "ポート番号が他のソフトに使われています。前のサーバーが残っていないか確認するか、「設定」でポート番号を変えてください。"),
        (("out of memory", "outofmemory", "not enough memory", "memory allocation"),
         "メモリが足りません。他のソフトを閉じるか、最大人数を減らしてください。"),
        (("no space left", "disk full", "not enough space"),
         "ディスクの空き容量が足りません。不要なファイルを消してください。"),
        (("fatal error", "unhandled exception", "access violation"),
         "サーバーが異常終了しています。設定を初期値に近づけて、もう一度起動できるか試してください。"),
        (("vcruntime", "msvcp", "0xc000007b"),
         "Windows の部品(Visual C++)が足りません。Microsoft の公式サイトから「Visual C++ 再頒布可能パッケージ」を入れてください。"),
    ]

    # ---------- 設定 ----------
    @property
    def ini_path(self):
        return self.server_dir / "Pal" / "Saved" / "Config" / "WindowsServer" / "PalWorldSettings.ini"

    def _validate_game(self, merged, new):
        if not 1 <= int(merged["max_players"]) <= 32:
            return "最大人数は 1〜32 にしてください。"
        return ""

    def check_before_start(self):
        if not self.settings["admin_password"]:
            return "管理者パスワードが空です。「設定」で入力してください(安全な停止や保存に必要です)。"
        if not str(self.settings["server_name"]).strip():
            return "サーバー名が空です。"
        return super().check_before_start()

    # ---------- 起動 ----------
    def build_command(self):
        s = self.settings
        args = [f"-port={s['port']}", f"-players={s['max_players']}"]
        if s["multithread"]:
            args += ["-useperfthreads", "-NoAsyncLoadingThread", "-UseMultithreadForDS"]
        if s["public_lobby"]:
            args.append("-publiclobby")
        if str(s["extra_args"]).strip():
            args += str(s["extra_args"]).split()
        return [str(self.server_exe())] + args

    def prepare_start(self):
        """起動の直前に PalWorldSettings.ini の OptionSettings を書き換える。"""
        ini = self.ini_path
        ini.parent.mkdir(parents=True, exist_ok=True)
        text = ""
        if ini.exists():
            text = ini.read_text(encoding="utf-8", errors="replace")
        if "OptionSettings" not in text:
            default = self.server_dir / "DefaultPalWorldSettings.ini"
            if default.exists():
                text = default.read_text(encoding="utf-8", errors="replace")
        items = parse_option_settings(text)
        items = apply_settings_to_ini(items, self.settings)
        ini.write_text(render_ini(items), encoding="utf-8")

    # ---------- 動いているかの見分け(PalServer.exe は、本体を呼び出す係なので両方見る) ----------
    def _shipping_running(self) -> bool:
        if not IS_WIN:
            return False
        try:
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq PalServer-Win64-Shipping-Cmd.exe", "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True, creationflags=NOWIN).stdout
            return "palserver-win64-shipping-cmd.exe" in out.lower()
        except Exception:  # noqa: BLE001
            return False

    def running(self):
        return super().running() or (self.pid is not None and self._shipping_running())

    def _kill(self):
        super()._kill()
        if IS_WIN:
            subprocess.run(["taskkill", "/IM", "PalServer-Win64-Shipping-Cmd.exe", "/F"],
                           capture_output=True, creationflags=NOWIN)

    # ---------- 命令を送る(REST API → だめなら RCON) ----------
    def _rest(self, method, path, body=None, timeout=8):
        url = f"http://127.0.0.1:{int(self.settings['rest_port'])}/v1/api/{path}"
        token = base64.b64encode(f"admin:{self.settings['admin_password']}".encode("utf-8")).decode("ascii")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": "Basic " + token, "Accept": "application/json", "Content-Type": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
        try:
            return json.loads(raw) if raw.strip() else {}
        except ValueError:
            return {"raw": raw}

    def player_count(self):
        d = self._rest("GET", "players", timeout=4)
        players = d.get("players") if isinstance(d, dict) else None
        return len(players) if isinstance(players, list) else None

    def _rcon(self, cmd):
        r = Rcon("127.0.0.1", int(self.settings["rcon_port"]), self.settings["admin_password"])
        try:
            return r.command(cmd)
        finally:
            r.close()

    def send_command(self, cmd):
        try:
            return self._send_rest(cmd)
        except Exception as rest_err:  # noqa: BLE001
            try:
                return self._send_rcon(cmd)
            except Exception as rcon_err:  # noqa: BLE001
                raise RuntimeError(f"REST: {rest_err} / RCON: {rcon_err}") from rcon_err

    def _send_rest(self, cmd):
        if cmd == "showplayers":
            d = self._rest("GET", "players")
            players = d.get("players", []) if isinstance(d, dict) else []
            if not players:
                return "いま入っている人はいません。"
            return "\n".join(f"{p.get('name', '?')}(レベル {p.get('level', '?')})" for p in players)
        if cmd == "save":
            self._rest("POST", "save", {})
            return "保存しました。"
        if cmd == "info":
            d = self._rest("GET", "info")
            return "\n".join(f"{k}: {v}" for k, v in d.items()) if isinstance(d, dict) else str(d)
        if cmd.startswith("announce "):
            self._rest("POST", "announce", {"message": cmd[len("announce "):]})
            return "送りました。"
        raise ValueError("不明なコマンドです")

    def _send_rcon(self, cmd):
        if cmd == "showplayers":
            return self._rcon("ShowPlayers")
        if cmd == "save":
            return self._rcon("Save")
        if cmd == "info":
            return self._rcon("Info")
        if cmd.startswith("announce "):
            return self._rcon("Broadcast " + cmd[len("announce "):].replace(" ", "_"))
        raise ValueError("不明なコマンドです")

    def save_world(self):
        try:
            self.send_command("save")
            time.sleep(3)
            return True
        except Exception as e:  # noqa: BLE001
            self.log(f"セーブできませんでした(まだ起動中かもしれません): {e}")
            return False

    def graceful_exit(self):
        try:
            self._rest("POST", "shutdown", {"waittime": 1, "message": "サーバーを停止します"})
        except Exception:  # noqa: BLE001
            self._rcon("DoExit")

    def diag_ports(self):
        return [("ゲーム用", self.settings["port"], "UDP")]

    def install_note(self):
        return "初回の導入は約 6GB のダウンロードです(空き容量は 15GB ほど必要です)。"
