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
- 現行246個体キー、性格25種、サブスキル17種、食材19種、きのみ18種の日本語双方向解決
- macOS Visionによる日本語OCR（画像・動画）、差し替え可能なOCR、監査レポート、オフラインレビュー画面
- 固定6島（ワカクサ除外）の料理なし日次・週次エナジー予測
- 5匹同時シミュレーションによる、元気回復・おてつだい支援・全体スキル・サブスキル込みの編成探索と限界貢献
- 現在→Lv60の育成効果、全島汎用性、戦力不足、目標到達日数
- 一般的な理想個体と、手持ちの不足を分けた捕獲候補
- サブスキル・好物きのみから生成する「なぜ強いか」とデータ品質表示

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

実データのページは、公開デモの`site/`と分けてGit管理外の`site/private/`へ生成できます。

```sh
pokesleep-box --db data/box.sqlite decide
pokesleep-box --db data/box.sqlite evaluate
pokesleep-box --db data/box.sqlite render --out site/private
python3 -m http.server 8000 --directory site/private
```

日本語JSONもそのまま取り込めます。macOSでは、ポケモンの詳細画面を撮った画像または
画面収録を`inbox/`へ置くだけで、Apple Visionの日本語OCRを使ってローカル認識できます。
画像は外部へ送信されません。初回だけSwift製OCRヘルパーを`.cache/`へ自動ビルドします。

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
読み取りにくい項目は監査レポートに出ます。`--interval 0.5`で動画の認識間隔も調整できます。
きのみは認識文字ではなく種族データから自動確定します。アイコン表示の食材は種族ごとの
Lv1/30/60候補だけをレビュー画面に表示するので、画像を見ながら各枠を選択できます。
同じ個体を複数の動画で撮った場合は、種族と画面のSPで自動的に結合します。そのため、
1本目で生産情報、2本目でスクロールして性格を撮る運用ができます。現在レベルはSP付近の
ヘッダーだけから読み、食材のLv30/60やサブスキル解放Lvを現在レベルとして扱いません。
レビュー画面では現在レベル・SP・性格・メインスキルLv・食材を直接修正してから照合済みにできます。
macOS Visionを使えない環境では`--ocr sidecar --ocr-command /path/to/extractor`で従来の
ローカル抽出器を利用できます（sidecar方式の動画抽出にはffmpegが必要です）。取り込み後の
`audit_report.md`、`frames/review.html`、OCR画像、DBはすべてGit管理外です。

## 計算と検証

ゲーム式はPythonへ複製せず、固定したNeroli's Labをローカルビルドして使います。

```sh
./engine/install.sh
pokesleep-box verify
pokesleep-box evaluate
pokesleep-box benchmark
pokesleep-box decide --keep-top-n 2
pokesleep-box render
```

`verify`は表示SPとの一致を確認します。許容差一致の個体も監査対象のままとし、
厳密一致していない個体は`protected`から外しません。

入力形式は [data/example_individuals.json](data/example_individuals.json) を参照してください。
この例は架空のサンプルであり、実ユーザーのデータではありません。

`pokemon_type` にタイプ、`berry` にその個体のきのみ、`production_scores` に計算エンジンが
算出した現在値とLv50/60/70/80の総生産・きのみ生産を渡します。画面で好みのきのみ3種を
選ぶたび、好物ボーナスを加えて同一個体を重複させず上位5匹を再計算します。固定島を選ぶと
3種が自動入力され、ワカクサ本島ではその週の3種を自由に選べます。

島別予測では`energy_scores`に加え、`evaluate`が非公開の
`data/private/team_plans.json`を生成します。値は1日あたりで、料理・食材価値を含めず、
`berry`と直接エナジーを集計します。固定島の最強編成は単体値の足し算ではなく、5匹を
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
