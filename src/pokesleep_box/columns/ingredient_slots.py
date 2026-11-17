"""Column: what are the Lv30 and Lv60 ingredient slots worth?

Runs the engine's per-individual production (``evaluate`` mode,
``energyScores``) for a few species with every possible ingredient
combination, at levels around the Lv30 and Lv60 unlocks. Ingredients are
counted at their base energy (no recipe multiplier).
"""

from __future__ import annotations

from itertools import product
from statistics import mean
from typing import Any, Dict, List

from ..core import INGREDIENT_BASE_ENERGY
from ..localization import SUBSKILL_UNLOCKS, names
from . import Article, Column, Section
from .lv60_worth import BUILDS
from .page import figure, table
from .svg import bar_chart, fmt_int, legend, line_chart

# Four ingredient specialists with different slot patterns, plus one berry
# and one skill specialist for comparison.
SPECIES = ["GENGAR", "ABSOL", "FARFETCHD", "TYRANITAR", "RAICHU", "AMPHAROS"]
LEVELS = list(range(20, 61))
KEY_LEVELS = [29, 30, 50, 59, 60]
SLOT_LEVELS = [1, 30, 60]
ITERATIONS = 1000   # engine runs; only species whose skill is simulated randomly vary (about +-2% between runs)
SPECIALTY_JA = {"berry": "きのみタイプ", "ingredient": "食材タイプ", "skill": "スキルタイプ"}


def _combos(info: Dict[str, Any]) -> List[List[List[Any]]]:
    return [list(map(list, combo)) for combo in product(*(slot["choices"] for slot in info["ingredients"]))]


def generate(ctx) -> Dict[str, Any]:
    meta = ctx.engine({"mode": "metadata"})["pokemon"]
    instances = []
    for species in SPECIES:
        info = meta[species]
        nature, subskills = BUILDS[info["specialty"]]
        for index, combo in enumerate(_combos(info)):
            instances.append({"uid": f"{species}:{index}", "instance": {
                "species": species, "level": LEVELS[0], "nature": nature,
                "subskills": [[name, unlock] for name, unlock in zip(subskills, SUBSKILL_UNLOCKS)],
                "ingredients": combo, "skillLevel": 99, "ribbon": 0}})
    favorite = sorted({meta[s]["berry"] for s in SPECIES})
    response = ctx.engine({"mode": "evaluate", "anchors": LEVELS, "iterations": ITERATIONS,
                           "islands": {"favorite": favorite}, "instances": instances})
    by_uid = {r["uid"]: r["energyScores"]["favorite"] for r in response["results"]}
    ja = names()
    rows = []
    for species in SPECIES:
        info = meta[species]
        nature, subskills = BUILDS[info["specialty"]]
        combos = []
        for index, combo in enumerate(_combos(info)):
            scores = by_uid[f"{species}:{index}"]
            combos.append({
                "ingredients": [[ja["ingredients"].get(n, n), a, INGREDIENT_BASE_ENERGY[n]] for n, a in combo],
                "default": index == 0,
                "energy": {str(lv): {k: scores[str(lv)][k] for k in ("berry", "ingredient", "direct_skill", "expected")}
                           for lv in KEY_LEVELS},
                "ingredient_curve": [scores[str(lv)]["ingredient"] for lv in LEVELS] if index == 0 else None,
            })
        rows.append({
            "species": species, "species_ja": ja["species"].get(species, species),
            "specialty": info["specialty"],
            "main_skill": ja["mainskills"].get(info["main_skill"], info["main_skill"]),
            "berry": ja["berries"].get(info["berry"], info["berry"]),
            "nature": ja["natures"].get(nature, nature),
            "subskills": [[ja["subskills"].get(n, n), u] for n, u in zip(subskills, SUBSKILL_UNLOCKS)],
            "combos": combos,
        })
    return {
        "data": {"levels": LEVELS, "key_levels": KEY_LEVELS, "slot_levels": SLOT_LEVELS, "species": rows},
        "assumptions": [
            "1日のエナジーは計算エンジン（Neroli's Lab）の1匹ぶんの見積もりです。きのみ＋食材＋メインスキルのエナジーの合計です。",
            "食材は素材の基本エナジー（1個あたりの値。例: げきからハーブ130、あじわいキノコ167）で数えています。料理にするとレシピの倍率がかかるので、実際の食材の価値はこれより大きくなります。",
            "きのみが好物になる島（好物2倍）、睡眠は22:00〜6:00、リボンなし、おてつだいサポートなし、メインスキルは最大レベルです。",
            "サブスキルはLv10・25・50で開くので、Lv29→30とLv59→60の間ではサブスキルは変わりません。この2つの段差は食材の枠だけの効果です。",
            "最終進化のすがたで計算しています。食材の数は最終進化の値です。",
            "バンギラスのメインスキル（げんきチャージS）は乱数で計算されるので、バンギラスの数字は計算し直すと少し（±2%ほど）変わることがあります。",
        ],
        "sources": [
            "Neroli's Lab（計算エンジン、engine/src/cli.mjs の evaluate / metadata モード）",
            "src/pokesleep_box/core.py の INGREDIENT_BASE_ENERGY（食材の基本エナジー）",
        ],
    }


def _slot_values(combo: Dict[str, Any]) -> List[int]:
    return [amount * value for _, amount, value in combo["ingredients"]]


def _jump(combo: Dict[str, Any], a: int, b: int, part: str = "ingredient") -> float:
    return (combo["energy"][str(b)][part] / combo["energy"][str(a)][part] - 1) * 100


def _combo_label(combo: Dict[str, Any]) -> str:
    return " / ".join(f"{n}×{a}" for n, a, _ in combo["ingredients"])


def build(doc: Dict[str, Any]) -> Article:
    data = doc["data"]
    rows = data["species"]
    levels = data["levels"]
    ing_rows = [r for r in rows if r["specialty"] == "ingredient"]
    other_rows = [r for r in rows if r["specialty"] != "ingredient"]
    default = {r["species"]: next(c for c in r["combos"] if c["default"]) for r in rows}

    j30 = [_jump(c, 29, 30) for r in ing_rows for c in r["combos"]]
    j60 = [_jump(c, 59, 60) for r in ing_rows for c in r["combos"]]
    best = {r["species"]: max(r["combos"], key=lambda c: c["energy"]["60"]["ingredient"]) for r in rows}
    worst = {r["species"]: min(r["combos"], key=lambda c: c["energy"]["60"]["ingredient"]) for r in rows}
    choice_gap = [(best[r["species"]]["energy"]["60"]["ingredient"] / worst[r["species"]]["energy"]["60"]["ingredient"] - 1) * 100
                  for r in ing_rows]
    total60_ing = [(c["energy"]["60"]["expected"] / c["energy"]["59"]["expected"] - 1) * 100
                   for r in ing_rows for c in r["combos"]]
    total60_other = [(c["energy"]["60"]["expected"] / c["energy"]["59"]["expected"] - 1) * 100
                     for r in other_rows for c in r["combos"]]
    gain5060_ing = [(default[r["species"]]["energy"]["60"]["expected"] / default[r["species"]]["energy"]["50"]["expected"] - 1) * 100
                    for r in ing_rows]
    gain5060_other = [(default[r["species"]]["energy"]["60"]["expected"] / default[r["species"]]["energy"]["50"]["expected"] - 1) * 100
                      for r in other_rows]
    # The Lv60 slot pays off when its per-help value beats the average of the
    # first two slots (each open slot is picked equally often).
    def model_err(group: List[Dict[str, Any]]) -> List[float]:
        return [mean(_slot_values(c)) / mean(_slot_values(c)[:2]) * 100 - 100 - _jump(c, 59, 60)
                for r in group for c in r["combos"]]

    model_err_ing = model_err(ing_rows)
    model_err_other = [abs(e) for e in model_err(other_rows)]
    # Share of the Lv50->60 gain that arrives in the single step Lv59->60.
    last_step_share = [(default[r["species"]]["energy"]["60"]["expected"] - default[r["species"]]["energy"]["59"]["expected"])
                       / (default[r["species"]]["energy"]["60"]["expected"] - default[r["species"]]["energy"]["50"]["expected"]) * 100
                       for r in ing_rows]
    shares60 = [default[r["species"]]["energy"]["60"]["ingredient"] / default[r["species"]]["energy"]["60"]["expected"] * 100
                for r in ing_rows]

    conclusion = [
        f"食材タイプは、Lv30で2つめの枠が開くと、食材のエナジーが+{min(j30):.0f}〜{max(j30):.0f}%増えます。"
        f"Lv60で3つめの枠が開くと、さらに+{min(j60):.0f}〜{max(j60):.0f}%増えます（1レベル上げるだけでの差です）。",
        "増え方は「新しい枠の食材の数×1個のエナジー」が、それまでの枠の平均より大きいかで決まります。"
        "食材おてつだいのたびに、開いている枠から同じ確率で1つが選ばれるためです。"
        f"きのみ・スキルタイプでは、この考え方で出した予想と計算エンジンの値のずれは最大{max(model_err_other):.1f}ポイントです。"
        f"食材タイプでは予想より最大{max(model_err_ing):.0f}ポイント小さくなります。1回に持ち帰る食材が増えて、"
        "夜のあいだに持ち物がいっぱいになりやすくなるためと考えられます。",
        f"どの食材を選ぶかで、Lv60の食材のエナジーは{min(choice_gap):.0f}〜{max(choice_gap):.0f}%変わります"
        "（同じポケモンの、いちばん良い組み合わせと悪い組み合わせの差）。",
        f"食材タイプは1日のエナジーの{min(shares60):.0f}〜{max(shares60):.0f}%が食材なので、Lv59→60の1レベルで合計も"
        f"+{min(total60_ing):.0f}〜{max(total60_ing):.0f}%増えます。きのみタイプとスキルタイプでは"
        f"+{min(total60_other):.0f}〜{max(total60_other):.0f}%だけです。",
        f"Lv50→60で見ると、食材タイプは+{min(gain5060_ing):.0f}〜{max(gain5060_ing):.0f}%、きのみ・スキルタイプは"
        f"+{min(gain5060_other):.0f}〜{max(gain5060_other):.0f}%です。食材タイプでは、その伸びの{min(last_step_share):.0f}〜{max(last_step_share):.0f}%が"
        "Lv59→60の1レベルに集まっています。このアプリは役割ごとにLv50とLv60の良いほうで点をつけるので、"
        "食材タイプはLv60の点がはっきり上になり、3つめの食材が良いほどその差は大きくなります。",
        "料理ではレシピの倍率がかかるので、実際には食材の価値はこの数字より大きく、Lv60の3つめの枠もさらに大事になります。",
    ]

    curve_series = [{"name": r["species_ja"], "slot": i + 1,
                     "points": [(lv, v / default[r["species"]]["ingredient_curve"][levels.index(29)] * 100)
                                for lv, v in zip(levels, default[r["species"]]["ingredient_curve"])]}
                    for i, r in enumerate(ing_rows)]
    curve = line_chart(curve_series, title="食材のエナジーの伸び（Lv29を100としたとき）",
                       desc="4匹の食材タイプの1日の食材のエナジーを、Lv29を100として、Lv20から60まで線で表した図。Lv30とLv60で段差がある。",
                       x_label="レベル", y_label="Lv29=100", y_min=60, x_ticks=range(20, 61, 5),
                       y_format=lambda v: f"{v:.0f}", markers=[(30, "Lv30"), (60, "Lv60")])

    choice_series = [
        {"name": "Lv59（枠2つ、最初の組み合わせ）", "slot": 1,
         "values": [default[r["species"]]["energy"]["59"]["ingredient"] for r in rows]},
        {"name": "Lv60 いちばん悪い組み合わせ", "slot": 4,
         "values": [worst[r["species"]]["energy"]["60"]["ingredient"] for r in rows]},
        {"name": "Lv60 いちばん良い組み合わせ", "slot": 3,
         "values": [best[r["species"]]["energy"]["60"]["ingredient"] for r in rows]},
    ]
    choice_chart = bar_chart([r["species_ja"] for r in rows], choice_series,
                             title="食材の組み合わせで、Lv60の食材のエナジーはどう変わるか",
                             desc="6匹それぞれで、Lv59の食材のエナジーと、Lv60のいちばん悪い組み合わせ・いちばん良い組み合わせの食材のエナジーを比べた棒グラフ。",
                             y_label="1日の食材のエナジー", y_format=fmt_int, value_labels=False)

    species_sections = []
    for r in rows:
        trs = []
        ordered = sorted(r["combos"], key=lambda c: c["energy"]["60"]["ingredient"], reverse=True)
        for c in ordered:
            v = _slot_values(c)
            trs.append([_combo_label(c), " / ".join(fmt_int(x) for x in v),
                        fmt_int(c["energy"]["29"]["ingredient"]), fmt_int(c["energy"]["30"]["ingredient"]),
                        fmt_int(c["energy"]["59"]["ingredient"]), fmt_int(c["energy"]["60"]["ingredient"]),
                        f"{_jump(c, 59, 60):+.0f}%", f"{mean(v) / mean(v[:2]) * 100 - 100:+.0f}%",
                        fmt_int(c["energy"]["60"]["expected"])])
        species_sections.append(
            f"<h3>{r['species_ja']}（{SPECIALTY_JA[r['specialty']]}）</h3>"
            + table(["食材（Lv1 / 30 / 60）", "1回ぶんのエナジー", "Lv29", "Lv30", "Lv59", "Lv60",
                     "59→60", "予想", "Lv60の合計"], trs, numeric=list(range(1, 9))))

    build_rows = [[r["species_ja"], SPECIALTY_JA[r["specialty"]], r["main_skill"], r["berry"], r["nature"],
                   "、".join(f"{n}(Lv{u})" for n, u in r["subskills"])] for r in rows]
    builds = table(["ポケモン", "タイプ", "メインスキル", "きのみ", "性格", "サブスキル（開くレベル）"], build_rows)

    return Article(
        takeaway=(f"食材タイプはLv59→60の1レベルで食材のエナジーが+{min(j60):.0f}〜{max(j60):.0f}%。"
                  "3つめの枠の食材が良いほど、Lv60の価値が上がります。"),
        conclusion=conclusion,
        sections=[
            Section("レベルごとの食材のエナジー", figure(curve, "", legend(curve_series))),
            Section("どの食材を選ぶかの差", figure(choice_chart, "", legend(choice_series))),
            Section("ポケモンごとの組み合わせ", "".join(species_sections)),
            Section("計算に使った個体", builds),
        ],
        how_to_read=[
            "1つめの図は、食材のエナジーをLv29のときを100として表したものです。Lv30とLv60の段差が、食材の枠が開いた効果です。Lv25とLv50の小さな段差は、サブスキル（おてつだいスピードM、食材確率アップS）が開いた効果です。",
            "2つめの図は、Lv59の食材のエナジー（枠2つ）と、Lv60で枠が3つになったときの、いちばん悪い組み合わせ・いちばん良い組み合わせを並べています。",
            "ポケモンごとの表の「1回ぶんのエナジー」は、その枠が選ばれたときの「食材の数×1個のエナジー」です。「予想」は、3つの枠が同じ確率で選ばれるとして計算したLv59→60の増え方です。食材タイプでは、持ち物がいっぱいになりやすいぶん、計算エンジンの値は予想より少し小さくなります。",
            "表は、Lv60の食材のエナジーが高い順に並べています。",
        ],
        limits=[
            "食材は料理の倍率なしで数えています。レシピに必要な食材かどうかで、実際の価値は大きく変わります。",
            "食材の組み合わせは、ゲームで出る可能性のあるものを全部並べています。実際の個体の食材は捕まえたときに決まり、あとから変えられません。",
            "1匹だけの見積もりです。チームの中での役割は入っていません。",
        ],
    )


COLUMN = Column(slug="ingredient-slots", order=52, title="食材枠（Lv30・Lv60）の価値は？",
                generate=generate, build=build, uses_engine=True)
