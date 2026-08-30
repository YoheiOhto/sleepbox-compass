# Sleepbox Compass

Pokémon Sleepの手持ちを、Lv60とLv80の両方で評価し、育成候補と博士へ送る候補を
スマホ向けページにまとめる、プライバシー優先のローカルツールです。

> 日本語入力、JSON・静止画・動画取り込み、SQLite保存、Neroli's Labブリッジ、
> SP検算、全ロール×4アンカーの支配判定、監査・レビュー、静的サイト生成を実装しています。
> 計算エンジン未接続または未検証の個体は、自動的に保護されます。

## 特徴

- 個体IDは、変化しない種・性格・食材・サブスキル・メインスキルから生成
- きのみ・食材・スキルの全ロールについてLv50/60/70/80すべてで支配された場合だけ送る候補
- 全最終進化種の理想個体上位10%を100点基準にした0〜100の総合絶対評価（手持ち母集団に依存しない）
- 未検証・スコア不足の個体は必ず `protected`
- 個人データ、スクリーンショット、動画、SQLite、ローカル設定はGit管理外
- GitHub Pages向けのレスポンシブな静的HTML
- ポケモンタイプ表示と、島ごとの現在最強・Lv50/60/70/80育成後パーティ提案
- 「現在」は実際のレベル・進化段階、「育成後」は指定レベル・最終進化と残り進化回数ぶんのメインスキルLv上昇を前提に比較
- 現行246個体キー、性格25種、サブスキル17種、食材19種、きのみ18種の日本語双方向解決
- macOS Visionによる日本語OCR（画像・動画）、差し替え可能なOCR、監査レポート、オフラインレビュー画面
- 固定6島（ワカクサ除外）のきのみ・食材基礎エナジー・直接スキルによる日次・週次予測
- 5匹同時シミュレーションによる、元気回復・おてつだい支援・全体スキル・サブスキル込みの編成探索と限界貢献
- ローカルAPIで任意の5匹を選ぶ、料理・スキル込みのカスタム編成シミュレーション
- 現在→Lv60の育成効果、全島汎用性、戦力不足、目標到達日数
- 一般的な理想個体と、手持ちの不足を分けた捕獲候補
- サブスキル・好物きのみから生成する「なぜ強いか」とデータ品質表示
- 一覧を統合した検索・タイプ絞り込み付きボックスと、個体詳細ポップアップ
- ボックスの個体詳細から「博士に送る」を実行でき、送った個体は一覧から非表示（DBにはアーカイブ保存）
- 編成画面での目標到達シミュレーション、捕獲画面での外部Tier参考情報と理想個体の定義
- 「使い方」の直前に、OCR・個体評価・編成計算の稼働状況をまとめた状態画面
- 未検証個体の要確認マークと、状態画面から個体評価・編成・整理判定を更新する一括再計算
- 計算条件（フィールドボーナス、キャンプ、睡眠時間、鍋、料理、リボン）を設定からエンジンへ渡す
- ローカルJSONバックアップ／明示的な復元、たね予算からの実行順、捕獲確率目標の計算

## クイックスタート

Python 3.9以上が必要です。

```sh
python3 -m pip install -e .
pokesleep-box --db /tmp/pokesleep-demo.sqlite demo
python3 -m http.server 8000 --directory site
```

計算ブリッジを使う場合はNode.js 20以上、動画を使う場合はffmpegも必要です。

ブラウザで `http://localhost:8000` を開きます。実データは次のように処理します。

```sh
cp config/settings.example.json config/settings.local.json
pokesleep-box init-db
pokesleep-box import-json data/private/my-box.json
pokesleep-box decide --keep-top-n 2
pokesleep-box render
```

実データのページは、Git管理外の`site/private/`へ生成されます。`render`の出力先の既定は
`site/private`であり、公開デモの`site/`へは`demo`だけが書き込みます。個人データが
GitHub Pagesへ公開されるのを防ぐため、`render --out site`のように公開側を明示指定しないでください。

```sh
pokesleep-box --db data/box.sqlite decide
pokesleep-box --db data/box.sqlite evaluate
pokesleep-box --db data/box.sqlite render
python3 -m http.server 8000 --directory site/private
```

アプリ画面で確認済みの保存や再計算を使うときは、静的サーバーではなくローカルAPI付きで起動します。
`pokesleep-box` はプロジェクトの仮想環境に入っているため、初回は次のどちらかで起動します。

```sh
# 推奨：仮想環境を有効化してから起動
source .venv/bin/activate
pokesleep-box --db data/box.sqlite serve

# 有効化せずに直接起動する場合
./.venv/bin/pokesleep-box --db data/box.sqlite serve --port 8765
```

起動後、ブラウザで <http://127.0.0.1:8765/> を開きます。`--port` を省略すると
8000番を使うため、ほかの開発サーバーと競合した場合は8765番のように変更してください。

日本語JSONもそのまま取り込めます。macOSでは、ポケモンの詳細画面を撮った画像または
画面収録を`inbox/`へ置くだけで、Apple Visionの日本語OCRを使ってローカル認識できます。
画像は外部へ送信されません。初回だけSwift製OCRヘルパーを`.cache/`へ自動ビルドします。

### iPhoneでのおすすめの撮り方

ポケモン1匹の詳細は画面1枚に収まらないため、**スクリーンショットを何枚も撮るより、
1匹ごとに短い画面収録**にするのがおすすめです。詳細の一番上から始め、ゆっくり下まで
スクロールして止めます（5〜10秒程度）。次の個体へ移る前に収録を止めてください。
`pokesleep-box scan inbox` は動画からフレームを取り出し、同じ個体の性格・SP・食材・
サブスキルを統合します。

iOS 26でスクリーンショット直後に編集画面が開く場合は、iPhoneで
**設定 → 一般 → 画面の取り込み →「フルスクリーンプレビュー」をオフ**にしてください。
以後は編集画面ではなく左下の一時サムネールになります。サムネールは左へスワイプすれば
すぐ消せます。iOS 18以前では最初から一時サムネール表示です。画面収録では編集画面は
開かないため、今回の用途にはこちらが向いています。

```sh
# 架空画像で、実データを使わず先に一連の流れを試す
pokesleep-box make-ocr-demo
pokesleep-box --db data/demo-ocr.sqlite scan inbox/ocr-demo.png

# 自分の画像・動画を読み込む（inbox/の中身はGit管理外）
pokesleep-box scan inbox

# frames/review.htmlで画像と照合し、「照合済み」にチェックして保存
pokesleep-box import-json reviewed_individuals.json
pokesleep-box decide --keep-top-n 2
pokesleep-box render
```

認識結果は安全のため最初は必ず未検証になり、確認前の個体を博士へ送る候補にはしません。
ボックスで個体を開き、「博士に送る（一覧から消す）」を押すと確認後にアーカイブされ、通常のボックス表示から消えます。
読み取りにくい項目は監査レポートに出ます。`--interval 0.5`で動画の認識間隔も調整できます。
きのみは認識文字ではなく種族データから自動確定します。アイコン表示の食材は種族ごとの
Lv1/30/60候補に絞り、画面のSPと計算SPが完全一致する組み合わせが1つだけなら自動確定します。
一致なし・複数一致・計算エンジン未構築の場合はレビュー画面に候補を表示し、画像を見ながら選択できます。
Lv30時点で将来のLv60枠だけが複数残る場合は、完全一致候補すべてに共通する解放済み枠だけを
安全に確定します。通常種と画面名が同じアローラ・パルデア・イベント姿も、合法食材を含む
完全一致候補が1姿だけならSPから確定します。
同じ個体を複数の動画で撮った場合は、種族と画面のSPで自動的に結合します。そのため、
1本目で生産情報、2本目でスクロールして性格を撮る運用ができます。現在レベルはSP付近の
ヘッダーだけから読み、食材のLv30/60やサブスキル解放Lvを現在レベルとして扱いません。
レビュー画面では現在レベル・SP・性格・メインスキルLv・食材を直接修正してから照合済みにできます。

動画は1個体をスクロールしながら何フレームも撮るため、フレームの統合精度がそのまま
取り込み精度になります。個体の切れ目は、種族・性格・SPの食い違いが**連続2フレーム**
続いたときだけ確定します。1フレームだけのブレによる誤読は破棄するので、同じ個体が
複数の個体へ分裂しません。同じ項目を複数フレームが読んだ場合は、最後のフレームではなく
**信頼度が最も高いフレーム**の値を採用します（スクロール終盤ほどブレやすいため）。
サブスキルと食材はフレームごとに見える枠が変わるので、上書きではなく**和集合**で
統合し、多くのフレームが支持した枠を優先します。解放Lvのバッジはどのフレームで
読めても採用します。食材の個数は固定座標ではなく、`×n`の並びを画面の行としてまとめ、
Lv1/30/60バッジなどの根拠がある行だけを食材行として採用するため、画角やスクロール位置が
変わっても取りこぼしません。認識時にはVisionへ種族・性格・サブスキル・食材・きのみの
日本語名を語彙ヒントとして渡し、日本語補正が固有名を一般的な単語へ書き換えるのを防ぎます。
小さいヘッダーと薄い未解放サブスキルは領域別に再認識し、`Lv.14`が`Lv.141`になる結合や
S/M/Lの欠落を抑えます。1画像のVision失敗はその画像だけを報告して、残りの一括処理を継続します。

macOS Visionを使えない環境では`--ocr sidecar --ocr-command /path/to/extractor`で従来の
ローカル抽出器を利用できます（sidecar方式の動画抽出にはffmpegが必要です）。取り込み後の
`audit_report.md`、`frames/review.html`、OCR画像、DBはすべてGit管理外です。

## 計算と検証

ゲーム式はPythonへ複製せず、固定したNeroli's Labをローカルビルドして使います。

```sh
./engine/install.sh
```

初回のみ実行します（既にビルド済みなら不要です）。

### 起動（ワンコマンド）

実データの検証・評価・たね育成プラン計算・整理判定・画面生成・ローカルサーバー起動を
まとめて行うスクリプトです。`Ctrl+C`で停止します。

```sh
./scripts/serve.sh
```

ブラウザで `http://localhost:8000` を開きます。再計算せずサーバーだけ起動したい場合は
`./scripts/serve.sh --serve-only` を使います。DBや出力先を変える場合は
`POKESLEEP_DB` / `POKESLEEP_SITE` 環境変数で上書きできます（既定は
`data/box.sqlite` / `site/private`）。

内部では次のコマンドを順に実行しています（個別に実行したい場合の参考）。

```sh
pokesleep-box verify
pokesleep-box evaluate
pokesleep-box seed-evaluate
pokesleep-box main-seed-evaluate
pokesleep-box decide --keep-top-n 2
pokesleep-box render --out site/private
pokesleep-box --db data/box.sqlite serve --site site/private
```

`verify`は表示SPとの一致を確認します。許容差一致の個体も監査対象のままとし、
厳密一致していない個体は`protected`から外しません。

入力形式は [data/example_individuals.json](data/example_individuals.json) を参照してください。
この例は架空のサンプルであり、実ユーザーのデータではありません。

一般的な捕獲候補用の理想個体ベンチマークは、種族データや計算式を更新したときだけ
再生成します（`起動`のたびには実行されません）。

```sh
pokesleep-box benchmark
```

上記の`serve`は、`./scripts/serve.sh`が最終的に起動するのと同じローカルAPIサーバーです。
任意の5匹をその場で再計算する画面の編成機能は、このサーバー経由でのみ動作します。

`pokemon_type` にタイプ、`berry` にその個体のきのみを渡します。編成の順位づけは
`energy_scores` の島別・育成段階別の期待エナジーだけを使い、個体を重複させずに上位5匹を
選びます。固定島を選ぶときのみ3種が自動入力されます。きのみの表示は、その島で
好物になる個体を★で示すためのもので、順位そのものは島の選択で決まります。

島別予測では`energy_scores`に加え、`evaluate`が非公開の
`data/private/team_plans.json`を生成します。値は1日あたりで、`berry`、料理、直接エナジーを
集計します。登録前の既定値はカレー・サラダ・デザートの平均、レシピLv1です。
固定島の最強編成は単体値の足し算ではなく、5匹を
同時にMonte Carloシミュレーションし、元気回復・おてつだい支援・スキルコピー・
おてつだいボーナスなどの全体効果を反映します。各個体の「限界貢献」は、その個体を
チームから外した場合とのエナジー差です。Research/Sleep EXPとゆめのかけらボーナスは
エナジーへ恣意的に換算せず、育成効用として別扱いにします。

```json
"energy_scores": {
  "ラピスラズリ湖畔": {
    "current": {"berry": 42000, "direct_skill": 5000,
                "low": 45000, "expected": 47000, "high": 51000},
    "60": {"berry": 68000, "direct_skill": 7000,
           "low": 72000, "expected": 75000, "high": 80000}
  }
}
```

一般的な捕獲候補は`data/private/species_benchmarks.json`に同じ形式のLv60理想個体を
保存すると表示されます。公開デモのベンチマークはすべて架空であることを画面に明記しています。
画面上の「理想個体」は、最終進化・指定レベル・役割に合う性格とサブスキル・最大メインスキル・
種族の食材候補を前提にします。100点は全役割共通の完全個体ではなく、役割別の理想個体群における
上位10%水準です。外部Tier表は種族の一般評価を知る参考情報として分離し、手持ち個体の評価や
島別編成順位には直接加点しません。

### 計算条件と最適化

`config/settings.local.json` の `areaBonusByIsland`、`camp`、`bedtime`、`wakeup`、
`potSize`、`includeCooking`、`recipeLevel` はチーム計算へ渡されます。設定変更後は
`pokesleep-box evaluate` を実行してください。チーム探索は、上位候補から複数の開始点で
交換探索を行うヒューリスティックです。全組合せの数学的最適解を保証するものではありません。

### ローカル運用ツール

```sh
pokesleep-box backup --out data/private/sleepbox-backup.json
pokesleep-box restore data/private/sleepbox-backup.json --replace
pokesleep-box capture-odds --per-catch 0.10 --target 0.90
pokesleep-box resource-plan
```

図鑑の種族フレンド状態は寝顔図鑑のスクリーンショットをローカルOCRして、確認後にJSONとして
登録できます。バッジ画像は文字としては読めない場合があるため、OCR結果のバッジは必ず確認します。

```sh
pokesleep-box scan-dex inbox/dex-bulbasaur.png > data/private/dex.json
pokesleep-box import-dex data/private/dex.json

# {"Honey": 30, "Apple": 12} のような在庫JSON
pokesleep-box set-inventory data/private/inventory.json

# name / recipe_name / requirements / meals_per_day / team_id を持つ料理計画JSON
pokesleep-box save-cooking-plan data/private/cooking-plan.json
```

個体詳細の「送らない」は、色違い・思い出・将来の進化候補などを整理判定から保護します。
タグはローカルDBだけに保存されます。

バックアップには画像・動画を含めません。`restore --replace` は現在の計画データを置換します。
`resource-plan` は、ゲーム内コストを推測しません。アメとゆめのかけらは設定に記録できる残高として扱い、
正確な必要量が入力・検証できるようになるまで自動配分の根拠には含めません。

## プライバシー

次は `.gitignore` により公開されません。

- `inbox/` の動画・画像
- `frames/` の抽出フレーム
- `data/private/` と `data/*.sqlite`
- `config/*.local.json`
- 個人データから生成した監査レポート

公開前には `git status` と追跡対象の秘密情報検査も行ってください。公開Pagesに実データを
載せる場合は、コード用公開リポジトリとは別の非公開データ運用を推奨します。

## 設計と安全性

詳細仕様は [sekkei.md](sekkei.md) を参照してください。ゲーム仕様の計算式をPythonへ
再実装せず、Neroli's Labと連携する方針です。連携方法とライセンスは
[engine/README.md](engine/README.md) および [NOTICE](NOTICE) に記載しています。

本ツールは非公式であり、Pokémon、Pokémon Sleepおよび関連名称は各権利者に帰属します。

## 使い方

公開ページの「使い方」タブに、データ準備、最強パーティ選出、育成評価、一覧の見方を
まとめています。外部サービスへの通知は行いません。
