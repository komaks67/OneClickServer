# OneClickServer

マイクラ / ARK(ASA・ASE) / Palworld / Terraria のサーバーを、1つのソフトでかんたんに立てるWindows用ツール。

- Python 3.11〜3.13 + pywebview(WebView2)
- データは `data/` に保存(更新しても引き継がれる)
- ソースの置き場: `komaks67/OneClickServer`
- 配布: `OneClickServer_win.zip` と `.sha256` を GitHub Releases に置く。ソフトが更新を確認する先は `oneclick/appinfo.py` の `RELEASES_REPO`(`komaks67/OneClickServer`。このリポジトリ自身)
- 公式アイコンを使うときは `oneclick/web/icons/README.txt` を読むこと

## Discord から操作する(3.1.0〜)
- ホーム画面の「Discord から操作する(ボット)」で、5ステップの案内どおりに設定する(ボットは1つ。4ゲーム共通)
- チャンネルに「▶ 起動 / ■ 停止 / 📊 状態」のパネルを、サーバーごとに置ける。お知らせは、パネルのスレッドに「カード」で出る
- 設定は `data/minecraft/settings.json` の `discord_bot`(トークンは Windows の DPAPI で暗号化)
- コードは `oneclick/discordbot.py`(通信・カード)と `oneclick/discordhub.py`(4ゲームのつなぎ口)、画面は `oneclick/web/discord.js`

## 3.2.0 の変更
- マイクラ部分を旧 OneClickServerMC(2.8.0)の全機能に合わせた(MOD巻き戻し・起動前チェック・コマンド一覧・MODアイコン・参加前チェック・ネットワーク診断・ガイド画像・安全面の強化など)
- Discord の「サーバーが起動しました」が、人の出入りのたびに何度も出る不具合を修正
- 誤字(ぶ使って → ぶつかって)を修正

## 開発
```
python -m unittest discover -s tests -v   # 自動テスト
start.bat                                  # そのまま起動
build.bat                                  # exe を作る
make_release.bat                           # 配布zip + sha256 を作る
```
版の数字は `oneclick/appinfo.py` の `VERSION`。前より大きくしないと更新として扱われません。

## 更新のしくみ
- ソフトを開いたとき、1回だけ新しい版を確認 → あれば強制更新(動作中のサーバーがある間は待つ)
- 更新前に自動バックアップ、失敗したら自動で元に戻す
- SHA-256 が無いリリースは、安全のため自動では入れない

## 確認できていないこと(Windowsの実機で要確認)
SteamCMDでの導入、各サーバーの起動/停止、Palworldのプロセス判定、Terrariaの標準入力、exe化、WebView2の表示。

## 更新の通知(通知だけ。勝手には入れません)
- ソフトを開いたとき1回、導入済みの各ゲームについて確認します。ゲームのホーム画面の「更新を確認」でも調べられます。
- ARK / Palworld: SteamCMD で、入っている版と最新の版(buildid)を比べます。
- Terraria: 公式の最新の zip 名と、入れたときの zip 名を比べます。
- ARK ASE の MOD: Steam ワークショップの更新時刻を見ます(サーバーを起動すると「見た」ことになります)。
- ARK ASA の MOD: CurseForge は API キーが要るため通知できません(起動時にゲームが自動で最新にします)。
