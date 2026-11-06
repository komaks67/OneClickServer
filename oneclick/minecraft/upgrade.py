"""ワールドを残したまま、サーバーのバージョンを変える（アップデート）。

1. 先にワールドをバックアップ
2. 新しいバージョンのサーバー本体・土台（Fabric など）を入れる
3. MOD / プラグインを、新しいバージョン用に入れかえる（ないものはOFFにする）
4. 失敗したら、元のファイルにもどす

マイクラは「新しいバージョンで開いたワールドは、古いバージョンでは開けない」ので、
古いバージョンにもどす（ダウングレード）はできないようにしている。
"""
from __future__ import annotations

import glob
import os
import shutil
import time

from . import javamgr, loaders, modrinth, net, versions
from .modrinth import ModList

# 新しいサーバー本体を入れる前によけておくファイル（失敗したらもどす）
KEEP_FOR_ROLLBACK = ["*.jar", "libraries", "run.bat", "run.sh", "user_jvm_args.txt", "mcmh.json", "mods.json"]


def candidates(prof) -> list[str]:
    """今より新しい正式版（新しい順）。"""
    cur = prof.get("mc") or ""
    try:
        rel = loaders.release_versions()
    except net.NetError:
        return []
    return [v for v in rel if versions.parse(v) > versions.parse(cur)]


def plan(prof, mc: str) -> dict:
    cur = prof.get("mc") or ""
    loader = prof.get("loader")
    if versions.parse(mc) < versions.parse(cur):
        return {"ok": False, "reason": f"マイクラ {cur} のワールドを、古い {mc} で開くことはできません（ワールドがこわれます）。"}
    lv = loaders.loader_version(loader, mc) if loader != "vanilla" else "-"
    if not lv:
        return {"ok": False, "reason": f"{loaders.LOADER_LABEL.get(loader, loader)} の マイクラ {mc} 用がまだありません。"}
    same = mc == cur
    ml = ModList(prof.dir)
    ok, ng, manual = [], [], []
    for m in ml.items:
        if not m.get("enabled", True):
            continue
        if not m.get("project_id"):
            manual.append(m["title"])
            continue
        try:
            ver = modrinth.best_version(modrinth.versions_for(m["project_id"], mc, loader))
        except net.NetError:
            ver = None
        if ver:
            ok.append({"title": m["title"], "path": m["path"], "from": m.get("version_number"),
                       "to": ver.get("version_number"), "version_id": ver["id"], "same": ver["id"] == m.get("version_id")})
        else:
            ng.append({"title": m["title"], "path": m["path"]})
    return {"ok": True, "mc": mc, "from_mc": cur, "loader": loader, "loader_label": loaders.LOADER_LABEL.get(loader),
            "loader_version": lv, "from_loader_version": prof.get("loader_version"), "same_mc": same,
            "mods_ok": ok, "mods_ng": ng, "manual": manual,
            "java": loaders.required_java(mc) if not same else prof.get("java_major")}


def run(prof, mc: str, make_backup, log=print) -> dict:
    pl = plan(prof, mc)
    if not pl["ok"]:
        raise RuntimeError(pl["reason"])
    if os.path.isdir(prof.world_dir()):
        log("ワールドをバックアップしています（失敗したらこれでもどせます）…")
        make_backup(prof, "before-version")
    # 古いサーバー本体をよけておく
    hold = os.path.join(prof.dir, "_mcmh_old_version")
    shutil.rmtree(hold, ignore_errors=True)
    os.makedirs(hold)
    moved = []
    for pat in KEEP_FOR_ROLLBACK:
        for src in glob.glob(os.path.join(prof.dir, pat)):
            dst = os.path.join(hold, os.path.basename(src))
            if os.path.basename(src) in ("mcmh.json", "mods.json"):
                shutil.copy2(src, dst)
            else:
                shutil.move(src, dst)
            moved.append(os.path.basename(src))
    old_data = dict(prof.data)
    try:
        need = loaders.required_java(mc)
        java = javamgr.ensure_java(need, log)
        log(f"マイクラ {mc} のサーバーを入れています…")
        lv = pl["loader_version"] if pl["loader_version"] != "-" else None
        start = loaders.install_server(prof.dir, prof.get("loader"), mc, lv, java, log)
        prof.data.update({"mc": mc, "loader_version": start.pop("loader_version", None), "start": start,
                          "java_major": need, "upgraded": time.strftime("%Y-%m-%d %H:%M"),
                          "upgraded_from": old_data.get("mc")})
        prof.save()
        # MOD を入れかえる
        ml = ModList(prof.dir)
        changed, disabled = [], []
        by_path = {m["path"]: m for m in ml.items}
        for it in pl["mods_ok"]:
            rec = by_path.get(it["path"])
            if not rec or it["same"]:
                continue
            log(f"入れかえ: {it['title']} {it['from']} → {it['to']}")
            modrinth.apply_update(ml, rec, modrinth.version(it["version_id"]), log)
            changed.append(it["title"])
        for it in pl["mods_ng"]:
            rec = by_path.get(it["path"])
            if rec:
                ml.set_enabled(rec, False)
                disabled.append(it["title"])
        shutil.rmtree(hold, ignore_errors=True)
        log("バージョンを変えました！")
        return {"mc": mc, "from": old_data.get("mc"), "changed": changed, "disabled": disabled, "manual": pl["manual"]}
    except Exception:
        log("失敗したので、元のサーバーにもどしています…")
        for name in os.listdir(hold):
            dst = os.path.join(prof.dir, name)
            if os.path.isdir(dst):
                shutil.rmtree(dst, ignore_errors=True)
            elif os.path.exists(dst):
                os.remove(dst)
            shutil.move(os.path.join(hold, name), dst)
        shutil.rmtree(hold, ignore_errors=True)
        prof.data = old_data
        prof.save()
        raise
