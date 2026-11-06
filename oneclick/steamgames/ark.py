"""ARK: Survival Ascended(ASA) / ARK: Survival Evolved(ASE)。"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .manager import BaseManager, field
from .rcon import Rcon

ARK_LOG_RULES = [
    (("address already in use", "failed to bind", "bind failed"),
     "ポート番号が他のソフトに使われています。前のサーバーが残っていないか確認するか、「設定」でポート番号を変えてください。"),
    (("out of memory", "outofmemory", "not enough memory", "memory allocation"),
     "メモリが足りません。他のソフトを閉じるか、MOD を減らしてください。"),
    (("no space left", "disk full", "not enough space"),
     "ディスクの空き容量が足りません。不要なファイルを消してください。"),
    (("failed to download mod", "unable to download mod", "mod download"),
     "MOD のダウンロードに失敗しています。ネット接続と、MOD の ID が合っているか確認してください。"),
    (("fatal error", "lowlevelfatalerror", "unhandled exception", "access violation"),
     "サーバーが異常終了しています。MOD を全部外して起動できるか試してください。直るなら、原因は MOD です。"),
    (("steam api", "steamapi_init", "steam is not running"),
     "Steam との接続に失敗しています。ネット接続を確認して、サーバーを更新し直してください。"),
    (("vcruntime", "msvcp", "0xc000007b"),
     "Windows の部品(Visual C++)が足りません。Microsoft の公式サイトから「Visual C++ 再頒布可能パッケージ」を入れてください。"),
]


class ArkBase(BaseManager):
    PROTO = "UDP"
    DISK_GB = 30
    RAM_GB = 16
    SAVE_DIRS = ("ShooterGame/Saved/SavedArks", "ShooterGame/Saved/Config")
    LOG_REL = "ShooterGame/Saved/Logs/ShooterGame.log"
    CHAT_COMMAND = "ServerChat {text}"
    ADMIN_BUTTONS = [
        {"id": "listplayers", "label": "いま入っている人を見る", "cmd": "listplayers"},
        {"id": "saveworld", "label": "ワールドを保存", "cmd": "saveworld"},
        {"id": "destroydinos", "label": "野生の恐竜をリセット", "cmd": "destroywilddinos",
         "confirm": "野生の恐竜を全部消します(自動で再び生まれます)。よろしいですか?",
         "help": "野生の恐竜を<b>全部消します</b>。しばらくすると、また自動で生まれます。動きがおかしいときや、重いときに使います。"},
    ]
    LOG_RULES = ARK_LOG_RULES
    MAPS: dict = {}

    @classmethod
    def build_fields(cls):
        return [
            field("session_name", "サーバー名", group="basic",
                  help="ゲームのサーバー一覧に出る<b>名前</b>です。「?」と「\"」は使えません。"),
            field("map", "マップ", "select", options=cls.MAPS,
                  help="遊ぶ<b>マップ(島)</b>を選びます。マップごとにワールドが別に保存されるので、変えても元のワールドは消えません。"),
            field("server_password", "参加パスワード(空なら誰でも入れます)", "password",
                  help="入るときに必要な<b>合言葉</b>です。空なら、アドレスを知っている人は<b>誰でも</b>入れます。外に公開するときは、付けるのがおすすめです。"),
            field("admin_password", "管理者パスワード(必須)", "password",
                  help="サーバーを<b>管理するための合言葉</b>です(必須)。このソフトが安全に止めたり保存したりするときにも使います。参加パスワードとは<b>別のもの</b>にしてください。"),
            field("max_players", "最大人数", "number", min=1, max=200,
                  help="同時に入れる人数の上限です。多いほど、PCのメモリと回線が必要です。友達と遊ぶなら<b>5〜10人</b>くらいからがおすすめです。"),
            field("port", "ゲーム用(UDP)", "number", group="port", port=True,
                  help="ゲームの通信に使う番号(UDP)です。普通は<b>7777のまま</b>で大丈夫です。" + cls.PORT_NOTE),
            field("query_port", "クエリ用(UDP)", "number", group="port", port=True,
                  help="サーバー一覧に表示するための番号(UDP)です。普通は<b>27015のまま</b>で大丈夫です。"),
            field("rcon_port", "遠隔操作用(TCP・このPCの中だけで使います)", "number", group="port", port=True,
                  help="このソフトが、サーバーに命令を送るための番号(TCP)です。<b>外には開けないでください</b>。普通は "
                       f"{cls.DEFAULTS['rcon_port']} のままで大丈夫です。"),
            field("battleye", "BattlEye(不正対策)を有効にする", "bool", group="adv",
                  help="不正をするプログラムを防ぐ仕組みです。入れると安全ですが、起動が重くなったり、MODと合わないことがあります。普通は<b>オフ</b>で大丈夫です。"),
            field("extra_args", "追加の起動オプション(空白区切り)", group="adv",
                  help="上級者向けです。起動のときに足したいオプションを、空白で区切って書きます。分からなければ<b>空のまま</b>で大丈夫です。"),
        ]

    PORT_NOTE = ""

    # ---------- 設定 ----------
    def _after_load(self, s):
        if s.get("map") not in self.MAPS:
            s["map"] = self.DEFAULTS["map"]
        if not isinstance(s.get("mods"), list):
            s["mods"] = []
        return s

    def _validate_game(self, merged, new):
        for m in merged.get("mods", []):
            try:
                if int(m) <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                return "MOD の ID は、数字で入力してください。"
        try:
            if not 1 <= int(merged["max_players"]) <= 200:
                return "最大人数は 1〜200 にしてください。"
        except (TypeError, ValueError):
            return "最大人数が数字ではありません。"
        return ""

    def check_before_start(self):
        s = self.settings
        if not s["admin_password"]:
            return "管理者パスワードが空です。「設定」で入力してください(安全な停止や保存に必要です)。"
        if not str(s["session_name"]).strip():
            return "サーバー名が空です。"
        return super().check_before_start()

    # ---------- RCON ----------
    def send_command(self, cmd):
        r = Rcon("127.0.0.1", int(self.settings["rcon_port"]), self.settings["admin_password"])
        try:
            return r.command(cmd)
        finally:
            r.close()

    def player_count(self):
        from . import extras
        return extras.count_listplayers(self.send_command("listplayers"))

    def save_world(self):
        try:
            self.send_command("saveworld")
            time.sleep(3)
            return True
        except Exception as e:  # noqa: BLE001
            self.log(f"セーブできませんでした(まだ起動中かもしれません): {e}")
            return False

    def graceful_exit(self):
        self.send_command("doexit")

    def _restore_target(self, member):
        # 統合前の ARK 版のバックアップは「SavedArks/…」「Config/…」から始まっていた
        if member.startswith(("SavedArks/", "Config/")):
            return "ShooterGame/Saved/" + member
        return member

    def diag_ports(self):
        return [("ゲーム用", self.settings["port"], "UDP"), ("クエリ用", self.settings["query_port"], "UDP")]

    def tunnel_text(self):
        return "種類は <b>Game Server (UDP)</b>、Local Port は <b>7777</b> です。"

    def router_ports_text(self):
        return f"UDP の {self.settings['port']} と {self.settings['query_port']}"

    def firewall_rules(self):
        s = self.settings
        return [(f"OneClickServer {self.SHORT} Game", "UDP", s["port"]),
                (f"OneClickServer {self.SHORT} Query", "UDP", s["query_port"])]


class ArkAsa(ArkBase):
    KEY = "ark_asa"
    NAME = "ARK: Survival Ascended"
    SHORT = "ASA"
    APPID = "2430930"
    PROCS = ("ArkAscendedServer.exe",)
    EXE_REL = "ShooterGame/Binaries/Win64/ArkAscendedServer.exe"
    MAPS = {
        "TheIsland_WP": "The Island(基本のマップ)",
        "ScorchedEarth_WP": "Scorched Earth",
        "TheCenter_WP": "The Center",
        "Aberration_WP": "Aberration",
        "Extinction_WP": "Extinction",
    }
    DEFAULTS = {"map": "TheIsland_WP", "port": 7777, "query_port": 27015, "rcon_port": 27020, "mods": [],
                "session_name": "OneClickServer ASA", "server_password": "", "admin_password": "",
                "max_players": 10, "battleye": False}
    MOD_UPDATE_NOTE = "ASA の MOD は、サーバーを起動するときに、ゲームが自動で最新にします。(CurseForge の仕組み上、このソフトからは更新の通知ができません)"
    MODS = {"label": "CurseForge の MOD ページにある「Project ID」(数字)",
            "note": "サーバーを起動すると、MOD は自動でダウンロードされます。友達もゲームに入るときに自動で入ります。",
            "example": "929578"}

    def build_command(self):
        s = self.settings
        q = [s["map"], "listen", f"SessionName={s['session_name']}", f"Port={s['port']}",
             f"QueryPort={s['query_port']}", "RCONEnabled=True", f"RCONPort={s['rcon_port']}"]
        if s["server_password"]:
            q.append(f"ServerPassword={s['server_password']}")
        q.append(f"ServerAdminPassword={s['admin_password']}")
        args = ["?".join(q), f"-WinLiveMaxPlayers={s['max_players']}", "-log"]
        mods = [str(m) for m in s["mods"]]
        if mods:
            args.append("-mods=" + ",".join(mods))
        if not s["battleye"]:
            args.append("-NoBattlEye")
        if str(s["extra_args"]).strip():
            args += str(s["extra_args"]).split()
        return [str(self.server_exe())] + args


class ArkAse(ArkBase):
    KEY = "ark_ase"
    NAME = "ARK: Survival Evolved"
    SHORT = "ASE"
    APPID = "376030"
    PROCS = ("ShooterGameServer.exe",)
    EXE_REL = "ShooterGame/Binaries/Win64/ShooterGameServer.exe"
    DISK_GB = 20
    RAM_GB = 8
    MAPS = {
        "TheIsland": "The Island(基本のマップ)",
        "ScorchedEarth_P": "Scorched Earth",
        "TheCenter": "The Center",
        "Ragnarok": "Ragnarok",
        "Aberration_P": "Aberration",
        "Extinction": "Extinction",
        "Valguero_P": "Valguero",
        "Genesis": "Genesis",
        "CrystalIsles": "Crystal Isles",
        "Gen2": "Genesis Part 2",
        "LostIsland": "Lost Island",
        "Fjordur": "Fjordur",
    }
    DEFAULTS = {"map": "TheIsland", "port": 7777, "query_port": 27015, "rcon_port": 32330, "mods": [],
                "session_name": "OneClickServer ASE", "server_password": "", "admin_password": "",
                "max_players": 10, "battleye": False}
    PORT_NOTE = "ASE では、<b>次の番号(7778)も</b>使います。"
    MODS = {"label": "Steam ワークショップの MOD ページにある ID(数字)",
            "note": "サーバーを起動すると、MOD は自動でダウンロードされます。友達もゲームに入るときに自動で入ります。",
            "example": "731604991"}

    def _validate_game(self, merged, new):
        msg = super()._validate_game(merged, new)
        if msg:
            return msg
        ports = [int(merged["port"]), int(merged["query_port"]), int(merged["rcon_port"])]
        if (ports[0] + 1) in ports:
            return "ASE はゲーム用の次の番号(例: 7777 なら 7778)も使います。他のポートと重ならないようにしてください。"
        return ""

    def build_command(self):
        s = self.settings
        q = [s["map"], "listen", f"SessionName={s['session_name']}", f"Port={s['port']}",
             f"QueryPort={s['query_port']}", "RCONEnabled=True", f"RCONPort={s['rcon_port']}"]
        if s["server_password"]:
            q.append(f"ServerPassword={s['server_password']}")
        q.append(f"ServerAdminPassword={s['admin_password']}")
        q.append(f"MaxPlayers={s['max_players']}")
        args = ["?".join(q), "-server", "-log"]
        if s["mods"]:
            args.append("-automanagedmods")
        if not s["battleye"]:
            args.append("-NoBattlEye")
        if str(s["extra_args"]).strip():
            args += str(s["extra_args"]).split()
        return [str(self.server_exe())] + args

    MOD_UPDATE_NOTE = "MOD が更新されると、このページでお知らせします。サーバーを再起動すると、最新になります。"

    @property
    def _mod_seen_file(self):
        return self.root / "mod_seen.json"

    def _load_mod_seen(self) -> dict:
        try:
            return json.loads(self._mod_seen_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def check_mod_updates(self) -> list:
        """Steam ワークショップで、入れている MOD の更新を調べる。初めて見た MOD は、基準として覚えるだけ。"""
        from . import extras
        ids = [str(m) for m in self.settings.get("mods", [])]
        if not ids:
            return []
        details = extras.workshop_details(ids)
        self._mod_latest = {i: d["updated"] for i, d in details.items()}
        seen = self._load_mod_seen()
        out, changed = [], False
        for i, d in details.items():
            if i not in seen:
                seen[i] = d["updated"]
                changed = True
            elif d["updated"] > int(seen[i]):
                out.append({"id": i, "title": d["title"],
                            "ago": extras.fmt_ago(time.time() - d["updated"])})
        if changed:
            try:
                self._mod_seen_file.write_text(json.dumps(seen), encoding="utf-8")
            except OSError:
                pass
        return out

    def _after_start(self):
        """起動すると、ゲームが MOD を最新にするので、「更新を見た」記録を進める。"""
        latest = getattr(self, "_mod_latest", None)
        if latest:
            seen = self._load_mod_seen()
            seen.update(latest)
            try:
                self._mod_seen_file.write_text(json.dumps(seen), encoding="utf-8")
            except OSError:
                pass
        self.upd["mods"] = []

    def prepare_start(self):
        """ASE の MOD は、設定ファイル(GameUserSettings.ini)の ActiveMods に書く。"""
        path = self.server_dir / "ShooterGame" / "Saved" / "Config" / "WindowsServer" / "GameUserSettings.ini"
        write_active_mods(path, self.settings["mods"])

    def firewall_rules(self):
        rules = super().firewall_rules()
        rules.append((f"OneClickServer {self.SHORT} Raw", "UDP", int(self.settings["port"]) + 1))
        return rules

    def router_ports_text(self):
        s = self.settings
        return f"UDP の {s['port']}、{int(s['port']) + 1}、{s['query_port']}"

    def tunnel_text(self):
        return "種類は <b>Game Server (UDP)</b> で、Local Port は <b>7777</b> と <b>7778</b> の2つ分を作ります。"


def write_active_mods(path: Path, mods) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []
    value = ",".join(str(m) for m in mods)
    out, in_sec, done = [], False, False
    for ln in lines:
        st = ln.strip()
        if st.startswith("["):
            if in_sec and not done:
                out.append(f"ActiveMods={value}")
                done = True
            in_sec = st.lower() == "[serversettings]"
        if in_sec and st.lower().startswith("activemods="):
            if not done:
                out.append(f"ActiveMods={value}")
                done = True
            continue
        out.append(ln)
    if not done:
        if not in_sec:
            out.append("[ServerSettings]")
        out.append(f"ActiveMods={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


# FIELDS は、受け継いだクラスごとの MAPS・DEFAULTS を使うので、定義の後で作る
ArkAsa.FIELDS = ArkAsa.build_fields()
ArkAse.FIELDS = ArkAse.build_fields()
