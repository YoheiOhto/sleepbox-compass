# Sleepbox Compass

Pokémon Sleepの手持ちを、Lv60とLv80の両方で評価し、育成候補と博士へ送る候補を
スマホ向けページにまとめる、プライバシー優先のローカルツールです。

> 現在は公開可能なMVPです。JSON取込、SQLite保存、全ロール×4アンカーの支配判定、
> フェイルセーフ、静的サイト生成を実装しています。動画OCRとNeroli's Labブリッジは
> 次の実装段階で、スコア未入力の個体は誤って送らないよう自動的に保護されます。

## 特徴

- 個体IDは、変化しない種・性格・食材・サブスキル・メインスキルから生成
- きのみ・食材・スキルの全ロールについてLv50/60/70/80すべてで支配された場合だけ送る候補
- 固定基準値との比較による0〜100の総合絶対評価（手持ち母集団に依存しない）
- 未検証・スコア不足の個体は必ず `protected`
- 個人データ、スクリーンショット、動画、SQLite、ローカル設定はGit管理外
- GitHub Pages向けのレスポンシブな静的HTML
- ポケモンタイプ表示と、島ごとの現在最強・Lv50/60/70/80育成後パーティ提案

## クイックスタート

Python 3.9以上が必要です。

```sh
python3 -m pip install -e .
pokesleep-box --db /tmp/pokesleep-demo.sqlite demo
python3 -m http.server 8000 --directory site
```

ブラウザで `http://localhost:8000` を開きます。実データは次のように処理します。

```sh
cp config/settings.example.json config/settings.local.json
pokesleep-box init-db
pokesleep-box import-json data/private/my-box.json
pokesleep-box decide --keep-top-n 2
pokesleep-box render
```

入力形式は [data/example_individuals.json](data/example_individuals.json) を参照してください。
この例は架空のサンプルであり、実ユーザーのデータではありません。

`pokemon_type` にタイプ、`berry` にその個体のきのみ、`production_scores` に計算エンジンが
算出した現在値とLv50/60/70/80の総生産・きのみ生産を渡します。画面で好みのきのみ3種を
選ぶたび、好物ボーナスを加えて同一個体を重複させず上位5匹を再計算します。固定島を選ぶと
3種が自動入力され、ワカクサ本島ではその週の3種を自由に選べます。

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
