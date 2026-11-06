"""アイテムのID一覧を作る（/give などのコマンド用）。

- バニラのアイテム: マイクラ公式のサーバーに付いている「データ書き出し」で、IDの一覧を作る（初回だけ。結果は保存）
- MODのアイテム: サーバーに入っているMODの中身（モデルの名前・言語ファイル）から読みとる
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile

from . import javamgr, loaders, net
from .paths import CACHE_DIR

ITEMS_DIR = os.path.join(CACHE_DIR, "items")

# よく使うアイテムの日本語名（マイクラ公式の呼び方）。ここにないものは、英語のIDで表示する。
JA = {
    "diamond": "ダイヤモンド", "emerald": "エメラルド", "gold_ingot": "金インゴット", "iron_ingot": "鉄インゴット",
    "netherite_ingot": "ネザライトインゴット", "netherite_scrap": "ネザライトの欠片", "copper_ingot": "銅インゴット",
    "coal": "石炭", "charcoal": "木炭", "redstone": "レッドストーンダスト", "lapis_lazuli": "ラピスラズリ", "quartz": "ネザークォーツ",
    "diamond_sword": "ダイヤモンドの剣", "diamond_pickaxe": "ダイヤモンドのツルハシ", "diamond_axe": "ダイヤモンドの斧",
    "diamond_shovel": "ダイヤモンドのシャベル", "diamond_hoe": "ダイヤモンドのクワ", "diamond_helmet": "ダイヤモンドのヘルメット",
    "diamond_chestplate": "ダイヤモンドのチェストプレート", "diamond_leggings": "ダイヤモンドのレギンス", "diamond_boots": "ダイヤモンドのブーツ",
    "netherite_sword": "ネザライトの剣", "netherite_pickaxe": "ネザライトのツルハシ", "netherite_axe": "ネザライトの斧",
    "netherite_shovel": "ネザライトのシャベル", "netherite_helmet": "ネザライトのヘルメット", "netherite_chestplate": "ネザライトのチェストプレート",
    "netherite_leggings": "ネザライトのレギンス", "netherite_boots": "ネザライトのブーツ",
    "iron_sword": "鉄の剣", "iron_pickaxe": "鉄のツルハシ", "iron_axe": "鉄の斧", "iron_shovel": "鉄のシャベル",
    "wooden_sword": "木の剣", "stone_sword": "石の剣", "golden_sword": "金の剣", "bow": "弓", "crossbow": "クロスボウ", "arrow": "矢",
    "trident": "トライデント", "shield": "盾", "elytra": "エリトラ", "totem_of_undying": "不死のトーテム", "fishing_rod": "釣り竿",
    "golden_apple": "金のリンゴ", "enchanted_golden_apple": "エンチャントされた金のリンゴ", "apple": "リンゴ", "bread": "パン",
    "cooked_beef": "ステーキ", "cooked_porkchop": "焼き豚", "cooked_chicken": "焼き鳥", "golden_carrot": "金のニンジン",
    "ender_pearl": "エンダーパール", "ender_eye": "エンダーアイ", "blaze_rod": "ブレイズロッド", "nether_star": "ネザースター",
    "experience_bottle": "エンチャントの瓶", "enchanted_book": "エンチャントの本", "book": "本", "name_tag": "名札", "saddle": "鞍",
    "water_bucket": "水入りバケツ", "lava_bucket": "溶岩入りバケツ", "bucket": "バケツ", "milk_bucket": "牛乳入りバケツ",
    "torch": "松明", "tnt": "TNT", "chest": "チェスト", "ender_chest": "エンダーチェスト", "crafting_table": "作業台", "furnace": "かまど",
    "anvil": "金床", "enchanting_table": "エンチャントテーブル", "beacon": "ビーコン", "shulker_box": "シュルカーボックス",
    "elytra_": "", "firework_rocket": "ロケット花火", "spyglass": "望遠鏡", "compass": "コンパス", "clock": "時計", "map": "地図",
    "oak_log": "オークの原木", "oak_planks": "オークの板材", "stone": "石", "cobblestone": "丸石", "dirt": "土", "grass_block": "草ブロック",
    "sand": "砂", "gravel": "砂利", "glass": "ガラス", "obsidian": "黒曜石", "bedrock": "岩盤", "netherrack": "ネザーラック",
    "diamond_block": "ダイヤモンドブロック", "iron_block": "鉄ブロック", "gold_block": "金ブロック", "emerald_block": "エメラルドブロック",
    "diamond_ore": "ダイヤモンド鉱石", "iron_ore": "鉄鉱石", "gold_ore": "金鉱石", "coal_ore": "石炭鉱石", "ancient_debris": "古代の残骸",
    "bone_meal": "骨粉", "wheat_seeds": "小麦の種", "wheat": "小麦", "carrot": "ニンジン", "potato": "ジャガイモ", "sugar_cane": "サトウキビ",
    "string": "糸", "stick": "棒", "leather": "革", "feather": "羽根", "flint": "火打石", "flint_and_steel": "火打石と打ち金",
    "slime_ball": "スライムボール", "gunpowder": "火薬", "bone": "骨", "rotten_flesh": "腐った肉", "spider_eye": "クモの目",
    "command_block": "コマンドブロック", "barrier": "バリアブロック", "light": "光源ブロック", "structure_block": "ストラクチャーブロック",
    "elytra__": "", "white_wool": "白色の羊毛", "red_bed": "赤色のベッド", "minecart": "トロッコ", "rail": "レール", "powered_rail": "パワードレール",
    "piston": "ピストン", "sticky_piston": "粘着ピストン", "hopper": "ホッパー", "dispenser": "ディスペンサー", "dropper": "ドロッパー",
    "observer": "オブザーバー", "repeater": "リピーター", "comparator": "コンパレーター", "lever": "レバー", "stone_button": "石のボタン",
}
JA = {k: v for k, v in JA.items() if v}


def _title(name: str) -> str:
    return " ".join(w.capitalize() for w in name.replace("/", " ").split("_"))


def vanilla_ids(mc: str, log=print) -> list[str]:
    os.makedirs(ITEMS_DIR, exist_ok=True)
    cache = os.path.join(ITEMS_DIR, f"vanilla_{mc}.json")
    if os.path.exists(cache):
        try:
            with open(cache, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
    dl = loaders.version_info(mc)["downloads"]["server"]
    jar = os.path.join(ITEMS_DIR, f"server_{mc}.jar")
    log("マイクラ公式のサーバーを取ってきています（アイテムの一覧づくり・初回だけ）…")
    net.download(dl["url"], jar, sha1=dl.get("sha1"))
    java = javamgr.ensure_java(loaders.required_java(mc), log)
    work = tempfile.mkdtemp(prefix="items_")
    try:
        log("アイテムの一覧を作っています…")
        ok = False
        for cmd in ([java, "-DbundlerMainClass=net.minecraft.data.Main", "-jar", jar, "--reports", "--output", work],
                    [java, "-cp", jar, "net.minecraft.data.Main", "--reports", "--output", work]):
            try:
                subprocess.run(cmd, cwd=work, capture_output=True, timeout=240,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except (subprocess.TimeoutExpired, OSError):
                continue
            if os.path.exists(os.path.join(work, "reports", "registries.json")):
                ok = True
                break
        if not ok:
            raise RuntimeError("バニラのアイテム一覧を作れませんでした。IDは手で入れてください。")
        with open(os.path.join(work, "reports", "registries.json"), encoding="utf-8") as f:
            regs = json.load(f)
        ids = sorted(regs["minecraft:item"]["entries"].keys())
    finally:
        shutil.rmtree(work, ignore_errors=True)
        try:
            os.remove(jar)
        except OSError:
            pass
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(ids, f)
    return ids


_MODEL = re.compile(r"^assets/([a-z0-9_.-]+)/models/item/([a-z0-9_.-]+)\.json$")
_LANGKEY = re.compile(r"^(?:item|block)\.([a-z0-9_.-]+?)\.([a-z0-9_]+)$")


def mod_items(prof, titles: dict[str, str] | None = None) -> list[dict]:
    """サーバーのMODの中身から、アイテムのIDと名前を読みとる。"""
    out, seen = [], set()
    mods_dir = prof.mods_dir
    if not os.path.isdir(mods_dir):
        return out
    titles = titles or {}
    for fn in sorted(os.listdir(mods_dir)):
        if not fn.endswith(".jar"):
            continue
        src = titles.get(fn) or os.path.splitext(fn)[0]
        try:
            z = zipfile.ZipFile(os.path.join(mods_dir, fn))
        except (zipfile.BadZipFile, OSError):
            continue
        with z:
            names = z.namelist()
            ids = []
            for n in names:
                m = _MODEL.match(n)
                if m and m.group(1) != "minecraft":
                    ids.append((m.group(1), m.group(2)))
            ns_all = {ns for ns, _ in ids}
            lang = {}
            for ns in ns_all:
                for code in ("en_us", "ja_jp"):
                    p = f"assets/{ns}/lang/{code}.json"
                    if p in names:
                        try:
                            lang.setdefault(code, {}).update(json.loads(z.read(p).decode("utf-8", "replace")))
                        except ValueError:
                            pass
            for ns, name in ids:
                iid = f"{ns}:{name}"
                if iid in seen:
                    continue
                seen.add(iid)
                en = ja = ""
                for kind in ("item", "block"):
                    key = f"{kind}.{ns}.{name}"
                    en = en or (lang.get("en_us", {}).get(key) or "")
                    ja = ja or (lang.get("ja_jp", {}).get(key) or "")
                out.append({"id": iid, "name": en or _title(name), "ja": ja, "src": src})
    return out


def catalog(prof, titles=None, log=print) -> dict:
    mc = prof.get("mc") or ""
    items = []
    warn = ""
    try:
        for i in vanilla_ids(mc, log):
            short = i.split(":", 1)[1]
            items.append({"id": i, "name": _title(short), "ja": JA.get(short, ""), "src": "マイクラ"})
    except Exception as e:  # noqa: BLE001  バニラが作れなくても、MODの分は出す
        warn = str(e)
    items += mod_items(prof, titles)
    return {"items": items, "warn": warn}


# ---------------- アイテムの画像 ----------------
# バニラ: 公開されている画像集（PrismarineJS/minecraft-assets・GitHub）から、必要なものだけ取って、このPCに保存する。
# MOD: サーバーに入っているMODの jar の中の画像を使う（ネット不要）。
ICON_DIR = os.path.join(CACHE_DIR, "icons")
ICON_URL = "https://raw.githubusercontent.com/PrismarineJS/minecraft-assets/master/data/{ver}/{kind}/{name}.png"
ICON_VERSIONS = ["1.21.1", "1.20.4", "1.19.4", "1.18.2", "1.16.5"]
_ICON_ID = re.compile(r"^([a-z0-9_.-]+):([a-z0-9_.-]+)$")
_PNG = b"\x89PNG\r\n\x1a\n"
_mod_index: dict = {}


def _mod_textures(mods_dir: str) -> dict:
    """MODの jar の中の、アイテム・ブロックの画像の場所の一覧: {(名前空間, 名前): (jarのパス, 中のパス)}"""
    try:
        key = (mods_dir, os.path.getmtime(mods_dir), len(os.listdir(mods_dir)))
    except OSError:
        return {}
    hit = _mod_index.get(mods_dir)
    if hit and hit[0] == key:
        return hit[1]
    idx: dict = {}
    rx = re.compile(r"^assets/([a-z0-9_.-]+)/textures/(item|block)/([a-z0-9_.-]+)\.png$")
    for fn in sorted(os.listdir(mods_dir)):
        if not fn.endswith(".jar"):
            continue
        path = os.path.join(mods_dir, fn)
        try:
            with zipfile.ZipFile(path) as z:
                for n in z.namelist():
                    m = rx.match(n)
                    if m:
                        k = (m.group(1), m.group(3))
                        if k not in idx or m.group(2) == "item":      # アイテムの画像を優先
                            idx[k] = (path, n)
        except (zipfile.BadZipFile, OSError):
            continue
    _mod_index[mods_dir] = (key, idx)
    return idx


def icon_png(iid: str, mc: str, mods_dir: str | None = None) -> bytes | None:
    """アイテムIDの画像（PNG）を返す。なければ None。"""
    m = _ICON_ID.match(iid or "")
    if not m or len(iid) > 120:
        return None
    ns, name = m.groups()
    if ns != "minecraft":
        if not mods_dir:
            return None
        hit = _mod_textures(mods_dir).get((ns, name))
        if not hit:
            return None
        try:
            with zipfile.ZipFile(hit[0]) as z:
                data = z.read(hit[1])
            return data if data.startswith(_PNG) and len(data) < 200_000 else None
        except (zipfile.BadZipFile, OSError, KeyError):
            return None
    ver = mc if re.match(r"^[0-9][0-9a-z._-]{0,15}$", mc or "") else "1.21.1"
    cache = os.path.join(ICON_DIR, "vanilla", name + ".png")
    miss = cache + ".none"
    if os.path.exists(cache):
        with open(cache, "rb") as f:
            return f.read()
    if os.path.exists(miss):
        return None
    chain = [ver] + [v for v in ICON_VERSIONS if v != ver]
    for v in chain[:3]:
        for kind in ("items", "blocks"):
            try:
                data = net.request(ICON_URL.format(ver=v, kind=kind, name=name), timeout=15, retries=0)
            except net.NetError as e:
                if "404" in str(e):
                    continue
                return None        # ネットにつながらない。あとでまた試す（失敗は保存しない）
            if data.startswith(_PNG) and len(data) < 200_000:
                os.makedirs(os.path.dirname(cache), exist_ok=True)
                with open(cache, "wb") as f:
                    f.write(data)
                return data
    try:
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        open(miss, "wb").close()
    except OSError:
        pass
    return None
