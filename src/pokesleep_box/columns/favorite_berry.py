"""Column: how much does an island's favorite berry (x2) change daily energy?

Runs the engine's per-individual production (``evaluate`` mode,
``energyScores``) for every final evolution in the engine's pokedex with a
simple role build, once on an island that likes its berry and once on an
island that does not. A few berry specialists are also run with a worse
nature and with no useful subskills, to put the favorite bonus next to
those differences.
"""

from __future__ import annotations

from statistics import median
from typing import Any, Dict, List

from ..analytics import ISLANDS
from ..engine import EngineUnavailable
from ..localization import SUBSKILL_UNLOCKS, names
from . import Article, Column, Section
from .lv60_worth import BUILDS
from .page import figure, table
from .svg import bar_chart, fmt_int, legend

LEVEL = 60
ITERATIONS = 1000   # engine runs; only species whose skill is simulated randomly vary (about +-2% between runs)
SPECIALTIES = ["berry", "ingredient", "skill"]
SPECIALTY_JA = {"berry": "きのみタイプ", "ingredient": "食材タイプ", "skill": "スキルタイプ"}
# Berry specialists compared against nature / subskill differences.
BERRY_EXAMPLES = ["RAICHU", "HOUNDOOM", "TYPHLOSION"]
# Only the first three subskills (Lv10/25/50) are open at Lv60.
PLAIN_SUBSKILLS = ["Dream Shard Bonus", "Research EXP Bonus", "Sleep EXP Bonus",
                   "Energy Recovery Bonus", "Inventory Up S"]
VARIANTS = {
    "base": ("Adamant", BUILDS["berry"][1]),
    "bad_nature": ("Modest", BUILDS["berry"][1]),
    "plain_subskills": ("Adamant", PLAIN_SUBSKILLS),
}
VARIANT_JA = {"base": "基本（いじっぱり・きのみ向けサブスキル）",
              "bad_nature": "性格だけ ひかえめ（スピード↓）",
              "plain_subskills": "サブスキルだけ役に立たないもの"}
TOP = 10


def _final_species(meta: Dict[str, Any]) -> List[str]:
    evolved_from = {info["evolves_from"] for info in meta.values() if info.get("evolves_from")}
    # Species with an "all" specialty (e.g. Darkrai) are left out: they have
    # no single role build to compare with.
    return sorted(name for name, info in meta.items()
                  if name not in evolved_from and info["specialty"] in BUILDS)


def _instance(species: str, info: Dict[str, Any], nature: str, subskills: List[str]) -> Dict[str, Any]:
    return {"species": species, "level": LEVEL, "nature": nature,
            "subskills": [[name, unlock] for name, unlock in zip(subskills, SUBSKILL_UNLOCKS)],
            "ingredients": [slot["choices"][0] for slot in info["ingredients"]],
            "skillLevel": 99, "ribbon": 0}


def _evaluate(ctx, payload: Dict[str, Any], instances: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Energy scores by uid. The engine stops on a species whose main skill it
    cannot simulate, so on failure each instance is retried alone and the
    ones that fail are left out (their uids are returned under ``None``)."""
    try:
        results = ctx.engine({**payload, "instances": instances})["results"]
        return {r["uid"]: r["energyScores"] for r in results}
    except EngineUnavailable:
        scores: Dict[str, Any] = {None: []}
        for item in instances:
            try:
                result = ctx.engine({**payload, "instances": [item]})["results"][0]
                scores[item["uid"]] = result["energyScores"]
            except EngineUnavailable:
                scores[None].append(item["uid"])
        return scores


def _pick(scores: Dict[str, Any]) -> Dict[str, int]:
    return {k: scores[k] for k in ("berry", "ingredient", "direct_skill", "expected")}


def generate(ctx) -> Dict[str, Any]:
    meta = ctx.engine({"mode": "metadata"})["pokemon"]
    all_berries = sorted({info["berry"] for info in meta.values()})
    islands = {"favorite": all_berries, "other": []}
    instances = []
    for species in _final_species(meta):
        info = meta[species]
        nature, subskills = BUILDS[info["specialty"]]
        instances.append({"uid": species, "instance": _instance(species, info, nature, subskills)})
    for species in BERRY_EXAMPLES:
        for key, (nature, subskills) in VARIANTS.items():
            instances.append({"uid": f"{species}:{key}",
                              "instance": _instance(species, meta[species], nature, subskills)})
    by_uid = _evaluate(ctx, {"mode": "evaluate", "anchors": [LEVEL], "iterations": ITERATIONS,
                             "islands": islands}, instances)
    skipped = sorted(by_uid.pop(None, []))
    ja = names()
    pokedex = []
    for species in _final_species(meta):
        if species not in by_uid:
            continue
        info = meta[species]
        scores = by_uid[species]
        pokedex.append({
            "species": species, "species_ja": ja["species"].get(species, species),
            "specialty": info["specialty"], "berry": info["berry"],
            "berry_ja": ja["berries"].get(info["berry"], info["berry"]),
            "favorite": _pick(scores["favorite"][str(LEVEL)]),
            "other": _pick(scores["other"][str(LEVEL)]),
        })
    examples = []
    for species in BERRY_EXAMPLES:
        examples.append({
            "species": species, "species_ja": ja["species"].get(species, species),
            "berry_ja": ja["berries"].get(meta[species]["berry"], meta[species]["berry"]),
            "variants": {key: {"nature": ja["natures"].get(nature, nature),
                               "subskills": [ja["subskills"].get(n, n) for n in subskills[:3]],
                               "favorite": _pick(by_uid[f"{species}:{key}"]["favorite"][str(LEVEL)]),
                               "other": _pick(by_uid[f"{species}:{key}"]["other"][str(LEVEL)])}
                         for key, (nature, subskills) in VARIANTS.items()},
        })
    builds = {s: {"nature": ja["natures"].get(BUILDS[s][0], BUILDS[s][0]),
                  "subskills": [ja["subskills"].get(n, n) for n in BUILDS[s][1][:3]]}
              for s in SPECIALTIES}
    return {
        "data": {
            "level": LEVEL,
            "islands": {name: [ja["berries"].get(b, b) for b in berries] for name, berries in ISLANDS.items()},
            "island_berries": {name: list(berries) for name, berries in ISLANDS.items()},
            "builds": builds,
            "skipped": [ja["species"].get(s, s) for s in skipped],
            "pokedex": pokedex,
            "examples": examples,
        },
        "assumptions": [
            "1日のエナジーは計算エンジン（Neroli's Lab）の1匹ぶんの見積もりです。きのみ＋食材（素材の基本エナジー）＋メインスキルのエナジーの合計です。",
            "好物のきのみは、エナジーが2倍になるものとして計算しています（フィールドボーナスは入れていません）。",
            f"どのポケモンもLv{LEVEL}、メインスキルは最大レベル、食材は各枠の1つめ、リボンなし、睡眠は22:00〜6:00です。",
            "性格とサブスキルはタイプごとに決めた形です（きのみ: いじっぱり、食材: うっかりや、スキル: しんちょう）。Lv60で開いているサブスキルは3つです。",
            "島ごとの好物はゲームの表のとおりです。ワカクサ本島は好物が週ごとに変わるので入れていません。",
            "食材は料理にしたときの倍率を入れていません。",
            "1匹ずつの計算です。チームを組んだときの、げんき回復やおてつだいサポートの効果は入っていません。",
            "げんきを回復するスキルなど、一部のメインスキルは乱数で計算されます。そうしたポケモンの数字は、計算し直すと±2%ほど変わることがあります。",
        ],
        "sources": [
            "Neroli's Lab（計算エンジン、engine/src/cli.mjs の evaluate / metadata モード）",
            "src/pokesleep_box/analytics.py の ISLANDS（島ごとの好物のきのみ）",
        ],
    }


def _gain_pct(row: Dict[str, Any]) -> float:
    return (row["favorite"]["expected"] / row["other"]["expected"] - 1) * 100


def build(doc: Dict[str, Any]) -> Article:
    data = doc["data"]
    rows = data["pokedex"]
    by_spec = {s: [r for r in rows if r["specialty"] == s] for s in SPECIALTIES}
    med = {s: median(_gain_pct(r) for r in by_spec[s]) for s in SPECIALTIES}
    lo = {s: min(_gain_pct(r) for r in by_spec[s]) for s in SPECIALTIES}
    hi = {s: max(_gain_pct(r) for r in by_spec[s]) for s in SPECIALTIES}

    # Example berry specialists: favorite bonus vs nature and subskill gaps.
    ex_rows = []
    fav_gain, nat_gap, sub_gap = [], [], []
    for ex in data["examples"]:
        v = ex["variants"]
        base_o, base_f = v["base"]["other"]["expected"], v["base"]["favorite"]["expected"]
        fav_gain.append((base_f / base_o - 1) * 100)
        nat_gap.append((base_o / v["bad_nature"]["other"]["expected"] - 1) * 100)
        sub_gap.append((base_o / v["plain_subskills"]["other"]["expected"] - 1) * 100)
        for key in ("base", "bad_nature", "plain_subskills"):
            ex_rows.append([ex["species_ja"], VARIANT_JA[key], fmt_int(v[key]["other"]["expected"]),
                            fmt_int(v[key]["favorite"]["expected"]),
                            f"{(v[key]['favorite']['expected'] / v[key]['other']['expected'] - 1) * 100:+.0f}%"])

    # Per island: who is on top once the favorite bonus is counted.
    island_tops = {}
    for island, berries in data["island_berries"].items():
        scored = sorted(rows, key=lambda r: (r["favorite"] if r["berry"] in berries else r["other"])["expected"],
                        reverse=True)
        island_tops[island] = [(r, r["berry"] in berries) for r in scored[:TOP]]
    fav_counts = {island: sum(1 for _, fav in top if fav) for island, top in island_tops.items()}
    best_other_berry = max((r for r in by_spec["berry"]), key=lambda r: r["other"]["expected"])
    beaten = {}
    for island, berries in data["island_berries"].items():
        favs = [r for r in by_spec["berry"] if r["berry"] in berries]
        beaten[island] = sum(1 for r in favs if r["favorite"]["expected"] > best_other_berry["other"]["expected"])
    n_fav_berry = {island: sum(1 for r in by_spec["berry"] if r["berry"] in berries)
                   for island, berries in data["island_berries"].items()}

    nonfav_berry_top = sum(1 for top in island_tops.values() for r, fav in top
                           if not fav and r["specialty"] == "berry")
    all_beat = all(beaten[i] == n_fav_berry[i] for i in beaten)
    conclusion = [
        f"好物の島では、きのみタイプの1日のエナジーが+{lo['berry']:.0f}〜{hi['berry']:.0f}%（真ん中の値で+{med['berry']:.0f}%）増えます（Lv{data['level']}）。",
        "好物になると、きのみのぶんだけがそのまま2倍になります。なので、増える%は「もとのエナジーのうち、きのみが何%か」と同じです。"
        f"食材タイプは+{med['ingredient']:.0f}%、スキルタイプは+{med['skill']:.0f}%です（どちらも真ん中の値）。",
        f"きのみタイプにとって、好物かどうかの差（+{min(fav_gain):.0f}〜{max(fav_gain):.0f}%）は、"
        f"性格をひかえめからいじっぱりに変える差（+{min(nat_gap):.0f}〜{max(nat_gap):.0f}%）の約{sum(fav_gain) / sum(nat_gap):.0f}倍です。"
        f"サブスキル3つを全部はずれから当たりに変える差（+{min(sub_gap):.0f}〜{max(sub_gap):.0f}%）と比べても、同じか少し大きい差です。",
        (f"好物のきのみタイプは、好物の島では1匹残らず、好物でない島で最も高いきのみタイプ"
          f"（{best_other_berry['species_ja']}、{fmt_int(best_other_berry['other']['expected'])}）を上回ります。"
         if all_beat else "")
        + f"島ごとの上位{TOP}匹のうち、好物のポケモンは{min(fav_counts.values())}〜{max(fav_counts.values())}匹です。",
        "チームづくりでは、よく行く島の好物のきのみタイプを先に育てるのが効きます。"
        + (f"好物でないきのみタイプは、6つの島の上位{TOP}匹に1匹も入りません。" if nonfav_berry_top == 0 else
           f"好物でないきのみタイプが島の上位{TOP}匹に入るのは、6つの島を合わせて{nonfav_berry_top}回だけです。"),
    ]

    spec_series = [{"name": "好物でない島", "slot": 1,
                    "values": [round(median(r["other"]["expected"] for r in by_spec[s])) for s in SPECIALTIES]},
                   {"name": "好物の島", "slot": 2,
                    "values": [round(median(r["favorite"]["expected"] for r in by_spec[s])) for s in SPECIALTIES]}]
    spec_chart = bar_chart([SPECIALTY_JA[s] for s in SPECIALTIES], spec_series,
                           title=f"タイプ別の1日のエナジー（Lv{data['level']}、真ん中の値）",
                           desc="きのみ・食材・スキルタイプそれぞれで、好物でない島と好物の島の1日のエナジーの真ん中の値を比べた棒グラフ。",
                           y_label="1日のエナジー", y_format=fmt_int)
    spec_table = table(["タイプ", "ポケモンの数", "好物でない島", "好物の島", "増え方（真ん中）", "増え方（最小〜最大）"],
                       [[SPECIALTY_JA[s], len(by_spec[s]), fmt_int(spec_series[0]["values"][i]),
                         fmt_int(spec_series[1]["values"][i]), f"+{med[s]:.0f}%",
                         f"+{lo[s]:.0f}〜{hi[s]:.0f}%"]
                        for i, s in enumerate(SPECIALTIES)],
                       numeric=[1, 2, 3, 4, 5], caption="1日のエナジーは、各タイプのポケモンを並べたときの真ん中の値です")

    cmp_series = [{"name": "好物の島にする", "slot": 2, "values": [round(v, 1) for v in fav_gain]},
                  {"name": "性格を良くする", "slot": 1, "values": [round(v, 1) for v in nat_gap]},
                  {"name": "サブスキルを良くする", "slot": 3, "values": [round(v, 1) for v in sub_gap]}]
    cmp_chart = bar_chart([ex["species_ja"] for ex in data["examples"]], cmp_series,
                          title="きのみタイプで、何を変えると何%増えるか",
                          desc="3匹のきのみタイプで、好物の島にしたとき、性格をひかえめからいじっぱりにしたとき、サブスキルを役に立たないものからきのみ向けにしたときの、1日のエナジーの増え方を比べた棒グラフ。",
                          y_label="増え方（%）", y_format=lambda v: f"{v:.0f}")
    ex_table = table(["ポケモン", "個体", "好物でない島", "好物の島", "好物での増え方"], ex_rows,
                     numeric=[2, 3, 4], caption=f"Lv{data['level']}の1日のエナジー")

    island_sections = []
    for island, top in island_tops.items():
        trs = [[i + 1, r["species_ja"], SPECIALTY_JA[r["specialty"]], r["berry_ja"], "好物" if fav else "",
                fmt_int((r["favorite"] if fav else r["other"])["expected"])] for i, (r, fav) in enumerate(top)]
        island_sections.append(
            f"<h3>{island}（好物: {'・'.join(data['islands'][island])}）</h3>"
            + table(["順位", "ポケモン", "タイプ", "きのみ", "好物", "1日のエナジー"], trs, numeric=[0, 5],
                    highlight=[i for i, (_, fav) in enumerate(top) if fav]))
    island_rows = [[island, n_fav_berry[island], beaten[island], fav_counts[island]]
                   for island in data["island_berries"]]
    island_summary = table(["島", "好物のきのみタイプの数", "そのうち、好物でない最強のきのみタイプをこえる数", f"上位{TOP}匹のうち好物"],
                           island_rows, numeric=[1, 2, 3],
                           caption=f"好物でない島で1日のエナジーがいちばん高いきのみタイプは{best_other_berry['species_ja']}"
                                   f"（{fmt_int(best_other_berry['other']['expected'])}）です")

    top_rows = sorted(by_spec["berry"], key=_gain_pct, reverse=True)
    berry_rows = [[r["species_ja"], r["berry_ja"], fmt_int(r["other"]["expected"]), fmt_int(r["favorite"]["expected"]),
                   f"+{_gain_pct(r):.0f}%"] for r in top_rows]
    berry_table = table(["ポケモン", "きのみ", "好物でない島", "好物の島", "増え方"], berry_rows, numeric=[2, 3, 4],
                        caption=f"すべてのきのみタイプ（Lv{data['level']}、増え方の大きい順）")

    limits = [
        "島のフィールドボーナス（島ごとの％の上乗せ）は入れていません。これは島の全員に同じようにかかるので、好物かどうかの比べ方は変わりません。",
        "チームの中での役割（げんき回復やおてつだいサポート）、料理の倍率は入っていないので、食材タイプとスキルタイプの実際の価値はこの表より高いことがあります。",
        "ここでは「いちばん高い1匹」の順位です。実際のチームは5匹で、食材やスキルの役割も必要です。",
    ]
    limits.append("メインスキルは、計算エンジンが1日のエナジーとして数えるぶんだけが入っています。げんき回復や食材を増やすスキルは数に入らないので、"
                  "そうしたスキルタイプは、きのみの割合が大きく見え、好物での増え方も大きく出ます。")
    if data.get("skipped"):
        limits.append(f"計算エンジンがメインスキルを計算できなかった{'・'.join(data['skipped'])}は入っていません。")

    b = data["builds"]
    build_rows = [[SPECIALTY_JA[s], b[s]["nature"], "、".join(b[s]["subskills"])] for s in SPECIALTIES]
    builds = table(["タイプ", "性格", "サブスキル（Lv10/25/50）"], build_rows)

    return Article(
        takeaway=(f"好物の島では、きのみタイプの1日のエナジーが約+{med['berry']:.0f}%。"
                  "性格やサブスキルの差より大きく、島ごとの上位は好物のきのみタイプが占めます。"),
        conclusion=conclusion,
        sections=[
            Section("タイプ別の増え方", figure(spec_chart, "", legend(spec_series)) + spec_table),
            Section("性格・サブスキルの差と比べる", figure(cmp_chart, "", legend(cmp_series)) + ex_table),
            Section("島ごとの上位のポケモン", island_summary + "".join(island_sections)),
            Section("きのみタイプの一覧", berry_table),
            Section("計算に使った個体", builds),
        ],
        how_to_read=[
            "1つめの図は、タイプごとに全部のポケモンを並べたときの真ん中の値です。2本の棒の差が、好物の島での増え方です。",
            "2つめの図は、同じポケモンで1つだけ条件を変えたときの増え方です。性格とサブスキルは、好物でない島で比べています。",
            "島ごとの表は、その島の好物ならきのみを2倍にして、1日のエナジーが高い順に並べたものです。色つきの行が好物のポケモンです。",
            "増え方は、きのみのエナジーの割合で決まります。好物の島では、きのみのぶんだけがもう1回足されます。",
        ],
        limits=limits,
    )


COLUMN = Column(slug="favorite-berry", order=51, title="島の好物きのみ（2倍）はどれだけ効く？",
                generate=generate, build=build, uses_engine=True)
