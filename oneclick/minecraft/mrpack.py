"""MODパック（.mrpack 形式）の書き出し・読み込み。

.mrpack は Modrinth の公式形式。このソフトだけでなく Prism Launcher / Modrinth App /
ATLauncher などでも「ファイルを開くだけ」で同じMODセットが入る。
"""
from __future__ import annotations

import io
import json
import os
import re
import struct
import zipfile

from . import modrinth, net
from .modrinth import ModList

INDEX = "modrinth.index.json"
LOADER_DEP = {"fabric": "fabric-loader", "neoforge": "neoforge", "forge": "forge", "quilt": "quilt-loader"}
DEP_LOADER = {v: k for k, v in LOADER_DEP.items()}


# ---------------- servers.dat（マイクラのサーバーリスト）を作る ----------------

def _nbt_str(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack(">H", len(b)) + b


def make_servers_dat(entries: list[tuple[str, str]]) -> bytes:
    """[(表示名, アドレス)] からサーバーリストのファイルを作る（NBT形式・圧縮なし）。"""
    out = io.BytesIO()
    out.write(b"\x0a" + _nbt_str(""))                    # ルート（compound）
    out.write(b"\x09" + _nbt_str("servers"))             # list
    out.write(b"\x0a" + struct.pack(">i", len(entries)))  # 中身は compound
    for name, ip in entries:
        out.write(b"\x08" + _nbt_str("name") + _nbt_str(name))
        out.write(b"\x08" + _nbt_str("ip") + _nbt_str(ip))
        out.write(b"\x01" + _nbt_str("acceptTextures") + b"\x01")
        out.write(b"\x00")                                # compound の終わり
    out.write(b"\x00")                                    # ルートの終わり
    return out.getvalue()


def read_servers_dat(data: bytes) -> list[tuple[str, str]]:
    """make_servers_dat で作ったような単純な servers.dat を読む（テスト・合体用）。"""
    pos = 0

    def rd(n):
        nonlocal pos
        b = data[pos:pos + n]
        pos += n
        return b

    def rstr():
        (n,) = struct.unpack(">H", rd(2))
        return rd(n).decode("utf-8")

    def skip_payload(t):
        if t == 1: rd(1)
        elif t == 2: rd(2)
        elif t in (3, 5): rd(4)
        elif t in (4, 6): rd(8)
        elif t == 7: rd(struct.unpack(">i", rd(4))[0])
        elif t == 8: rstr()
        elif t == 9:
            et = rd(1)[0]
            for _ in range(struct.unpack(">i", rd(4))[0]):
                skip_payload(et)
        elif t == 10:
            while True:
                tt = rd(1)[0]
                if tt == 0:
                    break
                rstr()
                skip_payload(tt)
        elif t == 11: rd(4 * struct.unpack(">i", rd(4))[0])
        elif t == 12: rd(8 * struct.unpack(">i", rd(4))[0])

    result = []
    if rd(1) != b"\x0a":
        return result
    rstr()
    while True:
        t = rd(1)
        if not t or t[0] == 0:
            break
        name = rstr()
        if t[0] == 9 and name == "servers":
            et = rd(1)[0]
            count = struct.unpack(">i", rd(4))[0]
            for _ in range(count):
                entry = {}
                while True:
                    tt = rd(1)[0]
                    if tt == 0:
                        break
                    key = rstr()
                    if tt == 8:
                        entry[key] = rstr()
                    else:
                        skip_payload(tt)
                result.append((entry.get("name", ""), entry.get("ip", "")))
            del et
        else:
            skip_payload(t[0])
    return result


# ---------------- 書き出し（友達に配る用） ----------------

def friend_need(m: dict) -> str | None:
    """友達（遊ぶ人）にとって、そのMODが 必須 / 選べる / 配らない のどれか。"""
    if not m.get("enabled", True) or m.get("client_side") == "unsupported":
        return None  # OFF のMOD・サーバー専用MODは配らない
    if str(m.get("path", "")).startswith("plugins/"):
        return None  # プラグインはサーバーだけで動く
    if m.get("location") == "client_only":
        return "optional"  # サーバーに無い＝遊ぶ人だけのMOD。友達が入れるか選べる
    if m.get("mark") == "optional" and can_be_optional(m):
        return "optional"  # 持ち主が「あると便利」の印をつけた（入れなくても入れるMOD）
    # サーバーに入っているMODは、Modrinth 上で「クライアント: 任意」でも必須にする。
    # 例: Fabric API は「任意」だが、ほかのMODが必要とするので、選べる扱いにすると
    # 友達の起動ソフトが入れてくれず「fabric-api がありません」で起動できなくなる。
    return "required"


def can_be_optional(m: dict) -> bool:
    """「あると便利（なくても入れる）」にしてよいMODか。部品（ライブラリ）や、クライアント必須のMODはだめ。"""
    if m.get("location") == "client_only":
        return True
    if m.get("client_side") != "optional":
        return False
    cats = {str(c).lower() for c in (m.get("categories") or [])}
    title = str(m.get("title") or "").lower()
    if "library" in cats or "api" in title or "lib" in title.split():
        return False
    return True


def pack_candidates(prof) -> list[dict]:
    """友達用パックに入れられるMODの一覧。"""
    ml = ModList(prof.dir)
    ml.sync_with_folder()
    return [m for m in ml.items if friend_need(m)]


UPDATE_FILE = "mcmh-update.json"


def fingerprint(prof, address, include_configs=True, exclude=None) -> str:
    """パックの中身が変わったかを調べるための「指紋」。MODや設定が変わると値が変わる。"""
    import hashlib
    h = hashlib.sha1()
    exclude = exclude or set()
    for m in sorted(pack_candidates(prof), key=lambda m: m["path"]):
        if friend_need(m) == "optional" and m["path"] in exclude:
            continue
        h.update(f"{m['path']}|{m.get('sha1')}|{friend_need(m)}|{m.get('location')}\n".encode())
    h.update(f"{address}|{prof.get('mc')}|{prof.get('loader')}|{prof.get('loader_version')}|{include_configs}".encode())
    cfg = os.path.join(prof.dir, "config")
    if include_configs and os.path.isdir(cfg):
        for root, _d, fns in sorted(os.walk(cfg)):
            for fn in sorted(fns):
                full = os.path.join(root, fn)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                h.update(f"{os.path.relpath(full, cfg)}|{st.st_size}|{int(st.st_mtime)}\n".encode())
    return h.hexdigest()


def bump(version: str) -> str:
    """1.0.3 → 1.0.4"""
    parts = re.findall(r"\d+", version or "")
    if not parts:
        return "1.0.1"
    parts[-1] = str(int(parts[-1]) + 1)
    return ".".join(parts)


def read_update_info(pack_path: str) -> dict | None:
    try:
        with zipfile.ZipFile(pack_path) as z:
            if UPDATE_FILE in z.namelist():
                return json.loads(z.read(UPDATE_FILE).decode("utf-8"))
    except (OSError, ValueError, zipfile.BadZipFile):
        return None
    return None


def export_pack(prof, out_path: str, address: str | None, pack_version: str = "1.0.0",
                include_configs: bool = True, exclude: set[str] | None = None,
                update_info: dict | None = None) -> dict:
    """サーバーと同じMODセットの .mrpack を作る。exclude はパックに入れないMODのパス。"""
    exclude = exclude or set()
    files, manual = [], []
    for m in pack_candidates(prof):
        need = friend_need(m)
        if need == "optional" and m["path"] in exclude:
            continue  # 必須MODは外せない（外すと友達が入れなくなる）
        if m.get("url") and m.get("sha1") and m.get("sha512"):
            env_server = "unsupported" if m.get("location") == "client_only" else "required"
            files.append({
                "path": m["path"], "hashes": {"sha1": m["sha1"], "sha512": m["sha512"]},
                "env": {"client": need, "server": env_server},
                "downloads": [m["url"]], "fileSize": int(m.get("size") or 0)})
        elif m.get("location") == "server":
            manual.append(m)

    deps = {"minecraft": prof.get("mc")}
    if prof.get("loader") in LOADER_DEP:
        deps[LOADER_DEP[prof.get("loader")]] = prof.get("loader_version")
    index = {"formatVersion": 1, "game": "minecraft", "versionId": pack_version,
             "name": prof.name, "summary": f"{prof.name} に参加するためのMODセット（OneClickServerMCで作成）",
             "files": files, "dependencies": deps}

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(INDEX, json.dumps(index, ensure_ascii=False, indent=2))
        if update_info:
            z.writestr(UPDATE_FILE, json.dumps(update_info, ensure_ascii=False))
        # Modrinth に無い（手で入れた）MOD はファイルごと同梱
        for m in manual:
            src = os.path.join(prof.dir, m["path"])
            if os.path.exists(src):
                z.write(src, "overrides/" + m["path"])
        # 元にしたMODパックの、遊ぶ人向けファイル（設定・リソースパックなど）
        src_pack = os.path.join(prof.dir, "source.mrpack")
        if os.path.exists(src_pack):
            with zipfile.ZipFile(src_pack) as sz:
                for info in sz.infolist():
                    if info.is_dir():
                        continue
                    if info.filename.startswith(("overrides/", "client-overrides/")):
                        if info.filename in z.namelist():
                            continue
                        z.writestr(info.filename, sz.read(info.filename))
        elif include_configs:
            cfg = os.path.join(prof.dir, "config")
            if os.path.isdir(cfg):
                for root, _d, fns in os.walk(cfg):
                    for fn in fns:
                        full = os.path.join(root, fn)
                        rel = os.path.relpath(full, prof.dir).replace(os.sep, "/")
                        z.write(full, "overrides/" + rel)
        # サーバーリストに最初から入るようにする
        if address:
            z.writestr("client-overrides/servers.dat", make_servers_dat([(prof.name, address)]))
    return {"files": len(files), "manual": len(manual)}


# ---------------- 読み込み ----------------

def read_index(pack_path: str) -> dict:
    with zipfile.ZipFile(pack_path) as z:
        return json.loads(z.read(INDEX).decode("utf-8"))


def pack_info(pack_path: str) -> dict:
    idx = read_index(pack_path)
    deps = idx.get("dependencies", {})
    loader, lver = "vanilla", None
    for key, l in DEP_LOADER.items():
        if key in deps:
            loader, lver = l, deps[key]
    return {"name": idx.get("name", "MODパック"), "mc": deps.get("minecraft"), "loader": loader,
            "loader_version": lver, "files": idx.get("files", []), "version": idx.get("versionId")}


def _safe_target(root: str, rel: str) -> str:
    rel = rel.replace("\\", "/")
    if rel.startswith("/") or ":" in rel or any(part == ".." for part in rel.split("/")):
        raise ValueError(f"危険なパスを含むので中止しました: {rel}")
    target = os.path.abspath(os.path.join(root, *rel.split("/")))
    root_abs = os.path.abspath(root)
    if not target.startswith(root_abs + os.sep):
        raise ValueError(f"危険なパスを含むので中止しました: {rel}")
    return target


# Modrinth の決まり（.mrpack の仕様）で許されているダウンロード先だけ使う
ALLOWED_HOSTS = {"cdn.modrinth.com", "github.com", "raw.githubusercontent.com", "gitlab.com", "edge.forgecdn.net", "mediafilez.forgecdn.net"}


def url_allowed(url: str) -> bool:
    from urllib.parse import urlparse
    u = urlparse(url)
    return u.scheme == "https" and (u.hostname or "") in ALLOWED_HOSTS


def pack_risks(pack_path: str) -> dict:
    """友達にもらったパックの中の「Modrinth 以外のファイル」を数える（安全のため画面で知らせる）。"""
    info = pack_info(pack_path)
    bad_urls = [f["path"] for f in info["files"] if not any(url_allowed(u) for u in f.get("downloads", []))]
    with zipfile.ZipFile(pack_path) as z:
        extra_code = [n for n in z.namelist() if n.startswith(("overrides/", "client-overrides/", "server-overrides/"))
                      and n.lower().endswith((".jar", ".exe", ".bat", ".cmd", ".ps1", ".dll", ".zip"))]
    return {"bad_urls": bad_urls, "extra_code": extra_code}


def install_pack_files(pack_path: str, target_dir: str, side: str, log=print,
                       mod_list: ModList | None = None, skip: set[str] | None = None):
    """パックの中身を target_dir に入れる。side は "server" か "client"。
    skip は入れないことにしたファイルのパス（「選べる」MODだけ外せる）。"""
    info = pack_info(pack_path)
    skip = {f["path"] for f in info["files"]
            if f["path"] in (skip or set()) and f.get("env", {}).get(side) == "optional"}
    files = info["files"]
    other = "client" if side == "server" else "server"
    total = len(files)
    for i, f in enumerate(files, 1):
        env = f.get("env", {})
        if f["path"] in skip:
            target = _safe_target(target_dir, f["path"])
            if os.path.exists(target):
                os.remove(target)
            continue
        if env.get(side) == "unsupported":
            if mod_list is not None and f["path"].startswith("mods/"):
                mod_list.items.append({
                    "project_id": None, "title": os.path.basename(f["path"]), "path": f["path"],
                    "sha1": f["hashes"].get("sha1"), "sha512": f["hashes"].get("sha512"),
                    "url": f["downloads"][0], "size": f.get("fileSize", 0),
                    "client_side": "required", "server_side": "unsupported",
                    "location": "client_only", "enabled": True})
            continue
        target = _safe_target(target_dir, f["path"])
        log(f"[{i}/{total}] {os.path.basename(f['path'])}")
        if os.path.exists(target) and net.file_hash(target, "sha1") == f["hashes"].get("sha1"):
            pass  # もう同じものがある
        else:
            last = None
            urls = [u for u in f.get("downloads", []) if url_allowed(u)]
            if not urls:
                raise net.NetError(f"{os.path.basename(f['path'])} のダウンロード先が安全な場所ではないので中止しました")
            for url in urls:
                try:
                    net.download(url, target, sha1=f["hashes"].get("sha1"), sha512=f["hashes"].get("sha512"))
                    last = None
                    break
                except net.NetError as e:
                    last = e
            if last:
                raise last
        if mod_list is not None and f["path"].startswith("mods/"):
            mod_list.items.append({
                "project_id": None, "title": os.path.basename(f["path"]), "path": f["path"],
                "sha1": f["hashes"].get("sha1"), "sha512": f["hashes"].get("sha512"),
                "url": f["downloads"][0], "size": f.get("fileSize", 0),
                "client_side": "unsupported" if env.get(other) == "unsupported" and side == "server" else "required",
                "server_side": "required", "location": "server", "enabled": True})
    # overrides
    with zipfile.ZipFile(pack_path) as z:
        for prefix in ("overrides/", f"{side}-overrides/"):
            for info_ in z.infolist():
                if info_.is_dir() or not info_.filename.startswith(prefix):
                    continue
                rel = info_.filename[len(prefix):]
                if not rel:
                    continue
                target = _safe_target(target_dir, rel)
                if rel == "servers.dat" and os.path.exists(target):
                    merge_servers_dat(target, z.read(info_.filename))
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(target, "wb") as out:
                    out.write(z.read(info_.filename))
    if mod_list is not None:
        _enrich_titles(mod_list)
        mod_list.save()
    return info


def merge_servers_dat(path: str, new_data: bytes):
    try:
        with open(path, "rb") as f:
            old = read_servers_dat(f.read())
    except (OSError, struct.error, IndexError, UnicodeDecodeError):
        old = []
    new = read_servers_dat(new_data)
    ips = {ip for _n, ip in new}
    merged = new + [e for e in old if e[1] not in ips]
    with open(path, "wb") as f:
        f.write(make_servers_dat(merged))


def _enrich_titles(ml: ModList):
    """ハッシュから Modrinth の正式名・ID を調べて記録を見やすくする。"""
    recs = [m for m in ml.items if not m.get("project_id") and m.get("sha1")]
    if not recs:
        return
    try:
        found = net.post_json(f"{modrinth.API}/version_files",
                              {"hashes": [m["sha1"] for m in recs], "algorithm": "sha1"})
        projs = modrinth.projects([v["project_id"] for v in found.values()]) if found else {}
    except net.NetError:
        return
    for m in recs:
        v = found.get(m["sha1"])
        p = projs.get(v["project_id"]) if v else None
        if p:
            m.update(project_id=p["id"], version_id=v["id"], title=p["title"], slug=p.get("slug"),
                     version_number=v.get("version_number"),
                     client_side=p.get("client_side", m["client_side"]),
                     server_side=p.get("server_side", m["server_side"]))
