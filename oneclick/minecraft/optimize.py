"""サーバーを軽くする：ワンクリック最適化・重さの原因さがし・お掃除。

- plan()   … PCの性能・MODの数・人数から「変えたほうがいい設定」の一覧を作る（まだ変えない）
- apply()  … 選んだものだけ変える（変える前の値は記録しておき、元にもどせる）
- scan()   … 動いているサーバーで、アイテム・生き物がどこにどれだけたまっているかを数える
"""
from __future__ import annotations

import os
import re
import time

from . import loaders, versions
from .paths import load_json, save_json

UNDO_FILE = "mcmh-optimize-undo.json"


# ============================================================ かんたんな YAML の書きかえ

def yaml_get(text: str, dotted: str):
    """「a.b.c」の値（文字）を返す。なければ None。"""
    found = _find(text, dotted)
    return found[2] if found else None


def yaml_set(text: str, dotted: str, value) -> tuple[str, bool]:
    """すでにある「a.b.c: 値」の値だけを書きかえる（コメントや並びはそのまま）。
    戻り値: (新しい文, 書きかえたか)"""
    found = _find(text, dotted)
    if not found:
        return text, False
    lines = text.split("\n")
    i, prefix, _old, suffix = found[0], found[1], found[2], found[3]
    lines[i] = prefix + _yaml_value(value) + suffix
    return "\n".join(lines), True


def _yaml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _find(text: str, dotted: str):
    want = dotted.split(".")
    stack: list[tuple[int, str]] = []
    for i, line in enumerate(text.split("\n")):
        if not line.strip() or line.lstrip().startswith("#") or line.lstrip().startswith("- "):
            continue
        m = re.match(r"^(\s*)(['\"]?)([^'\":#]+?)\2\s*:(\s*)(.*)$", line)
        if not m:
            continue
        indent = len(m.group(1))
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, m.group(3).strip()))
        path = [k for _, k in stack]
        if path == want:
            rest = m.group(5)
            vm = re.match(r"^([^#]*?)(\s+#.*)?$", rest)
            val = (vm.group(1) if vm else rest).strip()
            if val == "":
                return None  # 下に続く「まとまり」なので値ではない
            prefix = line[: len(line) - len(rest)]
            suffix = (vm.group(2) or "") if vm else ""
            return i, prefix, val, suffix
    return None


# ============================================================ 何を変えるか

def _num(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def targets(total_ram_mb: int, cpus: int, mods: int, players: int, loader: str) -> dict:
    """PC・MOD・人数から、ちょうどいい値を決める。"""
    view = 10
    if mods > 150:
        view = 6
    elif mods > 60:
        view = 7
    elif mods > 20 or loader in loaders.MOD_LOADERS:
        view = 8
    if cpus and cpus <= 4:
        view -= 1
    if players >= 15:
        view -= 1
    view = max(5, view)
    sim = max(4, min(view, 6 if mods > 60 or (cpus and cpus <= 4) else 8))
    return {"view": view, "sim": sim}


# まだ一度も起動していなくて server.properties に無いときの、本体の決まりの値
VANILLA_DEFAULTS = {"view-distance": "10", "simulation-distance": "10", "entity-broadcast-range-percentage": "100",
                    "network-compression-threshold": "256", "sync-chunk-writes": "true", "pause-when-empty-seconds": "60"}


def plan(prof, total_ram_mb: int, cpus: int, mods: int, mem_rec: int) -> list[dict]:
    loader = prof.get("loader") or "fabric"
    mc = prof.get("mc") or ""
    props = prof.props()
    players = _num(props.get("max-players"), 10)
    t = targets(total_ram_mb, cpus, mods, players, loader)
    items: list[dict] = []

    def prop(key, new, label, why, risky=False):
        cur = props.get(key, VANILLA_DEFAULTS.get(key))
        if cur is None or str(cur) == str(new):
            return
        if key in ("view-distance", "simulation-distance") and _num(cur, 99) <= _num(new):
            return  # すでに小さいなら、大きくはしない
        items.append({"id": f"prop:{key}", "kind": "prop", "key": key, "label": label, "current": cur,
                      "new": str(new), "why": why, "risky": risky, "file": "server.properties"})

    prop("view-distance", t["view"], "見える距離",
         f"MOD {mods}個・CPU {cpus}コア・最大{players}人なら {t['view']} がちょうどいい重さです。大きいほど重くなります。")
    prop("simulation-distance", t["sim"], "動く範囲",
         "生き物・作物・機械が動く範囲です。小さくするとサーバーがかなり軽くなります。")
    prop("entity-broadcast-range-percentage", 75, "生き物を送る距離",
         "遠くの生き物の情報を送る量を減らして、通信とサーバーを軽くします（遊び方はほぼ変わりません）。")
    prop("network-compression-threshold", 256, "通信の圧縮", "標準の値にもどします（小さすぎるとCPUを使いすぎます）。")
    prop("sync-chunk-writes", "false", "ワールドの書きこみ方",
         "保存のたびに待たなくなるので、カクつきが減ります。停電などで急にPCが落ちると、最後の数秒が保存されないことがあります。",
         risky=True)
    if versions.at_least(mc, (1, 21, 2)):
        prop("pause-when-empty-seconds", 60, "だれもいないとき止める",
             "だれもいない時間が1分続いたら、世界の時間を止めてCPUを休ませます。")

    mem = _num(prof.get("memory_mb"), 4096)
    if mem_rec and mem + 512 < mem_rec:
        items.append({"id": "memory", "kind": "memory", "label": "サーバーのメモリ", "current": f"{mem} MB",
                      "new": f"{mem_rec} MB", "value": mem_rec,
                      "why": "MODの数に対してメモリが足りません。足りないと、こまかく止まる（カクつく）原因になります。",
                      "risky": False, "file": "このソフトの設定"})
    elif mem_rec and mem > mem_rec * 2 and mem > 8192:
        items.append({"id": "memory", "kind": "memory", "label": "サーバーのメモリ", "current": f"{mem} MB",
                      "new": f"{mem_rec} MB", "value": mem_rec,
                      "why": "メモリが多すぎると、ときどき長く止まる（掃除の時間が長くなる）ことがあります。",
                      "risky": False, "file": "このソフトの設定"})

    if loader in loaders.PLUGIN_LOADERS:
        items += _paper_items(prof)
    return items


# Paper / Purpur の細かい設定（定番の軽量化）
PAPER_WORLD = [
    ("environment.optimize-explosions", True, "爆発の計算を速くする", "TNT や クリーパーの爆発の計算を速くします。見た目は同じです。"),
    ("collisions.max-entity-collisions", 2, "生き物どうしのぶつかり判定", "1匹が同時にぶつかる相手の数を減らします。牧場がたくさんあると効果大。"),
    ("misc.redstone-implementation", "ALTERNATE_CURRENT", "レッドストーンの計算方法", "レッドストーン回路をとても速く計算する方法にします。普通の回路はそのまま動きます。"),
    ("chunks.max-auto-save-chunks-per-tick", 8, "自動保存のペース", "自動保存を少しずつ行って、保存のたびのカクつきを減らします。"),
    ("chunks.prevent-moving-into-unloaded-chunks", True, "読みこまれていない場所へ入らない", "速く飛びすぎて、まだ読みこまれていない場所に入るのを防ぎます（重さの原因になる）。"),
    ("tick-rates.mob-spawner", 2, "スポナーの処理", "スポナーの処理を2回に1回にします。湧く量はほとんど変わりません。"),
    ("hopper.disable-move-event", True, "ホッパーの処理", "ホッパーの中の計算を軽くします（プラグインがホッパーを見張っていないときだけ）。"),
]
PAPER_ENTITY_LIMITS = [("experience_orb", 16), ("arrow", 16), ("snowball", 8), ("ender_pearl", 8), ("fireball", 8), ("small_fireball", 8)]
SPIGOT = [
    ("world-settings.default.merge-radius.item", 3.5, "落ちたアイテムをまとめる距離", "近くに落ちた同じアイテムを1つにまとめて、数を減らします。"),
    ("world-settings.default.merge-radius.exp", 4.0, "経験値をまとめる距離", "近くの経験値を1つにまとめて、数を減らします。"),
]
BUKKIT = [
    ("ticks-per.monster-spawns", 2, "モンスターが湧く間かく", "モンスターが湧く計算を2回に1回にします。湧く量はほとんど変わりません。"),
]


def _paper_items(prof) -> list[dict]:
    out = []
    files = [
        (os.path.join("config", "paper-world-defaults.yml"), PAPER_WORLD),
        ("spigot.yml", SPIGOT),
        ("bukkit.yml", BUKKIT),
    ]
    for rel, entries in files:
        path = os.path.join(prof.dir, rel)
        if not os.path.exists(path):
            continue
        text = open(path, encoding="utf-8", errors="replace").read()
        for key, new, label, why in entries:
            cur = yaml_get(text, key)
            if cur is None or cur.strip("'\"").lower() == _yaml_value(new).lower():
                continue
            out.append({"id": f"yaml:{rel}:{key}", "kind": "yaml", "path": rel, "key": key, "label": label,
                        "current": cur, "new": _yaml_value(new), "why": why, "risky": False, "file": rel.replace(os.sep, "/")})
        if rel.endswith("paper-world-defaults.yml"):
            for etype, lim in PAPER_ENTITY_LIMITS:
                key = f"chunks.entity-per-chunk-save-limit.{etype}"
                cur = yaml_get(text, key)
                if cur is not None and _num(cur, -1) in (-1, 0) or (cur is not None and _num(cur) > lim):
                    out.append({"id": f"yaml:{rel}:{key}", "kind": "yaml", "path": rel, "key": key,
                                "label": f"1チャンクに保存する「{etype}」の上限", "current": cur, "new": str(lim),
                                "why": "矢や経験値が何千個もたまって重くなるのを防ぎます。", "risky": False,
                                "file": rel.replace(os.sep, "/")})
    if not os.path.exists(os.path.join(prof.dir, "config", "paper-world-defaults.yml")):
        out.append({"id": "paper:first", "kind": "info", "label": "Paper の細かい設定",
                    "why": "一度サーバーを起動すると、Paper の細かい設定（爆発・レッドストーン・生き物の上限など）も最適化できます。",
                    "current": "", "new": "", "risky": False, "file": ""})
    return out


def apply(prof, items: list[dict], write_props, set_memory) -> list[str]:
    """選んだ項目を変える。変える前の値を記録しておく（元にもどす用）。"""
    undo = load_json(os.path.join(prof.dir, UNDO_FILE), {"props": {}, "yaml": {}, "memory": None, "at": None})
    done = []
    props_new = {}
    for it in items:
        if it["kind"] == "prop":
            undo["props"].setdefault(it["key"], it["current"])
            props_new[it["key"]] = it["new"]
            done.append(it["label"])
        elif it["kind"] == "memory":
            if undo.get("memory") is None:
                undo["memory"] = prof.get("memory_mb")
            set_memory(int(it["value"]))
            done.append(it["label"])
        elif it["kind"] == "yaml":
            path = os.path.join(prof.dir, it["path"])
            text = open(path, encoding="utf-8", errors="replace").read()
            old = yaml_get(text, it["key"])
            text, changed = yaml_set(text, it["key"], it["new"])
            if changed:
                undo["yaml"].setdefault(f"{it['path']}|{it['key']}", old)
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(text)
                done.append(it["label"])
    if props_new:
        write_props(prof.props_path, props_new)
    undo["at"] = time.strftime("%Y-%m-%d %H:%M")
    save_json(os.path.join(prof.dir, UNDO_FILE), undo)
    return done


def undo(prof, write_props, set_memory) -> int:
    path = os.path.join(prof.dir, UNDO_FILE)
    u = load_json(path, None)
    if not u:
        return 0
    n = 0
    if u.get("props"):
        write_props(prof.props_path, {k: v for k, v in u["props"].items() if v is not None})
        n += len(u["props"])
    if u.get("memory"):
        set_memory(int(u["memory"]))
        n += 1
    for k, old in (u.get("yaml") or {}).items():
        rel, key = k.split("|", 1)
        fp = os.path.join(prof.dir, rel)
        if os.path.exists(fp) and old is not None:
            text = open(fp, encoding="utf-8", errors="replace").read()
            text, ch = yaml_set(text, key, old)
            if ch:
                with open(fp, "w", encoding="utf-8", newline="\n") as f:
                    f.write(text)
                n += 1
    os.remove(path)
    return n


def has_undo(prof) -> str | None:
    u = load_json(os.path.join(prof.dir, UNDO_FILE), None)
    return u.get("at") if u else None


# ============================================================ 重さの原因さがし

SCAN_TYPES = [
    ("item", "落ちているアイテム", True),
    ("experience_orb", "経験値", True),
    ("arrow", "矢", True),
    ("villager", "村人", False),
    ("chicken", "ニワトリ", False),
    ("cow", "ウシ", False),
    ("sheep", "ヒツジ", False),
    ("pig", "ブタ", False),
    ("zombified_piglin", "ゾンビピグリン", False),
    ("hopper_minecart", "ホッパー付きトロッコ", False),
]
DIMS = [("minecraft:overworld", "地上"), ("minecraft:the_nether", "ネザー"), ("minecraft:the_end", "エンド")]
COUNT_RE = re.compile(r"Test passed, count:\s*(\d+)", re.I)
FAILED_RE = re.compile(r"Test failed", re.I)
PLAYER_RE = re.compile(r"^[A-Za-z0-9_]{2,16}$")
CLEAR_TYPES = {"item": "落ちているアイテム", "experience_orb": "経験値", "arrow": "矢"}


def _count(cap, cmd: str) -> int | None:
    lines = cap.run_until(cmd, r"Test (passed|failed)|Unknown|Incorrect|No entity", timeout=2.0)
    for ln in lines:
        m = COUNT_RE.search(ln)
        if m:
            return int(m.group(1))
        if FAILED_RE.search(ln):
            return 0
    return None


def scan(cap, players: list[str], log=print, cancel=None) -> dict:
    """ディメンションごと・プレイヤーのまわりごとに数える。"""
    rows = []
    totals = {}
    for dim, dlabel in DIMS:
        log(f"{dlabel} を数えています…")
        all_n = _count(cap, f"execute in {dim} if entity @e[type=!minecraft:player]")
        if all_n is None:
            raise RuntimeError("このバージョンのサーバーでは数えられませんでした（execute コマンドが使えません）")
        totals[dim] = all_n
        if not all_n:
            continue
        for t, label, clearable in SCAN_TYPES:
            if cancel and cancel():
                break
            n = _count(cap, f"execute in {dim} if entity @e[type=minecraft:{t}]")
            if n:
                rows.append({"dim": dim, "dim_label": dlabel, "type": t, "label": label, "count": n, "clearable": clearable})
    near = []
    for name in players:
        if not PLAYER_RE.match(name):
            continue
        n = _count(cap, f"execute at {name} if entity @e[distance=..64,type=!minecraft:player]")
        items = _count(cap, f"execute at {name} if entity @e[distance=..64,type=minecraft:item]")
        if n is not None:
            near.append({"player": name, "count": n, "items": items or 0})
    rows.sort(key=lambda r: -r["count"])
    near.sort(key=lambda r: -r["count"])
    advice = []
    for r in rows:
        if r["type"] == "item" and r["count"] >= 300:
            advice.append(f"{r['dim_label']}に落ちているアイテムが {r['count']}個 あります。お掃除すると軽くなります。")
        elif r["type"] == "experience_orb" and r["count"] >= 200:
            advice.append(f"{r['dim_label']}に経験値が {r['count']}個 たまっています（経験値トラップ？）。お掃除で消せます。")
        elif r["type"] == "villager" and r["count"] >= 80:
            advice.append(f"村人が {r['count']}人 います。村人は特に重いので、交易所を小さくするか数を減らしましょう。")
        elif r["type"] in ("chicken", "cow", "sheep", "pig") and r["count"] >= 150:
            advice.append(f"{r['label']}が {r['count']}匹 います。牧場の数を減らすと軽くなります。")
        elif r["type"] == "hopper_minecart" and r["count"] >= 30:
            advice.append(f"ホッパー付きトロッコが {r['count']}台 あります。とても重いので減らしましょう。")
    for n in near:
        if n["count"] >= 400:
            advice.append(f"{n['player']} さんのまわり（64ブロック以内）に生き物・物が {n['count']}個 あります。ここが重さの原因かもしれません。")
    return {"rows": rows, "near": near, "totals": totals, "advice": advice,
            "total": sum(totals.values()), "at": time.strftime("%H:%M:%S")}


def clear_commands(types: list[str]) -> list[str]:
    return [f"kill @e[type=minecraft:{t}]" for t in types if t in CLEAR_TYPES]
