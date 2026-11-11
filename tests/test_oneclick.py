"""OneClickServer の自動テスト(Windows が無くても動く範囲)。

実行: python -m unittest discover -s tests -v
"""
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import zipfile

TMP = tempfile.mkdtemp(prefix="ocs_test_")
import atexit; atexit.register(shutil.rmtree, TMP, True)
os.environ["ONECLICK_DATA"] = os.path.join(TMP, "data")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oneclick import migrate, paths, registry, selfupdate, shell, steamgames  # noqa: E402
from oneclick.steamgames import ark, manager as mgr, palworld, terraria  # noqa: E402


def fresh(cls):
    """テストごとに、まっさらな保存場所で管理係を作る(見張りのスレッドは動かさない)。"""
    root = os.path.join(TMP, "g_" + cls.KEY + str(time.time_ns()))
    return cls(root=root, start_threads=False)


class PalworldIni(unittest.TestCase):
    SAMPLE = ('[/Script/Pal.PalGameWorldSettings]\n'
              'OptionSettings=(Difficulty=None,DayTimeSpeedRate=1.000000,ServerName="Default Palworld Server",'
              'ServerDescription="",AdminPassword="",CrossplayPlatforms=(Steam,Xbox,PS5,Mac),'
              'ServerPlayerMaxNum=32,bIsPvP=False,PublicIP="")\n')

    def test_parse_keeps_nested_and_quotes(self):
        items = palworld.parse_option_settings(self.SAMPLE)
        self.assertEqual(items["CrossplayPlatforms"], "(Steam,Xbox,PS5,Mac)")
        self.assertEqual(items["ServerName"], '"Default Palworld Server"')
        self.assertEqual(list(items)[0], "Difficulty")

    def test_apply_and_render_roundtrip(self):
        m = fresh(palworld.Palworld)
        m.settings.update(server_name="うちのサーバー", admin_password="pw1", exp_rate=2.0, pvp=True, max_players=8)
        items = palworld.apply_settings_to_ini(palworld.parse_option_settings(self.SAMPLE), m.settings)
        text = palworld.render_ini(items)
        self.assertEqual(text.count("OptionSettings="), 1)
        self.assertEqual(len(text.strip().splitlines()), 2)           # 1行にまとまっている
        back = palworld.parse_option_settings(text)
        self.assertEqual(back["ServerName"], '"うちのサーバー"')
        self.assertEqual(back["ExpRate"], "2.000000")
        self.assertEqual(back["bIsPvP"], "True")
        self.assertEqual(back["ServerPlayerMaxNum"], "8")
        self.assertEqual(back["CrossplayPlatforms"], "(Steam,Xbox,PS5,Mac)")   # 触っていない項目は残る
        self.assertEqual(back["RCONEnabled"], "True")
        self.assertEqual(back["RESTAPIEnabled"], "True")

    def test_prepare_start_uses_default_file(self):
        m = fresh(palworld.Palworld)
        m.server_dir.mkdir(parents=True)
        (m.server_dir / "DefaultPalWorldSettings.ini").write_text(self.SAMPLE, encoding="utf-8")
        m.settings["admin_password"] = "x"
        m.prepare_start()
        t = m.ini_path.read_text(encoding="utf-8")
        self.assertIn("CrossplayPlatforms=(Steam,Xbox,PS5,Mac)", t)
        self.assertIn('AdminPassword="x"', t)

    def test_command(self):
        m = fresh(palworld.Palworld)
        cmd = m.build_command()
        self.assertIn("-port=8211", cmd)
        self.assertIn("-players=16", cmd)
        self.assertNotIn("-publiclobby", cmd)
        m.settings["public_lobby"] = True
        self.assertIn("-publiclobby", m.build_command())

    def test_start_requires_admin_password(self):
        m = fresh(palworld.Palworld)
        self.assertIn("管理者パスワード", m.check_before_start())


class TerrariaTests(unittest.TestCase):
    def test_zip_names(self):
        self.assertEqual(terraria.normalize_zip_name("1458"), "terraria-server-1458.zip")
        self.assertEqual(terraria.normalize_zip_name("terraria-server-1453"), "terraria-server-1453.zip")
        self.assertEqual(terraria.normalize_zip_name(""), "")
        self.assertEqual(terraria.normalize_zip_name("../evil"), "")

    def test_latest_name(self):
        self.assertEqual(terraria.latest_zip_name(lambda u: '["terraria-server-1449.zip","terraria-server-1458.zip"]'),
                         "terraria-server-1458.zip")
        def boom(u):
            raise OSError("offline")
        self.assertEqual(terraria.latest_zip_name(boom), terraria.FALLBACK_ZIP)

    def test_config(self):
        m = fresh(terraria.Terraria)
        m.settings.update(world_name="テスト", password="pw", seed="abc")
        text = terraria.render_config(m.settings, m.worlds_dir)
        for want in ("worldname=テスト", "password=pw", "port=7777", "autocreate=2", "seed=abc", "difficulty=0",
                     "secure=1", "upnp=0"):
            self.assertIn(want, text)
        self.assertEqual([l for l in text.splitlines() if l.startswith("seed")][0], "seed=abc")

    def test_world_name_validation(self):
        m = fresh(terraria.Terraria)
        self.assertFalse(m.save_settings({"world_name": "a/b"})["ok"])
        self.assertFalse(m.save_settings({"world_name": ""})["ok"])
        self.assertTrue(m.save_settings({"world_name": "うちの世界"})["ok"])
        self.assertFalse(m.save_settings({"server_version": "latest"})["ok"])

    def test_safe_extract_blocks_zip_slip(self):
        z = os.path.join(TMP, "evil.zip")
        with zipfile.ZipFile(z, "w") as f:
            f.writestr("../outside.txt", "x")
        with self.assertRaises(RuntimeError):
            terraria.safe_extract(__import__("pathlib").Path(z), __import__("pathlib").Path(os.path.join(TMP, "ex1")))
        self.assertFalse(os.path.exists(os.path.join(TMP, "outside.txt")))

    def test_find_server_dir(self):
        from pathlib import Path
        root = Path(TMP) / "ex2" / "1458" / "Windows"
        root.mkdir(parents=True)
        (root / "TerrariaServer.exe").write_bytes(b"x")
        self.assertEqual(terraria.find_windows_server_dir(Path(TMP) / "ex2"), root)

    def test_command_has_config(self):
        m = fresh(terraria.Terraria)
        cmd = m.build_command()
        self.assertIn("-config", cmd)


class ArkTests(unittest.TestCase):
    def test_asa_command(self):
        m = fresh(ark.ArkAsa)
        m.settings.update(admin_password="adm", mods=[929578], session_name="S")
        cmd = m.build_command()
        self.assertTrue(cmd[1].startswith("TheIsland_WP?listen?SessionName=S"))
        self.assertIn("-mods=929578", cmd)
        self.assertIn("-NoBattlEye", cmd)
        self.assertIn("ServerAdminPassword=adm", cmd[1])

    def test_ase_command_and_ports(self):
        m = fresh(ark.ArkAse)
        self.assertIn("MaxPlayers=10", m.build_command()[1])
        self.assertFalse(m.save_settings({"port": 7777, "query_port": 7778})["ok"])   # 7777+1 とぶつかる
        self.assertFalse(m.save_settings({"port": 7777, "query_port": 7777})["ok"])
        self.assertTrue(m.save_settings({"port": 7780, "query_port": 27016})["ok"])

    def test_active_mods_ini(self):
        from pathlib import Path
        p = Path(TMP) / "gus.ini"
        p.write_text("[ServerSettings]\nFoo=1\nActiveMods=1,2\n[Other]\nBar=2\n", encoding="utf-8")
        ark.write_active_mods(p, [5, 6])
        t = p.read_text(encoding="utf-8")
        self.assertIn("ActiveMods=5,6", t)
        self.assertNotIn("ActiveMods=1,2", t)
        self.assertIn("Foo=1", t)

    def test_bad_chars_rejected(self):
        m = fresh(ark.ArkAsa)
        self.assertFalse(m.save_settings({"session_name": 'a"b'})["ok"])
        self.assertFalse(m.save_settings({"restart_times": ["25:00"]})["ok"])
        self.assertFalse(m.save_settings({"mods": ["abc"]})["ok"])
        self.assertTrue(m.save_settings({"restart_times": ["5:30"], "mods": ["929578"]})["ok"])
        self.assertEqual(m.settings["restart_times"], ["05:30"])

    def test_unknown_keys_ignored(self):
        m = fresh(ark.ArkAsa)
        m.save_settings({"evil": 1, "port": 7800})
        self.assertNotIn("evil", m.settings)

    def test_settings_persist(self):
        m = fresh(ark.ArkAsa)
        m.save_settings({"session_name": "永続", "admin_password": "x"})
        m2 = ark.ArkAsa(root=str(m.root), start_threads=False)
        self.assertEqual(m2.settings["session_name"], "永続")


class BackupTests(unittest.TestCase):
    def make(self, cls=ark.ArkAsa):
        m = fresh(cls)
        for sub in m.SAVE_DIRS:
            p = m.server_dir / sub
            if sub.endswith(".txt"):
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("cfg", encoding="utf-8")
            else:
                p.mkdir(parents=True)
                (p / "world.sav").write_text("DATA1", encoding="utf-8")
        return m

    def test_roundtrip(self):
        m = self.make()
        f = m.make_backup_file()
        sav = m.server_dir / m.SAVE_DIRS[0] / "world.sav"
        sav.write_text("BROKEN", encoding="utf-8")
        self.assertTrue(m.restore(f.name)["ok"])
        self.assertEqual(sav.read_text(encoding="utf-8"), "DATA1")

    def test_restore_blocks_escape(self):
        m = self.make()
        evil = m.backup_dir / "backup_evil.zip"
        with zipfile.ZipFile(evil, "w") as z:
            z.writestr("../../escaped.txt", "x")
        self.assertFalse(m.restore("backup_evil.zip")["ok"])
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(str(m.root)), "escaped.txt")))

    def test_legacy_ark_backup_layout(self):
        m = fresh(ark.ArkAsa)
        legacy = m.backup_dir / "backup_20260101_000000.zip"
        with zipfile.ZipFile(legacy, "w") as z:
            z.writestr("SavedArks/TheIsland_WP/a.ark", "OLD")
            z.writestr("Config/WindowsServer/GameUserSettings.ini", "[x]")
        self.assertTrue(m.restore(legacy.name)["ok"])
        self.assertEqual((m.server_dir / "ShooterGame/Saved/SavedArks/TheIsland_WP/a.ark").read_text(), "OLD")

    def test_terraria_backup_includes_worlds(self):
        m = self.make(terraria.Terraria)
        f = m.make_backup_file()
        with zipfile.ZipFile(f) as z:
            names = z.namelist()
        self.assertIn("worlds/world.sav", names)
        self.assertIn("serverconfig.txt", names)

    def test_keep_limit(self):
        m = self.make()
        m.settings["backup_keep"] = 2
        for i in range(4):
            (m.backup_dir / f"backup_2026010{i}_000000.zip").write_bytes(b"PK")
        m._backup_worker()
        self.assertLessEqual(len(m.list_backups()), 2)
        self.assertEqual(m.busy, "")

    def test_prune_keeps_only_newest(self):
        m = self.make()
        for i in range(4):
            (m.backup_dir / f"backup_2026010{i}_000000.zip").write_bytes(b"PK" * 10)
        r = m.prune_backups()
        self.assertTrue(r["ok"])
        self.assertEqual([b["name"] for b in m.list_backups()], ["backup_20260103_000000.zip"])
        self.assertIn("3個", r["message"])

    def test_prune_refuses_while_busy(self):
        m = self.make()
        for i in range(3):
            (m.backup_dir / f"backup_2026010{i}_000000.zip").write_bytes(b"PK")
        m.busy = "バックアップ"
        self.assertFalse(m.prune_backups()["ok"])
        self.assertEqual(len(m.list_backups()), 3)


class SysmonTests(unittest.TestCase):
    CFG = {"on": True, "disk_gb": 10, "cpu": 90, "mem": 90}

    def setUp(self):
        from oneclick import sysmon
        self.sm = sysmon
        sysmon._state.clear()

    def test_disk_warns_once_per_hour_then_recovers(self):
        ev = self.sm.evaluate
        self.assertEqual(ev({"disk_gb": 50}, self.CFG, 0), [])
        w = ev({"disk_gb": 5}, self.CFG, 100)
        self.assertEqual([x[:2] for x in w], [("disk", "warn")])
        self.assertEqual(ev({"disk_gb": 5}, self.CFG, 160), [])                 # 続けて出さない
        self.assertEqual(len(ev({"disk_gb": 5}, self.CFG, 100 + 3601)), 1)      # 1時間たったらもう一度
        r = ev({"disk_gb": 50}, self.CFG, 4000)
        self.assertEqual([x[:2] for x in r], [("disk", "recover")])
        self.assertEqual(ev({"disk_gb": 50}, self.CFG, 4060), [])               # 復旧は1回だけ

    def test_cpu_needs_five_minutes(self):
        ev = self.sm.evaluate
        self.assertEqual(ev({"cpu": 99}, self.CFG, 0), [])
        self.assertEqual(ev({"cpu": 99}, self.CFG, 200), [])
        self.assertEqual(len(ev({"cpu": 99}, self.CFG, 301)), 1)
        self.sm._state.clear()
        ev({"cpu": 99}, self.CFG, 0)
        ev({"cpu": 10}, self.CFG, 100)                                          # 途中で下がったら数え直し
        self.assertEqual(ev({"cpu": 99}, self.CFG, 301), [])

    def test_each_item_can_be_switched_off(self):
        ev = self.sm.evaluate
        off = dict(self.CFG, mem_on=False)
        v = {"disk_gb": 1, "mem": 99}
        self.assertEqual([x[0] for x in ev(v, off, 0)], ["disk"])               # メモリだけ出ない
        self.assertEqual(ev({"mem": 99}, off, 100), [])
        self.sm._state.clear()
        ev(v, self.CFG, 200)
        self.assertEqual([x[0] for x in ev(v, self.CFG, 261)], ["mem"])         # ONなら、メモリも(1分後に)出る
        self.assertEqual(ev({"mem": 10}, off, 400), [])                         # 警告中に OFF → 復旧通知も出さない
        self.assertEqual(self.sm.DEFAULTS["mem_on"], True)

    def test_real_measurements_work_here(self):
        self.assertGreater(self.sm.disk_free_gb("."), 0)
        m = self.sm.mem_percent()
        self.assertTrue(m is None or 0 <= m <= 100)


class JavaReqTests(unittest.TestCase):
    """MODが要る Java を調べる / 遊ぶ人の Java を自動で用意する / 入れない診断。"""

    @staticmethod
    def _jar(path, classes=None, fabric_java=None):
        with zipfile.ZipFile(path, "w") as z:
            for name, major in (classes or {}).items():
                z.writestr(name, b"\xca\xfe\xba\xbe\x00\x00" + major.to_bytes(2, "big") + b"\x00" * 8)
            if fabric_java:
                z.writestr("fabric.mod.json", json.dumps({"id": "x", "depends": {"java": fabric_java}}))

    def setUp(self):
        from oneclick.minecraft import javareq
        self.jr = javareq
        self.d = tempfile.mkdtemp(dir=TMP)

    def test_reads_class_version_and_ignores_multirelease(self):
        a = os.path.join(self.d, "a.jar")
        self._jar(a, {"a/A.class": 65, "META-INF/versions/21/B.class": 69})   # 65=Java21。META-INF の下は数えない
        self.assertEqual(self.jr.jar_need(a), 21)
        old = os.path.join(self.d, "old.jar")
        self._jar(old, {"o/O.class": 52})
        self.assertEqual(self.jr.jar_need(old), 8)
        self.assertIsNone(self.jr.jar_need(os.path.join(self.d, "nothing.jar")))

    def test_fabric_depends_java(self):
        f = os.path.join(self.d, "f.jar")
        self._jar(f, {"f/F.class": 61}, fabric_java=">=21")
        self.assertEqual(self.jr.jar_need(f), 21)

    def test_summary_flags_mods_newer_than_standard(self):
        self._jar(os.path.join(self.d, "new.jar"), {"n/N.class": 65})
        self._jar(os.path.join(self.d, "ok.jar"), {"o/O.class": 61})
        s = self.jr.summarize(self.d, "1.20.1")            # 1.20.1 の標準は Java 17
        self.assertEqual((s["base"], s["need"]), (17, 21))
        self.assertEqual([m["file"] for m in s["high"]], ["new.jar"])
        s2 = self.jr.summarize(self.d, "1.21.1")           # 1.21.1 の標準は Java 21 → 問題なし
        self.assertEqual(s2["high"], [])

    def test_server_java_goes_up_only_when_needed(self):
        class P:
            def __init__(self, d, mc="1.20.1", loader="fabric"):
                self.d, self.mc, self.loader = d, mc, loader
                self.mods_dir = d

            def get(self, k, default=None):
                return {"mc": self.mc, "loader": self.loader, "java_major": 17}.get(k, default)
        self._jar(os.path.join(self.d, "new.jar"), {"n/N.class": 65})
        self.assertEqual(self.jr.server_java(P(self.d)), 21)
        self.assertEqual(self.jr.server_java(P(self.d, loader="vanilla")), 17)
        empty = tempfile.mkdtemp(dir=TMP)
        self.assertEqual(self.jr.server_java(P(empty)), 17)

    def test_launcher_profile_gets_java_only_when_given(self):
        from oneclick.minecraft import client_install as ci
        mc = tempfile.mkdtemp(dir=TMP)
        from oneclick.minecraft import paths as mcp
        exe_dir = os.path.join(mcp.JAVA_DIR, "21", "jdk", "bin")
        os.makedirs(exe_dir, exist_ok=True)
        exe = os.path.join(exe_dir, "java.exe")
        open(exe, "w").close()
        ci.add_launcher_profile(mc, "k", "n", "v", mc, 4, exe)
        pj = json.load(open(os.path.join(mc, "launcher_profiles.json"), encoding="utf-8"))
        self.assertEqual(pj["profiles"]["k"]["javaDir"], exe)                      # javaw が無ければ java
        ci.add_launcher_profile(mc, "k", "n", "v", mc, 4, None)                    # 要らなくなったら、ここで入れたものだけ外す
        pj = json.load(open(os.path.join(mc, "launcher_profiles.json"), encoding="utf-8"))
        self.assertNotIn("javaDir", pj["profiles"]["k"])
        pj["profiles"]["k"]["javaDir"] = r"C:\mine\javaw.exe"                       # 自分で決めた Java は消さない
        json.dump(pj, open(os.path.join(mc, "launcher_profiles.json"), "w", encoding="utf-8"))
        ci.add_launcher_profile(mc, "k", "n", "v", mc, 4, None)
        pj = json.load(open(os.path.join(mc, "launcher_profiles.json"), encoding="utf-8"))
        self.assertEqual(pj["profiles"]["k"]["javaDir"], r"C:\mine\javaw.exe")

    def test_ensure_play_java_installs_when_missing(self):
        from oneclick.minecraft import client_install as ci, javamgr
        mc = tempfile.mkdtemp(dir=TMP)
        game = os.path.join(mc, "g")
        os.makedirs(os.path.join(game, "mods"))
        self._jar(os.path.join(game, "mods", "new.jar"), {"n/N.class": 65})
        ci.add_launcher_profile(mc, "k", "n", "v", game, 4)
        from oneclick.minecraft import paths as mcp
        fake = os.path.join(mcp.JAVA_DIR, "21", "fake", "bin")
        os.makedirs(fake, exist_ok=True)
        exe = os.path.join(fake, "java.exe")
        open(exe, "w").close()
        calls = []
        orig = javamgr.ensure_java
        javamgr.ensure_java = lambda need, log=print: (calls.append(need), exe)[1]
        try:
            r = ci.ensure_play_java(mc, "k", game, "1.20.1", lambda *_: None)
        finally:
            javamgr.ensure_java = orig
        self.assertEqual((r["status"], r["need"], calls), ("fixed", 21, [21]))
        pj = json.load(open(os.path.join(mc, "launcher_profiles.json"), encoding="utf-8"))
        self.assertEqual(pj["profiles"]["k"]["javaDir"], exe)

    def test_diagnose_finds_closed_port_and_stopped_server(self):
        from oneclick.minecraft import connectdiag

        class P:
            dir = tempfile.mkdtemp(dir=TMP)
            mods_dir = os.path.join(dir, "mods")

            def __init__(self, addr="", wl="false"):
                self.addr, self.wl = addr, wl

            def get(self, k, default=None):
                return {"public_address": self.addr, "mc": "1.20.1", "loader": "fabric"}.get(k, default)

            def props(self):
                return {"white-list": self.wl}
        stopped = connectdiag.run(P(), {"running": False, "port": 25565}, {})
        self.assertEqual(stopped["items"][0]["title"], "サーバーが動いていません")
        deps = {"ping": lambda a: {"version": "1.20.1", "online": 1, "max": 20},
                "reach_test": lambda a: {"online": False}, "global_ip": lambda: "5.6.7.8", "shared_v4": lambda: False}
        r = connectdiag.run(P("5.6.7.8"), {"running": True, "port": 25565}, deps)
        self.assertEqual(r["items"][0]["level"], "error")
        self.assertEqual(r["items"][0]["action"]["type"], "firewall")
        shared = dict(deps, shared_v4=lambda: True)
        r = connectdiag.run(P("5.6.7.8"), {"running": True, "port": 25565}, shared)
        self.assertIn("外から入れない", r["items"][0]["title"])
        moved = connectdiag.run(P("1.1.1.1"), {"running": True, "port": 25565}, deps)       # 住所が変わっていた
        self.assertTrue(any("いまのインターネット上の住所と違います" in i["title"] for i in moved["items"]))
        pl = connectdiag.run(P("abc.joinmc.link"), {"running": True, "port": 25565, "playit_alive": False}, deps)
        self.assertEqual(pl["items"][0]["title"], "playit が動いていません")
        ok = dict(deps, reach_test=lambda a: {"online": True})
        r = connectdiag.run(P("5.6.7.8", wl="true"), {"running": True, "port": 25565}, ok)
        self.assertTrue(any(i["title"].startswith("インターネットの外から") for i in r["items"]))
        self.assertTrue(any("ホワイトリスト" in i["title"] for i in r["items"]))

    def test_friendly_log_rules(self):
        from oneclick.minecraft import conflicts
        t = ("Taro (/1.2.3.4:5555) lost connection: You are not whitelisted on this server!\n"
             "Outdated client! Please use 1.20.1\n"
             "Can't keep up! Is the server overloaded? Running 5000ms or 100 ticks behind\n"
             "Could not reserve enough space for 9999MB object heap\n"
             "Mismatched mod channel list\n")
        titles = [i["title"] for i in conflicts.analyze_text(t)]
        for want in ("ホワイトリストにない人が、入ろうとしました", "友達のマイクラのバージョンが合っていません",
                     "サーバーが重くて、処理が追いついていません", "メモリの設定が、PCに合っていません",
                     "友達のMODが、サーバーと合っていません"):
            self.assertIn(want, titles)


class IpHideTests(unittest.TestCase):
    """IPアドレスは、通知に書かない・画面ではふだん隠す。"""

    def test_notifications_hide_ips(self):
        from oneclick.minecraft import watch
        self.assertEqual(watch.hide_ips("Steve（203.0.113.5）が入ろうとしました"), "Steve（(IPは非表示)）が入ろうとしました")
        self.assertEqual(watch.hide_ips("[/203.0.113.5:54321] lost connection"), "[/(IPは非表示)] lost connection")
        self.assertIn("127.0.0.1", watch.hide_ips("from 127.0.0.1"))                 # 自分のPCはそのまま
        self.assertEqual(watch.hide_ips("Fabric 0.16.9 と 1.21.1"), "Fabric 0.16.9 と 1.21.1")   # バージョンは消さない
        self.assertEqual(watch.hide_ips(""), "")

    def test_notify_strips_ips_before_sending(self):
        # 通知の入口(_notify)で hide_ips を通していること / 侵入の通知文に IP を書いていないこと
        from oneclick.minecraft import api
        src = open(api.__file__, encoding="utf-8").read()
        self.assertIn("watch.hide_ips(title), watch.hide_ips(text)", src)
        self.assertNotIn('_notify(id, "intrusion", "🚨 知らない人が入ろうとしました", msg', src)
        self.assertNotIn('_notify(id, "intrusion", "🚨 あやしいアクセス", msg', src)

    def test_ui_helpers_in_node(self):
        import re
        import subprocess
        if not shutil.which("node"):
            self.skipTest("node がありません")
        js = open(os.path.join(os.path.dirname(shell.__file__), "minecraft", "web", "app.js"), encoding="utf-8").read()
        a = js.index("const IP_MASK")
        b = js.index("const ipTimers")
        block = js[a:b]
        script = block + """
const esc = (s) => String(s ?? "");
const cpBtn = () => "";
const out = {
  masked: maskIps("Steve[/203.0.113.5:54321] logged in"),
  keep127: maskIps("from 127.0.0.1"),
  ver: maskIps("Fabric 0.16.9 / 1.21.1 / mod-1.2.3.4"),
  p1: isPublicIpLiteral("203.0.113.5:25565"), p2: isPublicIpLiteral("192.168.1.5"), p3: isPublicIpLiteral("xxxx.joinmc.link"),
  p4: isPublicIpLiteral("2400:1234::1"), p5: isPublicIpLiteral("[2400:1234::1]:25565"), p6: isPublicIpLiteral("10.0.0.2"), p7: isPublicIpLiteral("172.20.0.1"),
  html: ipSecret("203.0.113.5"),
};
console.log(JSON.stringify(out));
"""
        r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        o = json.loads(r.stdout)
        self.assertEqual(o["masked"], "Steve[/" + "●●●.●●●.●●●.●●●" + ":54321] logged in")
        self.assertEqual(o["keep127"], "from 127.0.0.1")
        self.assertIn("0.16.9", o["ver"]); self.assertIn("1.21.1", o["ver"]); self.assertIn("mod-1.2.3.4", o["ver"])
        self.assertTrue(o["p1"] and o["p4"] and o["p5"])
        self.assertFalse(o["p2"] or o["p3"] or o["p6"] or o["p7"])
        self.assertIn("●●●.●●●.●●●.●●●", o["html"])
        self.assertIn("data-ipshow", o["html"])
        self.assertNotRegex(re.sub(r'data-ipv="[^"]*"', "", o["html"]), r"203\.0\.113\.5.*●")   # 見える所には本物のIPがない
        # 表示用の文字(ボタンの外)に、本物の IP が出ていないこと
        visible = re.sub(r"<[^>]*>", "", o["html"])
        self.assertNotIn("203.0.113.5", visible)


class PresenceTests(unittest.TestCase):
    def test_card_lists_names_and_count(self):
        from oneclick import discordhub
        e = discordhub.presence_card("mc:a", {"はな", "たろう"}, [("21:03", "たろう", "in")])
        self.assertIn("いま 2 人", e["description"])
        self.assertIn("たろう が入りました", e["fields"][0]["value"])
        e0 = discordhub.presence_card("mc:a", set(), [])
        self.assertIn("いま 0 人", e0["description"])


class TrayTests(unittest.TestCase):
    def test_not_available_off_windows(self):
        from oneclick import tray
        if not sys.platform.startswith("win"):
            self.assertFalse(tray.available())

    def test_app_settings_roundtrip(self):
        from oneclick import shell as shell_mod
        sh = shell_mod.Shell()
        self.assertTrue(sh.get_app_settings()["resident"])         # 初期値は ON
        self.assertFalse(sh.set_app_settings(resident=False)["resident"])
        self.assertFalse(shell_mod.Shell().get_app_settings()["resident"])   # 保存されている


class SchedulerTests(unittest.TestCase):
    def test_crash_loop_stops(self):
        m = fresh(ark.ArkAsa)
        calls = []
        m.start = lambda auto=False: calls.append(auto) or {"message": "再起動した"}
        time.sleep_orig = time.sleep
        orig = time.sleep
        time.sleep = lambda s: None
        try:
            for i in range(4):
                m.pid = 99999999          # 動いていないはずのPID
                m.user_stopped = False
                m.scheduler_tick()
        finally:
            time.sleep = orig
        self.assertEqual(len(calls), 2)       # 3回目で「くり返さない」と止まる
        self.assertTrue(m.user_stopped)

    def test_no_restart_when_user_stopped(self):
        m = fresh(ark.ArkAsa)
        m.start = lambda auto=False: self.fail("勝手に起動した")
        m.pid = 99999999
        m.user_stopped = True
        m.scheduler_tick()


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.old = os.path.join(TMP, "old_" + str(time.time_ns()))
        os.makedirs(self.old)
        shutil.rmtree(paths.DATA_DIR, ignore_errors=True)

    def make_mc(self):
        d = os.path.join(self.old, "data", "servers", "survival")
        os.makedirs(os.path.join(d, "world"))
        open(os.path.join(d, "profile.json"), "w").write("{}")
        open(os.path.join(d, "world", "level.dat"), "w").write("LEVEL")
        open(os.path.join(self.old, "data", "settings.json"), "w").write('{"theme": "dark"}')

    def make_ark(self):
        data = os.path.join(self.old, "data")
        os.makedirs(os.path.join(data, "server", "ShooterGame", "Saved", "SavedArks"))
        open(os.path.join(data, "server", "ShooterGame", "Saved", "SavedArks", "a.ark"), "w").write("ARK")
        os.makedirs(os.path.join(data, "backups"))
        open(os.path.join(data, "backups", "backup_1.zip"), "wb").write(b"PK")
        os.makedirs(os.path.join(data, "steamcmd"))
        json.dump({"game": "asa", "map": "Aberration_WP", "port": 7790, "query_port": 27016, "rcon_port": 27021,
                   "mods": [929578], "session_name": "旧名", "admin_password": "adm", "max_players": 12,
                   "profiles": {"ase": {"map": "Ragnarok", "port": 7800, "query_port": 27030, "rcon_port": 32331, "mods": [1]}},
                   "restart_times": ["04:00"], "auto_restart": True},
                  open(os.path.join(data, "settings.json"), "w"))

    def test_classify(self):
        self.make_mc()
        self.assertEqual(migrate.classify(self.old), "mc")
        self.assertIsNone(migrate.classify(os.path.join(TMP)))

    def test_mc_import_and_undo(self):
        self.make_mc()
        r = migrate.run(self.old)
        dst = os.path.join(paths.game_dir("minecraft"), "servers", "survival", "world", "level.dat")
        self.assertEqual(open(dst).read(), "LEVEL")
        self.assertFalse(os.path.exists(os.path.join(self.old, "data", "servers", "survival")))   # 同じドライブなので移動
        self.assertTrue(os.path.exists(r["backup"]))
        migrate.undo_last()
        self.assertTrue(os.path.exists(os.path.join(self.old, "data", "servers", "survival", "world", "level.dat")))
        self.assertFalse(os.path.exists(dst))

    def test_ark_import_converts_settings(self):
        self.make_ark()
        self.assertEqual(migrate.classify(self.old), "ark")
        migrate.run(self.old)
        asa = json.load(open(os.path.join(paths.game_dir("ark_asa"), "settings.json"), encoding="utf-8"))
        ase = json.load(open(os.path.join(paths.game_dir("ark_ase"), "settings.json"), encoding="utf-8"))
        self.assertEqual(asa["map"], "Aberration_WP")
        self.assertEqual(asa["port"], 7790)
        self.assertEqual(asa["session_name"], "旧名")
        self.assertEqual(ase["map"], "Ragnarok")            # 切り替え前に使っていた ASE の設定
        self.assertEqual(ase["port"], 7800)
        self.assertEqual(ase["session_name"], "旧名")        # 名前やパスワードは共通
        self.assertTrue(os.path.exists(os.path.join(paths.game_dir("ark_asa"), "server", "ShooterGame", "Saved", "SavedArks", "a.ark")))
        self.assertTrue(os.path.exists(os.path.join(paths.game_dir("ark_asa"), "backups", "backup_1.zip")))
        self.assertTrue(os.path.isdir(os.path.join(paths.SHARED_DIR, "steamcmd")))
        # 引き継いだ設定が、そのまま管理係に読み込まれる
        m = ark.ArkAsa(root=paths.game_dir("ark_asa"), start_threads=False)
        self.assertEqual(m.settings["mods"], [929578])
        self.assertEqual(m.settings["restart_times"], ["04:00"])
        self.assertTrue(registry.steam_exists("ark_asa"))

    def test_existing_not_overwritten(self):
        self.make_mc()
        os.makedirs(os.path.join(paths.game_dir("minecraft"), "servers", "survival"))
        open(os.path.join(paths.game_dir("minecraft"), "servers", "survival", "mine.txt"), "w").write("NEW")
        migrate.run(self.old)
        self.assertTrue(os.path.exists(os.path.join(paths.game_dir("minecraft"), "servers", "survival", "mine.txt")))
        self.assertTrue(os.path.exists(os.path.join(paths.game_dir("minecraft"), "servers", "survival_imported2", "world", "level.dat")))

    def test_rejects_non_old_folder(self):
        with self.assertRaises(migrate.MigrateError):
            migrate.run(self.old)

    def test_scan_finds_extra_path(self):
        self.make_ark()
        found = migrate.scan(extra_paths=[self.old])
        self.assertTrue(any(f["path"] == self.old and f["kind"] == "ark" for f in found))


class UpdateTests(unittest.TestCase):
    def fake(self, newer=True, with_hash=True):
        import hashlib
        from oneclick import net
        tag = "v9.0.0" if newer else "v0.0.1"
        digest = "sha256:" + hashlib.sha256(b"zip").hexdigest() if with_hash else ""
        rel = {"tag_name": tag, "body": "新機能", "html_url": "https://github.com/x/y/releases/1",
               "assets": [{"name": "OneClickServer_win.zip", "size": 3, "digest": digest,
                           "browser_download_url": selfupdate.DOWNLOAD_PREFIX + tag + "/OneClickServer_win.zip"}]}
        net.set_fake_handler(lambda url, *a, **k: (json.dumps(rel).encode(), 200) if False else json.dumps(rel).encode())

    def tearDown(self):
        from oneclick import net
        net.set_fake_handler(None)

    def test_required_only_when_safe_and_frozen(self):
        self.fake()
        info = selfupdate.check()
        self.assertTrue(info["newer"])
        self.assertTrue(info["safe"])
        # Python で動かしているテスト中は、自動では入れられない = 強制しない
        self.assertFalse(info["can_apply"])
        self.assertIsNone(selfupdate.required())

    def test_required_when_frozen(self):
        self.fake()
        selfupdate.check()
        selfupdate._cache["info"]["can_apply"] = True
        req = selfupdate.required()
        self.assertEqual(req["latest"], "9.0.0")

    def test_not_newer(self):
        self.fake(newer=False)
        selfupdate.check()
        selfupdate._cache["info"]["can_apply"] = True
        self.assertIsNone(selfupdate.required())

    def test_no_hash_means_not_forced(self):
        self.fake(with_hash=False)
        info = selfupdate.check()
        self.assertFalse(info["safe"])
        selfupdate._cache["info"]["can_apply"] = True
        self.assertIsNone(selfupdate.required())

    def test_script_keeps_data(self):
        s = selfupdate._script()
        self.assertIn("/XD", s)
        self.assertIn("rollback", s)
        self.assertIn("%EXE%", s)

    def test_script_is_ascii_and_has_no_pipes(self):
        """日本語が入ると cmd が固まることがある。待つ処理に find・tasklist のパイプも使わない。"""
        s = selfupdate.SCRIPT
        s.encode("ascii")                       # 日本語・全角が入っていないこと
        self.assertNotIn("find ", s)
        self.assertNotIn("tasklist", s)
        self.assertNotIn("chcp", s)
        # ラベルは、gotoで飛ぶ先が全部ある
        import re
        labels = set(re.findall(r"^:(\w+)", s, re.M))
        for g in re.findall(r"goto (\w+)", s):
            self.assertIn(g, labels, g)

    def test_env_carries_paths_with_japanese(self):
        env = selfupdate._env("C:\\ユーザー\\app", "C:\\new", "C:\\ユーザー\\app\\data")
        self.assertEqual(env["APP"], "C:\\ユーザー\\app")
        self.assertEqual(env["EXE"], "OneClickServer.exe")
        self.assertIn("DATA", env)


class PreflightAndStatus(unittest.TestCase):
    def test_install_blocked_when_disk_low(self):
        from oneclick.steamgames import extras
        m = fresh(ark.ArkAsa)
        orig = extras.disk_free_gb
        extras.disk_free_gb = lambda p: 3.0
        try:
            msg = m.preflight_install()
            self.assertIn("空きが足りません", msg)
            self.assertIn("30GB", msg)
            extras.disk_free_gb = lambda p: 500.0
            self.assertEqual(m.preflight_install(), "")
            extras.disk_free_gb = lambda p: None        # 調べられないときは止めない
            self.assertEqual(m.preflight_install(), "")
        finally:
            extras.disk_free_gb = orig

    def test_install_action_returns_message(self):
        from oneclick.steamgames import extras, manager as mg
        m = fresh(ark.ArkAsa)
        orig, w = extras.disk_free_gb, mg.IS_WIN
        extras.disk_free_gb = lambda p: 1.0
        mg.IS_WIN = True
        try:
            r = m.install_or_update()
            self.assertFalse(r["ok"])
            self.assertIn("空き", r["message"])
            self.assertEqual(m.busy, "")                 # 作業中のまま残らない
        finally:
            extras.disk_free_gb, mg.IS_WIN = orig, w

    def test_start_memory_checks(self):
        from oneclick.steamgames import extras
        m = fresh(ark.ArkAsa)
        orig = extras.memory_gb
        try:
            extras.memory_gb = lambda: (16.0, 1.0)
            block, warn = m.preflight_start()
            self.assertIn("止まる", block)
            extras.memory_gb = lambda: (8.0, 6.0)         # 全体が足りない = 警告だけ
            block, warn = m.preflight_start()
            self.assertEqual(block, "")
            self.assertIn("16GB", warn)
            extras.memory_gb = lambda: (32.0, 28.0)
            self.assertEqual(m.preflight_start(), ("", ""))
            extras.memory_gb = lambda: (None, None)
            self.assertEqual(m.preflight_start(), ("", ""))
        finally:
            extras.memory_gb = orig

    def test_parse_buildid_and_manifest(self):
        from oneclick.steamgames import extras
        text = ('"2394010" { "depots" { "branches" { "public" { "buildid" "100200" "timeupdated" "1" } '
                '"beta" { "buildid" "999" } } } }')
        self.assertEqual(extras.parse_buildid(text), "100200")
        self.assertIsNone(extras.parse_buildid("nothing"))
        m = fresh(palworld.Palworld)
        (m.server_dir / "steamapps").mkdir(parents=True)
        (m.server_dir / "steamapps" / "appmanifest_2394010.acf").write_text(
            '"AppState"\n{\n\t"appid"\t\t"2394010"\n\t"buildid"\t\t"100100"\n}\n', encoding="utf-8")
        self.assertEqual(extras.installed_buildid(m.server_dir, "2394010"), "100100")

    def test_game_update_detected(self):
        from oneclick.steamgames import extras, manager as mg
        m = fresh(palworld.Palworld)
        (m.server_dir / "steamapps").mkdir(parents=True)
        (m.server_dir / "steamapps" / "appmanifest_2394010.acf").write_text('"buildid"\t"1"', encoding="utf-8")
        m.server_exe().parent.mkdir(parents=True, exist_ok=True)
        m.server_exe().write_bytes(b"x")
        w, f = mg.IS_WIN, extras.steam_latest_build
        mg.IS_WIN = True
        try:
            extras.steam_latest_build = lambda exe, appid, timeout=120: "2"
            self.assertEqual(m.check_game_update(), {"installed": "1", "latest": "2"})
            extras.steam_latest_build = lambda exe, appid, timeout=120: "1"
            self.assertIsNone(m.check_game_update())
            extras.steam_latest_build = lambda exe, appid, timeout=120: None      # 調べられない
            self.assertIsNone(m.check_game_update())
        finally:
            mg.IS_WIN, extras.steam_latest_build = w, f

    def test_run_update_check_fills_state(self):
        m = fresh(palworld.Palworld)
        m.check_game_update = lambda: {"installed": "1", "latest": "2"}
        m.run_update_check()
        st = m.state()
        self.assertTrue(st["upd"]["game"])
        self.assertFalse(st["upd"]["checking"])
        self.assertTrue(registry.steam_cards() is not None)

    def test_update_check_error_is_contained(self):
        m = fresh(palworld.Palworld)
        def boom():
            raise RuntimeError("offline")
        m.check_game_update = boom
        m.run_update_check()
        self.assertIn("失敗", m.upd["error"])

    def test_ase_mod_update_flow(self):
        from oneclick.steamgames import extras
        m = fresh(ark.ArkAse)
        m.settings["mods"] = [111, 222]
        orig = extras.workshop_details
        try:
            extras.workshop_details = lambda ids: {"111": {"title": "A", "updated": 1000},
                                                   "222": {"title": "B", "updated": 2000}}
            self.assertEqual(m.check_mod_updates(), [])              # 初回は基準を覚えるだけ
            self.assertEqual(m.check_mod_updates(), [])
            extras.workshop_details = lambda ids: {"111": {"title": "A", "updated": 5000},
                                                   "222": {"title": "B", "updated": 2000}}
            r = m.check_mod_updates()
            self.assertEqual([x["id"] for x in r], ["111"])
            self.assertEqual(r[0]["title"], "A")
            m._after_start()                                          # 起動すると「見た」ことになる
            self.assertEqual(m.check_mod_updates(), [])
            m.settings["mods"] = []
            self.assertEqual(m.check_mod_updates(), [])
        finally:
            extras.workshop_details = orig

    def test_workshop_parse(self):
        from oneclick import net
        from oneclick.steamgames import extras
        body = json.dumps({"response": {"publishedfiledetails": [
            {"publishedfileid": "1", "result": 1, "title": "T", "time_updated": 77},
            {"publishedfileid": "2", "result": 9}]}}).encode()
        net.set_fake_handler(lambda url, *a, **k: body)
        try:
            self.assertEqual(extras.workshop_details([1, 2]), {"1": {"title": "T", "updated": 77}})
            self.assertEqual(extras.workshop_details([]), {})
        finally:
            net.set_fake_handler(None)

    def test_asa_has_no_mod_detection(self):
        m = fresh(ark.ArkAsa)
        self.assertEqual(m.check_mod_updates(), [])
        self.assertIn("自動で最新", m.info()["mod_update_note"])

    def test_terraria_update(self):
        m = fresh(terraria.Terraria)
        (m.server_dir).mkdir(parents=True)
        m.server_exe().parent.mkdir(parents=True, exist_ok=True)
        m.server_exe().write_bytes(b"x")
        (m.root / "installed_zip.txt").write_text("terraria-server-1449.zip", encoding="utf-8")
        orig = terraria.latest_zip_name
        try:
            terraria.latest_zip_name = lambda *a, **k: "terraria-server-1458.zip"
            self.assertEqual(m.check_game_update()["latest"], "terraria-server-1458.zip")
            terraria.latest_zip_name = lambda *a, **k: "terraria-server-1449.zip"
            self.assertIsNone(m.check_game_update())
            m.settings["server_version"] = "1449"                      # 版を固定しているなら通知しない
            terraria.latest_zip_name = lambda *a, **k: "terraria-server-1458.zip"
            self.assertIsNone(m.check_game_update())
        finally:
            terraria.latest_zip_name = orig

    def test_status_detail_cached_and_players(self):
        m = fresh(ark.ArkAsa)
        self.assertFalse(m.status_detail()["running"])
        m._st = None
        m.running = lambda: True
        m.started_at = time.time() - 120
        m.player_count = lambda: 3
        d = m.status_detail()
        self.assertEqual(d["players"], 3)
        self.assertGreaterEqual(d["uptime"], 119)
        m.player_count = lambda: 99
        self.assertEqual(m.status_detail()["players"], 3)                # 8秒は記憶を使う
        self.assertEqual(m.cached_players(), 3)

    def test_player_count_failure_is_unknown(self):
        m = fresh(ark.ArkAsa)
        m.running = lambda: True
        def boom():
            raise OSError("no rcon")
        m.player_count = boom
        self.assertIsNone(m.status_detail()["players"])

    def test_count_listplayers(self):
        from oneclick.steamgames import extras
        self.assertEqual(extras.count_listplayers("0. Bob, 7656\n1. Alice, 7657"), 2)
        self.assertEqual(extras.count_listplayers("No Players Connected"), 0)

    def test_new_actions_exposed(self):
        self.assertIn("status_detail", shell.GAME_ACTIONS)
        self.assertIn("check_updates", shell.GAME_ACTIONS)
        for k in shell.GAME_ACTIONS.values():
            for cls in steamgames.CLASSES:
                self.assertTrue(hasattr(cls, k), f"{cls.KEY}.{k}")


class ShellHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sh = shell.Shell()
        cls.sh.mc_port = 0
        cls.sh.start(0)
        cls.base = f"http://127.0.0.1:{cls.sh.port}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        cls.opener = opener

    def get(self, path, headers=None):
        req = urllib.request.Request(self.base + path, headers=headers or {})
        return self.opener.open(req, timeout=5)

    def post(self, path, body=None, token=True, headers=None):
        h = {"Content-Type": "application/json"}
        if token:
            h["X-Token"] = self.sh.token
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=json.dumps(body or {}).encode(), method="POST", headers=h)
        return self.opener.open(req, timeout=5)

    def test_home_page_has_token(self):
        t = self.get("/").read().decode()
        self.assertIn(self.sh.token, t)
        self.assertNotIn("__TOKEN__", t)

    def test_static_and_traversal(self):
        self.assertEqual(self.get("/static/common.css").status, 200)
        with self.assertRaises(urllib.error.HTTPError) as c:
            self.get("/static/../shell.py")
        self.assertEqual(c.exception.code, 404)

    def test_post_needs_token(self):
        with self.assertRaises(urllib.error.HTTPError) as c:
            self.post("/api/get_home", token=False)
        self.assertEqual(c.exception.code, 403)

    def test_foreign_origin_blocked(self):
        with self.assertRaises(urllib.error.HTTPError) as c:
            self.post("/api/get_home", headers={"Origin": "http://evil.example"})
        self.assertEqual(c.exception.code, 403)

    def test_bad_host_blocked(self):
        with self.assertRaises(urllib.error.HTTPError) as c:
            self.get("/", headers={"Host": "evil.example"})
        self.assertEqual(c.exception.code, 403)

    def test_unknown_home_action(self):
        with self.assertRaises(urllib.error.HTTPError) as c:
            self.post("/api/shutdown")
        self.assertEqual(c.exception.code, 404)

    def test_create_and_use_game(self):
        shutil.rmtree(os.path.join(paths.DATA_DIR, "palworld"), ignore_errors=True)
        steamgames._instances.pop("palworld", None)
        r = json.load(self.post("/api/create_game", {"key": "palworld"}))
        self.assertEqual(r["url"], "/g/palworld/")
        self.assertIn(self.sh.token, self.get("/g/palworld/").read().decode())
        home = json.load(self.post("/api/get_home"))
        self.assertTrue(any(c["game"] == "palworld" for c in home["cards"]))
        allv = json.load(self.post("/g/palworld/api/get_all", {"args": []}))
        self.assertEqual(allv["info"]["key"], "palworld")
        self.assertTrue(any(f["key"] == "exp_rate" for f in allv["info"]["fields"]))
        saved = json.load(self.post("/g/palworld/api/save_settings", {"args": [{"exp_rate": 3, "admin_password": "p"}]}))
        self.assertTrue(saved["ok"])
        bad = json.load(self.post("/g/palworld/api/save_settings", {"args": [{"exp_rate": 999}]}))
        self.assertFalse(bad["ok"])
        # 許可していない機能は呼べない
        with self.assertRaises(urllib.error.HTTPError) as c:
            self.post("/g/palworld/api/__init__", {"args": []})
        self.assertEqual(c.exception.code, 404)
        with self.assertRaises(urllib.error.HTTPError) as c:
            self.post("/g/nonexistent/api/get_all", {"args": []})
        self.assertEqual(c.exception.code, 404)

    def test_unknown_game_page(self):
        with self.assertRaises(urllib.error.HTTPError) as c:
            self.get("/g/nope/")
        self.assertEqual(c.exception.code, 404)

    def test_minecraft_redirect(self):
        with self.assertRaises(urllib.error.HTTPError) as c:
            self.get("/go/minecraft")          # マイクラ部分が起動していないとき
        self.assertEqual(c.exception.code, 503)

    def test_theme(self):
        self.post("/api/set_theme", {"theme": "dark"})
        self.assertEqual(json.load(self.post("/api/get_theme"))["theme"], "dark")
        with self.assertRaises(urllib.error.HTTPError) as c:
            self.post("/api/set_theme", {"theme": "<script>"})
        self.assertEqual(c.exception.code, 400)


class DiagnoseAndAdmin(unittest.TestCase):
    def test_admin_command_whitelist(self):
        m = fresh(ark.ArkAsa)
        self.assertFalse(m.admin_command("rm -rf /")["ok"])
        self.assertFalse(m.admin_command("ServerChat hi")["ok"])      # 動いていない

    def test_diagnose_runs(self):
        for cls in steamgames.CLASSES:
            r = fresh(cls).diagnose()
            self.assertIn("items", r)
            self.assertTrue(r["items"])

    def test_info_has_fields_for_each_game(self):
        for cls in steamgames.CLASSES:
            info = fresh(cls).info()
            self.assertTrue(info["fields"])
            keys = [f["key"] for f in info["fields"]]
            defaults = cls.all_defaults()
            for k in keys:
                self.assertIn(k, defaults, f"{cls.KEY}: {k} の初期値がありません")
            for f in info["fields"]:
                self.assertTrue(f.get("help"), f"{cls.KEY}.{f['key']} に説明がありません")


class DiscordHubTests(unittest.TestCase):
    """Discord のボット(4ゲーム共通)。本物の Discord には つながず、中の判断だけを調べる。"""

    def setUp(self):
        from oneclick import discordbot, discordhub
        self.hub, self.bot = discordhub, discordbot
        self.posts = []
        self._orig = (discordhub.post, discordhub._bot, discordhub.restart, discordhub._save_cfg, discordhub.cfg)
        discordhub.post = lambda sid, title, desc="", kind="info", fields=None: self.posts.append((sid, title, kind))
        self.store = {}
        discordhub.cfg = lambda: self.store
        discordhub._save_cfg = lambda c: self.store.update(c) if c.get("panels") or True else None
        discordhub.restart = lambda: None

    def tearDown(self):
        h = self.hub
        h.post, h._bot, h.restart, h._save_cfg, h.cfg = self._orig
        h._last.clear()

    def test_steam_events_become_cards(self):
        self.hub._bot = object()
        self.hub.steam_event("ark_asa", "start", "サーバーを起動しました。")
        self.hub.steam_event("ark_asa", "start", "テスト通知です。このメッセージが見えれば成功です。")
        self.hub.steam_event("ark_asa", "stop", "x")
        self.hub.steam_event("ark_asa", "crash", "落ちました")
        self.assertEqual([(p[1], p[2]) for p in self.posts],
                         [("⏳ 起動しています", "wait"), ("⚪ 止まりました", "stop"), ("🔴 サーバーが落ちました", "bad")])

    def test_no_card_without_bot(self):
        self.hub._bot = None
        self.hub.steam_event("palworld", "start", "x")
        self.assertEqual(self.posts, [])

    def test_bot_set_checks_input(self):
        with self.assertRaises(self.hub.HubError):
            self.hub.bot_set(token="短い")
        with self.assertRaises(self.hub.HubError):
            self.hub.bot_set(channel="abc")
        with self.assertRaises(self.hub.HubError):
            self.hub.bot_set(users="!!!")

    def test_steam_start_stop_text(self):
        class Fake:
            NAME = "テスト"
            def __init__(self): self.on = False
            def start(self, auto=False):
                self.on = True
                return {"ok": True, "message": "起動しました。"}
            def stop(self):
                return {"ok": self.on, "message": "動いていません。"}
            def playit_running(self): return True
            def running(self): return self.on
            def display_name(self): return "テスト"
        f = Fake()
        orig = steamgames.get
        steamgames.get = lambda key: f
        try:
            self.assertIn("起動を始めました", self.hub._steam_start("terraria"))
            self.assertIn("playit は、もう動いています", self.hub._steam_start("terraria"))
            self.assertIn("停止を始めました", self.hub._steam_stop("terraria"))
            f.on = False
            self.assertIn("動いていません", self.hub._steam_stop("terraria"))
        finally:
            steamgames.get = orig

    def test_card_and_invite(self):
        c = self.bot.card("題", "本文", "ok", [("a", "b", True)], footer="f")
        self.assertEqual(c["color"], self.bot.COLORS["ok"])
        self.assertEqual(c["fields"][0]["name"], "a")
        self.assertEqual(self.bot.server_key("mc:x"), self.bot.server_key("mc:x"))
        self.assertNotEqual(self.bot.server_key("mc:x"), self.bot.server_key("ark_asa"))


if __name__ == "__main__":
    unittest.main()
