"""ゲームルールの日本語の説明と、新旧の名前の対応。

1.21.11 から名前が変わった（keepInventory → minecraft:keep_inventory など）。
意味が反対になったもの（disableRaids → raids など）もあるので、説明は名前ごとに持つ。
"""
from __future__ import annotations

# 新しい名前: (グループ, 日本語の名前, 説明)
NEW = {
    "minecraft:keep_inventory": ("player", "死んでも持ち物を残す", "ON だと、死んでもアイテムと経験値を落としません。"),
    "minecraft:natural_health_regeneration": ("player", "満腹で体力が回復する", ""),
    "minecraft:immediate_respawn": ("player", "すぐに復活する", "死んだときの画面を出さずに、すぐ復活します。"),
    "minecraft:respawn_radius": ("player", "復活する場所の広さ（ブロック）", "スポーン地点のまわり、この範囲のどこかで復活します。"),
    "minecraft:players_sleeping_percentage": ("player", "朝にするのに寝る人の割合（%）", "0 なら1人でも寝れば朝です。100 なら全員です。"),
    "minecraft:pvp": ("player", "プレイヤー同士の攻撃（PvP）", ""),
    "minecraft:drowning_damage": ("player", "おぼれるダメージ", ""),
    "minecraft:fall_damage": ("player", "落下ダメージ", ""),
    "minecraft:fire_damage": ("player", "火のダメージ", ""),
    "minecraft:freeze_damage": ("player", "こごえるダメージ", ""),
    "minecraft:show_death_messages": ("chat", "死んだメッセージを出す", ""),
    "minecraft:show_advancement_messages": ("chat", "進捗のメッセージを出す", ""),
    "minecraft:send_command_feedback": ("chat", "コマンドの結果をチャットに出す", ""),
    "minecraft:command_block_output": ("chat", "コマンドブロックの結果をチャットに出す", ""),
    "minecraft:log_admin_commands": ("chat", "管理コマンドを記録する", ""),
    "minecraft:reduced_debug_info": ("chat", "F3 画面の情報を減らす", "座標などが見えにくくなります。"),
    "minecraft:locator_bar": ("chat", "ほかの人の位置を表示するバー", ""),
    "minecraft:global_sound_events": ("chat", "ボスなどの音を全員に聞かせる", ""),
    "minecraft:advance_time": ("world", "昼と夜が進む", "OFF だと時間が止まります。"),
    "minecraft:advance_weather": ("world", "天気が変わる", ""),
    "minecraft:random_tick_speed": ("world", "作物や草が育つ速さ", "普通は 3 です。大きくすると速く育ちますが重くなります。"),
    "minecraft:fire_spread_radius_around_player": ("world", "火が広がる範囲", "0 で火が広がりません。"),
    "minecraft:mob_griefing": ("world", "生き物がブロックを壊す", "クリーパーの爆発やエンダーマンがブロックを持ち去るなど。"),
    "minecraft:spread_vines": ("world", "ツタが広がる", ""),
    "minecraft:water_source_conversion": ("world", "水の無限水源ができる", ""),
    "minecraft:lava_source_conversion": ("world", "溶岩の無限源ができる", ""),
    "minecraft:max_snow_accumulation_height": ("world", "雪が積もる高さ", ""),
    "minecraft:tnt_explodes": ("world", "TNT が爆発する", ""),
    "minecraft:projectiles_can_break_blocks": ("world", "飛び道具でブロックが壊れる", ""),
    "minecraft:block_explosion_drop_decay": ("world", "ブロックの爆発でアイテムが減る", ""),
    "minecraft:mob_explosion_drop_decay": ("world", "生き物の爆発でアイテムが減る", ""),
    "minecraft:tnt_explosion_drop_decay": ("world", "TNT の爆発でアイテムが減る", ""),
    "minecraft:allow_entering_nether_using_portals": ("world", "ネザーゲートでネザーに行ける", ""),
    "minecraft:ender_pearls_vanish_on_death": ("world", "死ぬと投げたエンダーパールが消える", ""),
    "minecraft:max_minecart_speed": ("world", "トロッコの最高速度", ""),
    "minecraft:spawn_mobs": ("mobs", "生き物がわく", ""),
    "minecraft:spawn_monsters": ("mobs", "モンスターがわく", ""),
    "minecraft:spawn_phantoms": ("mobs", "ファントムが出る", "寝ていない人のところにファントムが出ます。"),
    "minecraft:spawn_patrols": ("mobs", "略奪者の巡回が出る", ""),
    "minecraft:spawn_wandering_traders": ("mobs", "行商人が来る", ""),
    "minecraft:spawn_wardens": ("mobs", "ウォーデンが出る", ""),
    "minecraft:spawner_blocks_work": ("mobs", "スポナーが動く", ""),
    "minecraft:raids": ("mobs", "襲撃が起きる", ""),
    "minecraft:mob_drops": ("mobs", "生き物がアイテムを落とす", ""),
    "minecraft:entity_drops": ("mobs", "トロッコなどがアイテムを落とす", ""),
    "minecraft:block_drops": ("world", "壊したブロックがアイテムになる", ""),
    "minecraft:forgive_dead_players": ("mobs", "死んだ人への怒りを忘れる", ""),
    "minecraft:universal_anger": ("mobs", "怒った生き物が全員をねらう", ""),
    "minecraft:max_entity_cramming": ("mobs", "押しつぶされる生き物の数", "この数より多く重なるとダメージです。0 で無効です。"),
    "minecraft:spectators_generate_chunks": ("advanced", "スペクテイターも地形を作る", ""),
    "minecraft:player_movement_check": ("advanced", "プレイヤーの動きをチェックする", ""),
    "minecraft:elytra_movement_check": ("advanced", "エリトラの動きをチェックする", ""),
    "minecraft:limited_crafting": ("advanced", "覚えたレシピしか作れない", ""),
    "minecraft:command_blocks_work": ("advanced", "コマンドブロックが動く", ""),
    "minecraft:max_command_sequence_length": ("advanced", "コマンドの連続の上限", ""),
    "minecraft:max_command_forks": ("advanced", "コマンドの分かれ道の上限", ""),
    "minecraft:max_block_modifications": ("advanced", "1回のコマンドで変えられるブロック数", ""),
    "minecraft:players_nether_portal_default_delay": ("advanced", "ネザーゲートの待ち時間（tick）", ""),
    "minecraft:players_nether_portal_creative_delay": ("advanced", "クリエイティブのネザーゲート待ち時間（tick）", ""),
}

# 古い名前: (新しい名前, 型, 意味が反対か)
OLD = {
    "keepInventory": ("minecraft:keep_inventory", "boolean", False),
    "naturalRegeneration": ("minecraft:natural_health_regeneration", "boolean", False),
    "doImmediateRespawn": ("minecraft:immediate_respawn", "boolean", False),
    "spawnRadius": ("minecraft:respawn_radius", "integer", False),
    "playersSleepingPercentage": ("minecraft:players_sleeping_percentage", "integer", False),
    "pvp": ("minecraft:pvp", "boolean", False),
    "drowningDamage": ("minecraft:drowning_damage", "boolean", False),
    "fallDamage": ("minecraft:fall_damage", "boolean", False),
    "fireDamage": ("minecraft:fire_damage", "boolean", False),
    "freezeDamage": ("minecraft:freeze_damage", "boolean", False),
    "showDeathMessages": ("minecraft:show_death_messages", "boolean", False),
    "announceAdvancements": ("minecraft:show_advancement_messages", "boolean", False),
    "sendCommandFeedback": ("minecraft:send_command_feedback", "boolean", False),
    "commandBlockOutput": ("minecraft:command_block_output", "boolean", False),
    "logAdminCommands": ("minecraft:log_admin_commands", "boolean", False),
    "reducedDebugInfo": ("minecraft:reduced_debug_info", "boolean", False),
    "locatorBar": ("minecraft:locator_bar", "boolean", False),
    "globalSoundEvents": ("minecraft:global_sound_events", "boolean", False),
    "doDaylightCycle": ("minecraft:advance_time", "boolean", False),
    "doWeatherCycle": ("minecraft:advance_weather", "boolean", False),
    "randomTickSpeed": ("minecraft:random_tick_speed", "integer", False),
    "doFireTick": ("minecraft:fire_spread_radius_around_player", "boolean", False),
    "mobGriefing": ("minecraft:mob_griefing", "boolean", False),
    "doVinesSpread": ("minecraft:spread_vines", "boolean", False),
    "waterSourceConversion": ("minecraft:water_source_conversion", "boolean", False),
    "lavaSourceConversion": ("minecraft:lava_source_conversion", "boolean", False),
    "snowAccumulationHeight": ("minecraft:max_snow_accumulation_height", "integer", False),
    "tntExplodes": ("minecraft:tnt_explodes", "boolean", False),
    "projectilesCanBreakBlocks": ("minecraft:projectiles_can_break_blocks", "boolean", False),
    "blockExplosionDropDecay": ("minecraft:block_explosion_drop_decay", "boolean", False),
    "mobExplosionDropDecay": ("minecraft:mob_explosion_drop_decay", "boolean", False),
    "tntExplosionDropDecay": ("minecraft:tnt_explosion_drop_decay", "boolean", False),
    "allowEnteringNetherUsingPortals": ("minecraft:allow_entering_nether_using_portals", "boolean", False),
    "enderPearlsVanishOnDeath": ("minecraft:ender_pearls_vanish_on_death", "boolean", False),
    "minecartMaxSpeed": ("minecraft:max_minecart_speed", "integer", False),
    "doMobSpawning": ("minecraft:spawn_mobs", "boolean", False),
    "spawnMonsters": ("minecraft:spawn_monsters", "boolean", False),
    "doInsomnia": ("minecraft:spawn_phantoms", "boolean", False),
    "doPatrolSpawning": ("minecraft:spawn_patrols", "boolean", False),
    "doTraderSpawning": ("minecraft:spawn_wandering_traders", "boolean", False),
    "doWardenSpawning": ("minecraft:spawn_wardens", "boolean", False),
    "spawnerBlocksEnabled": ("minecraft:spawner_blocks_work", "boolean", False),
    "disableRaids": ("minecraft:raids", "boolean", True),
    "doMobLoot": ("minecraft:mob_drops", "boolean", False),
    "doEntityDrops": ("minecraft:entity_drops", "boolean", False),
    "doTileDrops": ("minecraft:block_drops", "boolean", False),
    "forgiveDeadPlayers": ("minecraft:forgive_dead_players", "boolean", False),
    "universalAnger": ("minecraft:universal_anger", "boolean", False),
    "maxEntityCramming": ("minecraft:max_entity_cramming", "integer", False),
    "spectatorsGenerateChunks": ("minecraft:spectators_generate_chunks", "boolean", False),
    "disablePlayerMovementCheck": ("minecraft:player_movement_check", "boolean", True),
    "disableElytraMovementCheck": ("minecraft:elytra_movement_check", "boolean", True),
    "doLimitedCrafting": ("minecraft:limited_crafting", "boolean", False),
    "commandBlocksEnabled": ("minecraft:command_blocks_work", "boolean", False),
    "maxCommandChainLength": ("minecraft:max_command_sequence_length", "integer", False),
    "maxCommandForkCount": ("minecraft:max_command_forks", "integer", False),
    "commandModificationBlockLimit": ("minecraft:max_block_modifications", "integer", False),
    "playersNetherPortalDefaultDelay": ("minecraft:players_nether_portal_default_delay", "integer", False),
    "playersNetherPortalCreativeDelay": ("minecraft:players_nether_portal_creative_delay", "integer", False),
}
INVERT_LABEL = {
    "disableRaids": "襲撃を起こさない", "disablePlayerMovementCheck": "プレイヤーの動きのチェックをしない",
    "disableElytraMovementCheck": "エリトラの動きのチェックをしない",
}

GROUPS = [("player", "プレイヤー"), ("world", "ワールド"), ("mobs", "生き物"), ("chat", "チャット・表示"),
          ("advanced", "上級者向け"), ("other", "そのほか（MODなど）")]

# おすすめのセット（新しい名前で書く。古いバージョンでは自動で古い名前にする）
PRESETS = [
    ("やさしいサバイバル", "死んでも持ち物が残る・1人寝れば朝・ファントムなし",
     {"minecraft:keep_inventory": True, "minecraft:players_sleeping_percentage": 0, "minecraft:spawn_phantoms": False}),
    ("建築に集中", "昼のまま・晴れのまま・モンスターがブロックを壊さない",
     {"minecraft:advance_time": False, "minecraft:advance_weather": False, "minecraft:mob_griefing": False}),
    ("普通に戻す", "上の2つで変えたものを元に戻す",
     {"minecraft:keep_inventory": False, "minecraft:players_sleeping_percentage": 100, "minecraft:spawn_phantoms": True,
      "minecraft:advance_time": True, "minecraft:advance_weather": True, "minecraft:mob_griefing": True}),
]

NEW_TO_OLD = {v[0]: k for k, v in OLD.items() if k != "doFireTick"}


def describe(key: str) -> dict:
    """ゲームルールの名前（新旧どちらでも）から、表示用の情報を作る。"""
    if key in OLD:
        new, _t, inverted = OLD[key]
        g, label, help_ = NEW.get(new, ("other", key, ""))
        if inverted:
            label, help_ = INVERT_LABEL.get(key, label), ""
        return {"group": g, "label": label, "help": help_}
    k = key if ":" in key else f"minecraft:{key}"
    if k in NEW:
        g, label, help_ = NEW[k]
        return {"group": g, "label": label, "help": help_}
    return {"group": "other", "label": key.split(":")[-1].replace("_", " "), "help": "MODなどが追加したルールです。"}


def preset_for(rules_new: dict, use_old: bool) -> dict:
    if not use_old:
        return dict(rules_new)
    out = {}
    for k, v in rules_new.items():
        old = NEW_TO_OLD.get(k)
        if old:
            out[old] = (not v) if OLD[old][2] and isinstance(v, bool) else v
    return out
