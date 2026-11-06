"""CurseForge（もう1つの大きなMOD配布サイト）から MOD を探して入れる。

使うには、使う人が自分で無料の「APIキー」を申請して、ソフトの設定に入れる必要がある
（https://console.curseforge.com/ で作れる）。
作者が「ほかのソフトからのダウンロード」を許可していない MOD は、自動では入れられない
（その場合はサイトのページを開いて、手で入れてもらう）。
"""
from __future__ import annotations

import os
import tempfile

from . import net

API = "https://api.curseforge.com/v1"
GAME_ID = 432                 # マイクラ
CLASS_MODS = 6
CLASS_PLUGINS = 5             # Bukkit プラグイン
LOADER_TYPE = {"forge": 1, "fabric": 4, "quilt": 5, "neoforge": 6}
LOADER_NAMES = {1: "forge", 4: "fabric", 5: "quilt", 6: "neoforge"}
SORT = {"downloads": 6, "relevance": 2, "follows": 2, "updated": 3, "newest": 11}
RELEASE = {1: "release", 2: "beta", 3: "alpha"}
DOWNLOAD_HOSTS = {"edge.forgecdn.net", "mediafilez.forgecdn.net"}


class KeyMissing(Exception):
    pass


# 入口のセキュリティ（Cloudflare）が、見慣れない名前の通信を断ることがあるため、
# その場合だけ 普通のブラウザ風の名前に切りかえる（テストで通ったときに自動で切りかえる）
_BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
_ua_override = {"on": False}


def _headers(key, extra=None):
    h = {"x-api-key": key, "Accept": "application/json"}
    if _ua_override["on"]:
        h["User-Agent"] = _BROWSER_UA
    if extra:
        h.update(extra)
    return h


def _get(path, key, params=None):
    if not key:
        raise KeyMissing("CurseForge の APIキーが入っていません（「ソフトの設定」で入れられます）")
    return net.get_json(f"{API}{path}", params, headers=_headers(key))


def _post(path, key, body):
    if not key:
        raise KeyMissing("CurseForge の APIキーが入っていません")
    return net.post_json(f"{API}{path}", body, headers=_headers(key))


def clean_key(key: str) -> str:
    """貼りつけたときに混ざりやすい 空白・改行・かぎかっこ を取りのぞく。"""
    k = "".join(str(key or "").split())
    return k.strip("\"'「」“”‘’`")


def _status(e: Exception):
    import re
    m = re.search(r"エラー (\d{3})", str(e))
    return int(m.group(1)) if m else None


def check_key(key: str):
    """APIキーを試す。(使えるか, 理由の文) を返す。"""
    try:
        _get("/games/432", key)
        return True, ""
    except net.NetError as e:
        code = _status(e)
        if code in (401, 403) and not _ua_override["on"]:
            # キーのせいではなく、通信の名前で断られた可能性 → ブラウザ風の名前でもう一度
            _ua_override["on"] = True
            try:
                _get("/games/432", key)
                return True, ""
            except net.NetError as e2:
                _ua_override["on"] = False
                code = _status(e2) or code
        if code in (401, 403):
            return False, (f"CurseForge に「このキーは使えません」と言われました（エラー {code}）。"
                           "console.curseforge.com の「API Keys」にある長いキー（$2a$10$ で始まる）を、"
                           "もう一度ぜんぶコピーしてください。")
        if code == 429:
            return False, "CurseForge への問い合わせが多すぎます（エラー 429）。少し待ってからもう一度ためしてください。"
        if code:
            return False, f"CurseForge が うまく答えてくれませんでした（エラー {code}）。しばらくしてからためしてください。"
        return False, ("CurseForge につながりませんでした。インターネットの接続や、"
                       "ウイルス対策ソフトの設定を確認してください。")


def test_key(key: str) -> bool:
    return check_key(key)[0]


# ---------------- 検索 ----------------

def search(key, query, mc, loader, sort="downloads", offset=0, limit=24, plugins=False):
    params = {"gameId": GAME_ID, "classId": CLASS_PLUGINS if plugins else CLASS_MODS,
              "searchFilter": query or "", "sortField": SORT.get(sort, 6), "sortOrder": "desc",
              "index": int(offset), "pageSize": int(limit)}
    if mc:
        params["gameVersion"] = mc
    if loader in LOADER_TYPE and not plugins:
        params["modLoaderType"] = LOADER_TYPE[loader]
    data = _get("/mods/search", key, params)
    return data.get("data", []), (data.get("pagination") or {}).get("totalCount", 0)


def mod(key, mod_id) -> dict:
    return _get(f"/mods/{int(mod_id)}", key)["data"]


def description(key, mod_id) -> str:
    try:
        return _get(f"/mods/{int(mod_id)}/description", key).get("data") or ""
    except net.NetError:
        return ""


def card_info(m: dict, mc=None, loader=None) -> dict:
    """検索結果を、画面のカードの形にする。"""
    idx = m.get("latestFilesIndexes") or []
    vers = sorted({x.get("gameVersion") for x in idx if x.get("gameVersion")})
    lds = sorted({LOADER_NAMES[x["modLoader"]] for x in idx if x.get("modLoader") in LOADER_NAMES})
    ok = None
    if mc:
        hit = [x for x in idx if x.get("gameVersion") == mc and
               (loader not in LOADER_TYPE or x.get("modLoader") == LOADER_TYPE.get(loader))]
        ok = bool(hit)
    return {"versions": vers, "loaders": lds, "ok": ok}


def files_for(key, mod_id, mc, loader) -> list[dict]:
    params = {"pageSize": 50}
    if mc:
        params["gameVersion"] = mc
    if loader in LOADER_TYPE:
        params["modLoaderType"] = LOADER_TYPE[loader]
    return _get(f"/mods/{int(mod_id)}/files", key, params).get("data", [])


def best_file(files: list[dict]) -> dict | None:
    for kind in (1, 2, 3):
        cands = [f for f in files if f.get("releaseType") == kind and f.get("isAvailable", True)]
        if cands:
            return max(cands, key=lambda f: f.get("fileDate", ""))
    return None


def _sha1(f):
    return next((h["value"] for h in f.get("hashes", []) if h.get("algo") == 1), None)


def download_url(f: dict) -> str | None:
    url = f.get("downloadUrl")
    if url:
        return url
    return None


# ---------------- 入れる ----------------

def install(ml, key, mod_id, mc, loader, log=print, result=None, visiting=None, side_of=None):
    """MOD を入れる（前提MODも自動）。ml は modrinth.ModList。"""
    from .modrinth import InstallResult
    result = result or InstallResult()
    visiting = visiting if visiting is not None else set()
    mod_id = int(mod_id)
    if mod_id in visiting:
        return result
    visiting.add(mod_id)
    m = mod(key, mod_id)
    title = m.get("name") or str(mod_id)
    f = best_file(files_for(key, mod_id, mc, None if ml.folder == "plugins" else loader))
    if not f:
        raise LookupError(f"「{title}」は マイクラ {mc} / {loader} 用がありません（CurseForge）")
    pid = f"cf:{mod_id}"
    existing = ml.by_project(pid)
    if existing and existing.get("cf_file") == f["id"]:
        result.skipped.append(title)
    else:
        url = download_url(f)
        page = (m.get("links") or {}).get("websiteUrl") or f"https://www.curseforge.com/minecraft/mc-mods/{m.get('slug')}"
        if not url:
            result.warnings.append(f"「{title}」は作者の設定で自動ダウンロードできません。サイト（{page}）から手でダウンロードして「ファイルから追加」で入れてください。")
            return result
        gvs = f.get("gameVersions") or []
        client_only = "Client" in gvs and "Server" not in gvs
        location = "client_only" if (client_only and ml.folder != "plugins") else "server"
        rec = {"project_id": pid, "source": "curseforge", "cf_id": mod_id, "cf_file": f["id"],
               "title": title, "slug": m.get("slug"), "version_number": f.get("displayName") or f.get("fileName"),
               "path": f"{ml.folder}/{f['fileName']}", "url": url, "sha1": _sha1(f), "size": f.get("fileLength", 0),
               "client_side": "required" if client_only else "unknown", "server_side": "unsupported" if client_only else "required",
               "location": location, "enabled": True, "icon_url": (m.get("logo") or {}).get("thumbnailUrl"),
               "description": m.get("summary"), "categories": [c.get("slug") for c in m.get("categories", [])],
               "game_versions": [g for g in gvs if g[:1].isdigit()], "loaders": [g.lower() for g in gvs if not g[:1].isdigit()],
               "page": page, "incompatible": []}
        if existing:
            ml.remove(existing)
        log(f"ダウンロード中（CurseForge）: {title}")
        if location == "server":
            dest = os.path.join(ml.server_dir, rec["path"])
            net.download(url, dest, sha1=rec["sha1"])
            rec["sha512"] = net.file_hash(dest, "sha512")
            result.installed.append(title)
        else:
            # 遊ぶ人だけのMOD：サーバーには置かないが、友達用パックのために指紋だけ取っておく
            fd, tmp = tempfile.mkstemp(suffix=".jar")
            os.close(fd)
            try:
                net.download(url, tmp, sha1=rec["sha1"])
                rec["sha512"] = net.file_hash(tmp, "sha512")
            finally:
                os.remove(tmp)
            result.client_only.append(title)
        ml.add(rec)
    for dep in f.get("dependencies", []):
        did = dep.get("modId")
        if dep.get("relationType") == 3 and did:
            if ml.by_project(f"cf:{did}"):
                continue
            try:
                install(ml, key, did, mc, loader, log, result, visiting)
            except LookupError as e:
                result.warnings.append(f"前提MODが見つかりません: {e}")
        elif dep.get("relationType") == 5 and did and ml.by_project(f"cf:{did}"):
            result.warnings.append(f"「{title}」と「{ml.by_project(f'cf:{did}')['title']}」は一緒に使えないかもしれません")
    return result


def preview_deps(ml, key, mod_id, mc, loader, seen=None) -> list[dict]:
    """入れる前に「一緒に入る前提MOD」を調べる。"""
    seen = seen if seen is not None else set()
    out = []
    f = best_file(files_for(key, mod_id, mc, None if ml.folder == "plugins" else loader))
    if not f:
        return out
    for dep in f.get("dependencies", []):
        did = dep.get("modId")
        if dep.get("relationType") != 3 or not did or did in seen or ml.by_project(f"cf:{did}"):
            continue
        seen.add(did)
        try:
            out.append({"id": f"cf:{did}", "title": mod(key, did).get("name", str(did))})
            out += preview_deps(ml, key, did, mc, loader, seen)
        except net.NetError:
            pass
    return out


def check_updates(ml, key, mc, loader) -> list[tuple[dict, dict]]:
    out = []
    for m in ml.items:
        if m.get("source") != "curseforge":
            continue
        try:
            f = best_file(files_for(key, m["cf_id"], mc, None if ml.folder == "plugins" else loader))
        except net.NetError:
            continue
        if f and f["id"] != m.get("cf_file") and download_url(f):
            out.append((m, f))
    return out
