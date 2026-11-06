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
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()


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


def translate(text: str, email: str | None = None, fallback: bool = True) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    hit = cached(text)
    if hit:
        return hit
    results = []
    use_fallback = False
    for chunk in _chunks(text):
        if not use_fallback:
            try:
                results.append(_mymemory(chunk, email))
                continue
            except TranslateLimit:
                if not fallback:
                    raise
                use_fallback = True  # 上限などのときは、残りも予備で訳す
        results.append(_fallback(chunk))
    out = "".join(results) if all(re.search(r"[ぁ-んァ-ン一-龥]", r) for r in results) else " ".join(results)
    with _lock:
        c = _load()
        c[_key(text)] = out
        if len(c) > 5000:  # 大きくなりすぎないように古いものを消す
            for k in list(c)[:1000]:
                del c[k]
        save_json(CACHE_FILE, c)
    return out
