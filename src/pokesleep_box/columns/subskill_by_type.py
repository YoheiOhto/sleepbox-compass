"""Column: how does a subskill's value change with the specialty?

Each subskill is put alone on a plain build (see ``subskill_lab``) and the
engine's daily energy is compared with the same build without it, at Lv50
and Lv60. A small 5-member team run shows what the team-wide subskills
(Helping Bonus, Energy Recovery Bonus) add beyond the holder's own output.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..analytics import ISLANDS
from ..localization import names
from . import Article, Column, Section
from .page import figure, table
from .subskill_lab import (NO_ENERGY_SUBSKILLS, SPECIALTY_JA, TEAM_SUBSKILLS, by_specialty, gain,
                           gain_pct, plain_instance, single_subskill_grid)
from .svg import bar_chart, fmt_int, legend

# Team run: the three Old Gold Power Plant species of the lab set plus two
# others, so the holders' berries are favorite berries on that island.
TEAM_ISLAND = "ゴールド旧発電所"
TEAM = ["RAICHU", "GENGAR", "AMPHAROS", "QUAQUAVAL", "ESPEON"]
TEAM_HOLDERS = ["RAICHU", "GENGAR", "AMPHAROS"]
# Ingredient-side subskills are left out: with the smallest pot, extra
# ingredients barely reach the dishes, so the team run would understate them.
TEAM_SUBSKILLS_TRIED = ["Helping Speed M", "Berry Finding S", "Skill Trigger M", "Skill Level Up M",
                        "Helping Bonus", "Energy Recovery Bonus"]
TEAM_LEVEL = 60
TEAM_ITERATIONS = 500
# Short community names for chart axis labels (the tables use full names).
SHORT = {"Helping Speed M": "スピM", "Helping Speed S": "スピS", "Berry Finding S": "きのみS",
         "Ingredient Finder M": "食材M", "Ingredient Finder S": "食材S", "Skill Trigger M": "スキ確M",
         "Skill Trigger S": "スキ確S", "Skill Level Up M": "スキレM", "Skill Level Up S": "スキレS",
         "Inventory Up L": "所持L", "Inventory Up M": "所持M", "Inventory Up S": "所持S",
         "Helping Bonus": "おてボ", "Energy Recovery Bonus": "げんき回復"}
CHART_SUBSKILLS = ["Berry Finding S", "Helping Speed M", "Skill Level Up M", "Ingredient Finder M",
                   "Skill Trigger M", "Helping Bonus", "Energy Recovery Bonus", "Inventory Up L"]
ORDER = ["berry", "ingredient", "skill"]


def _team_runs(ctx, meta: Dict[str, Any], max_skill: Dict[str, int]) -> Dict[str, Any]:
    island = {"name": TEAM_ISLAND, "berries": list(ISLANDS[TEAM_ISLAND])}

    def run(holder, subskill):
        instances = [{"uid": sp, "instance": plain_instance(meta[sp], sp, subskill if sp == holder else None,
                                                            level=TEAM_LEVEL, max_skill=max_skill[sp])}
                     for sp in TEAM]
        plan = ctx.engine({"mode": "custom-team", "island": island, "instances": instances,
                           "teamMode": "current", "teamIterations": TEAM_ITERATIONS})["plan"]
        return {"total": plan["total_energy"], "cooking": plan["cooking"],
                "members": {m["uid"]: m["energy"] for m in plan["members"]}}

    base = run(None, None)
    rows = []
    for holder in TEAM_HOLDERS:
        for subskill in TEAM_SUBSKILLS_TRIED:
            result = run(holder, subskill)
            rows.append({"holder": holder, "subskill": subskill, "total": result["total"],
                         "cooking": result["cooking"],
                         "holder_energy": result["members"][holder],
                         "others_energy": sum(v for k, v in result["members"].items() if k != holder)})
    return {"island": TEAM_ISLAND, "level": TEAM_LEVEL, "iterations": TEAM_ITERATIONS, "members": TEAM,
            "base": {"total": base["total"], "cooking": base["cooking"],
                     "members": base["members"]}, "rows": rows}


def generate(ctx) -> Dict[str, Any]:
    grid = single_subskill_grid(ctx)
    meta = ctx.engine({"mode": "metadata"})["pokemon"]
    max_skill = {r["species"]: r["skill_max_level"] for r in grid["species"]}
    grid["team"] = _team_runs(ctx, meta, max_skill)
    grid["team_species_ja"] = {sp: names()["species"].get(sp, sp) for sp in TEAM}
    return {
        "data": grid,
        "assumptions": [
            "1日のエナジーは計算エンジン（Neroli's Lab）の1匹ぶんの見積もりです。きのみ＋食材（素材の基本エナジー）＋メインスキルのエナジーの合計です。",
            f"土台の個体は、性格{grid['nature']}（補正なし）、サブスキルなし、メインスキルLv{grid['base_skill_level']}です。そこにサブスキルを1つだけ（Lv10の枠に）足して比べています。",
            "スキルレベルアップS/Mは、メインスキルのレベルを1/2上げた状態として計算しています（最大レベルで止まります）。",
            "きのみが好物になる島（好物2倍）、睡眠は22:00〜6:00、リボンなし、他のポケモンの助けなしで計算しています。",
            "食材は料理にしたときの倍率を入れていません。そのため、1匹の計算では食材タイプの食材の価値が小さめに出ます。",
            f"チームの計算は{TEAM_ISLAND}、5匹ともLv{TEAM_LEVEL}・同じ土台で、料理（レシピLv1・最小の鍋）を含みます。シミュレーションは{TEAM_ITERATIONS}回です。",
            f"1匹の計算のシミュレーションは{grid['iterations']}回です。乱数の並びは毎回同じなので、差はぶれにくくなっています。",
        ],
        "sources": [
            "Neroli's Lab（計算エンジン、engine/src/cli.mjs の evaluate / custom-team / metadata モード）",
            "src/pokesleep_box/columns/subskill_lab.py（個体の組み方）",
        ],
    }


def _avg(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _avg_pct(rows: List[Dict[str, Any]], subskill: str, level: int) -> float:
    return _avg([gain_pct(r, subskill, level) for r in rows])


def build(doc: Dict[str, Any]) -> Article:
    data = doc["data"]
    rows: List[Dict[str, Any]] = data["species"]
    groups = by_specialty(rows)
    ja = data["subskills_ja"]
    energy_subskills = [s for s in data["subskills"] if s not in NO_ENERGY_SUBSKILLS]
    ranked = {sp: sorted(energy_subskills, key=lambda s: -_avg_pct(groups[sp], s, 60)) for sp in ORDER}

    def top(sp, n=3):
        return "、".join(f"{ja[s]}（+{_avg_pct(groups[sp], s, 60):.0f}%）" for s in ranked[sp][:n])

    # Team numbers.
    team = data["team"]
    tja = data["team_species_ja"]
    base_total = team["base"]["total"]
    by_row = {(r["holder"], r["subskill"]): r for r in team["rows"]}
    solo = {r["species"]: r for r in rows}

    def team_gain(holder, subskill):
        return by_row[(holder, subskill)]["total"] - base_total

    def solo_gain(holder, subskill):
        return gain(solo[holder], subskill, team["level"])

    def ratios(subskill):
        return [team_gain(h, subskill) / max(1, solo_gain(h, subskill)) for h in TEAM_HOLDERS]

    def span(values, fmt="{:.1f}"):
        lo, hi = fmt.format(min(values)), fmt.format(max(values))
        return lo if lo == hi else f"{lo}〜{hi}"

    hb, erb = ratios("Helping Bonus"), ratios("Energy Recovery Bonus")
    hb_team = [team_gain(h, "Helping Bonus") for h in TEAM_HOLDERS]
    erb_team = [team_gain(h, "Energy Recovery Bonus") for h in TEAM_HOLDERS]
    solo_team_pct = [_avg_pct(groups[sp], s, 60) for sp in ORDER for s in TEAM_SUBSKILLS]
    slu = {sp: _avg_pct(groups[sp], "Skill Level Up M", 60) for sp in ORDER}
    ifm = {sp: _avg_pct(groups[sp], "Ingredient Finder M", 60) for sp in ORDER}
    hsm = [_avg_pct(groups[sp], "Helping Speed M", 60) for sp in ORDER]

    conclusion = [
        f"きのみタイプ: {top('berry')}。きのみの数Sが飛び抜けて効きます。",
        f"食材タイプ: {top('ingredient')}。1匹の計算では、食材確率アップMは+{ifm['ingredient']:.0f}%どまりです（料理の倍率を入れていないためで、料理まで含めた価値はもっと大きくなります）。",
        f"スキルタイプ: {top('skill')}。スキルレベルアップMは、スキルタイプで+{slu['skill']:.0f}%、きのみタイプでは+{slu['berry']:.0f}%と、タイプで差が大きいサブスキルです。",
        f"おてつだいスピードMは、どのタイプでも+{span(hsm, '{:.0f}')}%で、タイプを選ばない安定した強さです。",
        f"食材確率アップは、きのみタイプにつくとマイナスになります（{ifm['berry']:.0f}%）。きのみが2個ずつ出る手伝いが、食材に置き換わるためです。",
        f"おてつだいボーナスとげんき回復ボーナスは、1匹だけの計算では+{span(solo_team_pct, '{:.0f}')}%です。"
        f"5匹チームで計算すると、おてつだいボーナスはチームの合計を+{span(hb_team, '{:,}')}/日"
        f"（持ち主1匹の計算の{span(hb)}倍）、げんき回復ボーナスは+{span(erb_team, '{:,}')}/日（{span(erb)}倍）増やします。"
        "他の4匹の手伝いも増えるためで、1匹の数字だけで見ると、この2つは低く見えます。",
        "ゆめのかけらボーナス・リサーチEXPボーナス・睡眠EXPボーナスは、エナジーには効きません（0%）。",
    ]

    chart_series = [{"name": SPECIALTY_JA[sp], "slot": i + 1,
                     "values": [round(_avg_pct(groups[sp], s, 60), 1) for s in CHART_SUBSKILLS]}
                    for i, sp in enumerate(ORDER)]
    # Bars start at 0, so clip negative values for the chart; the table keeps the sign.
    chart = bar_chart(
        [ja[s] for s in CHART_SUBSKILLS],
        [{**s, "values": [max(0.0, v) for v in s["values"]]} for s in chart_series],
        title="サブスキル1つで増える1日のエナジー（Lv60）",
        desc="主なサブスキル8つについて、きのみ・食材・スキルタイプの1日のエナジーが何%増えるかを比べた棒グラフ。",
        y_label="エナジーの増え方（%）", y_format=lambda v: f"{v:.0f}%",
        tick_labels=[SHORT[s] for s in CHART_SUBSKILLS])

    pct_rows = []
    for s in energy_subskills:
        cells = [ja[s]]
        for sp in ORDER:
            cells += [f"{_avg_pct(groups[sp], s, 50):+.1f}%", f"{_avg_pct(groups[sp], s, 60):+.1f}%"]
        pct_rows.append(cells)
    pct_table = table(["サブスキル", "きのみ Lv50", "きのみ Lv60", "食材 Lv50", "食材 Lv60",
                       "スキル Lv50", "スキル Lv60"], pct_rows, numeric=[1, 2, 3, 4, 5, 6],
                      caption="1日のエナジーの増え方（各タイプ2匹の平均）")

    rank_rows = []
    for i in range(5):
        cells = [f"{i + 1}位"]
        for sp in ORDER:
            s = ranked[sp][i]
            cells.append(f"{ja[s]}（+{_avg_pct(groups[sp], s, 60):.1f}%）")
        rank_rows.append(cells)
    rank_table = table(["順位", "きのみタイプ", "食材タイプ", "スキルタイプ"], rank_rows,
                       caption="Lv60での順位（エナジーに効くサブスキルだけ）")

    detail_rows = []
    for r in rows:
        base = r["results"]["none"]["energy"]["60"]["expected"]
        for s in energy_subskills:
            detail_rows.append([r["species_ja"], ja[s], fmt_int(base), f"{gain(r, s, 60):+,}",
                                f"{gain(r, s, 60, 'berry'):+,}", f"{gain(r, s, 60, 'ingredient'):+,}",
                                f"{gain(r, s, 60, 'direct_skill'):+,}"])
    detail = table(["ポケモン", "サブスキル", "土台のエナジー", "増分（合計）", "きのみ", "食材", "スキル"],
                   detail_rows, numeric=[2, 3, 4, 5, 6], caption="Lv60、1日のエナジー（1匹ぶん）")

    team_series = []
    for i, s in enumerate(TEAM_SUBSKILLS):
        team_series.append({"name": f"{ja[s]}（1匹の計算）", "slot": 2 * i + 1,
                            "values": [max(0, solo_gain(h, s)) for h in TEAM_HOLDERS]})
        team_series.append({"name": f"{ja[s]}（5匹チームの合計）", "slot": 2 * i + 2,
                            "values": [max(0, team_gain(h, s)) for h in TEAM_HOLDERS]})
    team_chart = bar_chart(
        [tja[h] for h in TEAM_HOLDERS], team_series, title="チームに効くサブスキル：1匹の計算とチームの計算",
        desc="おてつだいボーナスとげんき回復ボーナスを3匹のどれかに持たせたとき、持ち主1匹だけの計算で増えるエナジーと、5匹チーム全体で増えるエナジーを比べた棒グラフ。",
        y_label="1日のエナジーの増分", x_label="サブスキルを持たせたポケモン", y_format=fmt_int)
    team_rows = []
    for h in TEAM_HOLDERS:
        for s in TEAM_SUBSKILLS_TRIED:
            row = by_row[(h, s)]
            team_rows.append([tja[h], ja[s], f"{solo_gain(h, s):+,}", f"{team_gain(h, s):+,}",
                              f"{row['holder_energy'] - team['base']['members'][h]:+,}",
                              f"{row['others_energy'] - sum(v for k, v in team['base']['members'].items() if k != h):+,}",
                              f"{row['cooking'] - team['base']['cooking']:+,}"])
    team_table = table(["持ち主", "サブスキル", "1匹の計算", "チーム合計", "うち持ち主（きのみ＋スキル）",
                        "うち他の4匹", "うち料理"], team_rows, numeric=[2, 3, 4, 5, 6],
                       highlight=[i for i, r in enumerate(team_rows) if r[1] in {ja[s] for s in TEAM_SUBSKILLS}],
                       caption=f"{team['island']}・Lv{team['level']}・5匹（{'、'.join(tja[s] for s in team['members'])}）。"
                               f"土台のチーム合計は{fmt_int(base_total)}/日")

    build_rows = [[r["species_ja"], SPECIALTY_JA[r["specialty"]], f"{r['main_skill']}（Lv{data['base_skill_level']}、最大{r['skill_max_level']}）",
                   f"{r['berry']}（{r['island']}）", "・".join(f"{n}×{a}" for n, a in r["ingredients"]),
                   fmt_int(r["results"]["none"]["energy"]["50"]["expected"]),
                   fmt_int(r["results"]["none"]["energy"]["60"]["expected"])] for r in rows]
    builds = table(["ポケモン", "タイプ", "メインスキル", "きのみ（好物の島）", "食材（Lv1/30/60）",
                    "土台 Lv50", "土台 Lv60"], build_rows, numeric=[5, 6],
                   caption=f"性格は{data['nature']}（補正なし）、サブスキルなしが土台")

    return Article(
        takeaway=(f"きのみの数Sはどのタイプでも最大、スキルレベルアップMはスキルタイプで+{slu['skill']:.0f}%。"
                  "おてつだいボーナスは1匹の計算では低く見える。"),
        conclusion=conclusion,
        sections=[
            Section("タイプごとのサブスキルの効き方", figure(chart, "マイナスになる組み合わせ（きのみタイプの食材確率アップ）は0として描いています。表に正しい値があります。",
                                                  legend(chart_series)) + rank_table),
            Section("全サブスキルの増え方（Lv50とLv60）", pct_table),
            Section("チームに効くサブスキル", figure(team_chart, "", legend(team_series)) + team_table),
            Section("1匹ずつの内訳（Lv60）", detail),
            Section("計算に使った個体", builds),
        ],
        how_to_read=[
            "1つめの図と表の%は、サブスキルなしの同じ個体と比べて、1日のエナジーが何%増えるかです。各タイプ2匹の平均です。",
            "順位の表は、タイプごとに、そのサブスキルを1つ持ったときの伸びが大きい順です。",
            "チームの図は、同じサブスキルを持たせたときに「持ち主1匹の計算」と「5匹チームの合計」でどれだけ増えるかを並べています。チームの棒のほうが高いほど、チーム全体に効いています。",
            "チームの表の「うち他の4匹」がプラスなら、持ち主以外のポケモンの手伝いも増えています。料理の欄は、食材が増えて料理が強くなった分です。",
            "Lv50とLv60で%があまり変わらないので、サブスキルの順位はレベルが上がってもほぼ同じです。",
        ],
        limits=[
            "サブスキルは1つずつ足しています。2つ以上あると効き方が重なります（たとえば、おてつだいスピードはMとSを合わせても手伝いの間隔の短縮に上限があります）。",
            "メインスキルはどれもエナジーを直接出す種類（エナジーチャージ）です。げんき回復や食材ゲットのメインスキルでは、スキル確率アップやスキルレベルアップの価値は1匹の計算に出ません。",
            "最大所持数アップは、手伝いが多くて持ちきれなくなる個体ほど効きます。ここでの土台（サブスキルなし）では、ほとんど効いていません。",
            "チームの計算は1つのチームの例です。メンバーや島が変わると数字は変わります。",
            "チームの計算の料理は、最小の鍋・レシピLv1です。食材を増やすサブスキルの効果は料理にほとんど出ないので、チームの表には入れていません。",
            "チームの表で、持ち主以外の4匹が少し減っている行は、シミュレーションのぶれです（数百ていど）。",
        ],
    )


COLUMN = Column(slug="subskill-by-type", order=40, title="サブスキルの価値はタイプでどう変わる？",
                generate=generate, build=build, uses_engine=True)
