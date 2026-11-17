"""Column: is raising a helper to Lv60 (or Lv70) worth the dream shards?

Uses the engine's per-individual production (``evaluate`` mode,
``energyScores``) for fixed example builds at every level from 50 to 70,
and the cost tables for the shard price of each step.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .. import costs
from ..analytics import ISLANDS
from ..localization import SUBSKILL_UNLOCKS, names
from . import Article, Column, Section
from .page import figure, table
from .svg import bar_chart, fmt_int, fmt_man, legend, line_chart

# Two common final evolutions per specialty, all with a strength-type main
# skill so the engine's skill strength is comparable across them.
SPECIES = ["RAICHU", "HOUNDOOM", "GENGAR", "QUAQUAVAL", "AMPHAROS", "ESPEON"]
# Simple role builds (same subskill lists as the engine's ideal individual)
# with EXP-neutral natures so every example has the same shard cost.
BUILDS = {
    "berry": ("Adamant", ["Berry Finding S", "Helping Speed M", "Helping Speed S",
                          "Helping Bonus", "Skill Trigger M"]),
    "ingredient": ("Rash", ["Ingredient Finder M", "Helping Speed M", "Ingredient Finder S",
                            "Inventory Up L", "Helping Speed S"]),
    "skill": ("Careful", ["Skill Trigger M", "Helping Speed M", "Skill Trigger S",
                          "Helping Speed S", "Helping Bonus"]),
}
LEVELS = list(range(50, 71))
STEPS = [(50, 60), (60, 70)]
ITERATIONS = 1000
SPECIALTY_JA = {"berry": "きのみタイプ", "ingredient": "食材タイプ", "skill": "スキルタイプ"}


def _favored_island(berry: str) -> str:
    return next(name for name, berries in ISLANDS.items() if berry in berries)


def generate(ctx) -> Dict[str, Any]:
    meta = ctx.engine({"mode": "metadata"})["pokemon"]
    instances = []
    for species in SPECIES:
        info = meta[species]
        nature, subskills = BUILDS[info["specialty"]]
        instances.append({"uid": species, "instance": {
            "species": species, "level": LEVELS[0], "nature": nature,
            "subskills": [[name, unlock] for name, unlock in zip(subskills, SUBSKILL_UNLOCKS)],
            "ingredients": [slot["choices"][0] for slot in info["ingredients"]],
            "skillLevel": 99, "ribbon": 0}})   # clamped to the skill's max level by the engine
    response = ctx.engine({"mode": "evaluate", "anchors": LEVELS, "iterations": ITERATIONS,
                           "islands": {k: list(v) for k, v in ISLANDS.items()},
                           "instances": instances})
    ja = names()
    rows = []
    for item, result in zip(instances, response["results"]):
        raw = item["instance"]
        info = meta[raw["species"]]
        island = _favored_island(info["berry"])
        energy = result["energyScores"][island]
        rows.append({
            "species": raw["species"],
            "species_ja": ja["species"].get(raw["species"], raw["species"]),
            "specialty": info["specialty"],
            "main_skill": ja["mainskills"].get(info["main_skill"], info["main_skill"]),
            "skill_level": result["mainSkillMaxLevel"],
            "berry": ja["berries"].get(info["berry"], info["berry"]),
            "island": island,
            "nature": ja["natures"].get(raw["nature"], raw["nature"]),
            "subskills": [[ja["subskills"].get(n, n), u] for n, u in raw["subskills"]],
            "ingredients": [[ja["ingredients"].get(n, n), a] for n, a in raw["ingredients"]],
            "energy": {str(level): {k: energy[str(level)][k]
                                    for k in ("berry", "ingredient", "direct_skill", "expected")}
                       for level in LEVELS},
            "costs": [{"from": a, "to": b,
                       **{k: v for k, v in costs.training_cost(raw["species"], raw["nature"], a, b).items()
                          if k in ("candies", "dream_shards")}} for a, b in STEPS],
        })
    return {
        "data": {"levels": LEVELS, "iterations": ITERATIONS, "subskill_unlocks": list(SUBSKILL_UNLOCKS),
                 "level_cap": costs.max_level(), "species": rows},
        "assumptions": [
            "1日のエナジーは計算エンジン（Neroli's Lab）の1匹ぶんの見積もりです。きのみ＋食材（素材の基本エナジー）＋メインスキルのエナジーの合計です。",
            "きのみが好物になる島（好物2倍）、睡眠は22:00〜6:00、リボンなし、おてつだいサポートなしで計算しています。",
            "食材は料理にしたときの倍率を入れていません。料理の倍率まで含めると、食材タイプの数字は変わります。",
            "メインスキルは最大レベルです。メインスキルのレベルはLv50→70で変わらないものとしています。",
            f"サブスキルはLv{'・'.join(str(u) for u in SUBSKILL_UNLOCKS)}で開きます（Ver.3.6.0、レベル上限{costs.max_level()}）。Lv70で4つめが開きます。",
            "コストはアメだけで育てたときの量です（EXP補正なしの性格、通常EXPタイプ）。",
            f"シミュレーションの回数は{ITERATIONS}回です。",
        ],
        "sources": [
            "Neroli's Lab（計算エンジン、engine/src/cli.mjs の evaluate / metadata モード）",
            "src/pokesleep_box/training_costs.json（アメ・かけらの表）",
        ],
    }


def _gain(row: Dict[str, Any], a: int, b: int) -> int:
    return row["energy"][str(b)]["expected"] - row["energy"][str(a)]["expected"]


def _per_10k(row: Dict[str, Any], a: int, b: int) -> float:
    shards = next(c["dream_shards"] for c in row["costs"] if c["from"] == a and c["to"] == b)
    return _gain(row, a, b) / (shards / 10000)


def build(doc: Dict[str, Any]) -> Article:
    data = doc["data"]
    rows: List[Dict[str, Any]] = data["species"]
    levels = data["levels"]
    cost = {(c["from"], c["to"]): c for c in rows[0]["costs"]}
    eff_early = [_per_10k(r, 50, 60) for r in rows]
    eff_late = [_per_10k(r, 60, 70) for r in rows]
    ratio = [e / l for e, l in zip(eff_early, eff_late) if l]
    pct60 = [_gain(r, 50, 60) / r["energy"]["50"]["expected"] * 100 for r in rows]
    pct70 = [_gain(r, 60, 70) / r["energy"]["60"]["expected"] * 100 for r in rows]
    ing_rows = [r for r in rows if r["specialty"] == "ingredient"]
    third = [r["energy"]["60"]["ingredient"] / r["energy"]["59"]["ingredient"] - 1 for r in ing_rows]

    conclusion = [
        f"Lv50→60はかけら{fmt_man(cost[(50, 60)]['dream_shards'])}で、1日のエナジーが"
        f"{min(pct60):.0f}〜{max(pct60):.0f}%増えます。",
        f"Lv60→70はかけら{fmt_man(cost[(60, 70)]['dream_shards'])}（約{cost[(60, 70)]['dream_shards'] / cost[(50, 60)]['dream_shards']:.1f}倍）"
        f"かかります。増え方は{min(pct70):.0f}〜{max(pct70):.0f}%で、Lv50→60より大きい場合もありますが、"
        "その一部はLv70で開く4つめのサブスキルの分です。",
        f"かけら1万あたりの伸びで比べると、Lv50→60はLv60→70の{min(ratio):.1f}〜{max(ratio):.1f}倍お得です。"
        "かけらが限られているなら、1匹をLv70にするより、何匹かをLv60にそろえるほうが効きます。",
    ]
    if ing_rows:
        conclusion.append(f"食材タイプはLv60で3つめの食材が開くので、Lv59→60の1レベルで食材のエナジーが"
                          f"{min(third) * 100:.0f}〜{max(third) * 100:.0f}%増えます。食材タイプは特にLv60が区切りです。")
    step70 = [_gain(r, 69, 70) / r["energy"]["69"]["expected"] * 100 for r in rows]
    conclusion.append(f"Lv70では4つめのサブスキルが開くので、Lv69→70の1レベルだけで1日のエナジーが"
                      f"{min(step70):.0f}〜{max(step70):.0f}%増えます。Lv60→70を始めたら、"
                      "途中で止めずにLv70まで上げきると効果が出ます。"
                      "Lv70を目指すのは、エースの1匹にかけらを集中できるときです。")

    line_series = [{"name": f"{r['species_ja']}（{SPECIALTY_JA[r['specialty']]}）", "slot": i + 1,
                    "points": [(lv, r["energy"][str(lv)]["expected"] / r["energy"]["50"]["expected"] * 100)
                               for lv in levels]} for i, r in enumerate(rows)]
    growth = line_chart(
        line_series, title="Lv50を100としたときの1日のエナジー",
        desc="6匹の1日のエナジーを、Lv50のときを100として、Lv50から70まで線で表した図。",
        x_label="レベル", y_label="Lv50=100", y_min=100, x_ticks=range(50, 71, 5),
        y_format=lambda v: f"{v:.0f}", markers=[(60, "Lv60"), (70, "Lv70")])

    eff_series = [{"name": "Lv50→60", "slot": 1, "values": [round(v) for v in eff_early]},
                  {"name": "Lv60→70", "slot": 2, "values": [round(v) for v in eff_late]}]
    efficiency = bar_chart(
        [r["species_ja"] for r in rows], eff_series,
        title="かけら1万あたりの1日のエナジーの伸び",
        desc="6匹それぞれで、Lv50→60とLv60→70の、かけら1万あたりに増える1日のエナジーを比べた棒グラフ。",
        y_label="エナジー/日（1万かけらあたり）", y_format=fmt_int)

    summary_rows = []
    for r, e, l in zip(rows, eff_early, eff_late):
        en = r["energy"]
        summary_rows.append([r["species_ja"], SPECIALTY_JA[r["specialty"]],
                             fmt_int(en["50"]["expected"]), fmt_int(en["60"]["expected"]),
                             fmt_int(en["70"]["expected"]), fmt_int(_gain(r, 50, 60)),
                             fmt_int(_gain(r, 60, 70)), fmt_int(e), fmt_int(l)])
    summary = table(
        ["ポケモン", "タイプ", "Lv50", "Lv60", "Lv70", "増分 50→60", "増分 60→70",
         "1万かけらあたり 50→60", "1万かけらあたり 60→70"],
        summary_rows, numeric=[2, 3, 4, 5, 6, 7, 8], caption="1日のエナジー")
    cost_table = table(["区間", "アメ", "かけら"],
                       [[f"Lv{a}→{b}", fmt_int(cost[(a, b)]["candies"]),
                         fmt_int(cost[(a, b)]["dream_shards"])] for a, b in STEPS],
                       numeric=[1, 2], caption="6匹とも同じコスト（通常EXPタイプ・EXP補正なしの性格）")

    breakdown_rows = []
    for r in rows:
        for lv in (50, 59, 60, 69, 70):
            en = r["energy"][str(lv)]
            breakdown_rows.append([r["species_ja"], f"Lv{lv}", fmt_int(en["berry"]),
                                   fmt_int(en["ingredient"]), fmt_int(en["direct_skill"]),
                                   fmt_int(en["expected"])])
    breakdown = table(["ポケモン", "レベル", "きのみ", "食材", "スキル", "合計"], breakdown_rows,
                      numeric=[2, 3, 4, 5],
                      highlight=[i for i, row in enumerate(breakdown_rows) if row[1] in ("Lv60", "Lv70")])

    build_rows = [[r["species_ja"], SPECIALTY_JA[r["specialty"]], r["main_skill"],
                   f"{r['berry']}（{r['island']}）", r["nature"],
                   "、".join(f"{n}(Lv{u})" for n, u in r["subskills"]),
                   "・".join(f"{n}×{a}" for n, a in r["ingredients"])] for r in rows]
    builds = table(["ポケモン", "タイプ", "メインスキル", "きのみ（好物の島）", "性格", "サブスキル（開くレベル）",
                    "食材（Lv1/30/60）"], build_rows)

    return Article(
        takeaway=(f"かけら1万あたりの伸びは、Lv50→60がLv60→70の{min(ratio):.1f}〜{max(ratio):.1f}倍。"
                  "Lv70はエース向け。"),
        conclusion=conclusion,
        sections=[
            Section("レベルごとの伸び", figure(growth, "", legend(line_series))),
            Section("かけら1万あたりの伸び", figure(efficiency, "", legend(eff_series)) + summary + cost_table),
            Section("きのみ・食材・スキルの内訳", breakdown),
            Section("計算に使った個体", builds),
        ],
        how_to_read=[
            "1つめの図は、Lv50のときの1日のエナジーを100としたときの伸びです。線の傾きが急なほど、レベルを上げた効果が大きいことを表します。",
            "食材タイプの線はLv60で段差があります。3つめの食材が開くためです。どのタイプもLv70で段差があるのは、4つめのサブスキルが開くためです。",
            "2つめの図は「1日に増えるエナジー÷使うかけら（1万単位）」です。棒が高いほど、かけらの使い道としてお得です。",
            "きのみのエナジーはレベルが上がるほど1個あたりの値が上がるので、きのみタイプは毎レベルじわじわ伸びます。",
        ],
        limits=[
            "1匹だけの見積もりです。チームの中での役割（げんき回復やおてつだいサポート）は入っていません。",
            "サブスキルの並びを変えると、Lv70の段差の大きさは変わります（ここでは4つめが役割に合ったサブスキルです）。",
        ],
    )


COLUMN = Column(slug="lv60-worth", order=20, title="Lv60まで上げる価値はある？",
                generate=generate, build=build, uses_engine=True)
