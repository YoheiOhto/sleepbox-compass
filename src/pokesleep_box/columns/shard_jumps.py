"""Column: where do dream-shard costs jump? (cost tables only, no engine)."""

from __future__ import annotations

from typing import Any, Dict, List

from .. import costs
from . import Article, Column, Section
from .page import figure, table
from .svg import bar_chart, fmt_int, fmt_man, legend, line_chart

SPECIES = "PIKACHU"      # any normal (600) EXP type species
NATURE = "Serious"       # EXP-neutral nature
BRACKETS = [(1, 30), (30, 50), (50, 60), (60, 70)]
JUMP_RATIO = 1.2         # a level costs >= 20% more shards than the level before
JUMP_FROM = 10           # early levels are tiny and jumpy; ignore them

COST_SOURCE = ("src/pokesleep_box/training_costs.json（必要EXP・アメ1個のEXP・アメ1個あたりのかけら。"
               "公開データの出典はファイル内の _comment）")


def _reasons(level: int) -> List[str]:
    """Which table change makes the step from ``level`` to ``level+1`` jump."""
    reasons = []
    exp_now = costs.cumulative_exp(level + 1) - costs.cumulative_exp(level)
    exp_before = costs.cumulative_exp(level) - costs.cumulative_exp(level - 1)
    if exp_now / exp_before >= 1.15:
        reasons.append(f"次のレベルまでの必要EXPが{fmt_int(exp_before)}→{fmt_int(exp_now)}に増える")
    if costs.candy_exp(level) < costs.candy_exp(level - 1):
        reasons.append(f"アメ1個のEXPが{costs.candy_exp(level - 1)}→{costs.candy_exp(level)}に減る")
    if costs.shards_per_candy(level) / costs.shards_per_candy(level - 1) >= 1.1:
        reasons.append(f"アメ1個のかけらが{costs.shards_per_candy(level - 1)}→"
                       f"{costs.shards_per_candy(level)}に増える")
    return reasons


def generate(ctx) -> Dict[str, Any]:
    cap = costs.max_level()
    cumulative = [costs.training_cost(SPECIES, NATURE, 1, level) for level in range(1, cap + 1)]
    levels = []
    for level in range(1, cap):
        # The single step is costed on its own (start of the level, nothing
        # carried over), so steps do not inherit carry-over rounding noise.
        step = costs.training_cost(SPECIES, NATURE, level, level + 1)
        after = cumulative[level]
        levels.append({
            "level": level,
            "exp_to_next": costs.cumulative_exp(level + 1) - costs.cumulative_exp(level),
            "candy_exp": costs.candy_exp(level),
            "shards_per_candy": costs.shards_per_candy(level),
            "candies": step["candies"],
            "shards": step["dream_shards"],
            "cum_candies": after["candies"],
            "cum_shards": after["dream_shards"],
        })
    jumps = []
    for prev, row in zip(levels, levels[1:]):
        ratio = row["shards"] / prev["shards"]
        if row["level"] >= JUMP_FROM and ratio >= JUMP_RATIO:
            jumps.append({"level": row["level"], "ratio": round(ratio, 3),
                          "shards_before": prev["shards"], "shards": row["shards"],
                          "reasons": _reasons(row["level"])})
    brackets = []
    for start, end in BRACKETS:
        cost = costs.training_cost(SPECIES, NATURE, start, end)
        brackets.append({"from": start, "to": end, "candies": cost["candies"],
                         "shards": cost["dream_shards"]})
    return {
        "data": {"levels": levels, "jumps": jumps, "brackets": brackets,
                 "jump_rule": {"ratio": JUMP_RATIO, "from_level": JUMP_FROM}},
        "assumptions": [
            "通常のEXPタイプ（600）で、EXP補正のない性格（まじめ）の場合です。",
            "アメだけで育てます。睡眠でもらうEXP、ばんのうアメの使い分け、ミニアメブーストは入れていません。",
            "1レベルごとの数字は、そのレベルになったばかりの状態から1つ上げる量です。合計の数字はLv1から続けて育てた量で、余ったEXPを次のレベルに持ち越します。このため、1レベルごとの数字を足すと合計より少し多くなります。",
            "Lv66以降の必要EXPとかけらはコミュニティの実測値です。",
        ],
        "sources": [COST_SOURCE],
    }


def build(doc: Dict[str, Any]) -> Article:
    data = doc["data"]
    levels, jumps, brackets = data["levels"], data["jumps"], data["brackets"]
    by_level = {row["level"]: row for row in levels}
    bracket = {(b["from"], b["to"]): b for b in brackets}
    b3050, b5060, b6070 = bracket[(30, 50)], bracket[(50, 60)], bracket[(60, 70)]
    to50 = by_level[49]["cum_shards"]
    to60 = by_level[59]["cum_shards"]
    jump50 = next((j for j in jumps if j["level"] == 50), None)

    conclusion = []
    if jump50:
        conclusion.append(f"Lv50→51の1レベルで、かけらは{fmt_int(jump50['shards_before'])}→"
                          f"{fmt_int(jump50['shards'])}（{jump50['ratio']:.2f}倍）に跳ね上がります。"
                          f"理由は{('、'.join(jump50['reasons']) or '必要量の増加')}ためです。")
    conclusion.append(f"合計のかけらは、Lv30→50（20レベル分）が{fmt_man(b3050['shards'])}、"
                      f"Lv50→60（10レベル分）が{fmt_man(b5060['shards'])}、"
                      f"Lv60→70（10レベル分）が{fmt_man(b6070['shards'])}です。")
    conclusion.append(f"Lv50→60だけで、Lv1→50の合計（{fmt_man(to50)}）の"
                      f"{b5060['shards'] / to50:.1f}倍かかります。Lv60→70はLv1→60の合計"
                      f"（{fmt_man(to60)}）の{b6070['shards'] / to60:.1f}倍です。")
    others = [j for j in jumps if j["level"] != 50]
    if others:
        conclusion.append("ほかに急に増えるのは " + "、".join(
            f"Lv{j['level']}→{j['level'] + 1}（{j['ratio']:.2f}倍）" for j in others) + " です。")

    jump_levels = {j["level"] for j in jumps}
    per_level = bar_chart(
        [f"Lv{r['level']}→{r['level'] + 1}" for r in levels],
        [{"name": "1レベル上げるのに必要なかけら", "values": [r["shards"] for r in levels], "slot": 1}],
        title="1レベルごとのゆめのかけら（Lv1→70）",
        desc="横軸は今のレベル、縦軸はその1レベルを上げるのに必要なかけら。Lv50を過ぎると棒が急に高くなる。",
        y_label="かけら", x_label="今のレベル",
        tick_labels=[f"Lv{r['level']}" if r["level"] % 10 == 0 or r["level"] == 1 else ""
                     for r in levels],
        highlight=[i for i, r in enumerate(levels) if r["level"] in jump_levels], value_labels=False)
    jump_legend = legend([{"name": "1レベルに必要なかけら", "slot": 1},
                          {"name": "前のレベルより20%以上増えたところ", "slot": 2}])

    cum_points = [(1, 0)] + [(r["level"] + 1, r["cum_shards"]) for r in levels]
    cumulative = line_chart(
        [{"name": "Lv1からの合計かけら", "points": cum_points, "slot": 1}],
        title="Lv1からの合計ゆめのかけら",
        desc="横軸はレベル、縦軸はLv1からそのレベルまでの合計かけら。Lv60を過ぎると線がほぼ垂直に近づく。",
        x_label="レベル", y_label="合計かけら", x_ticks=range(10, 71, 10),
        markers=[(50, "Lv50"), (60, "Lv60")])

    bracket_chart = bar_chart(
        [f"Lv{b['from']}→{b['to']}" for b in brackets],
        [{"name": "かけら", "values": [b["shards"] for b in brackets], "slot": 1}],
        title="レベル帯ごとの合計ゆめのかけら",
        desc="Lv1→30、30→50、50→60、60→70の合計かけら。上の帯ほど少ないレベル数で多くかかる。",
        y_label="かけら")
    bracket_table = table(
        ["レベル帯", "レベル数", "アメ", "かけら", "1レベルあたりのかけら"],
        [[f"Lv{b['from']}→{b['to']}", b["to"] - b["from"], fmt_int(b["candies"]),
          fmt_int(b["shards"]), fmt_int(b["shards"] / (b["to"] - b["from"]))] for b in brackets],
        numeric=[1, 2, 3, 4])

    detail_rows = [r for r in levels if r["level"] >= 45]
    detail = table(
        ["レベル", "必要EXP", "アメ1個のEXP", "アメ1個のかけら", "アメ", "かけら", "Lv1からの合計かけら"],
        [[f"Lv{r['level']}→{r['level'] + 1}", fmt_int(r["exp_to_next"]), r["candy_exp"],
          fmt_int(r["shards_per_candy"]), r["candies"], fmt_int(r["shards"]), fmt_int(r["cum_shards"])]
         for r in detail_rows],
        numeric=[1, 2, 3, 4, 5, 6],
        highlight=[i for i, r in enumerate(detail_rows) if r["level"] in jump_levels])

    jump_table = table(
        ["どこで", "前のレベル", "このレベル", "倍率", "理由"],
        [[f"Lv{j['level']}→{j['level'] + 1}", fmt_int(j["shards_before"]), fmt_int(j["shards"]),
          f"{j['ratio']:.2f}倍", "、".join(j["reasons"]) or "-"] for j in jumps],
        numeric=[1, 2, 3])

    return Article(
        takeaway=(f"Lv50→51で1レベルのかけらが{jump50['ratio']:.2f}倍に。"
                  if jump50 else "") + f"Lv60→70は合計{fmt_man(b6070['shards'])}かけら。",
        conclusion=conclusion,
        sections=[
            Section("1レベルごとのかけら", figure(per_level, "", jump_legend) + jump_table),
            Section("Lv1からの合計", figure(cumulative)),
            Section("レベル帯ごとの合計", figure(bracket_chart) + bracket_table),
            Section("Lv45からの細かい数字", detail),
        ],
        how_to_read=[
            "1つめの図の棒1本は「今のレベルから1つ上げるのに必要なかけら」です。オレンジの棒は、前のレベルより20%以上増えたところです。",
            "必要なかけら＝必要なアメの数×アメ1個のかけらです。アメの数は「必要EXP÷アメ1個のEXP」で決まります。",
            "Lv25とLv30はアメ1個のEXPが減るので急に増えます。Lv50は必要EXPそのものが大きく増えます。",
            "Lv50を過ぎると必要EXPとアメ1個のかけらが両方とも毎レベル増えるので、合計の線は急な坂になります。",
        ],
        limits=["今のレベル内ですでに持っているEXPは0として数えるので、実際より少し多めに出ます。"],
    )


COLUMN = Column(slug="shard-jumps", order=10, title="ゆめのかけらはどこで急に増える？",
                generate=generate, build=build)
