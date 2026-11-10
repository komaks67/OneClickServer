"""MODの「相性（競合）」チェックと、サーバーが止まった原因の推定。

1. MODファイルの中身（説明書きファイル）を読んで調べる
   - 種類（Fabric / Forge / NeoForge）がサーバーと合っているか
   - 同じMODが2つ入っていないか
   - 必要な前提MODがそろっているか
   - 「一緒に使えない」と書かれたMODが入っていないか
   - 遊ぶ人専用のMODがサーバーに入っていないか
2. よく知られている相性の悪い組み合わせ
3. サーバーのログ（記録）から、止まった原因を日本語で説明する
"""
from __future__ import annotations

import io
import json
import os
import re
import zipfile

try:
    import tomllib
except ImportError:  # Python 3.10
    tomllib = None

from .modrinth import ModList

IGNORE_DEPS = {"minecraft", "java", "fabricloader", "fabric-loader", "forge", "neoforge", "javafml",
               "lowcodefml", "quilt_loader", "quilt-loader", "mixinextras"}


# ============================================================ MODファイルを読む

def _read_fabric(data: dict, meta: dict):
    meta["loaders"].add("fabric")
    mid = data.get("id")
    if mid:
        meta["ids"].add(mid)
        meta["main_id"] = meta["main_id"] or mid
        meta["name"] = meta["name"] or data.get("name") or mid
    for p in data.get("provides", []) or []:
        meta["ids"].add(p if isinstance(p, str) else p.get("id", ""))
    for k in (data.get("depends") or {}):
        meta["depends"].add(k)
    for field in ("breaks", "conflicts"):
        for k in (data.get(field) or {}):
            meta["breaks"].add(k)
    if data.get("environment") == "client":
        meta["client_only"] = True


def _read_toml(text: str, meta: dict, loader: str):
    meta["loaders"].add(loader)
    if tomllib is None:
        for m in re.finditer(r'modId\s*=\s*"([^"]+)"', text):
            meta["ids"].add(m.group(1))
        return
    try:
        data = tomllib.loads(text)
    except Exception:  # noqa: BLE001  書き方がおかしいファイルもある
        for m in re.finditer(r'modId\s*=\s*"([^"]+)"', text):
            meta["ids"].add(m.group(1))
        return
    mods = data.get("mods") or []
    own = set()
    for m in mods:
        mid = m.get("modId")
        if mid:
            own.add(mid)
            meta["ids"].add(mid)
            meta["main_id"] = meta["main_id"] or mid
            meta["name"] = meta["name"] or m.get("displayName") or mid
    for deps in (data.get("dependencies") or {}).values():
        if not isinstance(deps, list):
            continue
        for d in deps:
            did = d.get("modId")
            if not did:
                continue
            side = str(d.get("side", "BOTH")).upper()
            typ = str(d.get("type", "")).lower()
            mandatory = d.get("mandatory")
            if typ == "incompatible":
                meta["breaks"].add(did)
            elif (typ == "required" or (not typ and mandatory is True)) and side != "CLIENT":
                meta["depends"].add(did)


def _yaml_list(text: str, key: str) -> list[str]:
    """plugin.yml の「key: [a, b]」や「key:\n  - a」を読む（かんたんな読み方）。"""
    m = re.search(rf"^{key}\s*:\s*\[(.*?)\]", text, re.M | re.S)
    if m:
        return [x.strip().strip("'\"") for x in m.group(1).split(",") if x.strip()]
    m = re.search(rf"^{key}\s*:\s*\n((?:[ \t]+-.*\n?)+)", text, re.M)
    if m:
        return [ln.strip()[1:].strip().strip("'\"") for ln in m.group(1).splitlines() if ln.strip().startswith("-")]
    m = re.search(rf"^{key}\s*:\s*([^\n\[]+)$", text, re.M)
    if m and m.group(1).strip():
        return [m.group(1).strip().strip("'\"")]
    return []


def _read_plugin_yml(text: str, meta: dict):
    meta["loaders"].add("bukkit")
    meta["plugin"] = True
    m = re.search(r"^name\s*:\s*['\"]?([^'\"\n]+)", text, re.M)
    if m:
        name = m.group(1).strip()
        meta["ids"].add(name.lower())
        meta["main_id"] = meta["main_id"] or name.lower()
        meta["name"] = meta["name"] or name
    for d in _yaml_list(text, "depend"):
        meta["depends"].add(d.lower())
    for d in _yaml_list(text, "provides"):
        meta["ids"].add(d.lower())


def read_jar_bytes(data: bytes, meta: dict | None = None, depth=0) -> dict:
    meta = meta or {"ids": set(), "depends": set(), "breaks": set(), "loaders": set(),
                    "client_only": False, "main_id": None, "name": None}
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        meta["broken"] = True
        return meta
    names = set(z.namelist())
    if "fabric.mod.json" in names:
        try:
            _read_fabric(json.loads(z.read("fabric.mod.json").decode("utf-8", "replace")), meta)
        except ValueError:
            pass
        if depth < 2:
            try:
                fj = json.loads(z.read("fabric.mod.json").decode("utf-8", "replace"))
                for j in fj.get("jars", []) or []:
                    f = j.get("file")
                    if f in names:
                        sub = read_jar_bytes(z.read(f), None, depth + 1)
                        meta["ids"] |= sub["ids"]
            except ValueError:
                pass
    if "quilt.mod.json" in names:
        meta["loaders"].add("quilt")
        try:
            q = json.loads(z.read("quilt.mod.json").decode("utf-8", "replace")).get("quilt_loader", {})
            if q.get("id"):
                meta["ids"].add(q["id"])
                meta["main_id"] = meta["main_id"] or q["id"]
        except ValueError:
            pass
    for toml_name, loader in (("META-INF/neoforge.mods.toml", "neoforge"), ("META-INF/mods.toml", "forge")):
        if toml_name in names:
            _read_toml(z.read(toml_name).decode("utf-8", "replace"), meta, loader)
    # 中に入っている MOD（jar-in-jar）も「入っている」とみなす
    if depth < 2:
        for n in names:
            if n.startswith("META-INF/jarjar/") and n.endswith(".jar"):
                sub = read_jar_bytes(z.read(n), None, depth + 1)
                meta["ids"] |= sub["ids"]
    for yml in ("paper-plugin.yml", "plugin.yml"):
        if yml in names:
            try:
                _read_plugin_yml(z.read(yml).decode("utf-8", "replace"), meta)
            except Exception:  # noqa: BLE001
                pass
            break
    if not meta["loaders"] and "mcmod.info" in names:
        meta["loaders"].add("forge")
    return meta


def read_jar(path: str) -> dict:
    with open(path, "rb") as f:
        meta = read_jar_bytes(f.read())
    meta["file"] = os.path.basename(path)
    return meta


# ============================================================ よく知られた相性の悪い組み合わせ

KNOWN = [
    # (グループA, グループB, レベル, 説明)
    ({"optifine"}, {"sodium", "embeddium", "rubidium", "iris", "oculus", "indium", "create", "entityculling"},
     "error", "OptiFine は多くのMODとぶつかってクラッシュの原因になります。代わりに軽量化は Sodium（Fabric）/ "
              "Embeddium（Forge・NeoForge）、影MODは Iris / Oculus を使いましょう。"),
    ({"embeddium"}, {"rubidium"}, "error", "Embeddium と Rubidium は同じ役割（軽量化）です。どちらか1つだけにしてください。"),
    ({"sodium"}, {"embeddium", "rubidium"}, "error", "Sodium と Embeddium / Rubidium は同じ役割です。どちらか1つだけにしてください。"),
    ({"iris"}, {"oculus"}, "error", "Iris と Oculus は同じ役割（影MOD）です。どちらか1つだけにしてください。"),
    ({"starlight"}, {"phosphor"}, "error", "Starlight と Phosphor は同じ役割（光の計算）です。どちらか1つだけにしてください。"),
    ({"radium"}, {"canary"}, "error", "Radium と Canary は同じ役割（内部処理の軽量化）です。どちらか1つだけにしてください。"),
    ({"lithium"}, {"radium", "canary"}, "warn", "Lithium と Radium / Canary は同じ役割です。どちらか1つにするのがおすすめです。"),
    ({"jei"}, {"roughlyenoughitems", "rei"}, "info", "JEI と REI は同じ役割（レシピ表示）です。画面がごちゃつく場合は、どちらか1つにしてください。"),
    ({"journeymap"}, {"xaerominimap"}, "info", "JourneyMap と Xaero's Minimap はどちらもミニマップです。画面に2つ出る場合は、どちらか1つにしてください。"),
]


# MODの説明書きには書かれていないのに、実際には必要になる前提MOD（作者の注意書きなどで知られているもの）
KNOWN_NEEDS = [
    {"when": {"distanthorizons"}, "also": {"sodium"}, "loader": "fabric", "need": "indium", "title": "Indium",
     "by": "Distant Horizons",
     "reason": "Distant Horizons を Sodium と一緒に使うときは、Indium が必要です（ないと、ゲームが始まる前に止まります）。"},
]


def known_needs(have: set[str], loader: str | None) -> list[dict]:
    """have = 入っている（入れる予定の）MODの名前を _norm したもの。足りない前提MODの一覧を返す。"""
    out = []
    for n in KNOWN_NEEDS:
        if n["loader"] and loader != n["loader"]:
            continue
        if (n["when"] & have) and (n["also"] & have) and n["need"] not in have:
            out.append(n)
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _keys_for(meta: dict, rec: dict | None) -> set[str]:
    keys = {_norm(i) for i in meta.get("ids", set())}
    if meta.get("main_id"):
        keys.add(_norm(meta["main_id"]))
    if rec:
        keys |= {_norm(rec.get("slug") or ""), _norm(rec.get("title") or "")}
    if "optifine" in meta.get("file", "").lower():
        keys.add("optifine")
    keys.discard("")
    return keys


def known_conflicts_for(keys_a: set[str], other: dict[str, set[str]]) -> list[tuple[str, str, str]]:
    """keys_a のMODと、other（名前→キー）の中でぶつかるものを返す。"""
    out = []
    for ga, gb, lv, msg in KNOWN:
        for x, y in ((ga, gb), (gb, ga)):
            if keys_a & {_norm(k) for k in x}:
                for name, keys in other.items():
                    if keys & {_norm(k) for k in y}:
                        out.append((name, lv, msg))
    return out


# ============================================================ まとめてチェック

def _issue(level, title, detail, fix=None, action=None, files=None):
    return {"level": level, "title": title, "detail": detail, "fix": fix, "action": action, "files": files or []}


def check_server(prof) -> dict:
    loader = prof.get("loader")
    mc = prof.get("mc") or ""
    ml = ModList(prof.dir)
    ml.sync_with_folder(lambda *_: None)
    by_path = {m["path"]: m for m in ml.items}
    mods_dir = prof.mods_dir
    fd = os.path.basename(mods_dir)
    metas = []
    for fn in sorted(os.listdir(mods_dir)) if os.path.isdir(mods_dir) else []:
        if not fn.endswith(".jar"):
            continue
        meta = read_jar(os.path.join(mods_dir, fn))
        meta["rec"] = by_path.get(f"{fd}/{fn}")
        rec = meta["rec"] or {}
        meta["display"] = (rec.get("title") if rec.get("project_id") else None) or meta.get("name") or rec.get("title") or fn
        metas.append(meta)
    issues = []
    present: set[str] = set()
    for m in metas:
        present |= m["ids"]
    if loader == "fabric":
        present |= {"fabric", "fabricloader"}
    if "klf" in present:  # Kotlin for Forge の別名
        present |= {"kotlinforforge", "kotlinlangforge"}
    for rec in ml.items:  # 「遊ぶ人だけ」に入っているMODも、入っているものとして数える
        if rec.get("slug"):
            present.add(rec["slug"])

    newer_neo = loader == "neoforge" and not mc.startswith(("1.20.2", "1.20.3", "1.20.4"))
    for m in metas:
        disp, fn = m["display"], m["file"]
        if m.get("broken"):
            issues.append(_issue("error", f"{disp} のファイルが壊れています",
                                 "ダウンロードの途中で失敗した可能性があります。",
                                 "一度外して、もう一度入れてください。", {"type": "disable", "path": f"{fd}/{fn}"}, [fn]))
            continue
        lds = m["loaders"]
        if loader in ("paper", "purpur"):
            if lds and "bukkit" not in lds:
                issues.append(_issue("error", f"{disp} はMODです（プラグインではありません）",
                                     f"{prof.loader_label()} のサーバーではMODは動きません。MODを使うなら Fabric / NeoForge / Forge でサーバーを作ります。",
                                     "OFFにしてください。", {"type": "disable", "path": f"{fd}/{fn}"}, [fn]))
                continue
            missing = sorted(d for d in m["depends"] if d not in present)
            for d in missing:
                issues.append(_issue("error", f"{disp} に必要な前提プラグイン「{d}」がありません",
                                     "前提プラグインがないと、このプラグインは動きません。",
                                     "下のボタンで前提プラグインを探して入れられます。",
                                     {"type": "install_dep", "id": d}, [fn]))
            continue
        wrong = False
        if lds and loader in ("fabric", "neoforge", "forge"):
            if loader == "fabric" and not (lds & {"fabric"}):
                wrong = True
            elif loader == "forge" and not (lds & {"forge"}):
                wrong = True
            elif loader == "neoforge" and not (lds & {"neoforge"}) and (newer_neo or not (lds & {"forge"})):
                wrong = True
        if wrong:
            need = "・".join(sorted({"fabric": "Fabric", "forge": "Forge", "neoforge": "NeoForge",
                                    "quilt": "Quilt"}.get(x, x) for x in lds))
            issues.append(_issue("error", f"{disp} は {need} 用です",
                                 f"このサーバーは {prof.loader_label()} なので、このMODは動きません（起動しない原因になります）。",
                                 f"OFFにして、{prof.loader_label().split()[0]} 用のものを入れ直してください。",
                                 {"type": "disable", "path": f"{fd}/{fn}"}, [fn]))
            continue
        if m["client_only"] or ((m["rec"] or {}).get("server_side") == "unsupported"):
            issues.append(_issue("error", f"{disp} は遊ぶ人のPC専用のMODです",
                                 "サーバーに入れると、起動しない・クラッシュする原因になります。",
                                 "サーバーではOFFにしてください（友達用パックには入ります）。",
                                 {"type": "disable", "path": f"{fd}/{fn}"}, [fn]))
        rec = m["rec"] or {}
        gv = rec.get("game_versions")
        if gv and mc and mc not in gv:
            issues.append(_issue("warn", f"{disp} は マイクラ {mc} 用ではありません",
                                 f"このMODのバージョンは {', '.join(gv[-4:])} 用です。",
                                 "「⟳ 更新を探す」で合うバージョンに入れ替えてください。", None, [fn]))
        missing = sorted(d for d in m["depends"] - IGNORE_DEPS if d not in present)
        for d in missing:
            issues.append(_issue("error", f"{disp} に必要な前提MOD「{d}」がありません",
                                 "前提MODがないと、サーバーが起動しません。",
                                 "下のボタンで前提MODを探して入れられます。",
                                 {"type": "install_dep", "id": d}, [fn]))
        for b in sorted(m["breaks"]):
            hit = [o for o in metas if o is not m and b in o["ids"]]
            for o in hit:
                issues.append(_issue("error", f"{disp} と {o['display']} は一緒に使えません",
                                     f"{disp} の作者が「{o['display']} とは一緒に使えない」と書いています。",
                                     "どちらか一方をOFFにしてください。",
                                     {"type": "disable", "path": f"{fd}/{o['file']}"}, [fn, o["file"]]))

    # 説明書きには出ない前提MOD
    have = set()
    for rec in ml.items:
        have |= {_norm(rec.get("slug") or ""), _norm(rec.get("title") or "")}
    for m in metas:
        have |= {_norm(i) for i in m["ids"]}
    for n in known_needs(have, loader):
        issues.append(_issue("error", f"{n['by']} に必要な前提MOD「{n['title']}」がありません", n["reason"],
                             "下のボタンで前提MODを入れられます。", {"type": "install_dep", "id": n["need"]}, []))

    # 同じMODが2つ
    seen: dict[str, list] = {}
    for m in metas:
        if m.get("main_id"):
            seen.setdefault(m["main_id"], []).append(m)
    for lst in seen.values():
        if len(lst) > 1:
            files = [x["file"] for x in lst]
            issues.append(_issue("error", f"同じMOD（{lst[0]['display']}）が {len(lst)} 個入っています",
                                 "ファイル: " + "、".join(files),
                                 "新しい方だけ残して、古い方をOFFにしてください。",
                                 {"type": "disable", "path": f"{fd}/{sorted(files)[0]}"}, files))

    # よく知られた相性
    keymap = {m["display"]: _keys_for(m, m["rec"]) for m in metas}
    done = set()
    for m in metas:
        for other, lv, msg in known_conflicts_for(keymap[m["display"]], {k: v for k, v in keymap.items()
                                                                         if k != m["display"]}):
            pair = tuple(sorted((m["display"], other)))
            if pair in done:
                continue
            done.add(pair)
            issues.append(_issue(lv, f"{pair[0]} と {pair[1]}", msg, None, None, []))

    # Modrinth に「一緒に使えない」と書かれているもの
    pid_to = {m.get("project_id"): m for m in ml.items if m.get("project_id")}
    for m in ml.items:
        for bad in m.get("incompatible", []) or []:
            if bad in pid_to:
                pair = tuple(sorted((m["title"], pid_to[bad]["title"])))
                if pair in done:
                    continue
                done.add(pair)
                issues.append(_issue("error", f"{pair[0]} と {pair[1]} は一緒に使えません",
                                     "Modrinth に「一緒に使えない」と書かれています。",
                                     "どちらか一方をOFFにしてください。", None, []))

    ignored = set(prof.get("ignored_deps") or [])
    if ignored:  # 「この警告は出さない」にした前提MODは、一覧から外す
        issues = [i for i in issues if not (i.get("action") and i["action"].get("type") == "install_dep"
                                            and i["action"].get("id") in ignored)]
    order = {"error": 0, "warn": 1, "info": 2}
    issues.sort(key=lambda i: order[i["level"]])
    return {"issues": issues, "checked": len(metas)}


# ============================================================ ログから原因を推定

LOG_RULES = [
    # ---- ワールドの鍵（前のサーバーが残っているとき） ----
    (re.compile(r"別のプロセスがファイルの一部をロックしています|another process has locked a portion of the file|"
                r"session\.lock[^\n]*(?:locked|already|使用中)|Failed to lock[^\n]*session\.lock|"
                r"The process cannot access the file because another process has locked", re.I),
     lambda m: _issue("error", "ワールドの鍵を、ほかのもの（前のサーバーなど）が使っています",
                      "ワールドのファイル（session.lock）が使用中のため、起動できませんでした。"
                      "前に動かしたサーバー（java）が、裏で残っていることが多いです。",
                      "「残っているjavaを確認する」を押してください。見つからないときは、数十秒待つか、PCを再起動してください。",
                      {"type": "kill_java", "pids": []})),
    # ---- 日本語表示のFabric：前提MODがない ----
    (re.compile(r"Mod '([^']+)' \(([^)]+)\) \S+ は (?:いずれかのバージョン|バージョン \S+(?: またはそれ以上)?) の "
                r"([\w\-]+) を必要としますが、そのModがありません"),
     lambda m: _issue("error", f"前提MOD「{m.group(3)}」がありません",
                      f"「{m.group(1)}」など、入っているMODが「{m.group(3)}」を必要としています。入っていないので起動できません。",
                      "前提MODを入れてください。友達のマイクラにも、同じ前提MODが必要です。",
                      {"type": "install_dep", "id": m.group(3)})),
    (re.compile(r"Mod '([^']+)' \(([^)]+)\) [^ ]+ requires (?:any version|version [^ ]+(?: or later)?)"
                r"(?: of(?: mod)?)? '?([^',]+)'? ?\(?([^),]*)\)?,? which is missing", re.I),
     lambda m: _issue("error", f"「{m.group(1)}」に必要な前提MOD「{m.group(3)}」がありません",
                      "前提MODがないためサーバーが起動できませんでした。",
                      "前提MODを入れてください。", {"type": "install_dep", "id": (m.group(4) or m.group(3)).strip()})),
    (re.compile(r"Mod ID: '([^']+)', Requested by: '([^']+)', Expected range: '([^']*)', Actual version: '\[?MISSING\]?'", re.I),
     lambda m: _issue("error", f"「{m.group(2)}」に必要な前提MOD「{m.group(1)}」がありません",
                      "前提MODがないためサーバーが起動できませんでした。",
                      "前提MODを入れてください。", {"type": "install_dep", "id": m.group(1)})),
    (re.compile(r"Mod ID: '([^']+)', Requested by: '([^']+)', Expected range: '([^']*)', Actual version: '([^']+)'", re.I),
     lambda m: _issue("error", f"「{m.group(1)}」のバージョンが合いません",
                      f"「{m.group(2)}」は {m.group(1)} の {m.group(3)} を必要としていますが、入っているのは {m.group(4)} です。",
                      "「⟳ 更新を探す」で更新するか、合うバージョンを入れ直してください。")),
    (re.compile(r"Mod '([^']+)' \(([^)]+)\) [^ ]+ is incompatible with (?:any version of )?(?:mod )?'?([^',(]+)", re.I),
     lambda m: _issue("error", f"「{m.group(1)}」と「{m.group(3).strip()}」は一緒に使えません",
                      "MODの作者が「一緒に使えない」と決めている組み合わせです。", "どちらか一方をOFFにしてください。")),
    (re.compile(r"(?:Found )?duplicate mods?|Duplicate mods? found|Mod ID '([^']+)' from mod files: ([^\n]+)", re.I),
     lambda m: _issue("error", "同じMODが2つ以上入っています",
                      (m.group(2) or "").strip() or "古いバージョンと新しいバージョンが両方入っている可能性があります。",
                      "「相性チェック」で重なっているMODを見つけて、片方をOFFにしてください。")),
    (re.compile(r"Attempted to load class (\S+) for invalid dist DEDICATED_SERVER", re.I),
     lambda m: _issue("error", "遊ぶ人のPC専用のMODがサーバーに入っています",
                      f"サーバーでは使えない部品（{m.group(1).split('/')[0]}…）を読み込もうとして止まりました。",
                      "「相性チェック」を実行して、遊ぶ人専用のMODをOFFにしてください。")),
    (re.compile(r"(?:Cannot load class|ClassNotFoundException|NoClassDefFoundError).*net/minecraft/client", re.I),
     lambda m: _issue("error", "遊ぶ人のPC専用のMODがサーバーに入っています",
                      "画面を描く部品はサーバーにはないので、読み込めずに止まりました。",
                      "「相性チェック」を実行して、遊ぶ人専用のMODをOFFにしてください。")),
    (re.compile(r"Mixin apply for mod ([\w\-]+) failed|Mixin (?:apply|transformation)[^\n]*?failed[^\n]*?(?:from mod|mod) ['(]?([\w\-]+)", re.I),
     lambda m: _issue("error", f"MOD「{m.group(1) or m.group(2)}」がほかのMODとぶつかっています",
                      "MODがゲームの中身を書きかえようとして失敗しました（相性が悪い組み合わせか、バージョン違い）。",
                      f"「{m.group(1) or m.group(2)}」を一度OFFにして起動してみてください。「原因のMODを探す」も使えます。")),
    (re.compile(r"has been compiled by a more recent version of the Java Runtime \(class file version (\d+)", re.I),
     lambda m: _issue("error", "Java のバージョンが古いです",
                      f"Java {int(m.group(1)) - 44} 以上が必要なMODが入っています。",
                      "このソフトが自動で Java を用意します。マイクラのバージョンに合ったMODか確認してください。")),
    (re.compile(r"Unsupported class file major version (\d+)", re.I),
     lambda m: _issue("error", "Java のバージョンが合いません",
                      f"Java {int(m.group(1)) - 44} 用に作られた部品が読み込めませんでした。",
                      "マイクラのバージョンに合ったMODか確認してください。")),
    (re.compile(r"java\.lang\.OutOfMemoryError", re.I),
     lambda m: _issue("error", "メモリが足りません",
                      "サーバーに割り当てたメモリを使い切って止まりました。",
                      "「ホーム」のメモリを 1〜2GB 増やしてください（PCのメモリが足りる範囲で）。")),
    (re.compile(r"FAILED TO BIND TO PORT|Address already in use", re.I),
     lambda m: _issue("error", "ポート番号がほかで使われています",
                      "同じポート番号のサーバー（またはほかのソフト）がすでに動いています。",
                      "動いている別のサーバーを止めるか、「設定」でポート番号を変えてください（例: 25566）。")),
    (re.compile(r"You need to agree to the EULA", re.I),
     lambda m: _issue("warn", "利用規約（EULA）に同意していません", "最初の起動で同意が必要です。",
                      "「▶ 起動」を押すと同意の確認が出ます。")),
    # ---- ここから：さらに詳しい原因（クラッシュレポートの中身） ----
    (re.compile(r"Mod '([^']+)' \(([^)]+)\) \S+ requires [^\n]*?of '?(Minecraft|Java)'? \(?(?:minecraft|java)\)?,? but only the wrong version is present: ([^!\n]+)", re.I),
     lambda m: _issue("error", f"「{m.group(1)}」はこのマイクラのバージョン用ではありません",
                      f"このMODは別の {m.group(3)} のバージョン用です（今は {m.group(4).strip()}）。",
                      "「⟳ 更新を探す」で合うバージョンにするか、OFFにしてください。")),
    (re.compile(r"Mod '([^']+)' \(([^)]+)\) \S+ requires [^\n]*?of '?(?!Minecraft|Java)([^',(]+?)'? \((?!minecraft\b|java\b)([\w\-]+)\),? but only the wrong version is present: ([^!\n]+)", re.I),
     lambda m: _issue("error", f"「{m.group(1)}」が使う「{m.group(3)}」のバージョンが合いません",
                      f"入っている {m.group(3)} は {m.group(5).strip()} ですが、別のバージョンが必要です。",
                      f"「{m.group(3)}」を更新してください（「⟳ 更新を探す」）。", {"type": "install_dep", "id": m.group(4)})),
    (re.compile(r"Suspected Mods?:\s*\n?\s*([^\n(]+?)\s*\(([\w\-]+)\)", re.I),
     lambda m: _issue("error", f"原因は「{m.group(1).strip()}」の可能性が高いです",
                      "マイクラのクラッシュレポートに「あやしいMOD」として書かれています。",
                      f"「{m.group(1).strip()}」を更新するか、一度OFFにして起動してみてください。")),
    (re.compile(r"-- Entity being ticked --.*?Entity Type: ([\w:.]+).*?Entity's Exact location: ([\-\d.]+), ([\-\d.]+), ([\-\d.]+)", re.I | re.S),
     lambda m: _issue("error", f"生き物（{m.group(1)}）の処理でサーバーが落ちました",
                      f"場所: X={float(m.group(2)):.0f} Y={float(m.group(3)):.0f} Z={float(m.group(4)):.0f} にいる {m.group(1)} が壊れているか、MODの不具合です。",
                      "その場所の近くに行かないようにするか、一つ前のバックアップに戻してください。MODが原因なら更新でなおることがあります。")),
    (re.compile(r"-- Block entity being ticked --.*?Name: ([\w:./]+).*?Block location: World: \((-?\d+),\s*(-?\d+),\s*(-?\d+)\)", re.I | re.S),
     lambda m: _issue("error", f"ブロック（{m.group(1)}）の処理でサーバーが落ちました",
                      f"場所: X={m.group(2)} Y={m.group(3)} Z={m.group(4)} の機械・ブロックが壊れているか、MODの不具合です。",
                      "一つ前のバックアップに戻すか、そのブロックのMODを更新してください。")),
    (re.compile(r"A single server tick took ([\d.]+) seconds|Considering it to be crashed, server will forcibly shutdown", re.I),
     lambda m: _issue("error", "サーバーが固まって止まりました（応答なし）",
                      "1回の処理に時間がかかりすぎたので、マイクラが自分でサーバーを止めました。"
                      "大量の生き物・アイテム・機械、地形の生成、MODの不具合が原因になります。",
                      "「重さ・最適化」タブの「重さの原因探し」と「ワンクリック最適化」を試してください。")),
    (re.compile(r"(?:Couldn't load chunk|Failed to read chunk|Chunk file at \[[^\]]+\] is in the wrong location|"
                r"Exception reading [^\n]*\.mca|Failed to load level|Exception reading [^\n]*level\.dat|"
                r"Corrupt(?:ed)? chunk|RegionFileStorage[^\n]*(?:Exception|Error))", re.I),
     lambda m: _issue("error", "ワールドのデータが壊れている可能性があります",
                      "ワールドの一部（チャンク）を読み込めませんでした。急に電源が切れた・PCが固まったときなどに起きます。",
                      "「バックアップ」タブの「ワンクリック巻き戻し」で、壊れる前に戻してください。")),
    (re.compile(r"Could not reserve enough space for (\d+)KB object heap|Invalid maximum heap size|Initial heap size set to a larger value", re.I),
     lambda m: _issue("error", "メモリの設定が大きすぎます",
                      "Java がメモリを確保できませんでした（PCのメモリより多い・古い32ビットの Java など）。",
                      "「設定」→「メモリ・そのほか」でメモリを減らしてください。")),
    (re.compile(r"Error: Unable to access jarfile (\S+)", re.I),
     lambda m: _issue("error", "サーバー本体のファイルが見つかりません",
                      f"{m.group(1)} がありません（消えた・別の場所に移した）。",
                      "「自動化・バージョン」で土台を入れ直すか、サーバーを作り直してください。")),
    (re.compile(r"Could not load '(?:plugins[/\\])?([^']+\.jar)'[^\n]*\n[^\n]*?(?:Unknown/missing dependency plugins|UnknownDependencyException)[^\[]*\[([^\]]+)\]", re.I),
     lambda m: _issue("error", f"プラグイン「{m.group(1)}」に必要な前提プラグイン（{m.group(2)}）がありません",
                      "前提プラグインがないので、このプラグインは読み込まれませんでした。",
                      "前提プラグインを入れてください。", {"type": "install_dep", "id": m.group(2).split(",")[0].strip()})),
    (re.compile(r"Unsupported API version (\S+)", re.I),
     lambda m: _issue("error", "サーバーより新しいバージョン用のプラグインが入っています",
                      f"プラグインが求めるバージョン（{m.group(1)}）にサーバーが対応していません。",
                      "プラグインの古い版を入れるか、サーバーのバージョンを上げてください。")),
    (re.compile(r"Invalid plugin\.yml|InvalidDescriptionException|Could not load '(?:plugins[/\\])?([^']+\.jar)' in folder", re.I),
     lambda m: _issue("error", f"読み込めないプラグインがあります{('（' + m.group(1) + '）') if m.lastindex and m.group(1) else ''}",
                      "プラグインではないファイル（MODなど）か、壊れたファイルです。",
                      "「相性チェック」で見つけて、OFFにしてください。")),
    (re.compile(r"java\.lang\.(NoSuchMethodError|NoSuchFieldError|AbstractMethodError|IncompatibleClassChangeError)[:\s]*'?([\w$./]+)?", re.I),
     lambda m: _issue("error", "MODどうしのバージョンが合っていません",
                      f"あるMODが、ほかのMODの「ない部品」を使おうとしました（{m.group(1)}）。前提MODだけ古い・新しいときによく起きます。",
                      "「⟳ 更新を探す」で全部のMODを新しくするか、「原因のMODを探す」を使ってください。")),
    (re.compile(r"(?:Failed loading config file|Failed to load config|Error loading config|ParsingException|Configuration file [^\n]*? is not correct)[^\n]*?([\w\-./\\]+\.(?:toml|json5?|cfg|yml|yaml|properties))", re.I),
     lambda m: _issue("warn", f"設定ファイル（{os.path.basename(m.group(1))}）の書き方がおかしいです",
                      "手で書きかえた・保存の途中で止まったなどで、設定ファイルが読めませんでした。",
                      "そのファイルを消すと、次の起動で最初の設定が作り直されます（MODを外すときの「設定ファイルも消す」でも消せます）。")),
    (re.compile(r"java\.lang\.StackOverflowError", re.I),
     lambda m: _issue("error", "MODの処理が止まらなくなりました（StackOverflow）",
                      "MODどうしが呼び合い続けて止まりました。相性が悪い組み合わせのことが多いです。",
                      "「起動テスト・相性」タブの「原因のMODを探す」を使ってください。")),
    (re.compile(r"Mixin \[?[^\n\]]*?\.mixins?\.json[^\n\]]*? from mod ([\w\-]+)\]", re.I),
     lambda m: _issue("warn", f"MOD「{m.group(1)}」が関係していそうです",
                      "エラーの記録に、このMODの名前が出ています。",
                      f"「{m.group(1)}」を更新するか、一度OFFにして起動してみてください。")),
    (re.compile(r"Incompatible mods? (?:found|set)!?", re.I),
     lambda m: _issue("error", "相性の悪いMODの組み合わせがあります",
                      "MODの組み合わせが合わないため起動できませんでした。",
                      "「相性チェック」を実行して原因のMODを探してください。")),
    (re.compile(r"Failed to (?:load|create) mod[^\n]*?['\"]?([\w\-]+)['\"]?", re.I),
     lambda m: _issue("error", f"MOD「{m.group(1)}」の読み込みに失敗しました",
                      "このMODが壊れているか、バージョンが合っていない可能性があります。",
                      f"「{m.group(1)}」を一度OFFにして起動してみてください。")),
]


def _tail(path: str, max_bytes=600_000) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


DESCRIPTIONS = {
    "Exception in server tick loop": "サーバーの処理中にエラーが起きました",
    "Watching Server": "サーバーが固まりました（応答なし）",
    "Ticking entity": "生き物の処理でエラーが起きました",
    "Ticking block entity": "ブロック（機械など）の処理でエラーが起きました",
    "Mod loading error has occurred": "MODの読み込み中にエラーが起きました",
    "Exception generating new chunk": "新しい地形を作る途中でエラーが起きました",
    "Exception while ticking": "処理の途中でエラーが起きました",
    "Initializing game": "起動の途中でエラーが起きました",
    "Loading NBT data": "ワールドのデータを読む途中でエラーが起きました",
}


def describe_crash(text: str) -> str | None:
    m = re.search(r"^Description: (.+)$", text, re.M)
    if not m:
        return None
    d = m.group(1).strip()
    return DESCRIPTIONS.get(d, d)


def analyze_text(text: str) -> list[dict]:
    out, seen = [], set()
    for rx, make in LOG_RULES:
        for m in rx.finditer(text):
            try:
                it = make(m)
            except (ValueError, IndexError):
                continue
            if it["title"] in seen:
                continue
            seen.add(it["title"])
            out.append(it)
            if len(out) >= 12:
                return out
    return out


def analyze_logs(prof, since: float | None = None, extra_lines: list[str] | None = None) -> dict:
    """最新のクラッシュレポートとログを読んで、原因を推定する。"""
    texts = []
    crash_dir = os.path.join(prof.dir, "crash-reports")
    crash_file = None
    if os.path.isdir(crash_dir):
        reports = sorted((os.path.join(crash_dir, f) for f in os.listdir(crash_dir)), key=os.path.getmtime)
        if reports and (since is None or os.path.getmtime(reports[-1]) >= since - 5):
            crash_file = reports[-1]
            texts.append(_tail(crash_file, 200_000))
    latest = os.path.join(prof.dir, "logs", "latest.log")
    if os.path.exists(latest):
        texts.append(_tail(latest))
    if extra_lines:
        texts.append("\n".join(extra_lines[-800:]))
    joined = "\n".join(texts)
    issues = analyze_text(joined)
    desc = describe_crash(joined)
    if not issues and texts:
        issues.append(_issue("info", "はっきりした原因は見つかりませんでした",
                             "ログに分かりやすいエラーが見つかりませんでした。",
                             "最後に入れたMODをOFFにして起動してみる → 起動したらそのMODが原因です。"
                             "「相性チェック」も試してください。"))
    return {"issues": issues, "crash_report": os.path.basename(crash_file) if crash_file else None,
            "description": desc}
