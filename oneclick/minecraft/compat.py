"""MODが「どのマイクラのバージョン・どの種類（ローダー）」で使えるかを調べる。"""
from __future__ import annotations

import re
import threading

from . import loaders, net
from .modrinth import API

MAIN_LOADERS = ("fabric", "neoforge", "forge")
_cache: dict[str, list] = {}
_lock = threading.Lock()


def all_versions(pid: str) -> list[dict]:
    with _lock:
        if pid in _cache:
            return _cache[pid]
    if ":" in str(pid):   # CurseForge など、Modrinth ではない MOD はここでは調べられない
        raise net.NetError(f"{pid} は Modrinth の MOD ではありません")
    data = net.get_json(f"{API}/project/{pid}/version", timeout=20, retries=1)
    with _lock:
        _cache[pid] = data
    return data


def support_map(pid: str) -> dict[str, set[str]]:
    """{ローダー: {使えるマイクラのバージョン}}"""
    out: dict[str, set[str]] = {}
    for v in all_versions(pid):
        for ld in v.get("loaders", []):
            out.setdefault(ld, set()).update(v.get("game_versions", []))
    # Quilt は Fabric 用MODも動く
    if "fabric" in out:
        out.setdefault("quilt", set()).update(out["fabric"])
    return out


def release_order() -> list[str]:
    try:
        return loaders.release_versions()
    except net.NetError:
        return []


def _vkey(v: str):
    return [int(x) for x in re.findall(r"\d+", v)]


def summarize(versions, order: list[str] | None = None, limit=6) -> str:
    """["1.20.1","1.20.2","1.20.4","1.21.1"] → "1.20.1〜1.20.2, 1.20.4, 1.21.1" のように短くする。"""
    order = order if order is not None else release_order()
    rel = [v for v in versions if v in set(order)] if order else list(versions)
    if not rel:
        return "なし"
    if order:
        pos = {v: i for i, v in enumerate(reversed(order))}  # 古い順の番号
        rel.sort(key=lambda v: pos[v])
    else:
        rel.sort(key=_vkey)
    groups = []
    start = prev = rel[0]
    for v in rel[1:]:
        if order and pos[v] == pos[prev] + 1:
            prev = v
            continue
        groups.append((start, prev))
        start = prev = v
    groups.append((start, prev))
    texts = [a if a == b else f"{a}〜{b}" for a, b in groups]
    if len(texts) > limit:
        texts = texts[:2] + ["…"] + texts[-(limit - 3):]
    return "、".join(texts)


def matrix(pid: str) -> list[dict]:
    """詳細画面用：ローダーごとの対応バージョン。"""
    sm = support_map(pid)
    order = release_order()
    out = []
    for ld in MAIN_LOADERS + ("quilt", "paper", "purpur", "spigot", "bukkit"):
        if ld in sm and (ld != "quilt" or "quilt" in {x for v in all_versions(pid) for x in v.get("loaders", [])}):
            newest = None
            if order:
                rel = [v for v in order if v in sm[ld]]
                newest = rel[0] if rel else None
            out.append({"loader": ld, "label": loaders.LOADER_LABEL.get(ld, ld.capitalize()),
                        "text": summarize(sm[ld], order), "newest": newest})
    return out


def works_with(pid: str, mc: str, loader: str) -> bool:
    from .modrinth import loader_keys
    sm = support_map(pid)
    return any(mc in sm.get(k, set()) for k in (loader_keys(loader) or [loader]))


def plan(pids: list[str], titles: dict[str, str] | None = None, top=8) -> dict:
    """選んだMODがなるべく全部使える「マイクラのバージョン × 種類」を探す。"""
    titles = titles or {}
    order = release_order()
    maps = {}
    errors = []
    # 1つずつ順番に調べると、MODが多いときに何分もかかって固まるので、同時にまとめて調べる。
    # 全体で 45秒までにして、間に合わなかった MOD は「調べられなかった」として先へ進む。
    from concurrent.futures import ThreadPoolExecutor, wait

    def one(pid):
        return pid, support_map(pid)

    ex = ThreadPoolExecutor(max_workers=8)
    futs = {ex.submit(one, pid): pid for pid in dict.fromkeys(pids)}
    done, not_done = wait(futs, timeout=45)
    for f in done:
        pid = futs[f]
        try:
            maps[pid] = f.result()[1]
        except Exception:
            errors.append(titles.get(pid, pid))
    for f in not_done:
        f.cancel()
        errors.append(titles.get(futs[f], futs[f]))
    ex.shutdown(wait=False, cancel_futures=True)
    rows = []
    candidates = order[:60] if order else sorted({v for m in maps.values() for s in m.values() for v in s},
                                                  key=_vkey, reverse=True)
    for idx, mc in enumerate(candidates):
        for ld in MAIN_LOADERS:
            if ld == "neoforge" and not loaders.neoforge_prefix(mc):
                continue  # 1.20.1 以前は NeoForge を選べない
            ok = [p for p, m in maps.items() if mc in m.get(ld, set())]
            if not ok:
                continue
            rows.append({"mc": mc, "loader": ld, "label": loaders.LOADER_LABEL[ld],
                         "ok": ok, "ng": [p for p in maps if p not in ok], "count": len(ok), "idx": idx})
    lorder = {l: i for i, l in enumerate(MAIN_LOADERS)}
    rows.sort(key=lambda r: (-r["count"], r["idx"], lorder[r["loader"]]))
    # 同じバージョンばかり並ばないように、上位を選ぶ
    picked, seen = [], set()
    for r in rows:
        k = (r["mc"], r["loader"])
        if k in seen:
            continue
        seen.add(k)
        picked.append(r)
        if len(picked) >= top:
            break
    for r in picked:
        r["ng_titles"] = [titles.get(p, p) for p in r["ng"]]
    per_mod = {p: [{"label": loaders.LOADER_LABEL.get(l, l), "text": summarize(m[l], order)}
                   for l in MAIN_LOADERS if l in m] for p, m in maps.items()}
    support = {p: {l: sorted(m.get(l, set()) & set(candidates)) for l in MAIN_LOADERS} for p, m in maps.items()}
    return {"rows": picked, "total": len(maps), "errors": errors, "per_mod": per_mod, "support": support}
