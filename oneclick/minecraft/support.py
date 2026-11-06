"""困ったときの「診断情報」を、1つのzipにまとめる（作者・詳しい人に送る用）。

入れるもの: ソフトの版・PCの情報・MODの一覧・診断の結果・ログ（記録）・クラッシュレポート
消すもの: 鍵（Webhook・APIキー・パスワード）・PCのユーザー名・IPアドレス・公開用のアドレス
ワールドのデータや、友達の持ち物などは入れない。
"""
from __future__ import annotations

import json
import os
import platform
import re
import sys
import time
import zipfile

from . import appinfo
from .paths import BASE_DIR, SETTINGS_FILE, load_json

SAFE_SETTING_KEYS = ("ui_mode", "style", "theme", "port_mode", "self_play", "hide_next_step", "tour_done",
                     "auto_update_check")
SECRET_PROP_KEYS = ("rcon.password", "management-server-secret", "server-ip", "query.port")

_RX = [
    # Discord の Webhook・トークンのようなもの
    (re.compile(r"https?://(?:[\w-]+\.)?discord(?:app)?\.com/api/webhooks/\S+", re.I), "<Webhookのアドレス（消しました）>"),
    # 鍵っぽい「名前=値」「名前: 値」
    (re.compile(r"(?im)\b([\w.\-]*(?:token|secret|password|passwd|api[_\-]?key|webhook|authorization)[\w.\-]*)(\s*[=:]\s*)(?:\"[^\"\n]*\"|'[^'\n]*'|[^\s,;\"']+)"),
     r"\1\2<消しました>"),
    # PCのユーザー名
    (re.compile(r"(?i)([A-Z]:[\\/]+Users[\\/]+)[^\\/\s\"'<>|:*?]+"), r"\1<user>"),
    (re.compile(r"(/(?:Users|home)/)[^/\s\"']+"), r"\1<user>"),
    # 公開用のドメイン（DDNS・トンネルなど）
    (re.compile(r"\b[\w\-]+(?:\.[\w\-]+)*\.(?:duckdns\.org|playit\.gg|ply\.gg|joinmc\.link|ddns\.net|no-ip\.(?:org|biz|info)|"
                r"hopto\.org|zapto\.org|servegame\.com|minecraft\.(?:fun|build))\b", re.I), "<ドメイン（消しました）>"),
    # IPv4（自分のPC内を表すものは残す）
    (re.compile(r"\b(?!127\.0\.0\.1\b|0\.0\.0\.0\b)(?:\d{1,3}\.){3}\d{1,3}\b"), "<IP>"),
    # UUID
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<uuid>"),
]


def scrub(text: str) -> str:
    for rx, rep in _RX:
        text = rx.sub(rep, text)
    return text


def _tail(path: str, max_bytes: int) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def _java_text() -> str:
    try:
        from . import javamgr
        d = getattr(javamgr, "JAVA_DIR", None) or os.path.join(BASE_DIR, "java")
        return ", ".join(sorted(os.listdir(d))[:8]) if os.path.isdir(d) else "なし"
    except Exception:  # noqa: BLE001
        return "不明"


def _ram_text() -> str:
    try:
        from . import api
        return f"{api.total_ram_mb()}MB"
    except Exception:  # noqa: BLE001
        return "不明"


def build(out_path: str, prof=None, issues: list[dict] | None = None, console_lines: list[str] | None = None) -> dict:
    """診断情報のzipを作る。"""
    files: dict[str, str] = {}
    lines = [
        "OneClickServerMC 診断情報",
        f"作成: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"ソフトの版: {appinfo.VERSION}",
        f"動き方: {'exe' if getattr(sys, 'frozen', False) else 'Python ' + platform.python_version()}",
        f"OS: {platform.platform()}",
        f"PCのメモリ: {_ram_text()}",
        f"用意されているJava: {_java_text()}",
    ]
    st = load_json(SETTINGS_FILE, {})
    lines.append("設定（鍵は含みません）: " + json.dumps({k: st.get(k) for k in SAFE_SETTING_KEYS if k in st}, ensure_ascii=False))
    if prof is not None:
        lines += ["", "【サーバー】",
                  f"マイクラ: {prof.get('mc')}",
                  f"種類: {prof.get('loader')} {prof.get('loader_version') or ''}",
                  f"メモリ設定: {prof.get('memory_mb')}MB",
                  f"自動バックアップ: {prof.get('backup_interval_min')}分ごと"]
        try:
            from .modrinth import ModList
            ml = ModList(prof.dir)
            lines += ["", f"【MOD一覧】（{len(ml.items)}個）"]
            for m in ml.items:
                flag = "" if m.get("enabled", True) else " [OFF]"
                where = m.get("location") or ""
                lines.append(f"- {m.get('title')} {m.get('version_number') or ''} ({m.get('path')}) {where}{flag}")
        except Exception as e:  # noqa: BLE001
            lines.append(f"（MOD一覧を読めませんでした: {e}）")
    files["info.txt"] = "\n".join(lines) + "\n"

    if issues is not None:
        out = ["診断の結果", ""]
        for i in issues:
            out.append(f"[{i.get('level')}] {i.get('title')}")
            if i.get("detail"):
                out.append(f"    {i['detail']}")
            if i.get("fix"):
                out.append(f"    どうする: {i['fix']}")
        files["diagnosis.txt"] = "\n".join(out) + "\n"

    if prof is not None:
        latest = os.path.join(prof.dir, "logs", "latest.log")
        if os.path.exists(latest):
            files["logs/latest.log"] = _tail(latest, 400_000)
        crash_dir = os.path.join(prof.dir, "crash-reports")
        if os.path.isdir(crash_dir):
            reps = sorted((os.path.join(crash_dir, f) for f in os.listdir(crash_dir)), key=os.path.getmtime)
            if reps:
                files["crash-reports/" + os.path.basename(reps[-1])] = _tail(reps[-1], 200_000)
        props = os.path.join(prof.dir, "server.properties")
        if os.path.exists(props):
            kept = []
            for ln in _tail(props, 60_000).splitlines():
                key = ln.split("=", 1)[0].strip()
                kept.append(f"{key}=<消しました>" if key in SECRET_PROP_KEYS and "=" in ln else ln)
            files["server.properties"] = "\n".join(kept) + "\n"
    if console_lines:
        files["logs/screen-log.txt"] = "\n".join(console_lines[-600:]) + "\n"
    err = os.path.join(BASE_DIR, "error.log")
    if os.path.exists(err):
        files["logs/app-error.log"] = _tail(err, 100_000)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(name, scrub(text))
        z.writestr("はじめにお読みください.txt",
                   "このzipは、困ったときに、原因を調べてもらうためのものです。\n"
                   "鍵（Webhook・APIキー・パスワード）・PCのユーザー名・IPアドレス・公開用のアドレスは、消してあります。\n"
                   "ワールドのデータは入っていません。\n"
                   "念のため、送る前に、中身を見て確認してください。\n")
    return {"files": sorted(files), "bytes": os.path.getsize(out_path)}
