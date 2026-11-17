"""Column: how much does the main skill level matter?

Two engine experiments (``skill-output`` mode, one individual at a time):

1. Representative species at Lv50 with every skill level from 1 to the
   skill's max: the daily skill output and total energy per level. The step
   between two levels is what one main skill seed (+1 level) is worth.
2. The same individual with a different Lv50 subskill: Skill Level Up M
   (+2 skill levels) against Berry Finding S, Inventory Up S and Skill
   Trigger S, with the app's 0-100 score (``evaluate`` mode RP components
   scored by ``core.absolute_role_scores``) next to the total energy.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..core import ABSOLUTE_REFERENCES, ANCHORS, absolute_role_scores
from ..localization import names
from . import Article, Column, Section
from .main_skill_by_type import BUILDS, KINDS, build_instance, favored_berries, output_fields
from .page import figure, table
from .svg import bar_chart, fmt_int, legend, line_chart

LEVEL = 50
ITERATIONS = 1000
# One species per common skill/role pairing.
CURVE_SPECIES = ["AMPHAROS", "SCEPTILE", "SWALOT", "SYLVEON", "RAICHU", "BLASTOISE"]
# Skill level up case: two evolutions from the first form, so a newly
# evolved individual has skill level 1 + 2 = 3.
CASE_SPECIES = ["AMPHAROS", "RAICHU"]
CASE_BASE_SKILL_LEVEL = 3
CASE_NATURE = "Serious"
CASE_FIRST = ["Helping Speed M", "Helping Speed S"]
CASE_OPTIONS = [("Skill Level Up M", 2), ("Skill Trigger S", 0), ("Berry Finding S", 0),
                ("Inventory Up S", 0)]
SPECIALTY_JA = {"berry": "きのみタイプ", "ingredient": "食材タイプ", "skill": "スキルタイプ"}


def _kind_key(fields: Dict[str, float]) -> str:
    return next((key for key, _, _ in KINDS if fields[key] > 0.05), "skill_energy")


def generate(ctx) -> Dict[str, Any]:
    meta = ctx.engine({"mode": "metadata"})["pokemon"]
    ja = names()

    items = []
    for species in CURVE_SPECIES:
        info = meta[species]
        nature, subskills = BUILDS[info["specialty"]]
        for level in range(1, 11):   # the engine clamps to the skill's max level
            items.append({"uid": f"{species}:{level}", "level": LEVEL, "skillLevel": level,
                          "berries": favored_berries(info["berry"]),
                          "instance": build_instance(species, info, LEVEL, nature, subskills)})
    results = ctx.engine({"mode": "skill-output", "iterations": ITERATIONS, "instances": items})["results"]
    curves = []
    for species in CURVE_SPECIES:
        info = meta[species]
        points, seen = [], set()
        for result in results:
            if not result["uid"].startswith(species + ":") or result["skill_level"] in seen:
                continue
            seen.add(result["skill_level"])
            points.append({"skill_level": result["skill_level"],
                           **{k: round(v, 2) for k, v in output_fields(result).items()}})
        kind = _kind_key(points[-1])
        curves.append({"species": species, "species_ja": ja["species"].get(species, species),
                       "specialty": info["specialty"],
                       "main_skill_ja": ja["mainskills"].get(info["main_skill"], info["main_skill"]),
                       "max_level": points[-1]["skill_level"], "kind": kind, "points": points})

    case_items, evaluate_items = [], []
    for species in CASE_SPECIES:
        info = meta[species]
        for option, bonus in CASE_OPTIONS:
            raw = build_instance(species, info, LEVEL, CASE_NATURE, CASE_FIRST + [option])
            raw["skillLevel"] = CASE_BASE_SKILL_LEVEL + bonus
            uid = f"{species}:{option}"
            case_items.append({"uid": uid, "level": LEVEL, "skillLevel": raw["skillLevel"],
                               "berries": favored_berries(info["berry"]), "instance": raw})
            evaluate_items.append({"uid": uid, "instance": raw})
    produced = ctx.engine({"mode": "skill-output", "iterations": ITERATIONS, "instances": case_items})["results"]
    evaluated = ctx.engine({"mode": "evaluate", "anchors": list(ANCHORS), "iterations": ITERATIONS,
                            "instances": evaluate_items})["results"]
    cases = []
    for item, result, rp in zip(case_items, produced, evaluated):
        species, option = item["uid"].split(":", 1)
        fields = output_fields(result)
        scores = absolute_role_scores({int(k): v for k, v in rp["scores"].items()}, ABSOLUTE_REFERENCES)
        best_role = max(scores, key=scores.get)
        cases.append({"species": species, "species_ja": ja["species"].get(species, species),
                      "specialty": meta[species]["specialty"],
                      "option": option, "option_ja": ja["subskills"].get(option, option),
                      "skill_level": result["skill_level"],
                      "berry": round(result["berry"]), "ingredient": round(result["ingredient"]),
                      "skill_energy": round(fields["skill_energy"]),
                      "total": round(fields["base_energy"] + fields["skill_energy"]),
                      "skill_procs": round(fields["skill_procs"], 2),
                      "scores": scores, "score": scores[best_role], "best_role": best_role})
    return {
        "data": {"level": LEVEL, "iterations": ITERATIONS,
                 "curve_builds": {s: {"nature": BUILDS[meta[s]["specialty"]][0],
                                      "subskills": BUILDS[meta[s]["specialty"]][1][:3]} for s in CURVE_SPECIES},
                 "curves": curves,
                 "case": {"base_skill_level": CASE_BASE_SKILL_LEVEL, "nature": CASE_NATURE,
                          "first_subskills": CASE_FIRST, "rows": cases}},
        "assumptions": [
            f"どれもLv{LEVEL}で、サブスキルはLv10・25・50の3つが開いた状態です。",
            "数字は計算エンジン（Neroli's Lab）の1匹ぶんの1日の見積もりです。睡眠は22:00〜6:00、リボンなし、"
            "仲間のスキルの助けなしで計算しています。",
            "きのみは、そのきのみが好物になる島（2倍）で数えています。食材は素材の基本エナジーで、料理の倍率は入れていません。",
            "メインスキルのたね1個はスキルレベル+1として、となりあうレベルの差をたね1個の価値としています。",
            f"スキルレベルアップMの例は、2回進化した直後（スキルレベル{CASE_BASE_SKILL_LEVEL}）の個体です。"
            f"スキルレベルアップMがLv50で開くと、スキルレベルが{CASE_BASE_SKILL_LEVEL + 2}になります。",
            "アプリのスコアは、きのみ・食材・スキルの役割ごとに理想の個体と比べた0〜100の値で、Lv50とLv60のよいほうです。"
            "総合スコアはいちばん高い役割の値です。",
            f"シミュレーションの回数は{ITERATIONS}回です。",
        ],
        "sources": [
            "Neroli's Lab（計算エンジン、engine/src/cli.mjs の skill-output / evaluate / metadata モード）",
            "src/pokesleep_box/core.py（アプリのスコアの基準値 ABSOLUTE_REFERENCES）",
        ],
    }


KIND_UNIT = {key: unit for key, _, unit in KINDS}
KIND_LABEL = {key: label for key, label, _ in KINDS}
ROLE_JA = {"berry": "きのみ", "ingredient": "食材", "skill": "スキル"}


def _num(value: float) -> str:
    return f"{value:,.1f}" if abs(value) < 100 else fmt_int(value)


def build(doc: Dict[str, Any]) -> Article:
    data = doc["data"]
    curves: List[Dict[str, Any]] = data["curves"]
    case = data["case"]
    rows: List[Dict[str, Any]] = case["rows"]

    def value(curve, point):
        return point[curve["kind"]]

    def seed(curve, level):
        pts = {p["skill_level"]: p for p in curve["points"]}
        if level + 1 not in pts:
            return None
        return value(curve, pts[level + 1]) - value(curve, pts[level])

    ratios = [value(c, c["points"][-1]) / value(c, c["points"][0]) for c in curves if value(c, c["points"][0])]
    by = {(r["species"], r["option"]): r for r in rows}

    def seed_range(species: str):
        curve = next((c for c in curves if c["species"] == species), None)
        if not curve:
            return None
        steps = [seed(curve, lv) for lv in range(1, curve["max_level"])]
        return curve, min(steps), max(steps)

    conclusion = [
        f"メインスキルの1日の成果は、スキルレベル1から最大レベルまで上げると{min(ratios):.1f}〜{max(ratios):.1f}倍になります"
        "（ここで調べた6匹）。スキルの回数は変わらず、1回の効果が大きくなるためです。",
    ]
    seeds = [seed_range(s) for s in ("AMPHAROS", "RAICHU", "BLASTOISE")]
    if all(seeds):
        conclusion.append(
            "メインスキルのたね1個（スキルレベル+1）で増える1日のエナジーは、"
            + "、".join(f"{SPECIALTY_JA[c['specialty']]}の{c['species_ja']}（{c['main_skill_ja']}）で{fmt_int(lo)}〜{fmt_int(hi)}"
                       for c, lo, hi in seeds)
            + "です。スキルが多く出るスキルタイプに使うのがいちばん得です。")
    for species in dict.fromkeys(r["species"] for r in rows):
        slu = by.get((species, "Skill Level Up M"))
        others = [r for r in rows if r["species"] == species and r["option"] != "Skill Level Up M"]
        if not slu or not others:
            continue
        best = max(others, key=lambda r: r["total"])
        beaten = [r for r in others if r["total"] < slu["total"]]
        name = f"{SPECIALTY_JA[slu['specialty']]}の{slu['species_ja']}"
        if beaten:
            lower_score = [r for r in beaten if r["score"] > slu["score"]]
            text = (f"{name}では、Lv50のスキルレベルアップM（スキルレベル{slu['skill_level'] - 2}→{slu['skill_level']}）は、"
                    + "・".join(r["option_ja"] for r in beaten)
                    + f"より1日のエナジーが{fmt_int(min(slu['total'] - r['total'] for r in beaten))}〜"
                      f"{fmt_int(max(slu['total'] - r['total'] for r in beaten))}多くなります。")
            if lower_score:
                text += ("それなのにアプリの総合スコアは、" + "・".join(f"{r['option_ja']}（{r['score']:.0f}）" for r in lower_score)
                         + f"より低い{slu['score']:.0f}です。")
        else:
            text = f"{name}では、スキルレベルアップMはほかのサブスキルより1日のエナジーが少なくなりました。"
        if best["total"] > slu["total"]:
            text += (f"ただし、{best['option_ja']}は1回のおてつだいのきのみを1個増やすので、この例ではいちばん多い"
                     f"{fmt_int(best['total'])}でした（スキルレベルアップMは{fmt_int(slu['total'])}）。")
        conclusion.append(text)
    conclusion.append(
        "アプリの総合スコアは、いちばん得意な1つの役割だけで決まります。チームのエナジーはきのみ＋食材＋スキルの合計なので、"
        "スコアが同じくらいか少し低い個体のほうが、合計エナジーで勝つことがあります。スキルレベルアップMはその代表です。")

    line_series = [{"name": f"{c['species_ja']}（{c['main_skill_ja']}）", "slot": i + 1,
                    "points": [(p["skill_level"], value(c, p) / value(c, c["points"][-1]) * 100)
                               for p in c["points"]]} for i, c in enumerate(curves)]
    curve_chart = line_chart(
        line_series, title="スキルレベルごとのスキルの成果（最大レベル=100）",
        desc="6匹のメインスキルの1日の成果を、最大レベルのときを100として、スキルレベル1から最大まで線で表した図。",
        x_label="スキルレベル", y_label="最大レベル=100", x_ticks=range(1, 9),
        y_format=lambda v: f"{v:.0f}")

    curve_rows = []
    for c in curves:
        for p in c["points"]:
            step = seed(c, p["skill_level"] - 1) if p["skill_level"] > 1 else None
            curve_rows.append([c["species_ja"], c["main_skill_ja"], p["skill_level"],
                               f"{_num(value(c, p))} {KIND_UNIT[c['kind']]}",
                               "—" if step is None else f"+{_num(step)}",
                               fmt_int(p["base_energy"] + p["skill_energy"])])
    curve_table = table(["ポケモン", "メインスキル", "スキルレベル", "スキルの成果/日", "前のレベルからの増え（たね1個ぶん）",
                         "合計エナジー/日"], curve_rows, numeric=[2, 4, 5],
                        highlight=[i for i, (c, p) in enumerate((c, p) for c in curves for p in c["points"])
                                   if p["skill_level"] == c["max_level"]],
                        caption=f"Lv{data['level']}。色つきの行は最大レベル")

    species_order = list(dict.fromkeys(r["species"] for r in rows))
    options = list(dict.fromkeys(r["option"] for r in rows))
    case_series = [{"name": next(r["option_ja"] for r in rows if r["option"] == o), "slot": i + 1,
                    "values": [by[(s, o)]["total"] for s in species_order]} for i, o in enumerate(options)]
    case_chart = bar_chart([next(r["species_ja"] for r in rows if r["species"] == s) for s in species_order],
                           case_series, title="Lv50のサブスキル別の1日のエナジー",
                           desc="デンリュウとライチュウで、Lv50のサブスキルをスキルレベルアップM・スキル確率アップS・きのみの数S・"
                                "最大所持数アップSにしたときの1日のエナジーを比べた棒グラフ。",
                           y_label="エナジー/日")
    case_rows = [[r["species_ja"], r["option_ja"], r["skill_level"], f"{r['skill_procs']:.1f}", fmt_int(r["berry"]),
                  fmt_int(r["ingredient"]), fmt_int(r["skill_energy"]), fmt_int(r["total"]),
                  f"{r['score']:.0f}（{ROLE_JA[r['best_role']]}）",
                  " / ".join(f"{r['scores'][k]:.0f}" for k in ("berry", "ingredient", "skill"))]
                 for r in rows]
    best_total = {s: max(by[(s, o)]["total"] for o in options) for s in species_order}
    case_table = table(["ポケモン", "Lv50のサブスキル", "スキルレベル", "スキル回数/日", "きのみ", "食材", "スキル",
                        "合計エナジー/日", "アプリの総合スコア", "役割別スコア（きのみ/食材/スキル）"],
                       case_rows, numeric=[2, 3, 4, 5, 6, 7],
                       highlight=[i for i, r in enumerate(rows) if r["total"] == best_total[r["species"]]],
                       caption="色つきの行は、そのポケモンで合計エナジーがいちばん多いサブスキル")

    build_rows = [[c["species_ja"], SPECIALTY_JA[c["specialty"]], c["main_skill_ja"], c["max_level"],
                   names()["natures"].get(data["curve_builds"][c["species"]]["nature"],
                                          data["curve_builds"][c["species"]]["nature"]),
                   "、".join(names()["subskills"].get(n, n) for n in data["curve_builds"][c["species"]]["subskills"])]
                  for c in curves]
    first = "、".join(names()["subskills"].get(n, n) for n in case["first_subskills"])
    builds = (table(["ポケモン", "タイプ", "メインスキル", "最大レベル", "性格", "サブスキル（Lv10/25/50）"], build_rows,
                    numeric=[3], caption="スキルレベル別の計算に使った個体")
              + f'<p class="meta">サブスキル別の比べ方は、性格「{names()["natures"].get(case["nature"], case["nature"])}」'
                f'（補正なし）、Lv10・25が「{first}」で、Lv50だけを変えています。'
                f'スキルレベルはスキルレベルアップMのとき{case["base_skill_level"] + 2}、ほかは{case["base_skill_level"]}です。</p>')

    return Article(
        takeaway=(f"スキルレベル1→最大で、スキルの成果は{min(ratios):.1f}〜{max(ratios):.1f}倍。"
                  "スキルレベルアップMはスコアに出にくいが、合計エナジーでは強い。"),
        conclusion=conclusion,
        sections=[
            Section("スキルレベルごとの伸び", figure(curve_chart, "", legend(line_series)) + curve_table),
            Section("スキルレベルアップMは、ほかのサブスキルとくらべてどうか",
                    figure(case_chart, "", legend(case_series)) + case_table),
            Section("計算に使った個体", builds),
        ],
        how_to_read=[
            "1つめの図は、スキルの1日の成果を最大レベルのときを100として表しています。単位がちがうスキル（エナジー・かけら・げんき）も同じ図で比べられます。",
            "表の「前のレベルからの増え」が、メインスキルのたね1個（スキルレベル+1）で増える1日の量です。",
            "2つめの図と表は、同じ個体のLv50のサブスキルだけを変えたときの1日のエナジーです。"
            "「アプリの総合スコア」はこのアプリの個体評価の値で、いちばん高い役割のスコアです。",
        ],
        limits=[
            "げんきオールSやゆめのかけらゲットSは、エナジーではなく、げんきやかけらで数えています。",
            "スキルレベルが上がると、効果のほかに、きのみバーストなどでチームに配るきのみも増えます。表のエナジーには自分の分だけが入っています。",
            "スキルレベルアップMの例は2匹だけです。もとのスキルレベルが高い個体では、最大レベルで止まるので、+2より小さくなります。",
        ],
    )


COLUMN = Column(slug="skill-level-value", order=41, title="メインスキルのレベルはどれだけ効く？",
                generate=generate, build=build, uses_engine=True)
