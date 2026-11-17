"""Column: what are the Lv70 and Lv80 subskill slots worth (game Ver.3.6.0)?

Ver.3.6.0 moved the 4th subskill slot from Lv75 to Lv70 and the 5th from
Lv100 to Lv80, while the level cap is 70. For each example build, the same
"good" subskill is put in the 3rd (Lv50), 4th (Lv70) or 5th (Lv80) slot,
with energy-free subskills in the other late slots, and the engine's daily
energy is followed from Lv50 to Lv80. Shard costs come from the cost tables.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .. import costs
from ..analytics import ISLANDS
from ..localization import SUBSKILL_UNLOCKS, names
from . import Article, Column, Section
from .page import figure, table
from .subskill_lab import SPECIALTY_JA, favored_island
from .svg import bar_chart, fmt_int, fmt_man, legend, line_chart

SPECIES = ["RAICHU", "HOUNDOOM", "GENGAR", "QUAQUAVAL", "AMPHAROS", "ESPEON"]
# EXP-neutral role natures and the first two role subskills (as in lv60-worth).
BUILDS = {
    "berry": ("Adamant", ["Berry Finding S", "Helping Speed M"]),
    "ingredient": ("Rash", ["Ingredient Finder M", "Helping Speed M"]),
    "skill": ("Careful", ["Skill Trigger M", "Helping Speed M"]),
}
# Energy-free subskills that fill the late slots in every variant.
FILLERS = ["Dream Shard Bonus", "Research EXP Bonus", "Sleep EXP Bonus"]
# Candidates for the "good" late subskill; the best one at Lv60 is used.
CANDIDATES = ["Helping Speed S", "Berry Finding S", "Ingredient Finder M", "Ingredient Finder S",
              "Skill Trigger M", "Skill Trigger S", "Inventory Up L", "Helping Bonus",
              "Energy Recovery Bonus"]
LEVELS = list(range(50, 81))
ITERATIONS = 1000
SLOTS = {3: SUBSKILL_UNLOCKS[2], 4: SUBSKILL_UNLOCKS[3], 5: SUBSKILL_UNLOCKS[4]}
OLD_UNLOCKS = {4: 75, 5: 100}
STEPS = [(50, 60), (60, 70)]


def _instance(info, species, nature, subskills, level=LEVELS[0]):
    return {"species": species, "level": level, "nature": nature,
            "subskills": [[name, unlock] for name, unlock in zip(subskills, SUBSKILL_UNLOCKS)],
            "ingredients": [slot["choices"][0] for slot in info["ingredients"]],
            "skillLevel": 99, "ribbon": 0}


def generate(ctx) -> Dict[str, Any]:
    meta = ctx.engine({"mode": "metadata"})["pokemon"]
    ja = names()
    rows = []
    for species in SPECIES:
        info = meta[species]
        nature, first = BUILDS[info["specialty"]]
        island = favored_island(info["berry"])
        islands = {island: list(ISLANDS[island])}
        # Pick the good subskill: the candidate that adds most at Lv60 in the 3rd slot.
        pool = [c for c in CANDIDATES if c not in first]
        screen = ctx.engine({"mode": "evaluate", "anchors": [60], "iterations": ITERATIONS, "islands": islands,
                             "instances": [{"uid": c, "instance": _instance(info, species, nature,
                                                                            first + [c] + FILLERS[1:])}
                                           for c in pool] +
                                          [{"uid": "base", "instance": _instance(info, species, nature,
                                                                                 first + FILLERS)}]})
        screened = {r["uid"]: r["energyScores"][island]["60"]["expected"] for r in screen["results"]}
        good = max(pool, key=lambda c: screened[c])
        variants = {"base": first + FILLERS,
                    "slot3": first + [good, FILLERS[1], FILLERS[2]],
                    "slot4": first + [FILLERS[0], good, FILLERS[2]],
                    "slot5": first + [FILLERS[0], FILLERS[1], good]}
        response = ctx.engine({"mode": "evaluate", "anchors": LEVELS, "iterations": ITERATIONS,
                               "islands": islands,
                               "instances": [{"uid": k, "instance": _instance(info, species, nature, v)}
                                             for k, v in variants.items()]})
        energy = {r["uid"]: {str(lv): r["energyScores"][island][str(lv)]["expected"] for lv in LEVELS}
                  for r in response["results"]}
        rows.append({
            "species": species, "species_ja": ja["species"].get(species, species),
            "specialty": info["specialty"],
            "main_skill": ja["mainskills"].get(info["main_skill"], info["main_skill"]),
            "skill_level": response["results"][0]["mainSkillMaxLevel"],
            "berry": ja["berries"].get(info["berry"], info["berry"]), "island": island,
            "nature": ja["natures"].get(nature, nature),
            "first": [ja["subskills"].get(s, s) for s in first],
            "good": ja["subskills"].get(good, good),
            "screened_gain_60": {ja["subskills"].get(c, c): screened[c] - screened["base"] for c in pool},
            "energy": energy,
            "costs": [{"from": a, "to": b,
                       **{k: v for k, v in costs.training_cost(species, nature, a, b).items()
                          if k in ("candies", "dream_shards")}} for a, b in STEPS],
        })
    cap = costs.max_level()
    return {
        "data": {"levels": LEVELS, "slots": {str(k): v for k, v in SLOTS.items()},
                 "old_unlocks": {str(k): v for k, v in OLD_UNLOCKS.items()}, "level_cap": cap,
                 "cost_beyond_cap": costs.training_cost(SPECIES[0], "Adamant", cap, cap + 1) is not None,
                 "fillers": [ja["subskills"].get(s, s) for s in FILLERS],
                 "iterations": ITERATIONS, "species": rows},
        "assumptions": [
            "1日のエナジーは計算エンジン（Neroli's Lab）の1匹ぶんの見積もりです。きのみ＋食材（素材の基本エナジー）＋メインスキルのエナジーの合計です。",
            f"サブスキルはLv{'・'.join(str(u) for u in SUBSKILL_UNLOCKS)}で開きます（Ver.3.6.0）。それまでは4つめがLv75、5つめがLv100でした。",
            f"レベル上限は{cap}です。Lv80の5つめの枠は、いまは開けられません。Lv71〜80の数字は、計算エンジンでレベルだけを上げた参考値です。",
            "1・2つめの枠は役割に合ったサブスキル（表を参照）、メインスキルは最大レベル、性格はEXP補正のない役割向きのものです。",
            "「よいサブスキル」は、候補の中で、3つめの枠に入れたときにLv60のエナジーが一番増えるものです。エナジーに効かない枠には、ゆめのかけらボーナスなどを入れています。",
            "きのみが好物になる島（好物2倍）、睡眠は22:00〜6:00、リボンなし、他のポケモンの助けなしで計算しています。食材は料理の倍率を入れていません。",
            "かけらはアメだけで育てたときの量です（通常EXPタイプ・EXP補正なしの性格）。",
            f"シミュレーションの回数は{ITERATIONS}回です。",
        ],
        "sources": [
            "Neroli's Lab（計算エンジン、engine/src/cli.mjs の evaluate / metadata モード）",
            "src/pokesleep_box/training_costs.json（アメ・かけらの表）",
        ],
    }


def _e(row, variant, level):
    return row["energy"][variant][str(level)]


def _slot_gain(row, slot, level):
    return _e(row, f"slot{slot}", level) - _e(row, "base", level)


def build(doc: Dict[str, Any]) -> Article:
    data = doc["data"]
    rows: List[Dict[str, Any]] = data["species"]
    slots = {int(k): v for k, v in data["slots"].items()}
    cap = data["level_cap"]
    cost = {(c["from"], c["to"]): c["dream_shards"] for c in rows[0]["costs"]}

    g3 = [_slot_gain(r, 3, 60) / _e(r, "base", 60) * 100 for r in rows]
    g4 = [_slot_gain(r, 4, 70) / _e(r, "base", 70) * 100 for r in rows]
    g5 = [_slot_gain(r, 5, 80) / _e(r, "base", 80) * 100 for r in rows]
    # Share of the Lv60->70 gain that comes from the 4th slot when it holds the good subskill.
    lift = [(_e(r, "slot4", 70) - _e(r, "slot4", 60)) for r in rows]
    share4 = [_slot_gain(r, 4, 70) / l * 100 for r, l in zip(rows, lift) if l]
    per10k_slot3 = [_slot_gain(r, 3, 60) / (cost[(50, 60)] / 10000) for r in rows]
    per10k_slot4 = [_slot_gain(r, 4, 70) / (cost[(60, 70)] / 10000) for r in rows]
    # At Lv60, an individual with a good 3rd slot vs one with a good 4th slot.
    lead60 = [(_e(r, "slot3", 60) - _e(r, "slot4", 60)) / _e(r, "slot4", 60) * 100 for r in rows]

    def span(values, fmt="{:.0f}"):
        lo, hi = fmt.format(min(values)), fmt.format(max(values))
        return lo if lo == hi else f"{lo}〜{hi}"

    by_good: Dict[str, List[float]] = {}
    for r, g in zip(rows, g4):
        by_good.setdefault(r["good"], []).append(g)
    g4_text = "、".join(f"{name}で{span(vals)}%" for name, vals in by_good.items())
    conclusion = [
        f"Lv70で開く4つめの枠によいサブスキルがあると、Lv70の1日のエナジーが増えます（{g4_text}）。"
        f"Lv60→70で増える分のうち{span(share4)}%が、この4つめの枠の分です。",
        f"ただし4つめの枠を使うには、Lv60→70のかけら{fmt_man(cost[(60, 70)])}が要ります"
        f"（Lv50→60は{fmt_man(cost[(50, 60)])}）。よいサブスキル1つの価値をかけら1万あたりで比べると、"
        f"3つめ（Lv50）の枠は{span(per10k_slot3, '{:,.0f}')}/日、4つめ（Lv70）の枠は{span(per10k_slot4, '{:,.0f}')}/日です。",
        f"同じよいサブスキルが3つめにある個体は、4つめにある個体より、Lv50〜69の間ずっと{span(lead60)}%強く（Lv60で比べた値）、"
        "Lv70でやっと並びます。育てる個体を選ぶときは、まずLv50までのサブスキル（1〜3つめ）を見ます。4つめは、そのあとの決め手です。",
        f"Lv80で開く5つめの枠は、レベル上限が{cap}なので、いまは開けられません。計算エンジンで仮にLv80にすると、"
        f"よいサブスキルで{span(g5)}%増えますが、Lv70より先のかけらの量はまだ分からず、いまは育成の判断に入れません。",
        f"Ver.3.6.0の前は、4つめはLv75、5つめはLv100でした。4つめが上限のLv70で開くようになったので、Lv70まで育てるエースでは4つめの枠も見る価値があります。",
    ]

    reps = []
    seen = set()
    for r in rows:
        if r["specialty"] not in seen:
            seen.add(r["specialty"])
            reps.append(r)
    variant_names = {"slot3": f"3つめ（Lv{slots[3]}）の枠", "slot4": f"4つめ（Lv{slots[4]}）の枠",
                     "slot5": f"5つめ（Lv{slots[5]}）の枠"}
    figures = ""
    for r in reps:
        series = [{"name": name, "slot": i + 1,
                   "points": [(lv, _e(r, v, lv) / _e(r, "base", lv) * 100 - 100) for lv in data["levels"]]}
                  for i, (v, name) in enumerate(variant_names.items())]
        chart = line_chart(
            series, title=f"{r['species_ja']}：よいサブスキル（{r['good']}）をどの枠に持つか",
            desc=f"{r['species_ja']}で、よいサブスキルを3つめ・4つめ・5つめの枠に持ったとき、持たない個体と比べて1日のエナジーが何%多いかをLv50から80まで表した図。",
            x_label="レベル", y_label="よいサブスキルなしと比べた増え方（%）",
            x_ticks=range(50, 81, 5), y_format=lambda v: f"{v:.0f}%",
            markers=[(cap, f"上限Lv{cap}"), (80, "Lv80")])
        figures += figure(chart, f"{r['species_ja']}（{SPECIALTY_JA[r['specialty']]}）。Lv{cap}より右は、いまは届かない参考値です。",
                          legend(series))

    bar_series = [{"name": f"3つめの枠（Lv{slots[3]}）", "slot": 1, "values": [round(v) for v in per10k_slot3]},
                  {"name": f"4つめの枠（Lv{slots[4]}）", "slot": 2, "values": [round(v) for v in per10k_slot4]}]
    bars = bar_chart([r["species_ja"] for r in rows], bar_series,
                     title="よいサブスキル1つの価値（かけら1万あたり）",
                     desc="6匹それぞれで、よいサブスキルを3つめの枠に持つ価値（Lv50→60のかけらで割った値）と、4つめの枠に持つ価値（Lv60→70のかけらで割った値）を比べた棒グラフ。",
                     y_label="エナジー/日（1万かけらあたり）", y_format=fmt_int)

    summary_rows = []
    for r in rows:
        summary_rows.append([r["species_ja"], r["good"], fmt_int(_e(r, "base", 60)),
                             f"+{fmt_int(_slot_gain(r, 3, 60))}", fmt_int(_e(r, "base", 70)),
                             f"+{fmt_int(_slot_gain(r, 4, 70))}", fmt_int(_e(r, "base", 80)),
                             f"+{fmt_int(_slot_gain(r, 5, 80))}"])
    summary = table(["ポケモン", "よいサブスキル", "Lv60 土台", "3つめの枠", "Lv70 土台", "4つめの枠",
                     "Lv80 土台（参考）", "5つめの枠（参考）"], summary_rows, numeric=[2, 3, 4, 5, 6, 7],
                    caption="1日のエナジー。「土台」はよいサブスキルなし、枠の列はその枠によいサブスキルがあるときの増分")
    cost_rows = [[f"Lv{a}→{b}", fmt_int(c)] for (a, b), c in cost.items()]
    cost_rows.append([f"Lv{cap}→80", "上限を超えるため、まだ分かりません"])
    cost_table = table(["区間", "かけら"], cost_rows, numeric=[1],
                       caption="6匹とも同じ（通常EXPタイプ・EXP補正なしの性格）")

    pick_rows = [[r["species_ja"], "、".join(f"{k}（{v:+,}）" for k, v in
                                           sorted(r["screened_gain_60"].items(), key=lambda kv: -kv[1])[:4])]
                 for r in rows]
    picks = table(["ポケモン", "Lv60で3つめの枠に入れたときの増分（上位4つ）"], pick_rows,
                  caption="「よいサブスキル」の選び方。一番上を使っています")
    build_rows = [[r["species_ja"], SPECIALTY_JA[r["specialty"]], f"{r['main_skill']}（Lv{r['skill_level']}）",
                   f"{r['berry']}（{r['island']}）", r["nature"], "、".join(r["first"]), r["good"]] for r in rows]
    builds = table(["ポケモン", "タイプ", "メインスキル", "きのみ（好物の島）", "性格", "1・2つめの枠",
                    "よいサブスキル"], build_rows,
                   caption=f"よいサブスキルを置かない枠には、{'・'.join(data['fillers'])}を入れています")

    return Article(
        takeaway=(f"Lv70の4つめの枠はLv70のエナジーを{span(g4)}%押し上げるが、かけら1万あたりではLv50の枠に及ばない。"
                  "Lv80の枠はまだ開けられない。"),
        conclusion=conclusion,
        sections=[
            Section("どの枠に持つかで、いつから効くかが変わる", figures),
            Section("かけら1万あたりの価値", figure(bars, "", legend(bar_series)) + summary + cost_table),
            Section("よいサブスキルの選び方", picks),
            Section("計算に使った個体", builds),
        ],
        how_to_read=[
            "線の図は、よいサブスキルを持たない同じ個体と比べて、1日のエナジーが何%多いかです。線が0%の間は、その枠がまだ開いていません。",
            f"3つめの枠の線はLv50からずっと上にあります。4つめの線はLv{slots[4]}で、5つめの線はLv{slots[5]}で、はじめて上がります。Lv70より右では3つめと4つめの線が重なります（同じサブスキルなので、開いたあとの効き方は同じです）。",
            "棒グラフは「よいサブスキルで増える1日のエナジー÷その枠を開けるのに使うかけら（1万単位）」です。3つめはLv50→60、4つめはLv60→70のかけらで割っています。",
            "Lv70より右（Lv71〜80）は、いまのゲームでは届かない参考値です。",
        ],
        limits=[
            "かけら1万あたりの比べ方は、レベルを上げたことによる伸び（きのみの価値など）を含めず、サブスキル1つの分だけを見ています。",
            "よいサブスキルの選び方は1匹ぶんの計算です。おてつだいボーナスやげんき回復ボーナスは、チームではもっと価値があります（コラム「サブスキルの価値はタイプでどう変わる？」）。",
            "Lv80のエナジーは、計算エンジンの式をそのまま延ばした値です。ゲームでLv70より先が開いたときの数字とは違うかもしれません。",
        ],
    )


COLUMN = Column(slug="subskill-slots", order=41, title="Lv70・Lv80で開くサブスキル枠の価値は？（Ver.3.6.0の変更）",
                generate=generate, build=build, uses_engine=True)
