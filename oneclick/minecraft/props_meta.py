"""server.properties の全設定の説明（日本語）と、正しい値かのチェック。

- since / until … その設定があるバージョン（since 以上、until 未満）
- live … サーバーが動いている間に「管理用の窓口」で即時に変えられるもの（窓口の名前, 値の名前）
- managed … このソフトが自動で管理する設定（普通は手で変えない）
"""
from __future__ import annotations

import re

from . import versions

GROUPS = [
    ("basic", "基本"), ("play", "遊び方"), ("world", "ワールド（作るときだけ使う）"), ("safety", "参加と安全"),
    ("perf", "重さ・通信"), ("packs", "リソースパック・データパック"), ("remote", "外から操作"), ("advanced", "上級者向け"),
]

B, I, S, E, P = "bool", "int", "str", "enum", "secret"
DIFF = [("peaceful", "ピース（敵が出ない）"), ("easy", "イージー"), ("normal", "ノーマル"), ("hard", "ハード")]
MODES = [("survival", "サバイバル"), ("creative", "クリエイティブ"), ("adventure", "アドベンチャー"), ("spectator", "スペクテイター")]

META: list[dict] = [
    # ---- 基本
    dict(key="motd", label="サーバーの説明文", group="basic", type=S, max_len=120, live=("motd", "message"),
         help="サーバー一覧で名前の下に出る文です。"),
    dict(key="max-players", label="最大人数", group="basic", type=I, min=1, max=1000, live=("max_players", "max"),
         help="同時に入れる人数です。"),
    dict(key="server-port", label="ポート番号", group="basic", type=I, min=1024, max=65535,
         help="PCの中の「部屋番号」。普通は 25565 です。変えたら友達に伝えるアドレスやポート開放も変えてください。"),
    dict(key="server-ip", label="使うネットのアドレス", group="advanced", type=S,
         help="普通は空のまま（すべてのアドレスで待つ）。PCに複数のネットがあるときだけ指定します。"),
    dict(key="gamemode", label="ゲームモード", group="basic", type=E, options=MODES, live=("game_mode", "mode"),
         help="はじめて入った人のゲームモードです。"),
    dict(key="force-gamemode", label="毎回このゲームモードにする", group="play", type=B, live=("force_game_mode", "force"),
         help="ON だと、入るたびに上のゲームモードに戻します。"),
    dict(key="difficulty", label="難しさ", group="basic", type=E, options=DIFF, live=("difficulty", "difficulty")),
    dict(key="hardcore", label="ハードコア", group="world", type=B,
         help="死ぬとスペクテイターになり、復活できません。新しく作るワールドだけに効きます。"),
    # ---- 遊び方
    dict(key="pvp", label="プレイヤー同士の攻撃（PvP）", group="play", type=B, until=versions.PROPS_TO_GAMERULES,
         help="1.21.9 以降は「ゲームルール」の pvp で設定します。"),
    dict(key="allow-nether", label="ネザーに行ける", group="play", type=B, until=versions.PROPS_TO_GAMERULES),
    dict(key="spawn-monsters", label="モンスターがわく", group="play", type=B, until=versions.PROPS_TO_GAMERULES),
    dict(key="spawn-animals", label="動物がわく", group="play", type=B, until=(1, 20, 2)),
    dict(key="spawn-npcs", label="村人がわく", group="play", type=B, until=(1, 20, 2)),
    dict(key="enable-command-block", label="コマンドブロックを使える", group="play", type=B, until=versions.PROPS_TO_GAMERULES),
    dict(key="allow-flight", label="飛行を許す", group="play", type=B, live=("allow_flight", "allowed"),
         help="OFF だと、MODの乗り物などで浮いたときに「飛んでいる」とみなされてキックされることがあります。MODサーバーは ON がおすすめです。"),
    dict(key="spawn-protection", label="スポーン地点の保護（ブロック）", group="play", type=I, min=0, max=1000,
         live=("spawn_protection_radius", "radius"), help="スポーン地点のまわりを、OP 以外壊せないようにする範囲です。0 で保護なしです。"),
    dict(key="player-idle-timeout", label="放置した人をキック（分）", group="play", type=I, min=0, max=10000,
         help="この分数なにもしないとキックします。0 でキックしません。"),
    dict(key="max-world-size", label="ワールドの大きさの上限（ブロック）", group="play", type=I, min=1, max=29999984,
         help="中心からこの距離が世界のはしになります。"),
    dict(key="enable-code-of-conduct", label="行動ルールを表示する", group="safety", type=B, since=versions.MSMP,
         help="入る人に、サーバーのルールを表示して同意してもらいます。ルールは「codeofconduct」フォルダに「ja_jp.txt」などで書きます。"),
    # ---- ワールド
    dict(key="level-name", label="ワールドのフォルダ名", group="world", type=S, max_len=80,
         help="変えると新しいワールドになります（前のワールドは残ります）。"),
    dict(key="level-seed", label="シード値", group="world", type=S, max_len=60, help="ワールドの作り方の番号です。空ならランダムです。"),
    dict(key="level-type", label="ワールドの種類", group="world", type=E,
         options=[("minecraft:normal", "普通"), ("minecraft:flat", "平ら（スーパーフラット）"),
                  ("minecraft:large_biomes", "大きなバイオーム"), ("minecraft:amplified", "アンプリファイド（高い山）"),
                  ("minecraft:single_biome_surface", "1つのバイオームだけ")]),
    dict(key="generate-structures", label="村や遺跡などの建造物を作る", group="world", type=B),
    dict(key="generator-settings", label="ワールド生成の細かい設定（JSON）", group="world", type=S, max_len=4000,
         help="上級者向けです。平らなワールドの層などを JSON で指定します。"),
    # ---- 参加と安全
    dict(key="white-list", label="ホワイトリストを使う", group="safety", type=B, live=("use_allowlist", "use"),
         help="ON だと、名簿にいる人しか入れません（おすすめ）。"),
    dict(key="enforce-whitelist", label="名簿から外した人をすぐ追い出す", group="safety", type=B,
         live=("enforce_allowlist", "enforce")),
    dict(key="online-mode", label="正規版チェック", group="safety", type=B,
         danger="OFF にすると、誰でも他人の名前で入れてしまいます。普通は ON のままにしてください。",
         help="正規版のマイクラを持っている人だけ入れるようにします。"),
    dict(key="enforce-secure-profile", label="チャットの署名を必須にする", group="safety", type=B,
         help="チャットの本人確認です。古い・改造したランチャーで入れない人がいれば OFF にします。"),
    dict(key="prevent-proxy-connections", label="VPN・プロキシからの接続を防ぐ", group="safety", type=B,
         danger="このソフトの「友達用パックを自動で配る」や playit を使っていると、誰も入れなくなることがあります。"),
    dict(key="hide-online-players", label="参加中の人の名前を隠す", group="safety", type=B, live=("hide_online_players", "hide"),
         help="サーバー一覧で、参加中の人の名前を見せません。"),
    dict(key="log-ips", label="ログにIPアドレスを残す", group="safety", type=B),
    dict(key="accepts-transfers", label="ほかのサーバーからの移動を受け入れる", group="safety", type=B, since=(1, 20, 5),
         live=("accept_transfers", "accept")),
    dict(key="chat-spam-threshold-seconds", label="チャット連投でキックする強さ", group="safety", type=I, min=0, max=1000,
         since=(26, 2), help="0 でキックしません。"),
    dict(key="command-spam-threshold-seconds", label="コマンド連投でキックする強さ", group="safety", type=I, min=0, max=1000,
         since=(26, 2), help="0 でキックしません。"),
    dict(key="op-permission-level", label="OPの強さ（1〜4）", group="safety", type=I, min=1, max=4,
         live=("operator_user_permission_level", "level"), help="4 がすべてのコマンドを使えます。普通は 4 です。"),
    dict(key="function-permission-level", label="データパックの関数の強さ（1〜4）", group="advanced", type=I, min=1, max=4),
    dict(key="broadcast-console-to-ops", label="コンソールの結果をOPに見せる", group="advanced", type=B),
    dict(key="broadcast-rcon-to-ops", label="RCONの結果をOPに見せる", group="advanced", type=B),
    dict(key="enable-status", label="サーバー一覧に表示する", group="safety", type=B, live=("status_replies", "enable"),
         help="OFF だと、サーバー一覧で「オンライン」と表示されません（入ることはできます）。"),
    # ---- 重さ・通信
    dict(key="view-distance", label="見える距離（チャンク）", group="perf", type=I, min=2, max=32, live=("view_distance", "distance"),
         help="大きいほど遠くまで見えますが重くなります。MODが多いなら 6〜8 です。"),
    dict(key="simulation-distance", label="動く範囲（チャンク）", group="perf", type=I, min=2, max=32,
         live=("simulation_distance", "distance"), help="生き物や機械が動く範囲です。小さいほど軽いです。6〜8 がおすすめです。"),
    dict(key="entity-broadcast-range-percentage", label="生き物を送る距離（%）", group="perf", type=I, min=10, max=1000,
         live=("entity_broadcast_range", "percentage_points"), help="小さくすると通信が軽くなります。100 が普通です。"),
    dict(key="pause-when-empty-seconds", label="誰もいないとき止める（秒）", group="perf", type=I, min=0, max=86400,
         since=(1, 21, 2), live=("pause_when_empty_seconds", "seconds"),
         help="誰もいない時間がこの秒数つづくと、ワールドの時間を止めて軽くします。0 で止めません。"),
    dict(key="network-compression-threshold", label="通信を圧縮する大きさ", group="perf", type=I, min=-1, max=65535,
         help="普通は 256 です。-1 で圧縮しません。"),
    dict(key="rate-limit", label="通信の回数の上限", group="perf", type=I, min=0, max=100000, help="0 で上限なしです。"),
    dict(key="max-tick-time", label="固まったと判断する時間（ミリ秒）", group="perf", type=I, min=-1, max=6000000,
         help="1コマの処理がこの時間を超えると、サーバーを止めます。重いMODパックなら -1（止めない）もありです。"),
    dict(key="max-chained-neighbor-updates", label="ブロック更新のつながりの上限", group="advanced", type=I, min=-1,
         max=100000000),
    dict(key="sync-chunk-writes", label="ワールドを確実に書き込む", group="perf", type=B,
         help="ON だと停電などでワールドが壊れにくい（少し重い）。"),
    dict(key="region-file-compression", label="ワールドの保存形式", group="advanced", type=E, since=(1, 20, 5),
         options=[("deflate", "普通（deflate）"), ("lz4", "速い・少し大きい（lz4）"), ("none", "圧縮しない")]),
    dict(key="use-native-transport", label="Linux用の高速な通信", group="advanced", type=B),
    # ---- パック
    dict(key="resource-pack", label="リソースパックのURL", group="packs", type=S, max_len=1000,
         help="入った人に自動で配るリソースパックのダウンロード先（https）。"),
    dict(key="resource-pack-sha1", label="リソースパックの指紋（SHA-1）", group="packs", type=S, max_len=40),
    dict(key="resource-pack-id", label="リソースパックのID（UUID）", group="packs", type=S, max_len=36),
    dict(key="resource-pack-prompt", label="リソースパックの説明文", group="packs", type=S, max_len=400),
    dict(key="require-resource-pack", label="リソースパックを必須にする", group="packs", type=B,
         help="ON だと、受け入れない人は入れません。"),
    dict(key="initial-enabled-packs", label="最初から使うデータパック", group="packs", type=S, max_len=1000),
    dict(key="initial-disabled-packs", label="最初は使わないデータパック", group="packs", type=S, max_len=1000),
    # ---- 外から操作
    dict(key="management-server-enabled", label="管理用の窓口", group="remote", type=B, since=versions.MSMP, managed=True),
    dict(key="management-server-host", label="管理用の窓口のアドレス", group="remote", type=S, since=versions.MSMP, managed=True),
    dict(key="management-server-port", label="管理用の窓口のポート", group="remote", type=I, since=versions.MSMP, managed=True),
    dict(key="management-server-secret", label="管理用の窓口の合言葉", group="remote", type=P, since=versions.MSMP, managed=True),
    dict(key="management-server-tls-enabled", label="管理用の窓口の暗号化", group="remote", type=B, since=versions.MSMP, managed=True),
    dict(key="management-server-tls-keystore", label="暗号化のかぎファイル", group="remote", type=S, since=versions.MSMP, managed=True),
    dict(key="management-server-tls-keystore-password", label="暗号化のかぎのパスワード", group="remote", type=P,
         since=versions.MSMP, managed=True),
    dict(key="management-server-allowed-origins", label="管理用の窓口に入れる相手", group="remote", type=S, since=versions.MSMP,
         managed=True),
    dict(key="status-heartbeat-interval", label="窓口に状態を知らせる間かく（秒）", group="remote", type=I, min=0, max=3600,
         since=versions.MSMP),
    dict(key="enable-rcon", label="RCON（外からコマンド）", group="remote", type=B, managed=True),
    dict(key="rcon.port", label="RCONのポート", group="remote", type=I, min=1024, max=65535, managed=True),
    dict(key="rcon.password", label="RCONのパスワード", group="remote", type=P, managed=True),
    dict(key="enable-query", label="Query（状態の問い合わせ）", group="remote", type=B, managed=True),
    dict(key="query.port", label="Queryのポート", group="remote", type=I, min=1024, max=65535, managed=True),
    dict(key="enable-jmx-monitoring", label="JMX（処理時間の数字）", group="remote", type=B, managed=True),
    # ---- 上級者向け
    dict(key="bug-report-link", label="不具合報告のリンク", group="advanced", type=S, max_len=500),
    dict(key="text-filtering-config", label="チャットのフィルター設定", group="advanced", type=S, max_len=2000),
    dict(key="text-filtering-version", label="チャットのフィルターの形式", group="advanced", type=I, min=0, max=1),
]
BY_KEY = {m["key"]: m for m in META}
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9.\-_]{0,80}$")


def applicable(meta: dict, mc: str) -> bool:
    return versions.in_range(mc, meta.get("since"), meta.get("until"))


def validate(key: str, value, mc: str):
    """値をチェックして、ファイルに書く文字にして返す。おかしければ ValueError。"""
    if not KEY_RE.match(key):
        raise ValueError(f"おかしな設定名です: {key}")
    value = "" if value is None else str(value)
    if "\n" in value or "\r" in value:
        raise ValueError("設定に改行は入れられません。")
    m = BY_KEY.get(key)
    if not m:
        if len(value) > 4000:
            raise ValueError(f"{key} の値が長すぎます。")
        return value
    label = m["label"]
    t = m["type"]
    if t == B:
        v = value.strip().lower()
        if v not in ("true", "false"):
            raise ValueError(f"「{label}」は ON か OFF です。")
        return v
    if t == I:
        v = value.strip()
        if not re.fullmatch(r"-?\d+", v):
            raise ValueError(f"「{label}」には数字を入れてください。")
        n = int(v)
        if "min" in m and n < m["min"] or "max" in m and n > m["max"]:
            raise ValueError(f"「{label}」は {m.get('min')}〜{m.get('max')} の数字にしてください。")
        return str(n)
    if t == E:
        allowed = [o[0] for o in m["options"]]
        if value not in allowed and value != "":
            # 古い書き方（normal / NORMAL など）も受け付ける
            low = value.lower()
            hit = next((a for a in allowed if a.split(":")[-1] == low), None)
            if not hit:
                raise ValueError(f"「{label}」の値がおかしいです。")
            return hit
        return value
    if len(value) > m.get("max_len", 2000):
        raise ValueError(f"「{label}」が長すぎます。")
    if key == "resource-pack" and value and not value.startswith(("https://", "http://")):
        raise ValueError("リソースパックのURLは https:// で始めてください。")
    return value


def describe(props: dict, mc: str, first_run: bool) -> list[dict]:
    """画面に出す一覧。サーバーを一度起動していれば、そのバージョンにある設定だけが出る。"""
    out = []
    for m in META:
        present = m["key"] in props
        if not present and (not first_run or not applicable(m, mc)):
            continue
        if present and not applicable(m, mc) and not first_run:
            pass  # ファイルにあるなら、そのバージョンで使えるもの
        item = {k: v for k, v in m.items() if k not in ("since", "until", "live")}
        item["value"] = props.get(m["key"], "")
        item["present"] = present
        item["live"] = bool(m.get("live"))
        out.append(item)
    known = set(BY_KEY)
    for k, v in sorted(props.items()):
        if k not in known:
            out.append({"key": k, "label": k, "group": "advanced", "type": S, "value": v, "present": True,
                        "unknown": True, "help": "このソフトが知らない設定です（MODや新しいバージョンのもの）。"})
    return out
