"""Column: for each specialty, which main skill pays off?

Simulates every final-evolution species once (``skill-output`` engine mode)
at Lv60 with a simple role build and the skill at its max level, then groups
the species by specialty and main skill. What the skill produces per day is
reported in its own unit (energy, energy recovery, dream shards, pot size,
...), together with how often it triggers, so the reader can see that daily
skill output = triggers per day x effect per trigger.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any, Dict, List

from ..analytics import ISLANDS
from ..localization import SUBSKILL_UNLOCKS, names
from . import Article, Column, Section
from .page import figure, table
from .svg import bar_chart, fmt_int, legend

LEVEL = 60
ITERATIONS = 1000
# Same simple role builds as the lv60-worth column (EXP-neutral natures).
BUILDS = {
    "berry": ("Adamant", ["Berry Finding S", "Helping Speed M", "Helping Speed S",
                          "Helping Bonus", "Skill Trigger M"]),
    "ingredient": ("Rash", ["Ingredient Finder M", "Helping Speed M", "Ingredient Finder S",
                            "Inventory Up L", "Helping Speed S"]),
    "skill": ("Careful", ["Skill Trigger M", "Helping Speed M", "Skill Trigger S",
                          "Helping Speed S", "Helping Bonus"]),
}
SPECIALTIES = ("berry", "ingredient", "skill")
SPECIALTY_JA = {"berry": "きのみタイプ", "ingredient": "食材タイプ", "skill": "スキルタイプ"}
# What a skill's daily output is measured in, in the order checked.
KINDS = [
    ("skill_energy", "エナジー", "エナジー/日"),
    ("recovery", "げんき回復", "げんき/日（受け取る1匹あたり）"),
    ("dream_shards", "ゆめのかけら", "かけら/日"),
    ("pot_size", "なべの容量", "食材数/日"),
    ("tasty_chance", "料理の大成功率", "%/日"),
    ("helps", "おてつだい回数", "回/日（1匹あたり）"),
]
# Skills whose effect is mostly outside what the single-individual
# simulation counts, shown with a short note instead of a number.
NOT_MODELED = {
    "Helper Boost": "チーム全員のおてつだい（1匹計算では数えられない）",
    "Ingredient Draw S": "食材（このエンジンの1匹計算では数えられない）",
    "Skill Copy": "ほかのポケモンのスキル（1匹計算では数えられない）",
}
# Main skills compared in the energy chart (present in at least two specialties
# or the strongest energy skills of skill types).
CHART_SKILLS = ["Charge Strength S", "Charge Strength M", "Ingredient Magnet S",
                "Extra Helpful S", "Berry Burst"]


def favored_berries(berry: str) -> List[str]:
    return next((list(b) for b in ISLANDS.values() if berry in b), [])


def build_instance(species: str, info: Dict[str, Any], level: int, nature: str,
                   subskills: List[str]) -> Dict[str, Any]:
    return {"species": species, "level": level, "nature": nature,
            "subskills": [[name, unlock] for name, unlock in zip(subskills, SUBSKILL_UNLOCKS)],
            "ingredients": [slot["choices"][0] for slot in info["ingredients"]],
            "skillLevel": 1, "ribbon": 0}


def output_fields(result: Dict[str, Any]) -> Dict[str, float]:
    """Per-day output of one engine ``skill-output`` result, by kind."""
    return {
        "skill_procs": result["skill_procs"],
        "base_energy": result["berry"] + result["ingredient"],
        "skill_energy": result["skill_strength"] + result["skill_berry"] + result["skill_ingredient"],
        "recovery": result["skill_energy_self"] + result["skill_energy_team"],
        "dream_shards": result["skill_dream_shards"],
        "pot_size": result["skill_pot_size"],
        "tasty_chance": result["skill_tasty_chance"],
        "helps": result["skill_helps"],
    }


def generate(ctx) -> Dict[str, Any]:
    meta = ctx.engine({"mode": "metadata"})["pokemon"]
    has_child = {info["evolves_from"] for info in meta.values() if info["evolves_from"]}
    items = []
    for species, info in sorted(meta.items()):
        if species in has_child or info["specialty"] not in BUILDS:
            continue
        nature, subskills = BUILDS[info["specialty"]]
        items.append({"uid": species, "level": LEVEL, "skillLevel": 99,
                      "berries": favored_berries(info["berry"]),
                      "instance": build_instance(species, info, LEVEL, nature, subskills)})
    response = ctx.engine({"mode": "skill-output", "iterations": ITERATIONS, "instances": items})
    ja = names()
    rows, skipped = [], []
    for result in response["results"]:
        name_ja = ja["species"].get(result["species"], result["species"])
        if "error" in result:
            skipped.append(name_ja)
            continue
        fields = output_fields(result)
        rows.append({"species": result["species"], "species_ja": name_ja,
                     "specialty": result["specialty"], "main_skill": result["main_skill"],
                     "main_skill_ja": ja["mainskills"].get(result["main_skill"], result["main_skill"]),
                     "skill_level": result["skill_level"],
                     **{k: round(v, 2) for k, v in fields.items()}})
    groups = defaultdict(list)
    for row in rows:
        groups[(row["specialty"], row["main_skill"])].append(row)
    summary = []
    for (specialty, skill), members in sorted(groups.items()):
        med = {key: round(statistics.median(m[key] for m in members), 2)
               for key in ("skill_procs", "base_energy", "skill_energy", "recovery", "dream_shards",
                           "pot_size", "tasty_chance", "helps")}
        summary.append({"specialty": specialty, "main_skill": skill,
                        "main_skill_ja": members[0]["main_skill_ja"],
                        "species_ja": [m["species_ja"] for m in members], **med})
    return {
        "data": {"level": LEVEL, "iterations": ITERATIONS, "builds": {k: list(v[1]) for k, v in BUILDS.items()},
                 "natures": {k: v[0] for k, v in BUILDS.items()}, "rows": rows, "groups": summary,
                 "skipped": skipped},
        "assumptions": [
            f"最終進化のポケモン{len(rows)}匹を1匹ずつ、Lv{LEVEL}・メインスキルは最大レベルで計算しました。"
            "サブスキルと性格は、タイプごとに同じ簡単な組み合わせです（下の表）。",
            "数字は計算エンジン（Neroli's Lab）の1匹ぶんの1日の見積もりです。睡眠は22:00〜6:00、リボンなし、"
            "おてつだいサポートやげんき回復をくれる仲間なしで計算しています。",
            "きのみは、そのきのみが好物になる島（2倍）で数えています。食材は素材の基本エナジーで、料理の倍率は入れていません。",
            "同じタイプ・同じスキルのポケモンが何匹かいるときは、真ん中の値（中央値）を出しています。",
            "「スキルのエナジー」は、エナジーチャージのエナジーと、スキルで手に入るきのみ・食材のエナジーの合計です。",
            f"シミュレーションの回数は{ITERATIONS}回です。",
        ] + ([f"エンジンが計算できなかったため外したポケモン: {'、'.join(skipped)}"] if skipped else []),
        "sources": [
            "Neroli's Lab（計算エンジン、engine/src/cli.mjs の skill-output / metadata モード）",
        ],
    }


def _kind(group: Dict[str, Any]):
    """(label, unit, value) of what this skill mainly makes, or None."""
    if group["main_skill"] == "Metronome":
        return None
    for key, label, unit in KINDS:
        if group[key] > 0.05:
            return label, unit, group[key]
    return None


def _output_text(group: Dict[str, Any]) -> str:
    if group["main_skill"] in NOT_MODELED:
        return NOT_MODELED[group["main_skill"]]
    if group["main_skill"] == "Metronome":
        return f"ランダム（エナジー{fmt_int(group['skill_energy'])}ほか）"
    kind = _kind(group)
    if not kind:
        return "不明"
    label, unit, value = kind
    text = f"{value:,.1f}" if value < 100 else fmt_int(value)
    return f"{label} {text} {unit}"


def _per_proc_text(group: Dict[str, Any]) -> str:
    kind = _kind(group)
    if not kind or group["main_skill"] in NOT_MODELED or not group["skill_procs"]:
        return "—"
    value = kind[2] / group["skill_procs"]
    return f"{value:,.1f}" if value < 100 else fmt_int(value)


def build(doc: Dict[str, Any]) -> Article:
    data = doc["data"]
    groups: List[Dict[str, Any]] = data["groups"]
    rows: List[Dict[str, Any]] = data["rows"]
    by_spec = {s: [r for r in rows if r["specialty"] == s] for s in SPECIALTIES}
    procs = {s: statistics.median(r["skill_procs"] for r in by_spec[s]) for s in SPECIALTIES}
    proc_range = {s: (min(r["skill_procs"] for r in by_spec[s]), max(r["skill_procs"] for r in by_spec[s]))
                  for s in SPECIALTIES}
    gmap = {(g["specialty"], g["main_skill"]): g for g in groups}

    def share(g: Dict[str, Any]) -> float:
        total = g["base_energy"] + g["skill_energy"]
        return g["skill_energy"] / total * 100 if total else 0.0

    def best_energy(specialty: str) -> Dict[str, Any]:
        cands = [g for g in groups if g["specialty"] == specialty and g["main_skill"] != "Metronome"]
        return max(cands, key=lambda g: g["skill_energy"])

    best = {s: best_energy(s) for s in SPECIALTIES}
    css = {s: gmap.get((s, "Charge Strength S")) for s in SPECIALTIES}
    shards = gmap.get(("skill", "Dream Shard Magnet S"))
    conclusion = [
        f"スキルが1日に出る回数は、スキルタイプが中央値{procs['skill']:.1f}回、きのみタイプが{procs['berry']:.1f}回、"
        f"食材タイプが{procs['ingredient']:.1f}回です。スキルの成果は「1日の回数 × 1回の効果」なので、"
        f"同じスキルでもスキルタイプが持つと約{procs['skill'] / procs['berry']:.1f}〜{procs['skill'] / procs['ingredient']:.1f}倍になります。",
        f"きのみタイプは、どのスキルでも1日のエナジーの大半がきのみです（スキルのエナジーは多くても全体の"
        f"{max(share(g) for g in groups if g['specialty'] == 'berry'):.0f}%ほど）。"
        "きのみタイプは、スキルより、きのみの多さと好物の島で選ぶのが基本です。",
        f"食材タイプでエナジーが多いのは{best['ingredient']['main_skill_ja']}"
        f"（1日{fmt_int(best['ingredient']['skill_energy'])}、全体の{share(best['ingredient']):.0f}%）です。"
        "ただ食材は料理にすると何倍にもなるので、食材タイプは食材で選び、スキルはおまけと考えるのが安全です。",
        f"スキルタイプでは、スキルが主役です。エナジーなら{best['skill']['main_skill_ja']}"
        f"（1日{fmt_int(best['skill']['skill_energy'])}、全体の{share(best['skill']):.0f}%）が一番で、"
        "きのみバースト・食材ゲットSが続きます。",
    ]
    if shards:
        e4e = gmap.get(("skill", "Energy For Everyone S"))
        cheer = gmap.get(("skill", "Energizing Cheer S"))
        recovery = (f"げんきオールSはチームの1匹あたり1日に約{e4e['recovery']:.0f}、"
                    f"げんきエールSは約{cheer['recovery']:.0f}のげんきを回復します。") if e4e and cheer else ""
        conclusion.append(f"エナジー以外では、スキルタイプのゆめのかけらゲットSが1日にかけら約{fmt_int(shards['dream_shards'])}を集めます。"
                          + recovery + "これらはエナジーと単位がちがうので、チームで足りないものに合わせて選びます。")

    proc_series = [{"name": "中央値", "slot": 1, "values": [round(procs[s], 1) for s in SPECIALTIES]},
                   {"name": "いちばん多いポケモン", "slot": 3,
                    "values": [round(proc_range[s][1], 1) for s in SPECIALTIES]}]
    proc_chart = bar_chart([SPECIALTY_JA[s] for s in SPECIALTIES], proc_series,
                           title="メインスキルが1日に出る回数",
                           desc="きのみ・食材・スキルタイプごとに、メインスキルが1日に出る回数の中央値と最大を比べた棒グラフ。",
                           y_label="回/日", y_format=lambda v: f"{v:.1f}" if v % 1 else f"{v:.0f}")
    proc_table = table(["タイプ", "匹数", "少ないポケモン", "中央値", "多いポケモン"],
                       [[SPECIALTY_JA[s], len(by_spec[s]), f"{proc_range[s][0]:.1f}", f"{procs[s]:.1f}",
                         f"{proc_range[s][1]:.1f}"] for s in SPECIALTIES],
                       numeric=[1, 2, 3, 4], caption="メインスキルが1日に出る回数")

    chart_skills = [k for k in CHART_SKILLS if any((s, k) in gmap for s in SPECIALTIES)]
    energy_series = [{"name": SPECIALTY_JA[s], "slot": i + 1,
                      "values": [round(gmap[(s, k)]["skill_energy"]) if (s, k) in gmap else 0
                                 for k in chart_skills]} for i, s in enumerate(SPECIALTIES)]
    energy_chart = bar_chart([gmap[next((s, k) for s in SPECIALTIES if (s, k) in gmap)]["main_skill_ja"]
                              for k in chart_skills], energy_series,
                             title="スキルで1日に増えるエナジー（タイプ別）",
                             desc="エナジーを生むメインスキルごとに、きのみ・食材・スキルタイプが1日にスキルで得るエナジーを比べた棒グラフ。"
                                  "棒がないのは、その組み合わせのポケモンがいないためです。",
                             y_label="エナジー/日")

    sections = [Section("スキルが出る回数はタイプで決まる", figure(proc_chart, "", legend(proc_series)) + proc_table),
                Section("エナジーを生むスキルの比べ方", figure(energy_chart, "", legend(energy_series)))]
    for s in SPECIALTIES:
        gs = sorted([g for g in groups if g["specialty"] == s],
                    key=lambda g: (-g["skill_energy"], g["main_skill"]))
        body = [[g["main_skill_ja"], "、".join(g["species_ja"]), f"{g['skill_procs']:.1f}", _per_proc_text(g),
                 _output_text(g), fmt_int(g["skill_energy"]), fmt_int(g["base_energy"] + g["skill_energy"]),
                 f"{share(g):.0f}%"] for g in gs]
        sections.append(Section(
            f"{SPECIALTY_JA[s]}のメインスキル",
            table(["メインスキル", "ポケモン", "回数/日", "1回の効果", "スキルの成果/日", "スキルのエナジー/日",
                   "合計エナジー/日", "スキルの割合"], body, numeric=[2, 3, 5, 6, 7],
                  highlight=[0], caption=f"Lv{data['level']}・スキル最大レベル。何匹かいるときは中央値")))
    sections.append(Section("計算に使った個体", table(
        ["タイプ", "性格", "サブスキル（Lv10/25/50/70/80）"],
        [[SPECIALTY_JA[s], names()["natures"].get(data["natures"][s], data["natures"][s]),
          "、".join(names()["subskills"].get(n, n) for n in data["builds"][s])] for s in SPECIALTIES])))

    return Article(
        takeaway=(f"スキルが出る回数はスキルタイプが{procs['skill']:.1f}回/日で、きのみ・食材タイプの約"
                  f"{procs['skill'] / procs['berry']:.1f}〜{procs['skill'] / procs['ingredient']:.1f}倍。"
                  "スキル選びが効くのはスキルタイプ。"),
        conclusion=conclusion,
        sections=sections,
        how_to_read=[
            "「回数/日」はメインスキルが1日に出る回数です。「1回の効果」は1日の成果を回数で割ったもので、スキルのレベルで決まります。",
            "「スキルの成果/日」はそのスキルが1日に生むものを、スキルの単位のまま書いています（エナジー、げんき、かけら など）。",
            "「スキルの割合」は、1日のエナジー（きのみ＋食材＋スキル）のうち、スキルで得た分の割合です。エナジーを生まないスキルは0%です。",
            "表の色つきの行は、そのタイプでスキルのエナジーがいちばん多いスキルです。",
        ],
        limits=[
            "げんき回復・おてつだいサポート・料理系のスキルは、チームのほかのポケモンや料理を通して効きます。"
            "1匹の計算ではその分のエナジーが入らないので、エナジーの数字だけで比べないでください。",
            "おてつだいブースト・食材セレクトS・ものまねは、この1匹計算では成果が数えられないため、数字を出していません。",
            "きのみバースト系は、自分のきのみに加えてチームにもきのみを配ります。表のエナジーには自分の分だけが入っています。",
        ],
    )


COLUMN = Column(slug="main-skill-by-type", order=40,
                title="タイプ（きのみ・食材・スキル）ごとに、どのメインスキルが得？",
                generate=generate, build=build, uses_engine=True)
