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
ICON_VERSIONS = ["1.21.8", "1.21.1", "1.20.4", "1.19.4", "1.16.5"]
_ICON_ID = re.compile(r"^([a-z0-9_.-]+):([a-z0-9_./-]+)$")
_PNG = b"\x89PNG\r\n\x1a\n"
_mod_index: dict = {}

# 名前どおりの画像がないものの、画像の名前（左: アイテムID、右: 画像の名前）
ICON_ALIAS = {
    "grass_block": "grass_block_side", "tnt": "tnt_side", "furnace": "furnace_front", "blast_furnace": "blast_furnace_front",
    "smoker": "smoker_front", "crafting_table": "crafting_table_front", "pumpkin": "pumpkin_side", "melon": "melon_side",
    "hay_block": "hay_block_side", "barrel": "barrel_side", "cactus": "cactus_side", "dispenser": "dispenser_front",
    "dropper": "dropper_front", "observer": "observer_front", "piston": "piston_side", "sticky_piston": "piston_top_sticky",
    "compass": "compass_00", "recovery_compass": "recovery_compass_00", "clock": "clock_00", "crossbow": "crossbow_standby",
    "podzol": "podzol_side", "mycelium": "mycelium_side", "dirt_path": "dirt_path_side", "snow_block": "snow",
    "magma_block": "magma", "quartz_block": "quartz_block_side", "bone_block": "bone_block_side", "loom": "loom_front",
    "composter": "composter_side", "lectern": "lectern_front", "fletching_table": "fletching_table_front",
    "smithing_table": "smithing_table_front", "enchanting_table": "enchanting_table_side", "jukebox": "jukebox_side",
    "target": "target_side", "bee_nest": "bee_nest_front", "beehive": "beehive_front", "tall_grass": "tall_grass_top",
    "large_fern": "large_fern_top", "sunflower": "sunflower_front", "lilac": "lilac_top", "rose_bush": "rose_bush_top",
    "peony": "peony_top", "ancient_debris": "ancient_debris_side", "basalt": "basalt_side",
    "polished_basalt": "polished_basalt_side", "dried_kelp_block": "dried_kelp_side",
    "enchanted_golden_apple": "golden_apple", "tipped_arrow": "tipped_arrow_base", "debug_stick": "stick",
    "cartography_table": "cartography_table_side1", "respawn_anchor": "respawn_anchor_side0", "scaffolding": "scaffolding_side",
    "glass_pane": "glass", "moss_carpet": "moss_block", "pale_moss_carpet": "pale_moss_block", "petrified_oak_slab": "oak_planks",
    "smooth_stone_slab": "smooth_stone", "chipped_anvil": "anvil", "damaged_anvil": "anvil", "water": "water_bucket",
    "lava": "lava_bucket", "sculk_sensor": "sculk_sensor_side", "calibrated_sculk_sensor": "calibrated_sculk_sensor_input_side",
    "tinted_glass": "tinted_glass", "chiseled_bookshelf": "chiseled_bookshelf_empty", "crafter": "crafter_north",
    "stonecutter": "stonecutter_side", "grindstone": "grindstone_side", "command_block": "command_block_front",
    "chain_command_block": "chain_command_block_front", "repeating_command_block": "repeating_command_block_front",
    "structure_block": "structure_block", "jigsaw": "jigsaw_top", "muddy_mangrove_roots": "muddy_mangrove_roots_side",
    "mangrove_roots": "mangrove_roots_side", "ochre_froglight": "ochre_froglight_side",
    "verdant_froglight": "verdant_froglight_side", "pearlescent_froglight": "pearlescent_froglight_side",
    "bamboo_block": "bamboo_block", "sea_pickle": "sea_pickle", "turtle_egg": "turtle_egg", "end_portal_frame": "end_portal_frame_side",
    "smooth_quartz": "quartz_block_bottom", "smooth_sandstone": "sandstone_top", "smooth_red_sandstone": "red_sandstone_top",
    "light_weighted_pressure_plate": "gold_block", "heavy_weighted_pressure_plate": "iron_block",
    "suspicious_sand": "suspicious_sand_0", "suspicious_gravel": "suspicious_gravel_0", "azalea": "azalea_top",
    "flowering_azalea": "flowering_azalea_top", "big_dripleaf": "big_dripleaf_top", "small_dripleaf": "small_dripleaf_top",
    "decorated_pot": "flower_pot", "reinforced_deepslate": "reinforced_deepslate_side", "sculk_catalyst": "sculk_catalyst_side",
    "sculk_shrieker": "sculk_shrieker_side", "honey_block": "honey_block_side", "daylight_detector": "daylight_detector_top",
    "lodestone": "lodestone_side", "trial_spawner": "trial_spawner_side_inactive", "vault": "vault_front_off",
    "farmland": "farmland", "snow": "snow", "vine": "vine", "lily_pad": "lily_pad", "light": "light_15",
}
_SHAPES = ("_slab", "_stairs", "_wall", "_fence_gate", "_fence", "_button", "_pressure_plate")


def icon_candidates(name: str) -> list[str]:
    """画像の名前の候補（名前そのまま → 言いかえ → 形や色から元のブロックを推測）。"""
    out = [name]

    def add(n):
        if n and n not in out:
            out.append(n)
    add(ICON_ALIAS.get(name))
    for pre in ("waxed_", "infested_"):
        if name.startswith(pre):
            for c in icon_candidates(name[len(pre):]):
                add(c)
    for suf in _SHAPES:
        if name.endswith(suf):
            base = name[:-len(suf)]
            for c in (ICON_ALIAS.get(base), base, base + "s", base + "_planks", base + "_block", ICON_ALIAS.get(base + "_block"),
                      base.replace("brick", "bricks").replace("tile", "tiles"), base + "_side", base + "_top"):
                add(c)
            break
    for suf, rep in (("_wood", "_log"), ("_hyphae", "_stem"), ("_carpet", "_wool"), ("_stained_glass_pane", "_stained_glass"),
                     ("_bed", "_wool"), ("_banner", "_wool"), ("_bundle", "_wool")):
        if name.endswith(suf):
            add(name[:-len(suf)] + rep)
    if name.endswith("_spawn_egg"):
        add("spawn_egg")
    return out[:12]


# ---- 絵のないもの（チェスト・頭・盾など）は、かんたんな絵をその場で作る ----
def _png(pixels: list[list[tuple]]) -> bytes:
    import struct
    import zlib
    h, w = len(pixels), len(pixels[0])
    raw = b"".join(b"\x00" + b"".join(bytes(px) for px in row) for row in pixels)

    def chunk(kind, data):
        c = struct.pack(">I", len(data)) + kind + data
        return c + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return _PNG + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def _paint(rows: list[str], colors: dict) -> bytes:
    px = [[colors.get(ch, (0, 0, 0, 0)) for ch in row.ljust(16, ".")[:16]] for row in (rows + ["."] * 16)[:16]]
    return _png(px)


_CHEST = ["................", "................", ".dddddddddddddd.", ".dbbbbbbbbbbbbd.", ".dbbbbbbbbbbbbd.", ".dbbbbbbbbbbbbd.",
          ".dddddddssddddd.", ".dbbbbbbssbbbbd.", ".dbbbbbbbbbbbbd.", ".dbbbbbbbbbbbbd.", ".dbbbbbbbbbbbbd.", ".dbbbbbbbbbbbbd.",
          ".dbbbbbbbbbbbbd.", ".dddddddddddddd.", "................", "................"]
_HEAD = ["................", "................", "...dddddddddd...", "...dbbbbbbbbd...", "...dbbbbbbbbd...", "...dbeebbeebd...",
         "...dbeebbeebd...", "...dbbbbbbbbd...", "...dbbbeebbbd...", "...dbbeeeebbd...", "...dbbebbebbd...", "...dbbbbbbbbd...",
         "...dddddddddd...", "................", "................", "................"]
_SHIELD = ["................", "..dddddddddddd..", "..dbbbbbbbbbbd..", "..dbbbbbbbbbbd..", "..dbbbbssbbbbd..", "..dbbbbssbbbbd..",
           "..dbbssssssbbd..", "..dbbssssssbbd..", "..dbbbbssbbbbd..", "..dbbbbssbbbbd..", "..dbbbbbbbbbbd..", "...dbbbbbbbbd...",
           "....dbbbbbbd....", ".....dbbbbd.....", "......dddd......", "................"]
_HEAD_COLORS = {"skeleton_skull": ((200, 200, 200), (60, 60, 60)), "wither_skeleton_skull": ((50, 50, 50), (150, 150, 150)),
                "zombie_head": ((80, 140, 70), (20, 40, 20)), "creeper_head": ((90, 180, 80), (20, 30, 20)),
                "player_head": ((190, 140, 110), (60, 40, 30)), "piglin_head": ((230, 160, 130), (70, 40, 30)),
                "dragon_head": ((40, 30, 50), (200, 100, 230))}


def _drawn_icon(name: str) -> bytes | None:
    def c(rgb, a=255):
        return (*rgb, a)
    if name in ("chest", "trapped_chest"):
        return _paint(_CHEST, {"d": c((70, 45, 20)), "b": c((170, 115, 50)), "s": c((200, 200, 200) if name == "chest" else (200, 60, 50))})
    if name == "ender_chest":
        return _paint(_CHEST, {"d": c((15, 30, 35)), "b": c((40, 65, 70)), "s": c((60, 220, 190))})
    if name in _HEAD_COLORS:
        b, e = _HEAD_COLORS[name]
        return _paint(_HEAD, {"d": c(tuple(max(0, v - 50) for v in b)), "b": c(b), "e": c(e)})
    if name == "shield":
        return _paint(_SHIELD, {"d": c((60, 40, 20)), "b": c((150, 110, 60)), "s": c((190, 190, 190))})
    return None


def _mod_textures(mods_dir: str) -> dict:
    """MODの jar の中の、画像と設計図（モデル）の場所の一覧。
    {"tex": {(名前空間, "item/名前"): (jar, 中のパス)}, "model": {(名前空間, "item/名前"): (jar, 中のパス)}, "base": {名前: [...]}}"""
    try:
        key = (mods_dir, os.path.getmtime(mods_dir), len(os.listdir(mods_dir)))
    except OSError:
        return {"tex": {}, "model": {}, "itemdef": {}, "base": {}}
    hit = _mod_index.get(mods_dir)
    if hit and hit[0] == key:
        return hit[1]
    idx: dict = {"tex": {}, "model": {}, "itemdef": {}, "base": {}}
    rx_t = re.compile(r"^assets/([a-z0-9_.-]+)/textures/([a-z0-9_./-]+)\.png$")
    rx_m = re.compile(r"^assets/([a-z0-9_.-]+)/models/([a-z0-9_./-]+)\.json$")
    rx_i = re.compile(r"^assets/([a-z0-9_.-]+)/items/([a-z0-9_./-]+)\.json$")
    for fn in sorted(os.listdir(mods_dir)):
        if not fn.endswith(".jar"):
            continue
        path = os.path.join(mods_dir, fn)
        try:
            with zipfile.ZipFile(path) as z:
                for n in z.namelist():
                    m = rx_t.match(n)
                    if m:
                        idx["tex"].setdefault((m.group(1), m.group(2)), (path, n))
                        if m.group(2).startswith(("item/", "items/", "block/", "blocks/")):
                            idx["base"].setdefault((m.group(1), m.group(2).rsplit("/", 1)[-1]), []).append((path, n))
                        continue
                    m = rx_m.match(n)
                    if m:
                        idx["model"].setdefault((m.group(1), m.group(2)), (path, n))
                        continue
                    m = rx_i.match(n)
                    if m:
                        idx["itemdef"].setdefault((m.group(1), m.group(2)), (path, n))
        except (zipfile.BadZipFile, OSError):
            continue
    _mod_index[mods_dir] = (key, idx)
    return idx


def _zip_read(hit, limit=400_000) -> bytes | None:
    try:
        with zipfile.ZipFile(hit[0]) as z:
            if z.getinfo(hit[1]).file_size > limit:
                return None
            return z.read(hit[1])
    except (zipfile.BadZipFile, OSError, KeyError):
        return None


def _split(ref: str, ns: str) -> tuple[str, str]:
    return tuple(ref.split(":", 1)) if ":" in ref else (ns, ref)


def _mod_icon(ns: str, name: str, mods_dir: str, mc: str) -> bytes | None:
    """MODのアイテムの画像を探す: ①名前どおりの画像 ②設計図（モデル）に書いてある画像 ③同じ名前の画像。"""
    import json
    idx = _mod_textures(mods_dir)

    def tex(tns, tpath):
        if tns == "minecraft":
            return icon_png("minecraft:" + tpath.rsplit("/", 1)[-1], mc, None)
        hit = idx["tex"].get((tns, tpath))
        data = _zip_read(hit) if hit else None
        return data if data and data.startswith(_PNG) else None

    for pre in ("item/", "items/", "block/", "blocks/"):
        d = tex(ns, pre + name)
        if d:
            return d
    # 設計図をたどる（アイテムの定義 → モデル → 親モデル）
    model_ref = None
    hit = idx["itemdef"].get((ns, name))
    if hit:
        try:
            def find(o):
                if isinstance(o, dict):
                    if isinstance(o.get("model"), str):
                        return o["model"]
                    for v in o.values():
                        r = find(v)
                        if r:
                            return r
                elif isinstance(o, list):
                    for v in o:
                        r = find(v)
                        if r:
                            return r
                return None
            model_ref = find(json.loads(_zip_read(hit) or b"{}"))
        except ValueError:
            model_ref = None
    cur = _split(model_ref, ns) if model_ref else (ns, "item/" + name)
    for _ in range(5):
        hit = idx["model"].get(cur)
        if not hit:
            break
        try:
            mj = json.loads(_zip_read(hit) or b"{}")
        except ValueError:
            break
        texs = mj.get("textures") or {}
        for k in ("layer0", "front", "side", "all", "texture", "top", "particle", *texs.keys()):
            v = texs.get(k)
            if isinstance(v, str) and not v.startswith("#"):
                d = tex(*_split(v, "minecraft"))
                if d:
                    return d
        parent = mj.get("parent")
        if not isinstance(parent, str):
            break
        cur = _split(parent, "minecraft")
        if cur[0] == "minecraft":       # バニラの形を借りているだけ（画像は書かれていない）
            break
    for hit in idx["base"].get((ns, name), []):
        d = _zip_read(hit)
        if d and d.startswith(_PNG):
            return d
    return None


def icon_png(iid: str, mc: str, mods_dir: str | None = None) -> bytes | None:
    """アイテムIDの画像（PNG）を返す。なければ None。"""
    m = _ICON_ID.match(iid or "")
    if not m or len(iid) > 120 or ".." in iid:
        return None
    ns, name = m.groups()
    if ns != "minecraft":
        if not mods_dir:
            return None
        return _mod_icon(ns, name, mods_dir, mc)
    name = name.rsplit("/", 1)[-1]
    ver = mc if re.match(r"^[0-9][0-9a-z._-]{0,15}$", mc or "") else "1.21.1"
    cache = os.path.join(ICON_DIR, "vanilla", name + ".png")
    miss = cache + ".none2"
    if os.path.exists(cache):
        with open(cache, "rb") as f:
            return f.read()
    drawn = _drawn_icon(name)
    if drawn:
        return drawn
    if os.path.exists(miss):
        return None
    chain = [v for v in dict.fromkeys(["1.21.8" if name.endswith("_spawn_egg") else ver, "1.21.1", ver]) if v][:2]
    for cand in icon_candidates(name):
        for v in chain:
            for kind in ("items", "blocks"):
                try:
                    data = net.request(ICON_URL.format(ver=v, kind=kind, name=cand), timeout=15, retries=0)
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
