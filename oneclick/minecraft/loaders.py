"""マイクラ本体とMODローダー（Fabric / NeoForge / Forge）の取得・サーバー用インストール。"""
from __future__ import annotations

import glob
import os
import re
import subprocess

from . import net
from .javamgr import NO_WINDOW
from .paths import IS_WINDOWS

MOJANG_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
FABRIC_META = "https://meta.fabricmc.net/v2"
NEOFORGE_VERSIONS = "https://maven.neoforged.net/api/maven/versions/releases/net/neoforged/neoforge"
NEOFORGE_MAVEN = "https://maven.neoforged.net/releases/net/neoforged/neoforge"
FORGE_PROMOS = "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
FORGE_MAVEN = "https://maven.minecraftforge.net/net/minecraftforge/forge"
PAPER_API = "https://fill.papermc.io/v3/projects/paper"
PURPUR_API = "https://api.purpurmc.org/v2/purpur"

LOADERS = ["fabric", "neoforge", "forge", "paper", "purpur", "vanilla"]
LOADER_LABEL = {"fabric": "Fabric", "neoforge": "NeoForge", "forge": "Forge", "paper": "Paper",
                "purpur": "Purpur", "vanilla": "バニラ（MODなし）"}
MOD_LOADERS = ("fabric", "neoforge", "forge")
PLUGIN_LOADERS = ("paper", "purpur")


def content_folder(loader: str | None) -> str:
    """MOD を入れるフォルダ（Paper / Purpur は plugins）。"""
    return "plugins" if loader in PLUGIN_LOADERS else "mods"


def uses_plugins(loader: str | None) -> bool:
    return loader in PLUGIN_LOADERS

_cache: dict = {}


def _cached(key, func):
    if key not in _cache:
        _cache[key] = func()
    return _cache[key]


# ---------------- マイクラ本体 ----------------

def release_versions() -> list[str]:
    """正式版のバージョン一覧（新しい順）。"""
    man = _cached("manifest", lambda: net.get_json(MOJANG_MANIFEST))
    return [v["id"] for v in man["versions"] if v["type"] == "release"]


def version_info(mc: str) -> dict:
    man = _cached("manifest", lambda: net.get_json(MOJANG_MANIFEST))
    for v in man["versions"]:
        if v["id"] == mc:
            return _cached("vj:" + mc, lambda url=v["url"]: net.get_json(url))
    raise net.NetError(f"マイクラ {mc} が見つかりません")


def required_java(mc: str) -> int:
    """そのマイクラに必要な Java のバージョン。"""
    try:
        return int(version_info(mc).get("javaVersion", {}).get("majorVersion", 8))
    except net.NetError:
        return guess_java(mc)


def guess_java(mc: str) -> int:
    """ネットにつながらない時の予想。"""
    m = re.match(r"1\.(\d+)(?:\.(\d+))?", mc)
    if not m:
        return 21  # 26.x など新しい形式
    minor, patch = int(m.group(1)), int(m.group(2) or 0)
    if minor >= 21 or (minor == 20 and patch >= 5):
        return 21
    if minor >= 18:
        return 17
    if minor == 17:
        return 16
    return 8


# ---------------- 各ローダーの対応状況 ----------------

def fabric_loader_for(mc: str) -> str | None:
    try:
        lst = _cached("fab:" + mc, lambda: net.get_json(f"{FABRIC_META}/versions/loader/{mc}"))
    except net.NetError:
        return None
    stable = [x["loader"]["version"] for x in lst if x["loader"].get("stable")]
    if stable:
        return stable[0]
    return lst[0]["loader"]["version"] if lst else None


def neoforge_prefix(mc: str) -> str | None:
    m = re.match(r"^1\.(\d+)(?:\.(\d+))?$", mc)
    if m:
        minor, patch = int(m.group(1)), int(m.group(2) or 0)
        if minor < 20 or (minor == 20 and patch < 2):
            return None  # 1.20.1 以前は NeoForge 非対応（Forge を使う）
        return f"{minor}.{patch}."
    if re.match(r"^\d{2}\.\d+(\.\d+)?$", mc):  # 26.1 など新しい番号の付け方
        parts = mc.split(".")
        if len(parts) == 2:
            parts.append("0")
        return ".".join(parts) + "."
    return None


def neoforge_for(mc: str) -> str | None:
    prefix = neoforge_prefix(mc)
    if not prefix:
        return None
    try:
        data = _cached("neo", lambda: net.get_json(NEOFORGE_VERSIONS))
    except net.NetError:
        return None
    vers = [v for v in data.get("versions", []) if v.startswith(prefix)]
    if not vers:
        return None
    stable = [v for v in vers if "beta" not in v and "alpha" not in v]
    pick = stable or vers
    return sorted(pick, key=_vkey)[-1]


def forge_for(mc: str) -> str | None:
    try:
        promos = _cached("forge", lambda: net.get_json(FORGE_PROMOS)).get("promos", {})
    except net.NetError:
        return None
    return promos.get(f"{mc}-recommended") or promos.get(f"{mc}-latest")


def paper_build(mc: str) -> dict | None:
    """Paper の一番新しい安定版（なければ None）。"""
    try:
        builds = _cached("paper:" + mc, lambda: net.get_json(f"{PAPER_API}/versions/{mc}/builds"))
    except net.NetError:
        return None
    if isinstance(builds, dict):
        builds = builds.get("builds", [])
    stable = [b for b in builds if str(b.get("channel", "")).upper() == "STABLE"]
    pick = stable or []
    if not pick:
        return None
    b = max(pick, key=lambda x: int(x.get("id") or x.get("number") or 0))
    d = (b.get("downloads") or {}).get("server:default") or {}
    if not d.get("url"):
        return None
    return {"build": str(b.get("id") or b.get("number")), "url": d["url"],
            "sha256": (d.get("checksums") or {}).get("sha256"), "name": d.get("name")}


def purpur_build(mc: str) -> str | None:
    try:
        data = _cached("purpur:" + mc, lambda: net.get_json(f"{PURPUR_API}/{mc}"))
    except net.NetError:
        return None
    latest = (data.get("builds") or {}).get("latest")
    return str(latest) if latest else None


def loader_version(loader: str, mc: str) -> str | None:
    if loader == "paper":
        b = paper_build(mc)
        return b["build"] if b else None
    return {"fabric": fabric_loader_for, "neoforge": neoforge_for, "forge": forge_for,
            "purpur": purpur_build}.get(loader, lambda _mc: "-")(mc)


def supported_loaders(mc: str) -> list[str]:
    return [l for l in LOADERS if loader_version(l, mc)]


def _vkey(v: str):
    return [int(x) if x.isdigit() else -1 for x in re.split(r"[.\-+]", v)]


# ---------------- サーバーのインストール ----------------

def _run_installer(java: str, jar: str, args: list[str], cwd: str, log):
    log("インストーラーを実行中…（数分かかることがあります）")
    p = subprocess.Popen([java, "-jar", jar] + args, cwd=cwd, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                         creationflags=NO_WINDOW)
    tail = []
    for line in p.stdout:
        line = line.rstrip()
        tail = (tail + [line])[-15:]
    if p.wait() != 0:
        raise RuntimeError("インストールに失敗しました:\n" + "\n".join(tail))


def install_server(server_dir: str, loader: str, mc: str, loader_ver: str | None,
                   java: str, log=print) -> dict:
    """サーバーのファイルを用意し、起動のしかた（start 情報）を返す。"""
    os.makedirs(server_dir, exist_ok=True)

    if loader == "vanilla":
        dl = version_info(mc)["downloads"]["server"]
        log(f"マイクラ {mc} のサーバーをダウンロード中…")
        net.download(dl["url"], os.path.join(server_dir, "server.jar"), sha1=dl.get("sha1"))
        return {"type": "jar", "jar": "server.jar"}

    if loader == "paper":
        b = paper_build(mc)
        if not b:
            raise RuntimeError(f"Paper の マイクラ {mc} 用がまだありません")
        log(f"Paper {mc} (ビルド {b['build']}) をダウンロード中…")
        net.download(b["url"], os.path.join(server_dir, "paper.jar"), sha256=b.get("sha256"))
        _accept_paper_defaults(server_dir)
        return {"type": "jar", "jar": "paper.jar", "loader_version": b["build"]}

    if loader == "purpur":
        v = loader_ver or purpur_build(mc)
        if not v:
            raise RuntimeError(f"Purpur の マイクラ {mc} 用がまだありません")
        info = {}
        try:
            info = net.get_json(f"{PURPUR_API}/{mc}/{v}")
        except net.NetError:
            pass
        log(f"Purpur {mc} (ビルド {v}) をダウンロード中…")
        net.download(f"{PURPUR_API}/{mc}/{v}/download", os.path.join(server_dir, "purpur.jar"),
                     md5=info.get("md5"))
        _accept_paper_defaults(server_dir)
        return {"type": "jar", "jar": "purpur.jar", "loader_version": v}

    if loader == "fabric":
        lv = loader_ver or fabric_loader_for(mc)
        inst = net.get_json(f"{FABRIC_META}/versions/installer")
        iv = next((x["version"] for x in inst if x.get("stable")), inst[0]["version"])
        log(f"Fabric {lv} をダウンロード中…")
        net.download(f"{FABRIC_META}/versions/loader/{mc}/{lv}/{iv}/server/jar",
                     os.path.join(server_dir, "fabric-server-launch.jar"))
        return {"type": "jar", "jar": "fabric-server-launch.jar", "loader_version": lv}

    if loader == "neoforge":
        v = loader_ver or neoforge_for(mc)
        jar = os.path.join(server_dir, f"neoforge-{v}-installer.jar")
        log(f"NeoForge {v} をダウンロード中…")
        net.download(f"{NEOFORGE_MAVEN}/{v}/neoforge-{v}-installer.jar", jar)
        _run_installer(java, jar, ["--installServer"], server_dir, log)
        os.remove(jar)
        return {"type": "args", "args_dir": f"libraries/net/neoforged/neoforge/{v}", "loader_version": v}

    if loader == "forge":
        v = loader_ver or forge_for(mc)
        full = f"{mc}-{v}"
        jar = os.path.join(server_dir, f"forge-{full}-installer.jar")
        log(f"Forge {v} をダウンロード中…")
        net.download(f"{FORGE_MAVEN}/{full}/forge-{full}-installer.jar", jar)
        _run_installer(java, jar, ["--installServer"], server_dir, log)
        os.remove(jar)
        for leftover in glob.glob(os.path.join(server_dir, "*installer.jar.log")):
            os.remove(leftover)
        args_dir = f"libraries/net/minecraftforge/forge/{full}"
        if os.path.exists(os.path.join(server_dir, args_dir, "win_args.txt")):
            return {"type": "args", "args_dir": args_dir, "loader_version": v}
        jars = [j for j in glob.glob(os.path.join(server_dir, "forge-*.jar")) if "installer" not in j]
        if not jars:
            raise RuntimeError("Forge の起動ファイルが見つかりませんでした")
        return {"type": "jar", "jar": os.path.basename(sorted(jars)[-1]), "loader_version": v}

    raise ValueError(loader)


def _accept_paper_defaults(server_dir: str):
    os.makedirs(os.path.join(server_dir, "plugins"), exist_ok=True)


def jvm_flags(mem_mb: int) -> list[str]:
    """Aikar's Flags（サーバーを軽くする定番の Java の設定）。メモリが 12GB 以上なら大きい用の値。"""
    big = mem_mb >= 12288
    return [
        "-XX:+UseG1GC", "-XX:+ParallelRefProcEnabled", "-XX:MaxGCPauseMillis=200",
        "-XX:+UnlockExperimentalVMOptions", "-XX:+DisableExplicitGC",
        f"-XX:G1NewSizePercent={40 if big else 30}", f"-XX:G1MaxNewSizePercent={50 if big else 40}",
        f"-XX:G1HeapRegionSize={16 if big else 8}M", f"-XX:G1ReservePercent={15 if big else 20}",
        "-XX:G1HeapWastePercent=5", "-XX:G1MixedGCCountTarget=4",
        f"-XX:InitiatingHeapOccupancyPercent={20 if big else 15}",
        "-XX:G1MixedGCLiveThresholdPercent=90", "-XX:G1RSetUpdatingPauseTimePercent=5",
        "-XX:SurvivorRatio=32", "-XX:+PerfDisableSharedMem", "-XX:MaxTenuringThreshold=1",
        "-Dusing.aikars.flags=https://mcflags.emc.gs", "-Daikars.new.flags=true",
        "-Dfile.encoding=UTF-8",
    ]


def start_command(java: str, start: dict, mem_mb: int, server_dir: str) -> list[str]:
    # 最初から全部確保すると PC の他のソフトが重くなるので、最初は半分（最大4GB）だけ
    xms = min(mem_mb, max(1024, min(4096, mem_mb // 2)))
    cmd = [java, f"-Xms{xms}M", f"-Xmx{mem_mb}M"] + jvm_flags(mem_mb)
    if start["type"] == "jar":
        cmd += ["-jar", start["jar"]]
    else:
        name = "win_args.txt" if IS_WINDOWS else "unix_args.txt"
        cmd += ["@" + start["args_dir"].replace("/", os.sep) + os.sep + name]
    cmd.append("nogui")
    return cmd
