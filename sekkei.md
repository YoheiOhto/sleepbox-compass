# ポケモンスリープ ボックス管理・育成優先度ツール 実装手順書

最終更新: 2026-08-17

---

## 0. この文書の使い方

本書は単体で実装着手できるよう設計されている。実装者は以下の順で読むこと。

1. §1〜§3 で前提と確定事実を把握する
2. §4 で既存資産（nerolis-lab）の何を使うか把握する
3. §9 の未検証項目を**実装前に必ず自分で確認する**
4. §10 のタスクを優先順に実装する

> **重要な原則**: 本書に書かれたゲーム仕様の数値は一次ソースではない。
> スキル係数・食材率・サブスキル倍率などは必ず `nerolis-lab` のコードから読むこと。
> 本書やLLMの記憶から数値を書き写してはならない。

---

## 1. 目的とスコープ

### 1.1 解きたい問題

1. **育成優先度**: 手持ちのどの個体を育てるべきか
2. **処分判断**: どの個体を博士に送るべきか
3. **捕獲指針**: どのポケモンを、どの条件で確保すべきか

### 1.2 前提条件（ユーザー確定事項）

| 項目 | 決定 |
|---|---|
| 育成コスト | **度外視**（アメ・ゆめのかけらの制約を考慮しない） |
| 評価レベル | **Lv60 と Lv80 の2点**。それ以下のレベルでの運用は想定しない |
| 進化 | **常に最終進化まで完了した状態で評価** |
| 取得手段 | スクリーンショット（詳細画面）のみ |
| 処理環境 | PC |
| 閲覧環境 | スマホ（ブラウザ） |
| 配信先 | **GitHub Pages** |

### 1.3 コスト度外視の帰結（重要）

コストを無視すると、以下が**機械的に価値0になる**。実装時に明示的に0を入れること。

- `dream shards`（ゆめのかけら）ユニット → 用途が育成コスト側のみ
- `Research EXP Bonus` / `Sleep EXP Bonus` サブスキル → レベリング加速＝コスト削減
- `Versatile`（candy ユニット）→ 同様

ただし**メインスキルレベルは無料にならない**。スキルLvは
`Skill Level Up S/M` サブスキル ＋ 進化回数 で決まる個体固有値であり、
アメを無限に積んでも上がらない。**個体アイデンティティの一部として扱う。**

### 1.4 スコープ外

- ゲーム内操作の自動化（利用規約違反リスク。§11 参照）
- APK / アセット / 通信からのデータ抽出（同上）
- 料理・備蓄計画、チーム編成そのものの最適化 UI（既存ツールで十分）

---

## 2. 実測で確定した事実（スクリーンショット解析）

Lv15 フシギダネの詳細画面（実物）から確認した内容。

### 2.1 隠れ情報はほぼ存在しない

**Lv15 の時点でサブスキル5枠すべてが名前入りで表示される。**
未開放枠はグレーアウトし開放レベルのバッジ（`Lv.25` 等）が付くが、**名前は読める**。
食材も3枠すべて個数付きで表示される（`x2` / `Lv.30 x5` / `Lv.60 x6`）。

**設計への影響:**

- 「未開放スロットを確率分布で周辺化する」処理は**不要**。評価は決定的
- `hold`（様子見）判定は原則不要
- **1個体1回撮れば永久に撮り直し不要**
- 変動するのは Lv / SP / おてつだい時間 / 最大所持数のみ。すべて導出可能
- 差分運用が成立する（新規捕獲分だけ撮る）

**不変コア** = `(種, 性格, サブスキル×5, 食材×3, メインスキル, スキルLv)`

### 2.2 実測サンプルのフィールド

```
species        フシギダネ          （Lv表記の右にテキストで存在）
level          15
sp             513
ingredients    [(slot1, x2), (Lv30, x5), (Lv60, x6)]
help_interval  1時間11分16秒       ← 導出可能 ⇒ 検算に使える
carry_limit    11個                ← 導出可能 ⇒ 検算に使える
main_skill     食材ゲットS Lv.1
subskills      [げんき回復ボーナス(開放済),
                きのみの数S(Lv.25),
                食材確率アップM(Lv.50),
                おてつだいスピードS(Lv.70),
                リサーチEXPボーナス(Lv.80)]
nature         おとなしい
nature_effect  メインスキル発生確率 ▲▲ / げんき回復量 ▼▼   ← 導出可能 ⇒ 検算
```

### 2.3 冗長性を検算に使う（重要な設計判断）

`SP` / `おてつだい時間` / `最大所持数` / `性格効果` は他フィールドから計算できる。
**パース結果と再計算値を照合すればOCR誤りが自動検出できる。**
チェックサム付きデータ形式として扱い、目視レビューを最小化する。

### 2.4 OCR実装上の制約

| 制約 | 対応 |
|---|---|
| 絶対座標クロップは不可（実測サンプルは既にスクロール中で上下が切れていた） | 緑のセクションヘッダ（`メインスキル・サブスキル` / `詳細ステータス` / `食材`）をテンプレートマッチしてアンカーにし、相対座標で切る |
| 1画面に全情報が入らない | **1個体あたり2フレーム**必要（上端側・下端側）。動画撮影ならコスト増ゼロ |
| サブスキル開放レベルが標準と異なる可能性 | 実測サンプルは `1 / 25 / 50 / 70 / 80`。**ハードコードせずバッジを読む** |
| 種名の認識 | **名前テキストを一次ソース**、sprite を照合用にする。sprite は進化前後で類似し誤りやすい |

---

## 3. 個体の同一性（設計前に決めること）

**ゲーム内に個体IDは露出していない。** スナップショットを取り直したとき
「これは前回のあの個体」と紐付ける手段が原理的に無い。

### 対策

```
uid = hash(種, 性格, サブスキル5つ(順序込み), 食材構成, ニックネーム)
```

- **レベルはキーに含めない**（変化するため）
- 衝突時（同種・同性格・同サブスキルの重複個体）は**ボックス並び順インデックス**でタイブレーク
- 完全に区別不能な個体は同一グループとして扱う（どちらを送っても同じなので実害なし）
- 育成確定個体には**ゲーム内でニックネームを付ける**と追跡が安定する（運用上の推奨）

---

## 4. 既存資産の棚卸し（nerolis-lab）

### 4.1 リポジトリ情報

| 項目 | 値 |
|---|---|
| URL | `https://github.com/nerolis-lab/nerolis-lab` |
| ライセンス | **Apache-2.0**（`NOTICE` の保持が必要） |
| NOTICE 内容 | `Neroli's Lab / Copyright The Neroli's Lab Authors` |
| 構成 | monorepo: `common` / `backend` / `frontend` / `docs` |
| npm公開 | **なし**（root も `common` も `"private": true`）→ ローカルビルド必須 |
| 前身 | Sleep API（`sleepapi.net`）は 2027-01-01 retire 予定 |
| ドキュメント | `docs.nerolislab.com` |

**方針**: fork せず、submodule または固定タグで参照する。

### 4.2 使えるマスタデータ

| 項目 | パス | 内容 |
|---|---|---|
| ポケモン | `common/src/types/pokemon/{berry,ingredient,skill,all}-pokemon.ts` | 254エントリ（71/88/90/5）。進化・イベント個体含む |
| サブスキル | `common/src/types/subskill/subskills.ts` | **17種**、係数付き |
| 性格 | `common/src/types/nature/nature.ts` | **25種**、5倍率 |
| メインスキル | `common/src/types/mainskill/mainskills/` | **17ファミリ / 35バリアント** |
| 島 | `common/src/types/island/island.ts` | 9種（`greengrass`/`cyan`/`taupe`/`snowdrop`/`lapis`/`powerplant`/`amber`/`GGEX`/`CBEX`） |
| 英語名 | `common/src/locales/en/pokemonNames.ts` | 244行、`ABOMASNOW: 'Abomasnow'` 形式 |

#### `Pokemon` 型（`common/src/types/pokemon/pokemon.ts`）

```ts
export interface Pokemon {
  name: string;
  displayName: string;
  pokedexNumber: number;
  specialty: 'berry' | 'ingredient' | 'skill' | 'all';
  frequency: number;
  ingredientPercentage: number;
  skillPercentage: number;
  berry: Berry;
  genders: GenderRatio;
  carrySize: number;
  previousEvolutions: number;
  remainingEvolutions: number;
  evolvesFrom?: string;
  evolvesInto: string[];
  ingredient0: IngredientSet[];
  ingredient30: IngredientSet[];
  ingredient60: IngredientSet[];
  skill: Mainskill;
  pityProcThreshold: number;
}
```

出典コメント: 基礎 skill% / ing% は Mathcord RP data project。
**マスクデータの推定を自分で行う必要はない。**

`OPTIMAL_POKEDEX` / `INFERIOR_POKEDEX` / `COMPLETE_POKEDEX` の区分が存在する
→ **捕獲指針のベースラインとして利用可能**。

#### `Subskill` 型と実例

```ts
export interface Subskill {
  name: string;
  shortName: string;
  amount: number;
  rarity: 'gold' | 'silver' | 'white';
}
```

17種一覧（`SUBSKILLS`）:
`Berry Finding S`, `Dream Shard Bonus`, `Energy Recovery Bonus`, `Helping Bonus`,
`Helping Speed S`, `Helping Speed M`, `Ingredient Finder S`, `Ingredient Finder M`,
`Inventory Up S`, `Inventory Up M`, `Inventory Up L`, `Research EXP Bonus`,
`Skill Level Up M`, `Skill Level Up S`, `Skill Trigger M`, `Skill Trigger S`,
`Sleep EXP Bonus`

#### `Nature` 型

```ts
export type NatureModifier = 'speed' | 'ingredient' | 'skill' | 'energy' | 'exp' | 'neutral';
export interface Nature {
  name: string;
  prettyName: string;
  positiveModifier: NatureModifier;
  negativeModifier: NatureModifier;
  frequency: number;   // 例: 1.1
  ingredient: number;  // 例: 0.8
  skill: number;
  energy: number;      // 例: 0.88
  exp: number;
}
```

### 4.3 スキル評価スキーマ（探していたもの・既存）

`common/src/types/mainskill/mainskill-unit.ts`:

```ts
export const mainskillUnits = [
  'energy', 'berries', 'ingredients', 'helps', 'skill helps',
  'dream shards', 'strength', 'pot size', 'crit chance', 'candy', 'items'
] as const;
```

`common/src/types/mainskill/mainskill.ts`:

```ts
export type AmountParams = { skillLevel: number; extra?: number; ingredient?: Ingredient };
export type AmountFunction = (params: AmountParams) => number;

export type MainskillActivation = {
  unit: MainskillUnit;
  amount: AmountFunction;
  teamAmount?: AmountFunction;
  critAmount?: AmountFunction;
};

export abstract class Mainskill {
  abstract readonly name: string;
  abstract readonly RP: number[];        // レベル別RP。length = maxLevel
  abstract readonly activations: ActivationsType;
  get maxLevel(): number;
  getRPValue(level: number): number;
  hasUnit(unit: string): boolean;
  getUnits(): MainskillUnit[];
  leveledAmount(amounts: number[]): AmountFunction;
}
export const MAINSKILLS: Mainskill[] = [];
export const INGREDIENT_SUPPORT_MAINSKILLS: Mainskill[] = [];  // チーム食材に影響するスキル
```

#### 全スキル → ユニット対応表（実測）

| スキル | ユニット |
|---|---|
| Charge Strength S / M | `strength` |
| Charge Energy S | `energy` |
| Berry Burst | `berries` |
| Ingredient Magnet S | `ingredients` |
| Ingredient Draw S | `ingredients` |
| Cooking Assist S | `ingredients` |
| Cooking Power-Up S | `pot size` |
| Tasty Chance S | `crit chance` |
| Extra Helpful S | `helps` |
| Helper Boost | `helps`（`uniqueBoostTable` でタイプ数依存） |
| Energizing Cheer S | `energy` |
| Energy For Everyone S | `energy` |
| Dream Shard Magnet S | `dream shards` |
| Versatile | `candy` |
| Metronome | **なし（`activations = {}`）** |
| Skill Copy | **なし（`activations = {}`）** |

modified variant（`-moonlight`, `-bad-dreams`, `-plus`, `-present`, `-lunar-blessing`,
`-berry-juice`, `-heal-pulse`, `-nuzzle`, `-super-luck`, `-hyper-cutter`,
`-stockpile`, `-disguise`, `-draco-meteor`, `-bulk-up`, `-minus`, `_mimic`,
`_transform`, `-aura-sphere`）は複数ユニットを持つものがある。
例: `energizing-cheer-s-nuzzle` → `energy` + `skill helps`。

> **`strength` と `energy` は別ユニット**。混同しないこと。

### 4.4 使える計算エンジン

| 機能 | パス |
|---|---|
| エナジー計算 | `backend/src/services/calculator/energy/energy-calculator.ts` |
| お手伝い計算 | `backend/src/services/calculator/help/help-calculator.ts` |
| 産出計算 | `backend/src/services/calculator/production/produce-calculator.ts` |
| スキル発動計算 | `backend/src/services/calculator/skill/skill-calculator.ts` |
| チームシミュレータ | `backend/src/services/simulation-service/team-simulator/` |
| セットカバーソルバ | `backend/src/services/solve/set-cover.ts` |
| 料理ティアリスト | `backend/src/services/tier-list/cooking-tier-list.ts` |

`skill-calculator.ts` には**スキル特化型が2発分バンクできる**処理が実装済み:

```ts
export function calculateAverageNumberOfSkillProcsForHelps(params: {
  skillPercentage: number; helps: number; pokemon: Pokemon;
}): number
```

#### Metronome / Skill Copy は解析不要（訂正事項）

`activations` は空だが、価値は**シミュレータ側で再帰的に解決**されている。

`backend/src/services/simulation-service/team-simulator/skill-state/skill-effects/metronome/metronome-effect.ts`:

```ts
export class MetronomeEffect implements SkillEffect {
  activate(skillState: SkillState): SkillActivation {
    const selectedSkill = skillState.rng.randomElement(Metronome.metronomeSkills);
    const metronomedSkill = skillState.skillEffects.get(selectedSkill)?.activate(skillState);
    ...
  }
}
```

`Metronome.blockedSkills` / `SkillCopy.blockedSkills` で対象外スキルも定義済み。
**不動点反復を自分で実装する必要はない。**

#### ΔTeam も既存API（最重要）

`backend/src/services/api-service/production/production-service.ts`:

```ts
export function calculateIv(
  params: { settings: TeamSettingsExt; members: TeamMemberExt[]; variants: TeamMemberExt[] },
  iterations = 1400
): CalculateIvResponse
```

`variants` の各個体を同一チームに差し替えて Monte Carlo する。
つまり `helps` / `energy` 系（げんきオール、おてつだいブースト）の
「単体では評価できない」スキルの評価が、そのまま使える形で実装済み。

**この関数はDBに触らない**（DAO を使うのはコントローラ層のみ）。
→ **MySQL もバックエンド起動も不要で、TSスクリプトから直接 import できる。**
これが実装コストを最も下げるポイント。

同ファイルの他のエクスポート:
`calculatePokemonProduction`, `calculateTeam`, `calculateSimple`

### 4.5 HTTPエンドポイント（バックエンドを起動する場合）

```
POST /calculator/production/:name
POST /calculator/team
POST /calculator/iv
POST /solve/recipe/:name
POST /solve/ingredient/:name
POST /tierlist/cooking
GET  /pokemon  /nature  /subskill  /mainskill
```

**推奨は直接 import 方式**（DB不要のため）。

### 4.6 入力型（エンジン呼び出しに必要）

```ts
// common/src/types/team/member.ts
export interface TeamMemberSettingsExt {
  level: number;
  nature: Nature;
  subskills: Set<string>;
  skillLevel: number;
  carrySize: number;
  ribbon: number;
  externalId: string;
  sneakySnacking: boolean;
}
export interface TeamMemberExt {
  pokemonWithIngredients: PokemonWithIngredients;  // { pokemon: Pokemon, ingredientList: IngredientSet[] }
  settings: TeamMemberSettingsExt;
}

// common/src/types/team/team.ts
export interface TeamSettingsExt {
  camp: boolean;
  bedtime: Time;
  wakeup: Time;
  includeCooking: boolean;
  stockpiledIngredients: IngredientIndexToFloatAmount;
  potSize: number;
  island: IslandInstance;   // { ...Island, areaBonus: number }
}

// common/src/types/instance/pokemon-instance.ts
export interface PokemonInstanceBase<P, N, S, I> {
  pokemon: P; level: number; ribbon: number; carrySize: number;
  skillLevel: number; nature: N; subskills: S[]; ingredients: I[];
  sneakySnacking: boolean;
}
```

`ribbon` と `sneakySnacking` の扱いは §9 で確認すること。

---

## 5. アーキテクチャ

```
┌─────────────────────────────────────────────────────────┐
│ スマホ                                                   │
│  画面録画（詳細画面をスワイプで送る）                     │
└────────────────────────┬────────────────────────────────┘
                         │ iCloud写真 / AirDrop / adb pull / Syncthing
┌────────────────────────▼────────────────────────────────┐
│ PC (Python)                                              │
│  ffmpeg フレーム抽出 → phash 重複除去                    │
│  アンカー検出 → クロップ → 分類/OCR                      │
│  冗長フィールドで検算 → 低信頼のみレビューUI              │
│  SQLite（スナップショット + 差分）                        │
└────────────────────────┬────────────────────────────────┘
                         │ stdin/stdout JSON （契約は §7）
┌────────────────────────▼────────────────────────────────┐
│ engine (TypeScript, 薄いCLI ~50行)                       │
│  nerolis-lab の calculateIv / calculatePokemonProduction │
│  を import して呼ぶだけ。ロジックは書かない               │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│ PC (Python)                                              │
│  スコア集計 → 支配判定 → 静的HTML生成                    │
└────────────────────────┬────────────────────────────────┘
                         │ git push
┌────────────────────────▼────────────────────────────────┐
│ GitHub Pages → スマホのブラウザで閲覧                     │
└─────────────────────────────────────────────────────────┘
```

### 設計原則

1. **境界は JSON 1本**。nerolis-lab のバージョン更新に追従しやすくする
2. **ゲーム仕様の計算は一切 Python に書かない**。すべてエンジン側に委譲
3. **エンジンは nerolis-lab を fork しない**。submodule / 固定タグで参照
4. Python 側の責務は「取り込み・永続化・意思決定・提示」のみ

### ディレクトリ構成

```
pokesleep-box/
├── README.md
├── NOTICE                      # Apache-2.0: nerolis-lab の帰属を記載
├── config/
│   ├── settings.yaml           # 島・フィールドボーナス・なべ容量・就寝起床
│   ├── valuation.yaml          # ユニット→エナジー換算（§6.2）
│   └── protected.yaml          # ホワイトリスト（寝顔図鑑・イベント限定等）
├── data/
│   ├── names_ja.yaml           # ★タスク1の成果物（日英対応）
│   ├── templates/              # アンカー・語彙テンプレート画像
│   └── box.sqlite
├── engine/                     # TypeScript
│   ├── package.json
│   ├── src/cli.ts              # stdin JSON → stdout JSON
│   └── vendor/nerolis-lab/     # submodule
├── src/                        # Python
│   ├── ingest/                 # 動画→フレーム→クロップ→分類
│   ├── review/                 # 低信頼フィールドのレビューUI
│   ├── store/                  # SQLite
│   ├── evaluate/               # エンジン呼び出し・スコア集計
│   ├── decide/                 # 支配判定・捕獲基準
│   └── render/                 # 静的HTML生成
├── site/                       # GitHub Pages 出力先
└── tests/
```

### 5.5 動画取り込みパイプライン（200匹スケール）

**前提**: iPhone + Mac。ゲームはモバイル専用でPC版が存在しないため、
他ゲームのインベントリスキャナのような「ツール側が自動スクロールしてライブキャプチャ」
方式は使えない。**人間がスワイプした画面収録が唯一の一括取り込み経路。**

先行実装は存在しない（§12.4 参照）。ただし部品はすべて既製品で、実装規模は200〜300行程度。

#### 撮影プロトコル

```
1. iOS コントロールセンター → 画面収録 開始
2. ボックスを固定の並び順（レベル昇順を推奨）に設定
3. 1匹目の詳細画面を開く
4. 各個体について:
     上端側が見える位置で 0.5秒静止
     下端側までスクロールして 0.5秒静止
     次の個体へ（スワイプ or 戻る→次をタップ）
5. 収録停止 → AirDrop で ~/pokesleep-box/inbox/
```

- **200匹想定の実測見積**: 撮影 4〜8分、フレーム抽出 `fps=4` で約1500〜2000枚、
  重複除去後 400〜500枚（1個体2〜3枚）
- 静止 0.5 秒は `fps=4` で 2フレーム確保するため。これが §5.6 L3 の多数決の材料になる
- **並び順を固定しておくこと**。`box_index` が §3 の同一性解決のタイブレークになり、
  かつ §10 タスク7 の作業リスト整列に使われる

#### フレーム処理

```bash
ffmpeg -i inbox/box.mov -vf "fps=4" -vsync 0 frames/%05d.png
```

Apple Silicon なら iOS の HEVC がハードウェアデコードされるため高速。

**重複除去の注意（重要）**:
ポケモン詳細画面の**スプライトはアニメーションしている**。
したがって画面全体の perceptual hash は同一個体でもフレーム間で変化し、
**全画面ハッシュによる重複除去は機能しない。**

```python
# NG: 全画面
dedup_key = phash(frame)

# OK: アンカー相対の静止テキスト領域のみ
dedup_key = phash(crop(frame, anchor_relative="name_and_level"))
```

同種の問題は他ゲームのOCRツールでも報告されている既知の罠（スプライトのアニメーションが
名前認識やフラグ検出を壊す）。

#### 上下フレームの対応付け

1個体につき「上端側フレーム」と「下端側フレーム」が必要（§2.4）。

```
アンカー検出結果でフレームを分類:
  「食材」ヘッダが見える            → 上端側
  「詳細ステータス」ヘッダが見える   → 下端側
同一 dedup_key グループ内でペアリング。
ペアが揃わない個体は incomplete としてレビューキューへ。
```

#### フォールバック経路（必須）

**動画に一本化してはいけない。** 1匹の取りこぼしで全体撮り直しになる。

```
inbox/  ← .mov / .MOV / .png / .PNG / .heic を同一パイプラインで受け付ける
```

取りこぼした個体だけスクショ1枚を投げ込めば補完できる設計にする。
写真アプリ経由の `.heic` は `pillow-heif` または `sips -s format png` で前段変換。

---

## 5.6 正確性の担保（Accuracy Assurance）

**200匹規模では「だいたい合っている」は使えない。** 1匹の誤読が誤った処分判断に直結する。
以下の7層を実装し、**全個体がL1〜L5を通過するまで判定フェーズに進ませない。**

### L1: 語彙制約（Closed Vocabulary Rejection）

種名(254) / サブスキル(17) / メインスキル(17ファミリ) / 性格(25) / 食材 / きのみ は
すべて**有限集合**。集合外の値が出たら値ではなく**エラーとして扱う**。

```python
if parsed_nature not in NATURES_JA:
    field_confidence[uid]['nature'] = 0.0   # 破棄。候補上位3件をレビューへ
```

自由文字列としてOCRしてはいけない。最近傍分類として実装すれば、
語彙内のどれかに必ず落ちるうえ、距離が信頼度になる。

### L2: 構造的検算 — SPチェックサム ★中核

`common/src/utils/rp-utils/rp.ts` の `RP` クラスが表示SPを再現する。

```ts
export class RP {
  constructor(pokemonInstance: PokemonInstanceWithoutRP)  // rp と carrySize を除く全フィールド
  calc(): number   // Math.round(miscFactor * (ingredientFactor + berryFactor + skillFactor))
}
```

`calc()` は **整数**を返す。したがって:

```
new RP(parsed_instance).calc() === 画面に表示されたSP
```

**この1つの整数が、種・レベル・性格・サブスキル・スキルLv・食材・ribbon の
結合チェックサムになる。** どれか1つでも誤読していればほぼ確実に一致しない。

実測サンプル（Lv15 フシギダネ、表示SP `513`）で必ず動作確認すること。

#### なぜ信用できるのか（決定性の根拠）

計算に**隠れパラメータが一つも無い**。乱数もサーバ値も入らず、
入力は種・レベル・性格・サブスキル・スキルLv・食材・ribbon のみ。

さらに実装はゲーム内部の**丸め順序まで再現**している:

```ts
get helpFactor() {
  const levelFactor = 1 - 0.002 * (this.level - 1);
  return 5 * MathUtils.floorWithIEEE754Correction(
    3600 / (this.pokemon.frequency *
      MathUtils.floorWithIEEE754Correction(
        levelFactor * natureFreq * helpSpeedSubskills * ribbonFactor, 4)), 2);
}
```

`floorWithIEEE754Correction` による4桁・2桁の段階的切り捨てが入っている。
単なる近似式ではなく丸めまで合わせ込まれているため、
SPが数百〜数千の整数であることを踏まえると**偶然一致する確率は無視できる**。

→ 「SPが一致した」＝「7〜11フィールドが全部正しい」がほぼ言える。

#### 制約1: ribbon が自由パラメータになる

式に `calculateRibbonFrequency(pokemon, ribbon)` が入っている。
ribbon が画面から読めない場合、未知変数として L2b の探索対象になり、
**解の一意性が落ちる**。§9-13 で必ず確認すること。

読めない場合の運用: まず `ribbon=0` で試し、不一致なら ribbon を探索変数に加える。

#### 制約2: Lv55 超では厳密一致しない（許容誤差が必要）

`rp.ts` にコメントで明記されている:

```ts
// We make assumption regarding ingredient growth past 55
const ingredientGrowth =
  RP.ingGrowth[this.level] ??
  0.000000398 * Math.pow(this.level, 3) + 0.000159 * Math.pow(this.level, 2)
    + 0.00367 * this.level - 0.00609 + 1;
```

Lv55 まではテーブル参照、**Lv56 以上は多項式フィットによる推定**。
また `ingredientsValue` を60食材で同値と仮定して3で割る旨のコメントもある。

したがって **Lv56 以上の個体では SP が微小にずれる可能性がある。**

**運用方針**:

| レベル帯 | 検算方式 |
|---|---|
| Lv1〜55 | **厳密一致**（`computedSp === displayedSp`）を要求 |
| Lv56〜 | **許容誤差**内なら pass（`abs(diff) <= tolerance`）。デフォルト `tolerance = 2` |
| 閾値超で誤差も超過 | `skipAboveLevel` 設定でスキップ可。ただし `verified` は立てず監査対象に残す |

通常運用では問題になりにくい。検算に使うのは**撮影時点のレベル**であり、
多くの個体は低〜中レベル。Lv60/80 は評価用の仮想レベルなので SP 照合には使わない。
既に高レベルまで育てた個体を取り込むときだけこの分岐が効く。

**不一致を機械的に「誤読」と断定しないこと。** レベル帯を必ず先に見る。

補助の検算（独立した2本目・3本目）:

| 表示値 | 再計算に使うもの |
|---|---|
| 最大所持数（例: 11個） | `common/src/utils/carry-size-utils/` |
| おてつだい時間（例: 1時間11分16秒） | `RP.helpFactor` / `backend/.../help-calculator.ts` |
| 性格効果の矢印（▲▲ / ▼▼） | `Nature.positiveModifier` / `negativeModifier` |

Lv56 以上で SP 検算の信頼度が落ちる個体では、
**最大所持数と性格効果の照合が相対的に重要になる**（こちらはレベル依存の推定を含まない）。

### L2b: 不一致時の自動訂正（候補探索）

SPが一致しない場合、**どのフィールドが誤りかは分からない**。
そこで各フィールドの候補上位k件から組み合わせを探索し、SPを再現する解を探す。

```python
def repair(parsed, displayed_sp, candidates, k=3, tolerance=0):
    # 候補空間は小さい（低信頼フィールドのみ × k件）
    for combo in itertools.product(*[candidates[f][:k] for f in low_conf_fields]):
        trial = parsed.replace(**dict(zip(low_conf_fields, combo)))
        if abs(engine_rp(trial) - displayed_sp) <= tolerance:
            yield trial, 'auto_repaired'
```

- **解が1つ** → 自動訂正して `verified=1`
- **解が複数** → レビューキューへ（候補を絞った状態で提示できる）
- **解が0** → レビューキューへ（パース全体を疑う）

> `tolerance > 0`（Lv56以上）では**解が複数になりやすい**。
> 高レベル個体は自動訂正を無効化し、素直にレビューへ回す方が安全。

低信頼フィールドは通常0〜2個なので探索空間は数十通り。**計算量は問題にならない。**

#### 応用: SPからの欠損フィールド逆算

L2b は「訂正」だけでなく「**欠損の確定**」にも使える。
たとえば性格の分類だけ失敗した個体なら、25通り試してSPが一致するものを探せば確定する。
サブスキル1枠なら17通り、食材1枠なら数通り。
**1フィールドの完全欠損は SP から一意に復元できることが多い。**


> **注意**: `ribbon` が未知パラメータとして残る場合、探索の自由度が増えてしまう。
> ribbon が画面から読めるかどうかを §9-13 で必ず確認すること。
> 読めない場合は ribbon=0 固定で試し、不一致なら ribbon を探索変数に加える。

### L3: 時間方向の多数決（動画方式の最大の利点）

同一個体を複数フレーム捉えているため、**フィールド単位で多数決が取れる。**
単発スクショ方式には無い精度上の優位性。

```python
for field in FIELDS:
    votes = [f[field] for f in frames_of_individual]
    value, count = Counter(votes).most_common(1)[0]
    confidence[field] = count / len(votes)
```

- 全フレーム一致 → confidence 1.0
- 割れた場合 → 多数派を採用し、confidence を下げてL2bの探索対象に含める

**撮影プロトコルで0.5秒静止させる理由がこれ。** フレーム数を稼ぐと精度が上がる。

### L4: 上下フレーム整合

上端側・下端側の両方に映るフィールド（レベル、種名など）が一致するか照合。
不一致 = ペアリングミス。個体の混線を検出できる。

### L5: 総数一致

ボックス一覧画面から総数を取得し、抽出できた個体数と照合する。

```
ボックス一覧の総数: 200
抽出された個体数:   199   → 1匹取りこぼし。差分を特定してフォールバック撮影
```

200匹規模ではこれが**唯一の「取りこぼしゼロ」保証**になる。
一覧画面のスクショ撮影を撮影プロトコルに必ず含めること。

### L6: サンプル監査（精度の実測）

パイプラインを信用する前に、**フィールド別精度を数値で出す。**

```
ランダムに20個体を抽出 → 元スクショを人間が目視 → フィールド単位で正解率を算出
出力: audit_report.md
  species     20/20  100.0%
  nature      20/20  100.0%
  subskills   99/100  99.0%   ← 5枠×20
  ingredients 59/60   98.3%
  skill_level 20/20  100.0%
```

**受け入れ基準**（これを満たすまで判定フェーズに進まない）:

| 指標 | 目標 |
|---|---|
| 不変コア（種/性格/サブスキル/食材/スキルLv）のフィールド精度 | **≥ 99.5%** |
| SPチェックサム一致率（Lv55以下・厳密一致・自動訂正後） | **≥ 98%** |
| Lv56以上の個体 | `tolerant` / `skipped` を許容。ただし全件レビュー消化 |
| L1〜L5 未通過で `verified=0` のまま残る個体 | **0** |
| L5 総数一致 | **必須** |

> SP一致率の分母は **Lv55以下の個体のみ**。Lv56以上を混ぜると
> §5.6 L2 制約2 の推定誤差で数字が汚れ、指標として機能しなくなる。
> 監査レポートはレベル帯で分けて出力すること。

### L7: 差分監査（再取り込み時）

2回目以降の取り込みで、**不変コアが変化していたら異常**（§2.1 より変わり得ないため）。

```
uid が一致するのに sub_slot3 が違う → どちらかの取り込みが誤り。両方をレビューへ
uid が一致しないのに他が酷似        → 同一性解決の失敗を疑う
```

これは運用を続けるほど効く。過去の誤読を後から発見できる唯一の仕組み。

### 実装順序と責務の分離

```
L1（語彙）      → ingest 内で即時。集合外を値として通さない
L3（多数決）    → ingest の集約段階
L4（上下整合）  → ingest の集約段階
L2（SP検算）    → engine 呼び出し。★ここが本命
L2b（自動訂正） → engine を候補分だけ繰り返し呼ぶ
L5（総数）      → ingest 完了時のゲート
L6（監査）      → 初回構築時に1度 + パイプライン変更時
L7（差分）      → 2回目以降の取り込み時
```

**L2 は engine（TypeScript）側の責務**。Python に RP 計算を再実装してはいけない
（§5 設計原則: ゲーム仕様の計算は一切 Python に書かない）。
engine CLI に `verify` モードを追加し、`{instance, displayedSp}` を渡して
`{match: bool, computed: number}` を返す形にする。

---

## 6. 評価仕様

### 6.1 評価アンカー

| アンカー | 有効サブスキル | 食材枠 | 位置づけ |
|---|---|---|---|
| **S60** | slot1, Lv25, Lv50 | 3枠すべて | **主軸** |
| **S80** | 全5枠 | 3枠 | 伸びしろ |

> **Lv50 ではなく Lv60 をアンカーにすること。**
> 食材3枠目が Lv60 開放のため、Lv50 評価では食材型を系統的に過小評価する。

出力は単一スコアではなく **`(S60, S80, ΔS)` の3値**。
`ΔS = S80 - S60` が大きい個体が「後半で化ける」枠。

**注意**: サブスキル開放レベルは個体/種によって異なる可能性がある（実測サンプルは
`1/25/50/70/80`）。ハードコードせず、パースしたバッジ値からアンカー時点の
有効サブスキル集合を決定すること。

### 6.2 valuation.yaml（自作する唯一の価値定義）

```yaml
# 11ユニット → エナジー換算。解決方法は4種類
units:
  energy:           { resolve: const,       rate: 1.0 }
  strength:         { resolve: const,       rate: 1.0 }   # ※ energy と別ユニット
  berries:          { resolve: derive }                    # 種族きのみ × 島一致 × areaBonus
  ingredients:      { resolve: from_recipes }              # スカラー不可。§6.3
  pot_size:         { resolve: from_recipes }
  crit_chance:      { resolve: from_recipes }
  helps:            { resolve: delta_team }                # 単体評価不能
  skill_helps:      { resolve: delta_team }
  items:            { resolve: delta_team }
  dream_shards:     { resolve: const,       rate: 0.0 }    # コスト度外視 → 0
  candy:            { resolve: const,       rate: 0.0 }    # 同上

# 解決順序（依存関係。これを守らないと動かない）
resolve_order: [const, derive, from_recipes, delta_team]

anchors: [60, 80]
roles: [berry, ingredient, skill]
keep_top_n_per_species: 2
delta_s_threshold: 0.15        # ΔS/S60 がこれ以上なら「後半型」とマーク
```

サブスキル側の 0 扱い（コスト度外視のため）:

```yaml
zero_value_subskills:
  - Research EXP Bonus
  - Sleep EXP Bonus
  - Dream Shard Bonus
```

### 6.3 食材単価は定数ではない

食材を「基礎エナジー」で換算すると**2〜3倍過小評価する**。実際の価値は

```
食材jの単価 = 料理の基礎エナジー × レシピボーナス × レシピLv倍率 ÷ 必要個数
```

で決まり、**なべ容量とその週のレシピに完全依存**する。
→ スカラー定数ではなく `config/settings.yaml` の料理環境から毎回導出するベクトル。

### 6.4 げんき系・helps 系は編成差分でしか測れない

「回復量」を価値にしてはいけない。げんきが高い状態で撒くと**上限で溢れて捨てる**ため、
価値が非線形かつチーム依存。

```
value(i) = E[チーム日産 | i を含む] − E[チーム日産 | i を同役割ベースラインに置換]
```

→ `calculateIv` の `variants` にこれをそのまま渡す。

### 6.5 2段構成（計算量対策）

1. **単体スコア**で全個体をふるい（`helps`/`energy` 系は暫定値）
2. **上位30〜50個体に絞って** `calculateIv` を回し、真の ΔTeam を確定

`calculateIv` のデフォルトは `iterations = 1400`。
100個体 × 2アンカー × 3ロール = 600シミュレーション。
`backend/src/services/simulation-service/team-simulator/team-simulator-benchmark.test.ts`
で実測してから並列度を決めること。

---

## 7. JSON契約（Python ↔ engine）

### 7.1 リクエスト

```json
{
  "settings": {
    "island": "greengrass",
    "areaBonus": 35,
    "camp": false,
    "bedtime": "22:00",
    "wakeup": "06:00",
    "potSize": 60,
    "includeCooking": true
  },
  "baselineTeam": [
    { "pokemon": "VENUSAUR", "level": 60, "nature": "Quiet",
      "subskills": ["Ingredient Finder M", "Helping Speed M"],
      "skillLevel": 3, "ingredients": ["Honey", "Honey", "Snoozy Tomato"],
      "ribbon": 0, "sneakySnacking": false, "externalId": "base-1" }
  ],
  "variants": [
    { "externalId": "uid-abc123", "pokemon": "VENUSAUR", "level": 60, "...": "同上" }
  ],
  "iterations": 1400
}
```

### 7.2 レスポンス

```json
{
  "variants": [
    {
      "externalId": "uid-abc123",
      "production": {
        "berries":     { "amount": 0.0, "energy": 0.0 },
        "ingredients": [{ "name": "Honey", "amount": 0.0 }],
        "skillProcs":  0.0,
        "skillUnits":  { "ingredients": 0.0, "energy": 0.0 }
      },
      "totalEnergy": 0.0
    }
  ],
  "engineVersion": "nerolis-lab@<commit-sha>"
}
```

`engineVersion` を必ず返し、SQLite の `evaluation` に保存する。
エンジン更新時に再計算が必要な行を特定できるようにする。

---

## 8. データスキーマ（SQLite）

```sql
-- 撮影素材
CREATE TABLE capture (
  id          INTEGER PRIMARY KEY,
  path        TEXT NOT NULL,
  sha256      TEXT NOT NULL UNIQUE,
  captured_at TEXT NOT NULL,
  kind        TEXT NOT NULL CHECK(kind IN ('detail_upper','detail_lower','box_list'))
);

-- 個体（不変コア + 変動値）
CREATE TABLE individual (
  uid            TEXT PRIMARY KEY,          -- §3 の代理キー
  species        TEXT NOT NULL,             -- nerolis-lab の name (例: BULBASAUR)
  nickname       TEXT,
  level          INTEGER,
  nature         TEXT NOT NULL,
  ing1 TEXT, ing1_amount INTEGER,
  ing2 TEXT, ing2_amount INTEGER,
  ing3 TEXT, ing3_amount INTEGER,
  sub_slot1 TEXT, sub_slot1_unlock INTEGER, -- unlock はパースしたバッジ値
  sub_slot2 TEXT, sub_slot2_unlock INTEGER,
  sub_slot3 TEXT, sub_slot3_unlock INTEGER,
  sub_slot4 TEXT, sub_slot4_unlock INTEGER,
  sub_slot5 TEXT, sub_slot5_unlock INTEGER,
  main_skill     TEXT NOT NULL,
  skill_level    INTEGER NOT NULL,
  sp             INTEGER,
  box_index      INTEGER,
  first_seen     TEXT NOT NULL,
  last_seen      TEXT NOT NULL,
  src_capture    INTEGER REFERENCES capture(id),
  confidence     REAL NOT NULL,             -- 最小フィールド信頼度
  verified       INTEGER NOT NULL DEFAULT 0,
  -- SP検算の結果（§5.6 L2）
  sp_computed    INTEGER,
  sp_diff        INTEGER,
  verify_mode    TEXT CHECK(verify_mode IN ('strict','tolerant','skipped','failed')),
  repaired       INTEGER NOT NULL DEFAULT 0  -- L2b で自動訂正されたか
);

-- フィールド単位の信頼度（レビューキュー生成用）
CREATE TABLE field_confidence (
  uid        TEXT NOT NULL REFERENCES individual(uid),
  field      TEXT NOT NULL,
  value      TEXT,
  confidence REAL NOT NULL,
  candidates TEXT,                          -- JSON: 上位3候補
  PRIMARY KEY (uid, field)
);

-- 評価結果
CREATE TABLE evaluation (
  uid            TEXT NOT NULL REFERENCES individual(uid),
  anchor_level   INTEGER NOT NULL,          -- 60 or 80
  role           TEXT NOT NULL,             -- berry/ingredient/skill
  score          REAL NOT NULL,
  percentile     REAL,                      -- 同種内
  delta_team     REAL,
  engine_version TEXT NOT NULL,
  valuation_hash TEXT NOT NULL,             -- valuation.yaml のハッシュ
  evaluated_at   TEXT NOT NULL,
  PRIMARY KEY (uid, anchor_level, role, engine_version, valuation_hash)
);

-- 判定
CREATE TABLE decision (
  uid        TEXT PRIMARY KEY REFERENCES individual(uid),
  verdict    TEXT NOT NULL CHECK(verdict IN ('keep','send','protected')),
  reason     TEXT NOT NULL,
  decided_at TEXT NOT NULL
);

-- 捕獲基準
CREATE TABLE catch_criterion (
  species      TEXT PRIMARY KEY,
  policy       TEXT NOT NULL,   -- always / conditional / never
  condition    TEXT,            -- 人間可読の条件文
  p_improve    REAL,            -- 手持ち改善確率
  e_gain       REAL,            -- 期待改善量
  computed_at  TEXT NOT NULL
);
```

`confidence` と `verified` を**最初から持たせること**。
OCRは必ず間違うので、低信頼フィールドだけレビューに回す設計でないと
全件目視という最悪の運用になる。

---

## 9. 実装前に必ず自分で確認すべき項目

本書に書けなかった、または情報が古い可能性がある項目。

| # | 確認事項 | 確認方法 |
|---|---|---|
| 1 | `ribbon` の意味と評価への影響 | `common/src/utils/` と member-state を grep |
| 2 | `sneakySnacking` の扱い（食材型の評価に影響） | 同上 |
| 3 | `carrySize` は基礎値か実効値か（サブスキル込みか） | `common/src/utils/carry-size-utils/` |
| 4 | `pityProcThreshold` の使われ方 | `skill-calculator.ts` |
| 5 | `INGREDIENT_SUPPORT_MAINSKILLS` フラグの意味 | `mainskill.ts` のコンストラクタと tier-list |
| 6 | 島の `areaBonus` の入力形式（0〜100 か 0〜1 か） | `island-utils.ts` |
| 7 | `Time` 型の形式 | `common/src/types/time/time.ts` |
| 8 | 進化によるスキルLv増加の実装場所 | `evolvesInto` / `previousEvolutions` の使用箇所 |
| 9 | 博士に送った際の戻りリソースの現行仕様 | ゲーム内で実確認（本書に定数を書かない） |
| 10 | ゲーム内に一括送り機能があるか、保護機能があるか | ゲーム内で実確認。作業リストの形式が変わる |
| 11 | サブスキル開放レベルが種/個体で異なるか | 複数個体のスクショで確認 |
| 12 | 詳細画面が左右スワイプで隣の個体に送れるか | 撮影方式が変わる（§5.5） |
| 13 | **`ribbon` が画面から読み取れるか** | §5.6 L2b の探索自由度に直結。読めない場合は探索変数化 |
| 14 | ボックス一覧画面に総数表示があるか | §5.6 L5（取りこぼしゼロ保証）の前提 |
| 15 | `RP.filteredSubskills` が現在レベルで開放済みのものだけを数えるか | SP検算の前提。`rp.ts` を読む |

**データ品質の既知の問題**:
`common/src/types/mainskill/mainskills/skill-copy/skill-copy.ts` に
`// TODO: skill doesn't exist yet, values are guessed` とある。
Skill Copy 持ち個体の評価は信用しないこと。

---

## 10. 実装タスク（優先順）

### タスク1: 日本語↔英語 名前対応表 ★最優先・最大のボトルネック

**なぜ必要か**: `common/src/locales/` には `en/pokemonNames.ts` のみ存在し、
**日本語ロケールが無い**。スクショは日本語なので変換表なしには何も動かない。

**規模**: 種254 + サブスキル17 + メインスキル17ファミリ + 性格25 + 食材 + きのみ ≒ 350エントリ

**実装方針**:
- 種名は `pokedexNumber` をキーにすれば全国図鑑番号から機械的に生成できる
- イベント個体・地方フォルムのみ手当て
- サブスキル・メインスキル・性格・食材は手作業（数が少ないので許容）
- 成果物: `data/names_ja.yaml`

```yaml
species:
  BULBASAUR: フシギダネ
  IVYSAUR:   フシギソウ
  VENUSAUR:  フシギバナ
subskills:
  "Energy Recovery Bonus": げんき回復ボーナス
  "Berry Finding S":       きのみの数S
  "Ingredient Finder M":   食材確率アップM
  "Helping Speed S":       おてつだいスピードS
  "Research EXP Bonus":    リサーチEXPボーナス
mainskills:
  "Ingredient Magnet S":   食材ゲットS
natures:
  Mild: おとなしい
```

**受け入れ条件**: 全254種が双方向に解決でき、逆引き衝突がない。
実測スクショの全フィールドが解決できる。

---

### タスク2: engine（TSブリッジ）

**実装**:
- `engine/vendor/nerolis-lab` を submodule 追加（固定タグ）
- `common` をビルド（`cd common && npm run build`）
- `engine/src/cli.ts`: stdin から §7.1 の JSON を読み、
  `backend/src/services/api-service/production/production-service.ts` の
  `calculateIv` / `calculatePokemonProduction` を呼び、§7.2 で返す
- **DB接続は不要**。DAO を import しないよう注意
- **`verify` モードを同時に実装する**（§5.6 L2 の中核。タスク5より先に必要）:

```
入力: { "mode": "verify",
        "tolerance": 0,            // 既定0（厳密一致）
        "strictBelowLevel": 56,    // このレベル未満は tolerance を無視して厳密一致
        "skipAboveLevel": null,    // 指定すると超過個体は検算スキップ
        "instances": [{ "uid": "...", "instance": {...}, "displayedSp": 513 }] }

出力: { "results": [{ "uid": "...", "computedSp": 513, "diff": 0,
                      "match": true, "mode": "strict" }] }   // strict | tolerant | skipped
```

実装は `common/src/utils/rp-utils/rp.ts` の `new RP(instance).calc()` を呼ぶだけ。
`PokemonInstanceWithoutRP = Omit<PokemonInstanceExt, 'rp' | 'carrySize'>` なので
carrySize は渡さない。バッチで受け付けること（L2b の候補探索で数十回呼ぶため、
プロセス起動を毎回やると遅い）。

`mode` を必ず返し、`individual` テーブルの検証状態に反映する。
`tolerant` / `skipped` の個体は `verified=1` にせず監査対象として残す（§5.6 L2 制約2）。

**受け入れ条件**: 実測サンプル（フシギダネ→フシギバナ Lv60）が計算でき、
`nerolis-lab` のフロントエンド（`nerolislab.com/calculator`）と数値が一致する。
**この一致確認は必須**（型の解釈ミスを検出する唯一の手段）。

---

### タスク3: Lv60/80 アンカー評価の統括

**実装**:
- 個体を最終進化・指定レベルに正規化する変換（`evolvesInto` を辿る）
- 有効サブスキル集合をアンカー別に決定（パースした unlock 値を使う）
- スキルLv = 基礎 + `Skill Level Up S/M` + 進化回数
- ロール別スコア算出、`(S60, S80, ΔS)` を `evaluation` に保存

**受け入れ条件**: 実測サンプルで S60/S80 が出て、
`リサーチEXPボーナス` が価値0として扱われている。

---

### タスク4: 送る判定（支配関係）★本命機能

既存ツールに完全に存在しない部分。

```python
def decide(individuals, config):
    for species, group in group_by_species(individuals):
        for i in group:
            if i.uid in protected_whitelist:
                yield i, 'protected', reason
                continue
            # 全ロール × 全アンカーで自分以上の個体が k 匹以上あるか
            dominated = all(
                count_better(group, i, role, anchor) >= config.keep_top_n
                for role in ROLES for anchor in (60, 80)
            )
            yield i, ('send' if dominated else 'keep'), reason
```

**重要**:
- **ロールごとに支配判定する**。食材型でも `きのみの数S` 持ちは別軸で価値があり得る
- **S60 と S80 の両方でトップN外を要求する**。片方だけで判定すると後半型を誤って送る
- `reason` を必ず生成（「同種上位に4匹、Lv10サブが最下位帯」等）。
  理由なしに送る判断はユーザーが受け入れられない

**受け入れ条件**: 手持ちに対して `keep/send/protected` が全件付き、
各 `send` に人間可読の理由がある。

---

### タスク5: 取り込み（動画 + フォールバック静止画）

詳細は **§5.5（パイプライン）** と **§5.6（正確性の担保）** を参照。
nerolis-lab には OCR 実装が一切ない（リポジトリ全体を grep して確認済み）。

**段階的に実装する**:

**第1段（すぐ動く）**: フレーム1枚を VLM に投げて JSON 抽出。
Claude API、または Apple Silicon なら MLX 経由で Qwen2.5-VL 等をローカル実行。
200個体で数分。**ここまでで実用になる。**

**第2段（速度・コスト最適化）**:
- 解像度正規化 → 緑セクションヘッダをアンカーにテンプレートマッチ → 相対座標クロップ
- 性格・サブスキル・食材・メインスキル・種名は**閉じた語彙**なので
  テンプレート埋め込みの最近傍分類（実質100%、距離が信頼度になる）
- レベル・SP・ニックネームのみ文字認識
- **第2段のテンプレートは第1段の VLM 出力から自動生成できる**。第1段は捨て実装ではない

**サブタスク**:

| # | 内容 | 参照 |
|---|---|---|
| 5-1 | `inbox/` 監視（`watchdog`）と `.mov`/`.png`/`.heic` 受付 | §5.5 |
| 5-2 | ffmpeg フレーム抽出 | §5.5 |
| 5-3 | アンカー検出（緑セクションヘッダのテンプレートマッチ） | §2.4 |
| 5-4 | **テキスト領域限定の phash 重複除去**（全画面phashは機能しない） | §5.5 |
| 5-5 | 上下フレームのペアリング | §5.5 / §5.6 L4 |
| 5-6 | フィールド分類（VLM → テンプレート最近傍） | §5.6 L1 |
| 5-7 | 時間方向の多数決 | §5.6 L3 |
| 5-8 | engine `verify` モードによるSP検算 | §5.6 L2 |
| 5-9 | 候補探索による自動訂正 | §5.6 L2b |
| 5-10 | 総数一致ゲート | §5.6 L5 |
| 5-11 | 監査レポート生成 | §5.6 L6 |

**受け入れ条件**:
- 実測サンプル（Lv15 フシギダネ）から §8 の `individual` 行が生成され、
  **SP再計算が `513` と一致する**
- 200匹の実データで §5.6 L6 の受け入れ基準
  （不変コア精度 ≥99.5%、SP一致率 ≥98%、`verified=0` 残存 0、総数一致）を満たす
- 監査レポート `audit_report.md` が出力される

---

### タスク6: レビューUI

ローカル単一HTML。`クロップ画像 + 候補上位3件` を並べてキー1/2/3で確定。
100個体で低信頼が10件なら30秒で終わる。

**ここを作らずに CSV を Excel で直す運用は長期的に負ける。**

---

### タスク7: GitHub Pages 出力

3ページ構成:

| ページ | 内容 | 更新頻度 |
|---|---|---|
| 育成キュー | `S60` 降順、`ΔS` で後半型マーク、目標レベルと理由 | 週次 |
| 送るリスト | **ボックス並び順に整列**、支配理由付き、`protected` は別枠 | 整理時 |
| 捕獲基準表 | 種別の最低確保条件。フィールド別に絞り込み | フィールド変更時 |

**送るリストの形式**（実作業の高速化はここが本体）:

```
【送る候補 18匹】ボックスを「レベル昇順」に並べてから上から順に
   3番目  ラルトス  Lv7   同種上位に4匹 / slot1サブが最下位帯
   7番目  ゼニガメ  Lv5   同種完全下位互換（uid-def456 に全ロールで劣位）
  12番目  コダック  Lv9   ...
```

捕獲基準表は**寝る前にスマホで見る**ものなので、
フィールド選択 → 種リスト → 条件が2タップで出る作りにする。

**リポジトリ構成の注意**: public リポジトリだと手持ちが全公開になる。
気になる場合は private リポジトリでデータを持ち、
ビルド済みHTMLのみ Pages 用リポジトリに push する2リポジトリ構成にする。

---

### タスク8: 同種内パーセンタイル

サブスキル×性格空間を Monte Carlo でサンプリングし、
手持ち個体が上位何%かを出す。既存の「厳選評価ツール」相当を全個体に自動適用。

計算量に注意（種数 × サンプル数 × エンジン呼び出し）。
単体スコアで近似し、上位のみ `calculateIv` で精緻化する。

---

### タスク9: 捕獲基準表

出力は「おすすめポケモン一覧」ではなく、**現在の手持ちを前提とした閾値表**。

```
種 s について:
  個体分布（性格25 × サブスキル組合せ × 食材構成）をサンプリング
  → 各サンプルの ΔTeam を計算
  → P(ΔTeam > 0) と E[ΔTeam | 改善]
  → 「確保すべき最低条件」を逆算
```

出力例:

```
フシギダネ  条件付き  食材3枠揃い型 かつ 性格が食材/スピード系のみ
                     （現手持ちに上位2匹。それ未満は即送り）
ピッピ      無条件    スキル型で手持ちに穴、代替不在
ウソッキー  条件付き  サブスキルに 食材確率アップM が来た場合のみ
```

**掛け合わせる要素**:
- フィールド別の出現期待数（基準が厳しくても出現数が多ければ狙う価値がある）
- 今週のレシピからの不足食材逆算（`OPTIMAL_POKEDEX` / `INFERIOR_POKEDEX` をベースに）
- ビスケット予算内で `E[ΔTeam] / ビスケット消費` 降順に切る

---

### 着手順まとめ

```
タスク1（名前対応表）
  → タスク2（engine）           ← ここで nerolislab.com と数値一致を確認
  → タスク3（アンカー評価）
  → タスク4（送る判定）          ← ここで実用価値の大半が出る
  → タスク5（取り込み・第1段）
  → タスク7（Pages 出力）
  → タスク6（レビューUI）
  → タスク5（第2段）/ タスク8 / タスク9
```

M1〜M4（タスク1〜4）で**価値の9割が出る**。

---

## 11. 制約・注意事項

### 11.1 規約ライン

- **公式なデータエクスポート/APIは存在しない**。個体データはサーバ管理で、
  クライアントには表示分しか来ない。既存ツールが例外なくスクショOCRか手入力を
  入口にしているのはこれが上限だから
- **通信傍受・改造クライアント・メモリ読み取り・APKアセット抽出は規約違反側**。
  アカウント停止リスク。実装しない
- 同 org の `pokemon-sleep-ripper` / `pokemon-sleep-apk-dumper`（RaenonX）は
  この領域なので**使わない**
- **ゲーム内操作の自動化（タップ注入・adb input）も行わない**。
  代わりに人間の操作回数を最小化する（＝ボックス並び順に整列した作業リスト）
- **画面キャプチャして自分に表示されている情報を読むのは別物**。
  アプリを改変せず、サーバにも触らない。RaenonX が OCR インポータを提供している通り、
  コミュニティの事実上の合意ライン。ここに留める

### 11.2 ライセンス

- nerolis-lab は Apache-2.0。`NOTICE`（`Neroli's Lab / Copyright The Neroli's Lab Authors`）を保持
- 他ツールのデータは有志のもので再配布制限があるものが多い
  （例: びっくる氏の期待値チェッカーは再配布・販売禁止と明記）。
  **スクレイピングせず Apache-2.0 の nerolis-lab を使う**
- RaenonX の UI リポジトリは非公開化済み。データは参照のみ

### 11.3 マスクデータの限界

プログラムを書いても原理的に取得できないもの:

- 食材確率・スキル発生確率の**真の内部値**（推定値に依存するしかない）
- 個体ID（代理キー＋ボックス並び順で近似）
- 出現テーブル・乱数

---

## 12. 調査済み事項（再調査不要）

### 12.1 公式なデータ取得手段は存在しない
個体データはサーバ管理で、クライアントには表示分しか来ない。
既存ツールが例外なくスクショOCRか手入力を入口にしているのはこれが上限だから。

### 12.2 スキル評価スキーマは nerolis-lab に存在する
11ユニットの `mainskillUnits` と `MainskillActivation` 型（§4.3）。
自分で分類体系を設計する必要はない。

### 12.3 Metronome / Skill Copy の再帰評価は実装済み
`MetronomeEffect` がシミュレータ内で再帰的に解決する（§4.4）。
不動点反復を自作する必要はない。

### 12.4 動画ベースの取り込みシステムは存在しない
3方向から確認済み:

1. **nerolis-lab のコード全体** — `grep -ril "ocr\|screenshot"` のヒットは
   `dish-infographic-page.vue` のみ（料理画像生成用）。取り込み機能ゼロ
2. **既存ポケスリツール** — RaenonX の Pokébox OCR importer が唯一の画像取り込みだが
   **スクリーンショット単位**。動画入力の記述は存在しない
3. **隣接ドメイン** — ポケモンGO側には複数スクショの一括インポートを持つアプリが
   存在する（Poke Rater 等）が、**静止画の束**であり動画→フレーム抽出ではない

**存在しない理由**（設計判断として意味がある）:
- 既存ツールの想定は「今捕まえた1匹を評価する」。「200匹を棚卸しする」需要が表に出ていない
- **PC版が存在しない**ため、他ゲームのインベントリスキャナのような
  「ツール側が自動スクロールしてライブキャプチャ」方式が使えない。
  結果、一括取り込みの唯一の経路が「人間がスワイプした録画」になる
- つまり iOS 専用であることが、動画方式を唯一の選択肢にし、
  同時に誰も実装していない理由にもなっている

**新規性はあるが難易度は高くない。** 部品（ffmpeg / imagehash / OpenCV matchTemplate / VLM）
はすべて既製品で、実装規模は200〜300行程度。

### 12.5 ΔTeam は既存API
`calculateIv` がDB非依存で使える（§4.4）。自作不要。

### 12.6 SP はチェックサムとして使える（決定的に計算可能）
`common/src/utils/rp-utils/rp.ts` の `RP.calc()` が整数SPを再現する（§5.6 L2）。

- **隠れパラメータなし**。乱数もサーバ値も入らない
- `floorWithIEEE754Correction` でゲーム内部の**丸め順序まで再現**されている
- したがって整数1個で7〜11フィールドの結合検証ができる。これが正確性担保の中核
- **例外**: Lv56 以上は多項式フィットによる推定（コードにコメントで明記）。
  許容誤差運用が必要（§5.6 L2 制約2）
- ribbon が式に入るため、画面から読めない場合は探索変数になる（§9-13）

---

## 13. 参考リンク

| ツール | URL | 用途 |
|---|---|---|
| Neroli's Lab | https://nerolislab.com | 計算エンジン本体・数値一致確認 |
| nerolis-lab (GitHub) | https://github.com/nerolis-lab/nerolis-lab | Apache-2.0 ソース |
| nerolis-lab docs | https://docs.nerolislab.com | セットアップ手順 |
| RaenonX | https://pks.raenonx.cc/ja | 総合。Pokébox OCR importer あり |
| 攻略・検証Wiki | https://wikiwiki.jp/poke_sleep/ | 日本語の一次照合先 |
| にとよん 個体値計算機 | https://nitoyon.github.io/pokesleep-tool/iv/index.ja.html | 単体評価の検算 |
| ポケスリ厳選管理 | https://zakopuro.github.io/pokemon-sleep-tools/ | 既存の厳選記録ツール |
| ポケスリシミュ | https://reimer0204.github.io/pokesle-simulator/#/ | 手持ちからのチーム編成 |
| ポケスリ便利リンク集 | https://note.com/shimosaki775/n/nb7339a782177 | ツール全体の俯瞰 |

---

## 付録A: 設定ファイル雛形

```yaml
# config/settings.yaml
island: greengrass          # greengrass/cyan/taupe/snowdrop/lapis/powerplant/amber/GGEX/CBEX
areaBonus: 0                # ※ 単位を §9-6 で確認
camp: false
bedtime: "22:00"
wakeup: "06:00"
potSize: 15
includeCooking: true

objective: energy           # energy / shards / sleepdex （目的関数の主軸）
boxCapacity: 100
boxUsed: 0
```

```yaml
# config/protected.yaml
by_uid: []
by_species:
  - species: PIKACHU
    reason: イベント限定（ホリデー）
rules:
  - keep_if_sleepdex_incomplete: true
  - keep_if_shiny: true
  - keep_if_event_exclusive: true
```

## 付録B: ユーザーから未取得の情報

実装開始前に確認すること。

**確定済み**:
- 端末: **iPhone**、処理: **Mac**（Apple Silicon 想定）
- 転送: AirDrop → `~/pokesleep-box/inbox/`（監視ディレクトリ方式）
- 取り込み: **画面収録ベース**、静止画フォールバック併用
- 規模: **約200匹**

**未取得**:
1. 現在のフィールドとフィールドボーナス、なべ容量 → `settings.yaml` 初期値
2. 目的関数の主軸（エナジー最大化 / ゆめのかけら重視 / 寝顔図鑑埋め重視）
3. ボックス残枠
4. **詳細画面が左右スワイプで隣の個体に送れるか**（§9-12。撮影プロトコルに影響）
5. ボックス一覧画面のスクショ（総数表示の有無 = §5.6 L5 の前提）
6. 詳細画面の下端側スクショ（`詳細ステータス` セクションの全内容が未確認）
7. `ribbon` が画面から読めるか（§9-13。SP検算の探索自由度に影響）

**Mac 環境の準備コマンド**:
```bash
brew install ffmpeg node
pip install imagehash opencv-python pillow-heif watchdog
# Apple Silicon でローカルVLMを使う場合
pip install mlx-vlm
```