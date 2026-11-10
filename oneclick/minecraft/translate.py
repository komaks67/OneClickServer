"""英語の説明を日本語に自動翻訳する（無料の MyMemory 翻訳サービス）。

一度訳した文はファイルに保存して、次からはネットを使わずに表示する。
無料版は1日に訳せる量に上限がある（メールアドレスを設定すると増える）。
"""
from __future__ import annotations

import hashlib
import html
import os
import re
import threading

from . import net
from .paths import CACHE_DIR, load_json, save_json

API = "https://api.mymemory.translated.net/get"
FALLBACK_API = "https://translate.googleapis.com/translate_a/single"  # 予備（キー不要。MyMemory の上限が来たときだけ使う）
CACHE_FILE = os.path.join(CACHE_DIR, "translations.json")
_lock = threading.Lock()
_cache: dict | None = None


class TranslateLimit(Exception):
    pass


def _load():
    global _cache
    if _cache is None:
        _cache = load_json(CACHE_FILE, {})
    return _cache


def _key(text: str) -> str:
    # "v2:" … 翻訳のしくみを良くしたので、前の訳とは別に覚える
    return hashlib.sha1(("v2:" + text.strip()).encode("utf-8")).hexdigest()


# マイクラの用語は、訳す前に日本語の正式な名前に置き換える（「Creeper → つる植物」のような誤訳を防ぐ）
GLOSSARY = [
    ("resource packs?", "リソースパック"), ("texture packs?", "リソースパック"), ("data ?packs?", "データパック"),
    ("mod ?packs?", "MODパック"), ("shader ?packs?", "シェーダーパック"), ("shaders?", "シェーダー"),
    ("crafting tables?", "作業台"), ("ender dragon", "エンダードラゴン"), ("the end", "ジ・エンド"), ("the nether", "ネザー"),
    ("nether", "ネザー"), ("overworld", "オーバーワールド"), ("mobs?", "モブ"), ("creepers?", "クリーパー"),
    ("endermen|enderman", "エンダーマン"), ("villagers?", "村人"), ("piglins?", "ピグリン"), ("wither", "ウィザー"),
    ("zombies?", "ゾンビ"), ("skeletons?", "スケルトン"), ("golems?", "ゴーレム"), ("biomes?", "バイオーム"),
    ("dimensions?", "ディメンション"), ("chunks?", "チャンク"), ("redstone", "レッドストーン"), ("netherite", "ネザライト"),
    ("elytra", "エリトラ"), ("enchantments?", "エンチャント"), ("enchanting", "エンチャント"), ("potions?", "ポーション"),
    ("furnaces?", "かまど"), ("anvils?", "金床"), ("beacons?", "ビーコン"), ("pickaxes?", "ツルハシ"),
    ("hotbar", "ホットバー"), ("inventory", "インベントリ"), ("inventories", "インベントリ"), ("waypoints?", "ウェイポイント"),
    ("minimaps?", "ミニマップ"), ("entities|entity", "エンティティ"), ("multiplayer", "マルチプレイ"),
    ("singleplayer|single-player", "シングルプレイ"), ("vanilla", "バニラ"), ("survival mode", "サバイバルモード"),
    ("creative mode", "クリエイティブモード"), ("hardcore", "ハードコア"), ("spawn eggs?", "スポーンエッグ"),
    ("mods", "MOD"), ("mod", "MOD"),
]
_GLOSS_RE = re.compile(r"(?<![A-Za-z0-9_-])(" + "|".join(f"(?P<g{i}>{pat})" for i, (pat, _) in enumerate(GLOSSARY)) + r")(?![A-Za-z0-9_-])",
                       re.I)
# 訳したあとに直す、よくある誤訳
POST_FIX = [("モッド", "MOD"), ("暴徒", "モブ"), ("MODS", "MOD"), ("Mods", "MOD"), ("マインクラフト", "マイクラ"),
            ("ＭＯＤ", "MOD"), (" MOD ", "MOD"), ("MOD パック", "MODパック")]


def _prepare(text: str) -> str:
    def sub(m):
        for i, (_, ja) in enumerate(GLOSSARY):
            if m.group(f"g{i}"):
                return ja
        return m.group(0)
    return _GLOSS_RE.sub(sub, text)


def _polish(text: str) -> str:
    for a, b in POST_FIX:
        text = text.replace(a, b)
    text = re.sub(r"(?<=[ぁ-んァ-ヶー一-龥、。]) +(?=[ぁ-んァ-ヶー一-龥])", "", text)   # 日本語の間の余分な空白
    return text.strip()


def cached(text: str) -> str | None:
    if not text:
        return None
    with _lock:
        return _load().get(_key(text))


def _chunks(text: str, size=450) -> list[str]:
    """長い文は文の区切りで分ける（1回に送れるのは500文字まで）。"""
    parts = re.split(r"(?<=[.!?。])\s+|\n+", text.strip())
    out, cur = [], ""
    for p in parts:
        if not p:
            continue
        while len(p) > size:
            out.append(p[:size])
            p = p[size:]
        if len(cur) + len(p) + 1 > size:
            out.append(cur)
            cur = p
        else:
            cur = (cur + " " + p).strip()
    if cur:
        out.append(cur)
    return out


def _mymemory(chunk: str, email: str | None) -> str:
    params = {"q": chunk, "langpair": "en|ja"}
    if email:
        params["de"] = email
    try:
        data = net.get_json(API, params, timeout=15, retries=1)
    except net.NetError as e:
        raise TranslateLimit("翻訳サービスにつながりませんでした。少し時間をおいて試してください。") from e
    t = (data.get("responseData") or {}).get("translatedText") or ""
    status = str(data.get("responseStatus"))
    if data.get("quotaFinished") or status == "429" or "MYMEMORY WARNING" in t.upper():
        raise TranslateLimit("今日の無料翻訳の上限に達しました。明日になるとまた使えます"
                             "（設定でメールアドレスを入れると上限が増えます）。")
    if status not in ("200", "None") and not t:
        raise TranslateLimit("翻訳できませんでした。")
    return html.unescape(t)


def _fallback(chunk: str) -> str:
    """予備の翻訳。Google の翻訳ページが使っている窓口（公式の約束はない）。送るのはMODの説明文だけ。"""
    try:
        data = net.get_json(FALLBACK_API, {"client": "gtx", "sl": "en", "tl": "ja", "dt": "t", "q": chunk},
                            timeout=15, retries=1)
        return "".join(seg[0] for seg in data[0] if seg and seg[0])
    except (net.NetError, ValueError, IndexError, TypeError, KeyError) as e:
        raise TranslateLimit("翻訳できませんでした。") from e


def _one(chunk: str, email: str | None, fallback: bool) -> str:
    """1かたまりを訳す。速くて正確な Google を先に使い、だめなら MyMemory（設定でGoogleを使わないときは MyMemory だけ）。"""
    src = _prepare(chunk)
    if fallback:
        try:
            return _fallback(src)
        except TranslateLimit:
            pass
    return _mymemory(src[:480], email)


def translate(text: str, email: str | None = None, fallback: bool = True) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    hit = cached(text)
    if hit:
        return hit
    chunks = _chunks(text, 1500 if fallback else 450)
    if len(chunks) == 1:
        results = [_one(chunks[0], email, fallback)]
    else:                                   # 長い文は、かたまりごとに同時に訳す（速くするため）
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(4, len(chunks))) as ex:
            results = list(ex.map(lambda c: _one(c, email, fallback), chunks))
    out = "".join(results) if all(re.search(r"[ぁ-んァ-ン一-龥]", r) for r in results) else " ".join(results)
    out = _polish(out)
    with _lock:
        c = _load()
        c[_key(text)] = out
        if len(c) > 5000:  # 大きくなりすぎないように古いものを消す
            for k in list(c)[:1000]:
                del c[k]
        save_json(CACHE_FILE, c)
    return out


def translate_many(texts: list[str], email: str | None = None, fallback: bool = True) -> list[str | None]:
    """たくさんの文を、同時にまとめて訳す（検索結果の一覧など）。失敗したものは None。"""
    from concurrent.futures import ThreadPoolExecutor

    def one(tx):
        try:
            return translate(tx, email, fallback)
        except TranslateLimit:
            return None
    if not texts:
        return []
    with ThreadPoolExecutor(max_workers=min(6, len(texts))) as ex:
        return list(ex.map(one, texts))
