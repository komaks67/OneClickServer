"""遊ぶ人（友達・自分）のパソコンに、MODセットとローダーを入れる。

公式ランチャーに「起動構成」を1つ追加し、MODはその構成専用のフォルダに入れるので、
ふだんのマイクラ（MODなし）とまざらない。
"""
from __future__ import annotations

import datetime
import glob
import os
import subprocess

from . import javamgr, loaders, mrpack, net
from .javamgr import NO_WINDOW
from .paths import IS_WINDOWS, load_json, safe_name, save_json


def ensure_launcher_profiles(mc_dir: str):
    p = os.path.join(mc_dir, "launcher_profiles.json")
    os.makedirs(mc_dir, exist_ok=True)
    if not os.path.exists(p):
        save_json(p, {"profiles": {}, "settings": {}, "version": 3})
    return p


IN_USE_MSG = ("マイクラ（またはマイクラのランチャー）が動いているので、入れられませんでした。\n"
              "① マイクラとランチャーを閉じる\n"
              "② タスクマネージャー（Ctrl+Shift+Esc）に「java」が残っていたら終了する\n"
              "③ もう一度、同じボタンを押す\n"
              "それでもだめなら、PCを再起動してからやり直してください。")


def _decode(b: bytes) -> str:
    """Java の出力は、日本語Windowsだと Shift_JIS のことがある（文字化けを防ぐ）。"""
    for enc in ("utf-8", "cp932"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", errors="replace")


def _run(java, args, cwd, log):
    p = subprocess.run([java, "-jar"] + args, cwd=cwd, capture_output=True, creationflags=NO_WINDOW)
    if p.returncode != 0:
        out = (_decode(p.stdout or b"") + _decode(p.stderr or b"")).strip()
        if "FileSystemException" in out or "AccessDeniedException" in out or "being used by another process" in out \
                or "使用中" in out:
            raise RuntimeError(IN_USE_MSG)
        tail = "\n".join(out.splitlines()[-12:])
        raise RuntimeError("ローダーのインストールに失敗しました:\n" + tail)


def _installed(mc_dir: str, vid: str) -> bool:
    """そのバージョンが、もうランチャーに入っているか（入っていれば入れ直さない）。"""
    j = os.path.join(mc_dir, "versions", vid, vid + ".json")
    try:
        return os.path.getsize(j) > 50
    except OSError:
        return False


def install_loader_client(mc_dir: str, mc: str, loader: str, lver: str | None, java: str,
                          work_dir: str, log=print) -> str:
    """ローダーを公式ランチャーに入れて、バージョンID を返す。"""
    ensure_launcher_profiles(mc_dir)
    if loader == "vanilla":
        return mc
    if loader == "fabric":
        lv = lver or loaders.fabric_loader_for(mc)
        if _installed(mc_dir, f"fabric-loader-{lv}-{mc}"):
            log(f"Fabric {lv} は、もう入っています（そのまま使います）")
            return f"fabric-loader-{lv}-{mc}"
        inst = net.get_json(f"{loaders.FABRIC_META}/versions/installer")
        pick = next((x for x in inst if x.get("stable")), inst[0])
        jar = os.path.join(work_dir, "fabric-installer.jar")
        log(f"Fabric {lv} を入れています…")
        net.download(pick["url"], jar)
        _run(java, [jar, "client", "-dir", mc_dir, "-mcversion", mc, "-loader", lv, "-noprofile"], work_dir, log)
        return f"fabric-loader-{lv}-{mc}"
    if loader == "neoforge":
        v = lver or loaders.neoforge_for(mc)
        if _installed(mc_dir, f"neoforge-{v}"):
            log(f"NeoForge {v} は、もう入っています（そのまま使います）")
            return f"neoforge-{v}"
        jar = os.path.join(work_dir, f"neoforge-{v}-installer.jar")
        log(f"NeoForge {v} を入れています…（数分かかります）")
        net.download(f"{loaders.NEOFORGE_MAVEN}/{v}/neoforge-{v}-installer.jar", jar)
        _run(java, [jar, "--installClient", mc_dir], work_dir, log)
        return f"neoforge-{v}"
    if loader == "forge":
        v = lver or loaders.forge_for(mc)
        full = f"{mc}-{v}"
        already = glob.glob(os.path.join(mc_dir, "versions", f"*forge*{v}*"))
        if already:
            return os.path.basename(already[0])
        jar = os.path.join(work_dir, f"forge-{full}-installer.jar")
        log(f"Forge {v} を入れています…（数分かかります）")
        net.download(f"{loaders.FORGE_MAVEN}/{full}/forge-{full}-installer.jar", jar)
        try:
            _run(java, [jar, "--installClient", mc_dir], work_dir, log)
        except RuntimeError:
            # 古い Forge は自動インストールできないので、Forge の画面を開いて手でやってもらう
            subprocess.Popen([java, "-jar", jar], cwd=work_dir)
            raise RuntimeError("Forge を自動で入れられませんでした。\n開いた Forge の画面で「Install client」→「OK」を押し、"
                               "終わったらもう一度「⬇ 入れる」を押してください。") from None
        guess = f"{mc}-forge-{v}"
        if os.path.isdir(os.path.join(mc_dir, "versions", guess)):
            return guess
        found = [os.path.basename(d) for d in glob.glob(os.path.join(mc_dir, "versions", f"*{v}*"))]
        if found:
            return found[0]
        return guess
    raise ValueError(loader)


def add_launcher_profile(mc_dir: str, key: str, name: str, version_id: str, game_dir: str, mem_gb: int):
    p = ensure_launcher_profiles(mc_dir)
    data = load_json(p, {"profiles": {}})
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    prof = data.setdefault("profiles", {}).get(key, {})
    prof.update({
        "name": name, "type": "custom", "lastVersionId": version_id, "gameDir": game_dir,
        "icon": "Crafting_Table", "lastUsed": now,
        "javaArgs": f"-Xmx{mem_gb}G -XX:+UnlockExperimentalVMOptions -XX:+UseG1GC "
                    "-XX:G1NewSizePercent=20 -XX:G1ReservePercent=20 -XX:MaxGCPauseMillis=50 "
                    "-XX:G1HeapRegionSize=32M",
    })
    prof.setdefault("created", now)
    data["profiles"][key] = prof
    save_json(p, data)


def expect_from_pack(pack_path: str, skip: set[str] | None = None) -> dict:
    """このパックを入れたとき、mods フォルダにあるはずのファイル（参加前チェックで使う）。"""
    import zipfile
    info = mrpack.pack_info(pack_path)
    files = {}
    for f in info["files"]:
        env = f.get("env", {})
        if not f["path"].startswith("mods/") or env.get("client") == "unsupported":
            continue
        if f["path"] in (skip or set()) and env.get("client") == "optional":
            continue
        files[os.path.basename(f["path"])] = f.get("hashes", {}).get("sha1") or ""
    extra = []
    try:
        with zipfile.ZipFile(pack_path) as z:
            for n in z.namelist():
                parts = n.replace("\\", "/").split("/")
                if len(parts) == 3 and parts[0] in ("overrides", "client-overrides") and parts[1] == "mods" and parts[2].endswith(".jar"):
                    extra.append(parts[2])
    except (OSError, zipfile.BadZipFile):
        pass
    return {"files": files, "bundled": sorted(set(extra))}


def verify_mods(game_dir: str, expect: dict) -> dict:
    """mods フォルダが、パックのとおりかを調べる。"""
    mods_dir = os.path.join(game_dir, "mods")
    have = {fn for fn in os.listdir(mods_dir) if fn.endswith(".jar")} if os.path.isdir(mods_dir) else set()
    files = expect.get("files") or {}
    bundled = set(expect.get("bundled") or [])
    missing = sorted(fn for fn in list(files) + sorted(bundled) if fn not in have)
    broken = []
    for fn, sha in files.items():
        if fn in have and sha:
            try:
                if net.file_hash(os.path.join(mods_dir, fn), "sha1") != sha:
                    broken.append(fn)
            except OSError:
                broken.append(fn)
    extra = sorted(fn for fn in have if fn not in files and fn not in bundled and not fn.startswith("_my_"))
    return {"missing": missing, "broken": sorted(broken), "extra": extra,
            "ok": not missing and not broken, "count": len(files) + len(bundled)}


def join_with_pack(pack_path: str, mc_dir: str, mem_gb: int, work_dir: str, log=print,
                   skip: set[str] | None = None) -> dict:
    """友達からもらった .mrpack を、このPCのマイクラに入れる。"""
    info = mrpack.pack_info(pack_path)
    if info["loader"] == "quilt":
        raise RuntimeError("Quilt のパックはこのソフトでは入れられません。Prism Launcher を使ってください。")
    key = "mcmh_" + safe_name(info["name"]).replace(" ", "_")
    game_dir = os.path.join(mc_dir, "mcmh_instances", safe_name(info["name"]))
    os.makedirs(game_dir, exist_ok=True)
    log(f"「{info['name']}」（マイクラ {info['mc']} / {loaders.LOADER_LABEL.get(info['loader'])}）を入れます")

    # 古いMODを片付ける（パックの更新に対応）
    mods_dir = os.path.join(game_dir, "mods")
    wanted = {os.path.basename(f["path"]) for f in info["files"]
              if f["path"].startswith("mods/") and f.get("env", {}).get("client") != "unsupported"
              and not (f["path"] in (skip or set()) and f.get("env", {}).get("client") == "optional")}
    if os.path.isdir(mods_dir):
        for fn in os.listdir(mods_dir):
            if fn.endswith(".jar") and fn not in wanted and not fn.startswith("_my_"):
                try:
                    os.remove(os.path.join(mods_dir, fn))
                except PermissionError:
                    raise RuntimeError(IN_USE_MSG) from None

    try:
        mrpack.install_pack_files(pack_path, game_dir, "client", log, skip=skip)
    except PermissionError:
        raise RuntimeError(IN_USE_MSG) from None

    need = loaders.required_java(info["mc"])
    java = javamgr.ensure_java(need, log)
    os.makedirs(work_dir, exist_ok=True)
    vid = install_loader_client(mc_dir, info["mc"], info["loader"], info["loader_version"], java, work_dir, log)
    add_launcher_profile(mc_dir, key, f"{info['name']}（マルチ用）", vid, game_dir, mem_gb)
    log("✅ 完了！ マイクラのランチャーで「" + info["name"] + "（マルチ用）」を選んで「プレイ」を押してください。")
    return {"name": info["name"], "game_dir": game_dir, "version_id": vid, "profile_key": key,
            "mc": info["mc"], "loader": info["loader"], "pack_version": info.get("version"),
            "update": mrpack.read_update_info(pack_path), "expect": expect_from_pack(pack_path, skip)}


def open_launcher() -> bool:
    """公式ランチャーを開いてみる（見つからなければ False）。"""
    if not IS_WINDOWS:
        return False
    paths = [
        r"C:\XboxGames\Minecraft Launcher\Content\Minecraft.exe",
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     "Minecraft Launcher", "MinecraftLauncher.exe"),
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                     "Minecraft Launcher", "MinecraftLauncher.exe"),
    ]
    for p in paths:
        if os.path.exists(p):
            subprocess.Popen([p])
            return True
    try:
        subprocess.Popen(["explorer.exe", r"shell:AppsFolder\Microsoft.4297127D64EC6_8wekyb3d8bbwe!Minecraft"])
        return True
    except OSError:
        return False
