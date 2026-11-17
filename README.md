# Sleepbox Compass

Pokémon Sleepの手持ちを、Lv60とLv80の両方で評価し、育成候補と博士へ送る候補を
スマホ向けページにまとめる、プライバシー優先のローカルツールです。

> 日本語入力、JSON・静止画・動画取り込み、SQLite保存、Neroli's Labブリッジ、
> SP検算、全ロール×4アンカーの支配判定、監査・レビュー、静的サイト生成を実装しています。
> 計算エンジン未接続または未検証の個体は、自動的に保護されます。

## 特徴

- 個体IDは、変化しない種・性格・食材・サブスキル・メインスキルから生成
- きのみ・食材・スキルの全ロールについてLv60/Lv80の両方で複数の同種個体に支配された場合だけ送る候補
- 全最終進化種の理想個体上位10%を100点基準にした0〜100の総合絶対評価（手持ち母集団に依存しない）
- 未検証・スコア不足の個体は必ず `protected`
- 個人データ、スクリーンショット、動画、SQLite、ローカル設定はGit管理外
- GitHub Pages向けのレスポンシブな静的HTML
- ポケモンタイプ表示と、島ごとの現在最強・Lv60/Lv80育成後パーティ提案（Lv80は長期予測）
- 「現在」は実際のレベル・進化段階、「育成後」は指定レベル・最終進化と残り進化回数ぶんのメインスキルLv上昇を前提に比較
- 現行246個体キー、性格25種、サブスキル17種、食材19種、きのみ18種の日本語双方向解決
- ローカルQwen VLM（推奨）またはmacOS Visionによる日本語OCR（画像・動画）、差し替え可能なOCR、監査レポート、オフラインレビュー画面
- 取り込み済みの画像・動画は記録され、次の`scan`では新しいものだけを読む差分同期。登録済みの個体を育成後に撮り直すと、新しい個体を増やさずにその個体を更新
- 固定6島（ワカクサ除外）のきのみ・食材基礎エナジー・直接スキルによる日次・週次予測
- 5匹同時シミュレーションによる、元気回復・おてつだい支援・全体スキル・サブスキル込みの編成探索と限界貢献
- ローカルAPIで任意の5匹を選ぶ、料理・スキル込みのカスタム編成シミュレーション
- 現在→Lv60の育成効果、全島汎用性、戦力不足、目標到達日数
- 一般的な理想個体と、手持ちの不足を分けた捕獲候補
- サブスキル・好物きのみから生成する「なぜ強いか」とデータ品質表示
- 一覧を統合した検索・タイプ絞り込み付きボックスと、個体詳細ポップアップ
- 編成画面での目標到達シミュレーション、捕獲画面での外部Tier参考情報と理想個体の定義
- 「使い方」の直前に、OCR・個体評価・編成計算の稼働状況をまとめた状態画面
- 未検証個体の要確認マークと、状態画面から個体評価・編成・整理判定を更新する一括再計算

## クイックスタート

Python 3.9以上が必要です。

```sh
python3 -m pip install -e .
pokesleep-box --db /tmp/pokesleep-demo.sqlite demo
python3 -m http.server 8000 --directory site
```

計算ブリッジを使う場合はNode.js 20以上、動画を使う場合はffmpegも必要です。

ブラウザで `http://localhost:8000` を開きます。

## いつもの使い方（`sync`）

自分のボックスは、次の4手順だけで更新できます。

1. ポケモンの詳細画面のスクリーンショットや画面収録を`inbox/`へ置く
2. `pokesleep-box sync` を実行する
3. 「レビューが必要な個体」が出たら`frames/review.html`を開き、直して保存する
4. 保存した`reviewed_individuals.json`を`pokesleep-box import-json reviewed_individuals.json`で取り込む

```sh
pokesleep-box sync                 # inbox/ の新しい画像・動画だけを処理
pokesleep-box import-json reviewed_individuals.json
pokesleep-box sync --force         # 取り込んだ修正で評価・判定・サイトを作り直す
python3 -m http.server 8000 --directory site/private
```

`sync`は次の順に動きます。

1. 取り込み: `inbox/`のうち、まだ取り込んでいない画像・動画だけを読みます（`scan --ocr auto`と同じく、
   mlx-vlmがあればQwen、なければVision）。撮り直した個体は新しい個体ではなく更新として扱います。
2. SP確認: 今回取り込んだ・更新した個体だけ、画面のSPと計算エンジンのSPを比べます（下記）。
3. 評価（`evaluate`）、4. 判定（`decide`）、5. 非公開サイトの生成（`site/private/`）。

新しい画像・動画がないときは、SP確認・評価・判定を省き、そのことを表示します（`--force`で実行）。
計算エンジンを使えないときは、SP確認と評価を省いてメッセージを出し、判定とサイト生成は行います。
最後に、新しい画像・動画の数、新しい個体と更新した個体の数、SP一致・SP不一致・要確認の数、
レビュー画面とサイトの場所、次にすることを表示します。

手順3〜4は`pokesleep-box sync --reviewed reviewed_individuals.json`でもまとめて実行できます。
このときは修正JSONを取り込んでから評価・判定・サイト生成まで進みます。同じ内容のJSONは
SHA-256で記録し、二度目は取り込みません。`sync`がJSONを自動で探して取り込むことはありません。

### SPによる自動確定（解放済みのみ確定）

SPはNeroli's Labの式で、種族・現在レベル・性格・メインスキルLv・現在レベルで解放済みの
サブスキル・解放済みの食材枠（Lv1/30/60）から決まります。まだ解放されていないサブスキルや
食材枠、メインスキル名はSPに影響しません。そこで`sync`は次のように扱います。

- 画面のSPと計算SPが完全一致（`verify_mode=strict`、Lv55以下）: 解放済みの部分は確定です。
  未解放のサブスキル・食材枠もOCRがはっきり読めていれば（`ocr_missing`に入っていない、食材は個数か
  アイコンで決まり推測ではない）、その個体を自動で検証済みにします（`auto_verified=1`。人の確認
  `review_confirmed`とは区別します）。はっきりしない未解放部分が残るときは未検証のままで、レビュー画面と
  監査レポートにはその項目だけを出します。SPで区別できない読み取り（効果のない性格どうし、
  スキルレベルアップS/Mや同じ係数のボーナス系サブスキル）が`ocr_missing`にあるときも同じです。
- SP不一致: 未検証のまま「SP不一致（画面 X / 計算 Y）」を付け、レビュー画面と監査レポートの先頭に出します。
- 画面のSPがない、計算エンジンを使えない、Lv56以上（計算が近似）: これまでどおり未検証です。
  Lv56以上は、画面のSPと計算SPが完全に一致しても自動では確定しません。
- 人が確認した個体（`review_confirmed`）は変更しません。未検証の個体は博士へ送る候補になりません。

レビュー画面で直した（または`import-json`で取り込んだ）が「照合済み」にチェックしていない個体も、
`import-json`と`sync --reviewed`の取り込み後に同じ規則でSP確認をします。レビュー画面は保存時に、
読み取り時から値を変えた項目を`human_edited`に記録します。この項目は`ocr_missing`に残っていても
読み取り済みとして扱い、値を変えていない項目はOCRの結果のまま扱います（保存しただけでは確定しません）。
SP一致なら`auto_verified=1`で検証済みにし、SP不一致なら「SP不一致」を付けます。`import-json`で
計算エンジンを使えないときは、これまでどおり取り込みだけを行い、SP確認を省いたことを表示します。

メインスキル名は種族で決まるため、OCRで読めなかったときは種族データの（基本の）メインスキルで
補います。種族データを使えないときだけ仮の値を入れ、`ocr_missing`に残して要確認にします。

### 出力先

`sync`の出力はすべてGit管理外の場所で、`src/pokesleep_box/paths.py`の1か所で決めています。
`config/settings.local.json`の`"paths"`で変更できます（公開用の`site/`は指定できません）。

| 内容 | 既定の場所 |
| --- | --- |
| 取り込む画像・動画 | `inbox/` |
| DB | `data/box.sqlite` |
| 抽出フレーム・監査レポート・レビュー画面 | `frames/`、`frames/audit_report.md`、`frames/review.html` |
| 編成計画・Qwenキャッシュ | `data/private/team_plans.json`、`data/private/vlm_cache/` |
| 非公開サイト | `site/private/index.html` |

```json
{"paths": {"db": "data/box.sqlite", "site": "site/private"}}
```

### 個別のコマンド

JSONから取り込む場合や、手順を分けて実行したい場合は個別のコマンドも使えます。

```sh
cp config/settings.example.json config/settings.local.json
pokesleep-box init-db
pokesleep-box import-json data/private/my-box.json
pokesleep-box evaluate
pokesleep-box decide --keep-top-n 2
pokesleep-box render --out site/private
```

日本語JSONもそのまま取り込めます。macOSでは、ポケモンの詳細画面を撮った画像または
画面収録を`inbox/`へ置くだけで、Apple Visionの日本語OCRを使ってローカル認識できます。
画像は外部へ送信されません。初回だけSwift製OCRヘルパーを`.cache/`へ自動ビルドします。

```sh
# 架空画像で、実データを使わず先に一連の流れを試す
pokesleep-box make-ocr-demo
pokesleep-box --db data/demo-ocr.sqlite scan inbox/ocr-demo.png

# 取り込みだけを行う（SP確認・評価・サイト生成まで行うなら sync）
pokesleep-box scan inbox
```

認識結果は安全のため最初は必ず未検証になり、確認前の個体を博士へ送る候補にはしません。
読み取りにくい項目は監査レポートに出ます。`--interval 0.5`で動画の認識間隔も調整できます。
きのみは認識文字ではなく種族データから自動確定します。アイコン表示の食材は種族ごとの
Lv1/30/60候補だけをレビュー画面に表示するので、画像を見ながら各枠を選択できます。
同じ個体を複数の動画で撮った場合は、種族と画面のSPで自動的に結合します。そのため、
1本目で生産情報、2本目でスクロールして性格を撮る運用ができます。現在レベルはSP付近の
ヘッダーだけから読み、食材のLv30/60やサブスキル解放Lvを現在レベルとして扱いません。
レビュー画面では現在レベル・SP・性格・メインスキルLv・食材を直接修正してから照合済みにできます。
### Qwen VLMによる読み取り（推奨）

Apple Silicon（Python 3.10以上）では、ローカルのQwen3.5-9B（mlx-vlm）で読み取れます。
雑に撮った画像やスクロール途中の動画でも、見えている項目だけを読み、同じ個体のフレームを
項目ごとの多数決でまとめます。食材は画面の個数を読み、種族の食材候補と照らして決めます。
同じ個数の候補が2つある枠は、食材アイコンを固定したNeroli's Labの食材画像
（`./engine/install.sh`で取得）と照合して決めます。アイコンと個数が食い違う枠は要確認のまま残ります。
初回だけモデル（約10GB）をHugging Faceからダウンロードし、以後はオフラインで動きます。

```sh
python3.12 -m venv .venv && .venv/bin/pip install -e '.[qwen]'
.venv/bin/pokesleep-box scan inbox            # mlx-vlmがあれば自動でQwenを使う
.venv/bin/pokesleep-box scan inbox --ocr vision  # 速いVisionを明示して使う
```

検証用の30枚（詳細画面のスクリーンショット）では、種族・Lv・SP・性格・メインスキル・
サブスキル5枠のすべてが正しかったのはVisionで14枚、Qwenで30枚でした。食材（3枠の種類と個数）は
Vision 0枚、Qwenとアイコン照合で30枚が正しく、要確認になった個体はありませんでした。同じ30枚を並べた動画でも、Qwenは30個体すべてを同じ精度で
取り出しました。速度は1枚あたり約23秒（M5・8bit）で、Vision（約0.4秒）よりかなり遅いため、
動画は同じに見えるフレームを読み飛ばします。

種族で決まる値は画面から読まずにゲームデータから補完します。きのみの種類、メインスキル、
Lv1の食材と各枠の食材候補・個数、1回のおてつだいで取れるきのみの数（1個、きのみタイプは2個、
「きのみの数S」解放後は+1）です。サブスキルの解放レベルはVer.3.6.0以降の10/25/50/70/80で扱い、
以前の75/100で保存された個体はDBを開いたときに自動で70/80へ移行します。新しいポケモンや食材
（Ver.3.9.0のひとくちナッツなど）は、`engine/install.sh`の固定コミットと`data/names_ja.yaml`を
更新して追従します。

`scan`は読み込んだ画像・動画のSHA-256をDBに記録し、次回からは新しく増えたものだけを読みます。
レビューで直した値が再読み取りで上書きされることもありません。読み直したいときは`--rescan`を付けます。
登録済みの個体をあとで撮り直した画像・動画は、新しい個体ではなくその個体の更新として取り込みます
（評価・判定・レビュー済みの印はそのまま残ります）。同じ個体とみなすのは、性格、進化系統（進化前から
進化後への変化だけ）、各枠の食材の種類が同じで、5つのサブスキル枠が同じか、たねで上位に変えたもの
（例: おてつだいスピードS→M）であり、レベル・メインスキルLvが下がっていないときです。SPも
下がる方向には更新しません。同じ`scan`で取り込んだ複数の画像を同じ既存個体にまとめることはありません。
同じ構成の個体が複数登録されていて、どれの撮り直しか決められないとき（レベル・SPが今の値以下の個体が
一体に絞れず、レベル・SPが完全に一致する個体もないとき）は、新しい個体として登録し、監査レポートに
「重複の可能性」として表示します。
Qwenの読み取り結果は画像ごとに`data/private/vlm_cache/`へ保存されるので、途中で止めた動画の
再開や`--rescan`でも、同じフレームを二度推論しません。

### Google ColabのGPUでまとめて読む（任意）

何十枚・何百枚をまとめて取り込むときは、`tools/colab_vlm.ipynb`でColabのGPUに読み取りだけを
任せられます。**画像はGoogle（ColabとGoogleドライブ）へ送られる**ので、それを許容できる場合だけ
使ってください。手元の`scan`/`sync`は今までどおり外部へ送信しません。

1. 画像をzipにしてGoogleドライブの`マイドライブ/sleepbox-colab/inbox.zip`に置く。
2. VS CodeにGoogle公式の「Colab」拡張機能を入れ、ノートブックのカーネルで「Colab」を選んでログインし、
   GPU（A100またはL4）のランタイムで「すべて実行」する（ブラウザのColabで開いても同じです）。
3. ドライブの`sleepbox-colab/mlx-community_Qwen3.5-9B-8bit-p3/`にできた結果を手元のキャッシュへ
   コピーして`sync`する。読み取り済みの画像はキャッシュに当たり、推論なしで終わります。

```sh
cp "<Googleドライブ>/マイドライブ/sleepbox-colab/mlx-community_Qwen3.5-9B-8bit-p3/"*.json \
   data/private/vlm_cache/mlx-community_Qwen3.5-9B-8bit-p3/
pokesleep-box sync
```

Colabでは同じプロンプトをbf16の重みで実行するため、8bitの手元と出力が少し異なる場合があります。
アイコン照合・SP検算・評価は手元で行うので、読み違いは今までどおりSP不一致や要確認として出ます。

macOS Visionを使えない環境では`--ocr sidecar --ocr-command /path/to/extractor`で従来の
ローカル抽出器を利用できます（sidecar方式の動画抽出にはffmpegが必要です）。取り込み後の
`frames/audit_report.md`、`frames/review.html`、OCR画像、DBはすべてGit管理外です。

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

任意の5匹をその場で再計算する場合は、静的HTTPサーバーではなく次で起動します。

```sh
PYTHONPATH=src python3 -m pokesleep_box.cli --db data/box.sqlite serve --site site/private
```

### 博士に送った個体をボックスから外す

ゲームで博士に送ったら、`serve`で開いた画面でポケモンの詳細を開き、
「博士に送った（ボックスから外す）」を押して確認します。外した個体は一覧・編成・自由編成・
整理判定から外れ、整理判定はその場で更新されます。データは消えず、「整理」タブの
「送った個体」から「戻す」で元に戻せます。保存済みの編成にその個体が入っていた場合は、
その編成を表示から外すので、「状態」タブの一括再計算で作り直してください。
静的ファイル（GitHub Pagesのデモなど）で開いた場合はAPIがないため、ボタンは押せません。

コマンドでも同じことができます。個体IDは画面の「個体ID」（末尾6文字）か、先頭・末尾の
4文字以上で、1匹に決まる場合に使えます。

```sh
PYTHONPATH=src python3 -m pokesleep_box.cli --db data/box.sqlite archive 1a2b3c 4d5e6f
PYTHONPATH=src python3 -m pokesleep_box.cli --db data/box.sqlite archived          # 送った個体の一覧
PYTHONPATH=src python3 -m pokesleep_box.cli --db data/box.sqlite archive --undo 1a2b3c
PYTHONPATH=src python3 -m pokesleep_box.cli --db data/box.sqlite render --out site/private
```

`pokemon_type` にタイプ、`berry` にその個体のきのみ、`production_scores` に計算エンジンが
算出した現在値とLv60/Lv80の総生産・きのみ生産を渡します。画面で好みのきのみ3種を
選ぶたび、好物ボーナスを加えて同一個体を重複させず上位5匹を再計算します。固定島を選ぶと
3種が自動入力され、ワカクサ本島ではその週の3種を自由に選べます。

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

## プライバシー

次は `.gitignore` により公開されません。

- `inbox/` の動画・画像
- `frames/` の抽出フレーム
- `data/private/` と `data/*.sqlite`
- `config/*.local.json`
- 個人データから生成した監査レポート

例外として、`tools/colab_vlm.ipynb`を使う場合だけ、画像をGoogle Colab/Googleドライブへ送ります。

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
