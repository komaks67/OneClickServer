"""Modrinth（無料のMOD配布サイト）から MOD を探して入れる・更新する。"""
from __future__ import annotations

import json
import os

from . import net
from .paths import load_json, save_json

API = "https://api.modrinth.com/v2"
SIDE_LABEL = {"required": "必要", "optional": "あってもOK", "unsupported": "不要", "unknown": "?"}
LOADER_KEY = {"fabric": "fabric", "neoforge": "neoforge", "forge": "forge", "paper": "paper", "purpur": "purpur"}
# そのサーバーで使えるファイルの種類（Paper は Spigot / Bukkit 用のプラグインも動く）
LOADER_KEYS = {"fabric": ["fabric"], "neoforge": ["neoforge"], "forge": ["forge"],
               "paper": ["paper", "spigot", "bukkit"], "purpur": ["purpur", "paper", "spigot", "bukkit"]}
PLUGIN_LOADERS = ("paper", "purpur")


def folder_for(loader: str | None) -> str:
    return "plugins" if loader in PLUGIN_LOADERS else "mods"


def loader_keys(loader: str | None) -> list[str]:
    return LOADER_KEYS.get(loader or "", [])

CATEGORIES = [
    ("", "すべて"), ("adventure", "冒険"), ("technology", "工業・技術"), ("magic", "魔法"),
    ("storage", "収納"), ("worldgen", "地形・世界"), ("mobs", "生き物"), ("equipment", "装備"),
    ("decoration", "建築・飾り"), ("food", "料理"), ("optimization", "軽量化"),
    ("utility", "便利"), ("transportation", "乗り物・移動"), ("game-mechanics", "仕組み"),
]


def _facets(groups):
    return json.dumps(groups)


# ---------------- 検索 ----------------

def search(query: str, mc: str, loader: str | None, project_type="mod", category="",
           sort="relevance", offset=0, limit=30) -> tuple[list[dict], int]:
    facets = [] if loader in PLUGIN_LOADERS and project_type == "mod" else [[f"project_type:{project_type}"]]
    if mc:
        facets.append([f"versions:{mc}"])
    if loader and loader in LOADER_KEYS:
        facets.append([f"categories:{k}" for k in LOADER_KEYS[loader]])
    if category:
        facets.append([f"categories:{category}"])
    data = net.get_json(f"{API}/search", {
        "query": query, "facets": _facets(facets), "index": sort,
        "offset": offset, "limit": limit})
    return data.get("hits", []), data.get("total_hits", 0)


def project(pid: str) -> dict:
    return net.get_json(f"{API}/project/{pid}")


def projects(ids: list[str]) -> dict[str, dict]:
    out = {}
    ids = list(dict.fromkeys(ids))
    for i in range(0, len(ids), 80):
        for p in net.get_json(f"{API}/projects", {"ids": json.dumps(ids[i:i + 80])}):
            out[p["id"]] = p
    return out


def versions_for(pid: str, mc: str | None, loader: str | None) -> list[dict]:
    params = {}
    if mc:
        params["game_versions"] = json.dumps([mc])
    if loader and loader in LOADER_KEYS:
        params["loaders"] = json.dumps(LOADER_KEYS[loader])
    return net.get_json(f"{API}/project/{pid}/version", params)


def best_version(versions: list[dict]) -> dict | None:
    """正式版 → ベータ → アルファ の順で、新しいものを選ぶ。"""
    for kind in ("release", "beta", "alpha"):
        cands = [v for v in versions if v.get("version_type") == kind]
        if cands:
            return max(cands, key=lambda v: v.get("date_published", ""))
    return None


def version(vid: str) -> dict:
    return net.get_json(f"{API}/version/{vid}")


def primary_file(ver: dict) -> dict:
    files = ver.get("files", [])
    return next((f for f in files if f.get("primary")), files[0])


# ---------------- 入っている MOD の記録（mods.json） ----------------

class ModList:
    """サーバーごとの MOD の記録。"""

    def __init__(self, server_dir: str):
        self.server_dir = server_dir
        self.loader = load_json(os.path.join(server_dir, "mcmh.json"), {}).get("loader")
        self.folder = folder_for(self.loader)
        self.path = os.path.join(server_dir, "mods.json")
        self.items: list[dict] = load_json(self.path, [])

    def save(self):
        save_json(self.path, self.items)

    def by_project(self, pid):
        return next((m for m in self.items if m.get("project_id") == pid), None)

    def file_path(self, m) -> str:
        p = os.path.join(self.server_dir, m["path"].replace("/", os.sep))
        return p if m.get("enabled", True) else p + ".disabled"

    def add(self, rec: dict):
        self.items = [m for m in self.items
                      if not (rec.get("project_id") and m.get("project_id") == rec["project_id"])
                      and m.get("path") != rec["path"]]
        self.items.append(rec)
        self.save()

    def remove(self, rec: dict):
        if rec.get("location") == "server":
            for p in (self.file_path(rec), os.path.join(self.server_dir, rec["path"])):
                if os.path.exists(p):
                    os.remove(p)
        self.items = [m for m in self.items if m is not rec]
        self.save()

    def set_enabled(self, rec: dict, enabled: bool):
        if rec.get("location") == "server":
            on = os.path.join(self.server_dir, rec["path"])
            off = on + ".disabled"
            if enabled and os.path.exists(off):
                os.replace(off, on)
            elif not enabled and os.path.exists(on):
                os.replace(on, off)
        rec["enabled"] = enabled
        self.save()

    def sync_with_folder(self, log=print):
        """手で mods フォルダに入れた/消したファイルを記録に反映する。"""
        mods_dir = os.path.join(self.server_dir, self.folder)
        os.makedirs(mods_dir, exist_ok=True)
        known = {m["path"] for m in self.items}
        # 消えたファイル
        self.items = [m for m in self.items
                      if m.get("location") != "server" or os.path.exists(self.file_path(m))]
        # 新しく見つけたファイル
        new_files = []
        for fn in os.listdir(mods_dir):
            enabled = True
            base = fn
            if fn.endswith(".jar.disabled"):
                enabled, base = False, fn[:-9]
            elif not fn.endswith(".jar"):
                continue
            if f"{self.folder}/{base}" in known:
                continue
            new_files.append((base, enabled))
        if new_files:
            log(f"手動で追加された MOD を {len(new_files)} 個見つけました。調べています…")
            identify_files(self, new_files)
        self.save()


def _record_from_version(ver: dict, proj: dict, location="server", enabled=True, folder="mods") -> dict:
    f = primary_file(ver)
    return {
        "project_id": ver["project_id"], "version_id": ver["id"],
        "title": proj.get("title", f["filename"]), "slug": proj.get("slug"),
        "version_number": ver.get("version_number"), "path": f"{folder}/{f['filename']}",
        "sha1": f["hashes"].get("sha1"), "sha512": f["hashes"].get("sha512"),
        "url": f["url"], "size": f.get("size", 0),
        "client_side": proj.get("client_side", "unknown"),
        "server_side": proj.get("server_side", "unknown"),
        "location": location, "enabled": enabled,
        "icon_url": proj.get("icon_url"), "description": proj.get("description"),
        "categories": proj.get("categories") or proj.get("display_categories"),
        "game_versions": ver.get("game_versions"), "loaders": ver.get("loaders"),
        "incompatible": [d["project_id"] for d in ver.get("dependencies", [])
                         if d.get("dependency_type") == "incompatible" and d.get("project_id")],
    }


def identify_files(ml: ModList, files: list[tuple[str, bool]]):
    hashes = {}
    for base, enabled in files:
        p = os.path.join(ml.server_dir, ml.folder, base + ("" if enabled else ".disabled"))
        hashes[net.file_hash(p, "sha1")] = (base, enabled, p)
    found = {}
    try:
        found = net.post_json(f"{API}/version_files", {"hashes": list(hashes), "algorithm": "sha1"})
        projs = projects([v["project_id"] for v in found.values()]) if found else {}
    except net.NetError:
        projs = {}
    for h, (base, enabled, p) in hashes.items():
        ver = found.get(h)
        if ver and ver.get("project_id") in projs:
            rec = _record_from_version(ver, projs[ver["project_id"]], "server", enabled, ml.folder)
            # 実際のファイル名に合わせる
            rec["path"] = f"{ml.folder}/{base}"
            for f in ver.get("files", []):
                if f["hashes"].get("sha1") == h:
                    rec.update(url=f["url"], sha512=f["hashes"].get("sha512"), sha1=h, size=f.get("size", 0))
        else:
            rec = {"project_id": None, "title": base, "path": f"{ml.folder}/{base}", "sha1": h,
                   "sha512": net.file_hash(p, "sha512"), "size": os.path.getsize(p),
                   "client_side": "unknown", "server_side": "unknown",
                   "location": "server", "enabled": enabled, "manual": True}
        ml.items.append(rec)


# ---------------- インストール（前提MODも自動） ----------------

class InstallResult:
    def __init__(self):
        self.installed: list[str] = []
        self.client_only: list[str] = []
        self.skipped: list[str] = []
        self.warnings: list[str] = []

    def summary(self) -> str:
        parts = []
        if self.installed:
            parts.append("入れたMOD: " + "、".join(self.installed))
        if self.client_only:
            parts.append("遊ぶ人だけに必要なMOD（友達用パックに入ります）: " + "、".join(self.client_only))
        if self.skipped:
            parts.append("すでに入っていたMOD: " + "、".join(self.skipped))
        parts += ["⚠ " + w for w in self.warnings]
        return "\n".join(parts) or "変更はありませんでした。"


def install(ml: ModList, pid: str, mc: str, loader: str, log=print,
            result: InstallResult | None = None, _visiting=None, version_id: str | None = None) -> InstallResult:
    result = result or InstallResult()
    _visiting = _visiting if _visiting is not None else set()
    if pid in _visiting:
        return result
    _visiting.add(pid)

    proj = project(pid)
    pid = proj["id"]  # slug で呼ばれても ID にそろえる
    existing = ml.by_project(pid)
    if version_id:
        ver = version(version_id)
    else:
        ver = best_version(versions_for(pid, mc, loader))
    if ver is None:
        raise LookupError(f"「{proj.get('title', pid)}」は マイクラ {mc} / {loader} 用がありません")

    if existing and existing.get("version_id") == ver["id"]:
        result.skipped.append(proj["title"])
    else:
        location = "client_only" if proj.get("server_side") == "unsupported" else "server"
        if ml.folder == "plugins":
            location = "server"
        rec = _record_from_version(ver, proj, location, folder=ml.folder)
        if existing:
            ml.remove(existing)
        if location == "server":
            log(f"ダウンロード中: {proj['title']} {ver.get('version_number', '')}")
            net.download(rec["url"], os.path.join(ml.server_dir, rec["path"]),
                         sha1=rec["sha1"], sha512=rec["sha512"])
            result.installed.append(proj["title"])
        else:
            result.client_only.append(proj["title"])
        ml.add(rec)

    # 前提MOD（required）と、相性が悪いMOD（incompatible）
    for dep in ver.get("dependencies", []):
        dpid = dep.get("project_id")
        if dep.get("dependency_type") == "required":
            if not dpid and dep.get("version_id"):
                dpid = version(dep["version_id"])["project_id"]
            if not dpid:
                continue
            if ml.by_project(dpid):
                if dpid not in _visiting:
                    result.skipped.append(ml.by_project(dpid)["title"])
                    _visiting.add(dpid)
                continue
            try:
                install(ml, dpid, mc, loader, log, result, _visiting, version_id=dep.get("version_id"))
            except LookupError as e:
                result.warnings.append(f"前提MODが見つかりません: {e}")
        elif dep.get("dependency_type") == "incompatible" and dpid and ml.by_project(dpid):
            result.warnings.append(
                f"「{proj['title']}」と「{ml.by_project(dpid)['title']}」は一緒に使えないかもしれません")
    return result


def preview_deps(ml: ModList, pid: str, mc: str, loader: str, seen=None) -> list[dict]:
    """入れる前に「一緒に入る前提MOD」を調べる（まだ入っていないものだけ）。"""
    seen = seen if seen is not None else set()
    out = []
    ver = best_version(versions_for(pid, mc, loader))
    if not ver:
        return out
    for dep in ver.get("dependencies", []):
        if dep.get("dependency_type") != "required":
            continue
        dpid = dep.get("project_id")
        if not dpid and dep.get("version_id"):
            try:
                dpid = version(dep["version_id"])["project_id"]
            except net.NetError:
                continue
        if not dpid or dpid in seen or ml.by_project(dpid):
            continue
        seen.add(dpid)
        try:
            proj = project(dpid)
        except net.NetError:
            continue
        if ml.by_project(proj["id"]):
            continue
        out.append({"id": proj["id"], "title": proj.get("title", dpid)})
        out += preview_deps(ml, proj["id"], mc, loader, seen)
    return out


# ---------------- 更新 ----------------

def check_updates(ml: ModList, mc: str, loader: str) -> list[tuple[dict, dict]]:
    recs = [m for m in ml.items if m.get("project_id") and m.get("sha1") and m.get("source") != "curseforge"]
    if not recs:
        return []
    data = net.post_json(f"{API}/version_files/update", {
        "hashes": [m["sha1"] for m in recs], "algorithm": "sha1",
        "loaders": loader_keys(loader) or [loader], "game_versions": [mc]})
    out = []
    for m in recs:
        nv = data.get(m["sha1"])
        if nv and nv.get("id") != m.get("version_id"):
            out.append((m, nv))
    return out


def apply_update(ml: ModList, rec: dict, new_ver: dict, log=print):
    proj = {"title": rec["title"], "slug": rec.get("slug"), "icon_url": rec.get("icon_url"),
            "description": rec.get("description"), "categories": rec.get("categories"),
            "client_side": rec.get("client_side"), "server_side": rec.get("server_side")}
    new = _record_from_version(new_ver, proj, rec.get("location", "server"), rec.get("enabled", True), ml.folder)
    ml.remove(rec)  # 先に古いファイルを消す（同じファイル名のこともあるため）
    if new["location"] == "server":
        log(f"更新中: {rec['title']} → {new_ver.get('version_number')}")
        net.download(new["url"], os.path.join(ml.server_dir, new["path"]), sha1=new["sha1"], sha512=new["sha512"])
        if not new["enabled"]:
            os.replace(os.path.join(ml.server_dir, new["path"]), ml.file_path(new))
    ml.add(new)


# ---------------- MODパック ----------------

def download_modpack(pid: str, dest_dir: str, mc: str | None = None, log=print) -> str:
    vers = versions_for(pid, mc, None) if mc else net.get_json(f"{API}/project/{pid}/version")
    ver = best_version([v for v in vers if any(f["filename"].endswith(".mrpack") for f in v.get("files", []))])
    if not ver:
        raise LookupError("このMODパックのダウンロードファイルが見つかりません")
    f = next(f for f in ver["files"] if f["filename"].endswith(".mrpack") and (f.get("primary") or True))
    dest = os.path.join(dest_dir, f["filename"])
    log(f"MODパックをダウンロード中: {f['filename']}")
    net.download(f["url"], dest, sha1=f["hashes"].get("sha1"), sha512=f["hashes"].get("sha512"))
    return dest


# ---------------- ローダーのおすすめ判定 ----------------

def recommend_loader(mc: str, names: list[str], candidates: list[str], log=print):
    """入れたいMODの名前から、一番多く対応しているローダーを選ぶ。"""
    support: dict[str, list[str]] = {c: [] for c in candidates}
    not_found = []
    for name in names:
        hits, _ = search(name, mc, None, limit=1)
        if not hits:
            not_found.append(name)
            continue
        hit = hits[0]
        loaders_ok = set()
        for v in versions_for(hit["project_id"], mc, None):
            loaders_ok.update(v.get("loaders", []))
        log(f"  {hit['title']}: {', '.join(sorted(loaders_ok)) or 'なし'}")
        for c in candidates:
            if LOADER_KEY.get(c) in loaders_ok:
                support[c].append(hit["title"])
    order = {"fabric": 0, "neoforge": 1, "forge": 2}
    ranked = sorted((c for c in candidates if c in order),
                    key=lambda c: (-len(support[c]), order[c]))
    return (ranked[0] if ranked else None), support, not_found


def recommend_loader_ids(mc: str, pids: list[str], candidates: list[str], log=print):
    """選んだMOD（ID）から、一番多く対応しているローダーを選ぶ。"""
    support: dict[str, list[str]] = {c: [] for c in candidates}
    for pid in pids:
        loaders_ok = set()
        for v in versions_for(pid, mc, None):
            loaders_ok.update(v.get("loaders", []))
        for c in candidates:
            if LOADER_KEY.get(c) in loaders_ok:
                support[c].append(pid)
    order = {"fabric": 0, "neoforge": 1, "forge": 2}
    ranked = sorted((c for c in candidates if c in order), key=lambda c: (-len(support[c]), order[c]))
    return (ranked[0] if ranked else None), support
