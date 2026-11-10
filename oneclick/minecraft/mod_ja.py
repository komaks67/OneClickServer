"""MODの日本語説明（人気MODは手書き）と、ジャンル名の日本語。"""
from __future__ import annotations

import re

CATEGORY_JA = {
    "adventure": "冒険", "cursed": "ネタ", "decoration": "建築・飾り", "economy": "お金・経済",
    "equipment": "装備", "food": "料理・食べ物", "game-mechanics": "ゲームの仕組み",
    "library": "前提MOD", "magic": "魔法", "management": "管理", "minigame": "ミニゲーム",
    "mobs": "生き物", "optimization": "軽量化", "social": "交流", "storage": "収納",
    "technology": "工業・技術", "transportation": "移動・乗り物", "utility": "便利",
    "worldgen": "地形・世界", "fabric": "Fabric", "forge": "Forge", "neoforge": "NeoForge",
    "quilt": "Quilt",
}
LOADER_CATS = {"fabric", "forge", "neoforge", "quilt", "liteloader", "rift", "modloader", "bukkit",
               "paper", "spigot", "purpur", "folia", "velocity", "bungeecord", "waterfall", "sponge",
               "datapack", "iris", "optifine", "minecraft", "canvas", "vanilla"}

_LIB = "他のMODを動かすための部品（前提MOD）。これだけ入れても遊び方は変わりません。"

# (キー（slug や 名前）, 日本語の説明)
_ENTRIES: list[tuple[tuple[str, ...], str]] = [
    # ---- プラグイン（Paper / Purpur） ----
    (("luckperms",), "「誰がどのコマンドを使えるか」を細かく決める権限管理プラグインです。管理プラグインの定番です。"),
    (("essentialsx", "essentials"), "/home・/warp・/tpa・あいさつ文など、便利なコマンドをまとめて追加する定番プラグインです。"),
    (("worldedit",), "広い範囲のブロックを一気に置く・消す・コピーできる建築用ツールです。"),
    (("worldguard",), "土地を保護して、荒らしやTNT・火の広がりを防ぎます。WorldEdit が必要です。"),
    (("coreprotect",), "誰がどのブロックを壊した・置いたかを記録し、荒らしを元に戻せます。マルチで特におすすめです。"),
    (("chunky",), "遊ぶ前に地形を作っておく（事前生成）。このソフトの「事前生成」で使います。"),
    (("spark",), "サーバーのどこが重いかを調べます。重さの原因探しに役立ちます。"),
    (("viaversion",), "サーバーと違うバージョンのマイクラでも入れるようにします。"),
    (("viabackwards",), "サーバーより古いバージョンのマイクラでも入れるようにする（ViaVersion と一緒に使う）。"),
    (("geyser", "geysermc"), "統合版（スマホ・Switch など）のプレイヤーも Java 版サーバーに入れるようにします。"),
    (("floodgate",), "統合版のプレイヤーが Java 版のアカウントなしで入れるようにする（Geyser と一緒に使う）。"),
    (("vault",), "お金・権限などをプラグイン同士でつなぐ部品（前提プラグイン）。"),
    (("multiverse-core", "multiverse"), "ワールドを何個も作って行き来できるようにします。"),
    (("griefprevention",), "金のシャベルで自分の土地を囲って守れる、初心者向けの荒らし対策です。"),
    (("discordsrv",), "Discord のチャンネルとマイクラのチャットをつなぎます。"),
    (("dynmap", "bluemap", "squaremap"), "ワールドの地図を、ブラウザで見られるようにします。"),
    (("tab", "tab-was-taken"), "プレイヤー一覧（Tabキー）の見た目を変えます。"),
    (("simple-voice-chat", "voicechat"), "近くの人と声で話せる（遊ぶ人にもMODが必要）。"),
    (("clearlag", "clearlagg"), "落ちているアイテムなどを定期的に消してサーバーを軽くします。"),
    (("placeholderapi",), "ほかのプラグインで使う文字の置き換え機能（前提プラグイン）。"),
    # ---- 軽量化 ----
    (("sodium",), "描画をとても軽くしてFPS（なめらかさ）を大きく上げる、定番の軽量化MODです。遊ぶ人のPC用です。"),
    (("lithium",), "生き物の動きや当たり判定などゲーム内部の計算を効率化して、サーバーを軽くします。遊び方は変わりません。"),
    (("iris", "iris-shaders"), "影MOD（シェーダー）を使えるようにします。Sodium と一緒に使います。遊ぶ人のPC用です。"),
    (("indium",), "Sodium と、描画を変える一部のMODを一緒に使うための部品（古い Sodium 用）。"),
    (("embeddium",), "Forge / NeoForge 用の軽量化MOD（Sodium の移植版）。FPSが上がります。"),
    (("rubidium",), "Forge 用の軽量化MOD（Sodium の移植版です。今は Embeddium が後継）。"),
    (("oculus",), "Forge 用の影MOD（Iris の移植版）。Embeddium / Rubidium と一緒に使います。"),
    (("ferrite-core", "ferritecore"), "メモリ（RAM）の使用量を減らします。MODが多いときほど効果が大きいです。"),
    (("modernfix",), "起動時間とメモリ使用量を減らします。大きなMODパックで特に効果が大きいです。"),
    (("entityculling", "entity-culling"), "見えていない生き物やチェストなどの描画を省いてFPSを上げます。"),
    (("immediatelyfast",), "文字・アイテム・地図などの描画を速くしてFPSを上げます。"),
    (("krypton",), "通信の処理を軽くして、サーバーとネットワークの負担を減らします。"),
    (("c2me-fabric", "c2me", "concurrentchunkmanagementengine"), "地形の生成や読み込みを同時並行で行い、速くします。"),
    (("starlight",), "光（明るさ）の計算を大幅に速くします。1.20 以降は本体が改善されたので基本いりません。"),
    (("noisium",), "地形の生成を速くします。"),
    (("moreculling",), "見えないブロックの面の描画を省いて軽くします。"),
    (("dynamic-fps", "dynamicfps"), "マイクラを最小化・裏に回しているときにFPSを下げて、PCの負担を減らします。"),
    (("distanthorizons", "distant-horizons"), "遠くの景色を軽い方法で描画して、ものすごく遠くまで見えるようにします。"),
    (("chunky",), "ワールドを先に作っておき、遊んでいるときのカクつきを減らす（サーバー向け）。"),
    (("spark",), "ゲームやサーバーが重い原因を調べるためのツールです。"),
    (("clumps",), "経験値オーブをまとめて処理して、ラグを減らします。"),
    (("badoptimizations",), "細かい処理をいろいろ軽くする軽量化MODです。"),
    (("radium", "radium-reforged"), "Forge 用の内部処理の軽量化MOD（Lithium の移植版）。"),
    (("canary",), "Forge 用の内部処理の軽量化MOD（Lithium の移植版）。"),
    # ---- 便利 ----
    (("jei", "just-enough-items"), "アイテムの作り方・使い道を画面で調べられます。MOD入りの定番中の定番です。"),
    (("rei", "roughly-enough-items"), "アイテムの作り方・使い道を調べられる（JEI と同じ役割）。"),
    (("emi",), "アイテムの作り方・使い道を調べられる（JEI / REI と同じ役割の新しいMOD）。"),
    (("jade",), "見ているブロックや生き物の名前・情報を画面の上に表示します。"),
    (("wthit",), "見ているブロックや生き物の情報を表示する（Jade と同じ役割）。"),
    (("appleskin",), "満腹度や、食べ物でどれだけ回復するかを見やすく表示します。"),
    (("mouse-tweaks", "mousetweaks"), "マウス操作でアイテムをまとめて動かしやすくします。"),
    (("inventory-profiles-next",), "インベントリの自動整理など、持ち物まわりの便利機能を追加します。"),
    (("controlling",), "キー設定の画面に検索をつけて、キーのかぶりを見つけやすくします。"),
    (("modmenu",), "MODの一覧と、各MODの設定画面を開くボタンを追加する（Fabric）。"),
    (("journeymap",), "ミニマップと全体マップを追加します。目印（ウェイポイント）も置けます。"),
    (("xaeros-minimap", "xaerosminimap"), "画面のすみにミニマップを表示します。"),
    (("xaeros-world-map", "xaerosworldmap"), "歩いた場所の全体マップを表示します。"),
    (("waystones",), "「ウェイストーン」を置いた場所どうしをワープで行き来できます。"),
    (("natures-compass", "naturescompass"), "探したいバイオーム（地形の種類）の方向を教えてくれるコンパスです。"),
    (("explorers-compass", "explorerscompass"), "探したい建造物の方向を教えてくれるコンパスです。"),
    (("shulkerboxtooltip",), "シュルカーボックスの中身を、マウスを乗せるだけで見られます。"),
    (("polymorph",), "同じ材料で作れるレシピが重なったとき、作るものを選べるようにします。"),
    (("carry-on", "carryon"), "チェストや動物を、そのまま持ち上げて運べます。"),
    (("lootr",), "宝箱の中身を人ごとに別にします。マルチで宝の取り合いになりません。"),
    (("torchmaster",), "広い範囲でモンスターがわかないようにする、特別なたいまつを追加します。"),
    (("ftb-chunks", "ftb-chunks-forge", "ftb-chunks-fabric"), "地図と、土地を守る（他の人に壊されない）機能を追加します。"),
    (("ftb-quests", "ftb-quests-forge", "ftb-quests-fabric"), "クエスト（やることリスト）とご褒美を作れます。"),
    (("ftb-teams", "ftb-teams-forge", "ftb-teams-fabric"), "チームを作る機能です。FTB系MODで使います。"),
    (("open-parties-and-claims",), "土地の保護と、パーティー（チーム）機能を追加します。"),
    (("gravestone-mod", "gravestone"), "死んだ場所にお墓ができ、持ち物が消えなくなります。"),
    (("corpse",), "死んだときに体が残り、持ち物を守ってくれます。"),
    (("worldedit",), "範囲を選んで、ブロックをまとめて置いたりコピーしたりできる（建築向け）。"),
    (("litematica",), "建物の設計図を半透明で表示して、建築を手助けする（遊ぶ人用）。"),
    (("minihud",), "座標やスライムがわく場所などの情報を画面に表示します。"),
    (("tweakeroo",), "便利な操作の小技をたくさん追加します。"),
    (("carpet",), "技術系の検証や便利な機能を追加する（主にサーバー用）。"),
    (("simple-backups", "simplebackups"), "ワールドを自動でバックアップする（このソフトにも同じ機能あり）。"),
    (("ftb-backups-2", "ftbbackups2", "ftb-backups"), "ワールドを自動でバックアップする（このソフトにも同じ機能あり）。"),
    (("zoomify",), "キーを押すと望遠鏡のようにズームできます。"),
    (("ok-zoomer", "okzoomer"), "キーを押すと望遠鏡のようにズームできます。"),
    # ---- 見た目・音 ----
    (("continuity",), "ガラスなどの「つながった見た目」を表示できるようにする（リソースパック用）。"),
    (("lambdynamiclights", "dynamic-lights"), "たいまつを手に持つと、周りが明るくなります。"),
    (("sound-physics-remastered",), "音の反響や響きをリアルにします。"),
    (("presence-footsteps",), "足音をリアルにします。"),
    (("falling-leaves", "fallingleaves"), "木から葉っぱがひらひら落ちる演出を追加します。"),
    (("skin-layers-3d", "3d-skin-layers"), "スキンの外側の層を立体的に表示します。"),
    (("notenoughanimations", "not-enough-animations"), "プレイヤーの動き（アニメーション）を自然にします。"),
    (("emotecraft",), "手を振るなどの動き（エモート）ができるようになります。"),
    # ---- 交流 ----
    (("simple-voice-chat", "simplevoicechat"), "マイクで近くの人と声で話せます。サーバーと全員のPCの両方に必要です。"),
    (("plasmo-voice", "plasmovoice"), "マイクで近くの人と声で話せます。サーバーと全員のPCの両方に必要です。"),
    # ---- 工業・技術 ----
    (("create",), "歯車やベルトコンベアを組み合わせて、動く装置や工場を作れる大人気の工業MODです。"),
    (("create-crafts-additions", "createaddition"), "Create の動力と電気を変換できるようにする追加MOD（Create が必要）。"),
    (("mekanism",), "鉱石を何倍にも増やす機械・発電・パワードスーツなどを追加する大型工業MODです。"),
    (("mekanism-generators", "mekanismgenerators"), "Mekanism に発電機を追加する（Mekanism が必要）。"),
    (("ae2", "applied-energistics-2", "appliedenergistics2"), "アイテムをデジタル化して大量に収納し、自動でクラフトできます。"),
    (("refined-storage", "refinedstorage"), "たくさんのアイテムを一つの画面で管理できる、大容量の収納システムです。"),
    (("toms-storage", "tomssimplestorage", "toms-simple-storage-mod"), "チェストをまとめて一つの画面で管理できる、シンプルな収納システムです。"),
    (("immersive-engineering", "immersiveengineering"), "見た目にこだわった大型の機械や電気設備を追加する工業MODです。"),
    (("thermal-expansion", "thermal-foundation", "thermal-series"), "機械や発電機を追加する Thermal シリーズです。"),
    (("powah",), "発電機と、電気を使う機械を追加します。"),
    (("industrial-foregoing", "industrialforegoing"), "農業・採掘・動物の世話などを自動化する機械を追加します。"),
    (("valkyrien-skies", "valkyrienskies"), "作った建物を、空飛ぶ船や乗り物として動かせます。"),
    # ---- 収納・装備 ----
    (("storage-drawers", "storagedrawers"), "1種類のアイテムを大量に入れられる引き出しを追加します。"),
    (("sophisticated-backpacks", "sophisticatedbackpacks"), "強化できるリュックを追加します。持ち運べる収納が増えます。"),
    (("sophisticated-storage", "sophisticatedstorage"), "強化できるチェストやたるを追加します。"),
    (("travelers-backpack", "travelersbackpack"), "旅行用のリュックを追加します。液体タンクや寝袋つきです。"),
    (("iron-chests", "ironchests"), "鉄・金・ダイヤなど、たくさん入るチェストを追加します。"),
    (("tinkers-construct", "tconstruct"), "部品を組み合わせて、自分だけの道具や武器を作れます。"),
    (("better-combat", "bettercombat"), "武器ごとに違う攻撃の動きや連続攻撃を追加する戦闘MODです。"),
    (("epic-fight", "epicfight"), "戦闘のアクションと動きを大きく変える戦闘MODです。"),
    (("comforts",), "寝袋とハンモックを追加します。"),
    # ---- 魔法 ----
    (("botania",), "花の魔力（マナ）を使って、いろいろな仕組みを作る自然系の魔法MODです。"),
    (("ars-nouveau", "arsnouveau"), "呪文のパーツを組み合わせて、自分だけの魔法を作れます。"),
    (("irons-spells-n-spellbooks", "ironsspellbooks"), "魔法の書とたくさんの攻撃魔法、ボスを追加する戦闘系の魔法MODです。"),
    (("origins",), "種族を選んで、それぞれ違う能力で遊べます。"),
    # ---- 世界・冒険・生き物 ----
    (("the-twilight-forest", "twilightforest", "twilight-forest"), "「黄昏の森」という新しい世界と、ボスやダンジョンを追加します。"),
    (("aether", "the-aether"), "空に浮かぶ島の世界「エーテル」を追加します。"),
    (("biomes-o-plenty", "biomesoplenty"), "新しいバイオーム（地形の種類）をたくさん追加します。"),
    (("terralith",), "新しい地形・バイオームを追加します。ブロックは増やさず、元からあるブロックで作られます。"),
    (("tectonic",), "山や谷などの地形の形を、ダイナミックにします。"),
    (("alexs-mobs", "alexsmobs"), "個性的な動物や生き物をたくさん追加します。"),
    (("naturalist",), "クマ・シカ・ヘビなど、実際にいる動物を追加します。"),
    (("mowzies-mobs", "mowziesmobs"), "個性的なボスや生き物を追加します。"),
    (("ice-and-fire-dragons", "ice-and-fire", "iceandfire"), "ドラゴンなど、神話の生き物を追加します。"),
    (("cobblemon",), "ポケモンのような生き物を捕まえて、育てて、戦わせることができます。"),
    (("yungs-better-dungeons",), "元からあるダンジョンを作り直して、豪華にします。"),
    (("yungs-better-mineshafts",), "廃坑を作り直して、豪華にします。"),
    (("yungs-better-strongholds",), "要塞を作り直して、豪華にします。"),
    (("yungs-better-desert-temples",), "砂漠の寺院を作り直して、豪華にします。"),
    (("yungs-better-ocean-monuments",), "海底神殿を作り直して、豪華にします。"),
    (("when-dungeons-arise", "dungeons-arise"), "巨大なダンジョンや建造物を追加します。"),
    (("repurposed-structures",), "いろいろなバイオームに合わせた建造物を追加します。"),
    (("towns-and-towers",), "村や前哨基地の種類を増やします。"),
    (("dungeons-and-taverns",), "酒場などの新しい建造物を追加します。"),
    (("serene-seasons", "sereneseasons"), "季節（春夏秋冬）を追加します。"),
    (("tough-as-nails", "toughasnails"), "体温と水分のシステムを追加して、サバイバルを難しくします。"),
    (("quark",), "元のマイクラの雰囲気を大事にした、小さな改良や追加をたくさん入れます。"),
    (("supplementaries",), "看板・びん・ロープ・風見鶏など、飾りや便利なブロックをたくさん追加します。"),
    # ---- 料理・建築 ----
    (("farmers-delight", "farmersdelight"), "料理と農業を大きく強化します。まな板や鍋で料理が作れます。"),
    (("croptopia",), "作物と料理をたくさん追加します。"),
    (("cooking-for-blockheads",), "料理本や台所のブロックを追加します。"),
    (("chipped",), "元からあるブロックの見た目違いを、たくさん追加します。"),
    (("handcrafted",), "いす・テーブル・ベッドなどの家具を追加します。"),
    (("macaws-furniture",), "家具を追加します。"),
    (("macaws-bridges",), "いろいろな橋を追加します。"),
    (("macaws-doors",), "いろいろなドアを追加します。"),
    (("macaws-windows",), "いろいろな窓を追加します。"),
    (("macaws-roofs",), "いろいろな屋根を追加します。"),
    # ---- 前提MOD ----
    (("fabric-api", "fabricapi"), "Fabric 用MODのほとんどが必要とする前提MODです。Fabric ならまず入れます。"),
    (("architectury-api", "architectury"), _LIB),
    (("cloth-config", "clothconfig", "cloth-config-api"), "MODの設定画面を作るための前提MODです。"),
    (("geckolib",), "動きのある生き物や防具などのアニメーションを表示するための前提MODです。"),
    (("curios", "curios-api"), "アクセサリー枠（指輪・首飾りなど）を追加する前提MODです。"),
    (("trinkets",), "アクセサリー枠（指輪・首飾りなど）を追加する前提MOD（Fabric）。"),
    (("patchouli",), "MODの説明書（ゲーム内ガイドブック）を作るための前提MODです。"),
    (("terrablender",), "新しいバイオームを追加するMODのための前提MODです。"),
    (("yungs-api",), "YUNG's シリーズのMODの前提MODです。"),
    (("balm", "puzzles-lib", "collective", "resourceful-lib", "bookshelf-lib", "bookshelf", "citadel",
      "moonlight", "moonlight-lib", "kotlin-for-forge", "fabric-language-kotlin", "yacl",
      "yetanotherconfiglib", "owo-lib", "forge-config-api-port", "cupboard", "creativecore", "framework",
      "playeranimator", "player-animator", "caelus", "malilib", "pehkui", "ftb-library",
      "ftb-library-forge", "ftb-library-fabric", "resourceful-config", "athena", "prism-lib", "kubejs",
      "rhino", "zeta", "midnightlib", "searchables", "attributefix", "lithostitched", "sinytra-connector",
      "forgified-fabric-api", "connector-extras", "glitchcore", "fzzy-config"), _LIB),
    # ---- 追加（人気の前提MOD・軽量化・便利系） ----
    (("veinminer",), "ブロックを壊すとき、つながった同じ種類のブロック（鉱石・木など）を、まとめて一度に掘れるようにするMODです。"),
    (("connector",), "Fabric 用のMODを、NeoForge / Forge で動かせるようにする前提MODです。Fabric 用のMODを入れるときに必要になることがあります。"),
    (("kotlinlangforge", "klf"), "Kotlin という言語で作られたMODを動かすための前提MODです。"),
    (("spruceui",), "設定画面などを作るための前提ライブラリです。これを使うMODと一緒に自動で入ります。"),
    (("pridelib", "pride-lib"), "ほかのMODが「プライドフラッグ」の表示に使う、前提ライブラリです。"),
    (("yumi-mc-foundation", "yumi"), "LambDynamicLights などが使う、共通の前提ライブラリです。"),
    (("sodium-extra", "sodiumextra"), "Sodium に、雲・空・粒子・表示の詳しい設定を足す追加MODです。"),
    (("reeses-sodium-options", "sodium-options"), "Sodium の設定画面を、見やすいデザインにするMODです。"),
    (("betterf3", "better-f3"), "F3 のデバッグ画面を、色分けして見やすく整理するMODです。"),
    (("no-chat-reports", "nochatreports"), "チャットの内容が、報告用に署名・記録されるのを防ぐMODです。"),
    (("ping-wheel", "pingwheel"), "ホイールを押した場所に目印（ピン）を出して、仲間に場所を教えられるMODです。"),
    (("better-ping-display", "betterpingdisplay"), "マルチプレイの一覧に、通信の遅さ（ping）を数字で表示するMODです。"),
    (("ambientsounds", "ambient-sounds"), "風・水・鳥の声など、場所に合った環境音を足して、雰囲気を良くするMODです。"),
    (("betternether", "better-nether"), "ネザーに、新しい地形・植物・ブロックを足して、見た目も豊かにするMODです。"),
    (("betterend", "better-end"), "エンドに、新しい地形・植物・生き物を足して、にぎやかにするMODです。"),
    (("bclib",), "Better Nether / Better End が使う、前提ライブラリです。"),
    (("artifacts",), "ダンジョンや宝箱で見つかる、特別な効果つきのアクセサリー（アーティファクト）を足すMOD。"),
    (("cc-tweaked", "cctweaked"), "コンピューターとロボット（タートル）を足し、プログラム（Lua）で動かして遊べるMODです。"),
    (("sophisticated-core", "sophisticatedcore"), "Sophisticated 系（バックパック・収納）のMODが使う、前提ライブラリです。"),
    (("enhanced-block-entities", "ebe"), "チェスト・看板などの描画を軽くして、FPS（動きのなめらかさ）を上げるMODです。"),
    (("ksyxis",), "ワールドに入るまでの読み込み時間を短くするMODです。"),
    (("fast-ip-ping", "fastipping"), "サーバー一覧の表示を速くするMOD（接続状態の確認を早める）。"),
]

_INDEX: dict[str, str] = {}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


for keys, text in _ENTRIES:
    for k in keys:
        _INDEX.setdefault(_norm(k), text)


def builtin(slug: str | None, title: str | None = None) -> str | None:
    """手書きの日本語説明があれば返す。"""
    for key in (slug, title):
        if key and _norm(key) in _INDEX:
            return _INDEX[_norm(key)]
    return None


def genres(categories) -> list[str]:
    return [CATEGORY_JA.get(c, c) for c in (categories or []) if c not in LOADER_CATS]


def is_library(categories) -> bool:
    return "library" in (categories or [])
