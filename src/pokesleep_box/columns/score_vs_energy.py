"""Column: when does the app's score disagree with the actual energy?

The app's 0-100 score (``core.absolute_role_scores``) rates each role
(berry / ingredient / skill) with the game's RP formula, takes the better of
Lv50 and Lv60 per role, and keeps only the best role. The engine's daily
energy instead adds berries, ingredients and skill together. Each subskill
is put alone on a plain build (``subskill_lab``) and both numbers are
compared with the same build without it.
"""

from __future__ import annotations

from typing import Any, Dict, List

from . import Article, Column, Section
from .page import figure, table
from .subskill_lab import NO_ENERGY_SUBSKILLS, SPECIALTY_JA, gain_pct, single_subskill_grid
from .svg import bar_chart, legend

SHORT = {"Helping Speed M": "スピM", "Berry Finding S": "きのみS", "Ingredient Finder M": "食材M",
         "Skill Trigger M": "スキ確M", "Skill Level Up M": "スキレM", "Inventory Up L": "所持L",
         "Helping Bonus": "おてボ", "Energy Recovery Bonus": "げんき", "Dream Shard Bonus": "かけら"}
CHART_SUBSKILLS = list(SHORT)
LEVEL = 60


def generate(ctx) -> Dict[str, Any]:
    grid = single_subskill_grid(ctx)
    return {
        "data": grid,
        "assumptions": [
            "点数は、このアプリの0〜100点です。役割（きのみ・食材・スキル）ごとに、ゲーム画面の数値と同じ式（Neroli's LabのRP）で出し、Lv50とLv60のよいほうを基準値と比べます。役割の中で一番高いものが点数です。",
            "1日のエナジーは計算エンジン（Neroli's Lab）の1匹ぶんの見積もりです。きのみ＋食材（素材の基本エナジー）＋メインスキルのエナジーの合計です。ここではLv60の値を使います。",
            f"土台の個体は、性格{grid['nature']}（補正なし）、サブスキルなし、メインスキルLv{grid['base_skill_level']}です。そこにサブスキルを1つだけ（Lv10の枠に）足して比べています。",
            "スキルレベルアップS/Mは、メインスキルのレベルを1/2上げた状態として計算しています。",
            "きのみが好物になる島（好物2倍）、睡眠は22:00〜6:00、リボンなし、他のポケモンの助けなしで計算しています。",
        ],
        "sources": [
            "Neroli's Lab（計算エンジン、engine/src/cli.mjs の evaluate / metadata モード）",
            "src/pokesleep_box/core.py（absolute_role_scores、点数の基準値）",
            "src/pokesleep_box/columns/subskill_lab.py（個体の組み方）",
        ],
    }


def _score_pct(row: Dict[str, Any], subskill: str) -> float:
    base = row["results"]["none"]["score"]
    return (row["results"][subskill]["score"] - base) / base * 100 if base else 0.0


def _score_pts(row: Dict[str, Any], subskill: str) -> float:
    return row["results"][subskill]["score"] - row["results"]["none"]["score"]


def _avg(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _best_role(result: Dict[str, Any]) -> str:
    scores = result["role_scores"]
    return max(scores, key=scores.get)


ROLE_JA = {"berry": "きのみ", "ingredient": "食材", "skill": "スキル"}


def build(doc: Dict[str, Any]) -> Article:
    data = doc["data"]
    rows: List[Dict[str, Any]] = data["species"]
    ja = data["subskills_ja"]
    subskills = data["subskills"]
    stats = {s: {"score_pct": _avg([_score_pct(r, s) for r in rows]),
                 "score_pts": _avg([_score_pts(r, s) for r in rows]),
                 "energy_pct": _avg([gain_pct(r, s, LEVEL) for r in rows])} for s in subskills}
    for s in subskills:
        stats[s]["gap"] = stats[s]["score_pct"] - stats[s]["energy_pct"]

    zero_score = _avg([stats[s]["score_pct"] for s in NO_ENERGY_SUBSKILLS])
    inv = {s: stats[s] for s in ("Inventory Up S", "Inventory Up M", "Inventory Up L")}
    skill_rows = [r for r in rows if r["specialty"] == "skill"]
    slu_skill_energy = _avg([gain_pct(r, "Skill Level Up M", LEVEL) for r in skill_rows])
    slu_skill_score = _avg([_score_pts(r, "Skill Level Up M") for r in skill_rows])
    skill_best = sorted({ROLE_JA[_best_role(r["results"]["none"])] for r in skill_rows})
    quaquaval = next(r for r in rows if r["species"] == "QUAQUAVAL")
    bfs_ing_energy = gain_pct(quaquaval, "Berry Finding S", LEVEL)
    bfs_ing_score = _score_pts(quaquaval, "Berry Finding S")
    over = sorted(subskills, key=lambda s: -stats[s]["gap"])
    under = sorted(subskills, key=lambda s: stats[s]["gap"])

    conclusion = [
        "点数は「一番得意な役割1つ」を、ゲーム画面の数値と同じ式で見ます。実際のエナジーは、きのみ・食材・スキルの全部の合計です。この違いで、サブスキルによっては点数とエナジーがずれます。",
        f"点数が高く出すぎるのは、ゆめのかけらボーナス・リサーチEXPボーナス・睡眠EXPボーナスです。点数は平均+{zero_score:.0f}%上がりますが、エナジーは0%です。"
        "ゲーム画面の数値の式が、これらに一定のボーナスをつけているためです。",
        f"最大所持数アップも点数のほうが高く出ます（L: 点数+{inv['Inventory Up L']['score_pct']:.0f}%、エナジー+{inv['Inventory Up L']['energy_pct']:.1f}%）。"
        "持ちきれずにあふれる個体でなければ、エナジーはほとんど増えません。",
        f"点数が低く出すぎるのは、スキルレベルアップとスキル確率アップです。スキルタイプでもスキルレベルアップMはエナジーを+{slu_skill_energy:.0f}%増やしますが、点数は+{slu_skill_score:.1f}点しか動きません。"
        f"この2匹は、点数で一番高い役割が「{'・'.join(skill_best)}」で、スキルの役割は点数に入っていないためです。",
        f"同じ理由で、食材タイプのウェーニバルにきのみの数Sがつくと、エナジーは+{bfs_ing_energy:.0f}%なのに点数は{bfs_ing_score:+.1f}点です。"
        "きのみの役割が伸びても、点数に使う食材の役割を超えないためです。",
        f"おてつだいボーナスとげんき回復ボーナスは、1匹のエナジーでは+{stats['Helping Bonus']['energy_pct']:.0f}%、点数では+{stats['Helping Bonus']['score_pct']:.0f}%です。"
        "この2つはチームのほかのポケモンにも効くので、1匹のエナジーは低く出ます。点数とエナジーのどちらが正しいとは言い切れません。",
        "おてつだいスピードMと、きのみタイプのきのみの数Sは、点数とエナジーがほぼ同じだけ動きます。点数で迷ったら、エナジーの数字（育成の評価やパーティの計算）も見てください。",
    ]

    series = [{"name": "点数の伸び", "slot": 1,
               "values": [round(max(0.0, stats[s]["score_pct"]), 1) for s in CHART_SUBSKILLS]},
              {"name": "エナジーの伸び（Lv60）", "slot": 2,
               "values": [round(max(0.0, stats[s]["energy_pct"]), 1) for s in CHART_SUBSKILLS]}]
    chart = bar_chart([ja[s] for s in CHART_SUBSKILLS], series,
                      title="サブスキル1つで、点数とエナジーは何%増える？",
                      desc="主なサブスキル9つについて、6匹の平均で、点数の伸びと1日のエナジーの伸びを並べた棒グラフ。",
                      y_label="伸び（%、6匹の平均）", y_format=lambda v: f"{v:.0f}%",
                      tick_labels=[SHORT[s] for s in CHART_SUBSKILLS])

    table_rows = [[ja[s], f"{stats[s]['score_pts']:+.1f}", f"{stats[s]['score_pct']:+.1f}%",
                   f"{stats[s]['energy_pct']:+.1f}%", f"{stats[s]['gap']:+.1f}"] for s in over]
    gap_table = table(["サブスキル", "点数（点）", "点数（%）", "エナジー（%）", "ずれ（点数%−エナジー%）"],
                      table_rows, numeric=[1, 2, 3, 4],
                      highlight=[i for i, s in enumerate(over) if abs(stats[s]["gap"]) >= 10],
                      caption="6匹の平均。ずれがプラスなら点数が高く出すぎ、マイナスなら低く出すぎ（±10以上に色）")

    detail_rows = []
    for r in rows:
        base = r["results"]["none"]
        for s in ("Skill Level Up M", "Skill Trigger M", "Inventory Up L", "Helping Bonus", "Dream Shard Bonus",
                  "Berry Finding S"):
            res = r["results"][s]
            detail_rows.append([r["species_ja"], SPECIALTY_JA[r["specialty"]], ja[s],
                                f"{base['score']:.1f}→{res['score']:.1f}", ROLE_JA[_best_role(res)],
                                f"{gain_pct(r, s, LEVEL):+.1f}%"])
    detail = table(["ポケモン", "タイプ", "サブスキル", "点数", "点数の役割", "エナジー（Lv60）"], detail_rows,
                   numeric=[3, 5])

    role_rows = [[r["species_ja"], SPECIALTY_JA[r["specialty"]],
                  *[f"{r['results']['none']['role_scores'][k]:.1f}" for k in ("berry", "ingredient", "skill")],
                  ROLE_JA[_best_role(r["results"]["none"])]] for r in rows]
    roles = table(["ポケモン", "タイプ", "きのみ", "食材", "スキル", "点数に使う役割"], role_rows,
                  numeric=[2, 3, 4], caption=f"土台（サブスキルなし・メインスキルLv{data['base_skill_level']}）の役割ごとの点数")

    return Article(
        takeaway=(f"ゆめのかけらボーナスなどは点数+{zero_score:.0f}%なのにエナジー0%。"
                  f"スキルレベルアップMはエナジー+{slu_skill_energy:.0f}%なのに点数がほぼ動かない。"),
        conclusion=conclusion,
        sections=[
            Section("点数とエナジーの伸びの比べ", figure(chart, "マイナスの値（きのみタイプの食材確率アップなど）は0として描いています。表に正しい値があります。",
                                                 legend(series)) + gap_table),
            Section("点数に使われる役割", roles),
            Section("1匹ずつの例", detail),
        ],
        how_to_read=[
            "棒グラフは、サブスキルなしの同じ個体と比べて、点数と1日のエナジーが何%増えるかです。2本の棒の高さが違うほど、点数と実際の働きがずれています。",
            "表の「ずれ」は、点数の伸び（%）からエナジーの伸び（%）を引いた値です。プラスは点数が高く出すぎ、マイナスは低く出すぎです。",
            "「点数に使う役割」は、3つの役割のうち点数が一番高いものです。点数はこの1つしか見ないので、ほかの役割が伸びても点数は変わりません。",
        ],
        limits=[
            f"メインスキルLv{data['base_skill_level']}の土台なので、スキルタイプでもスキルの役割の点数が低く、点数にはきのみの役割が使われています。メインスキルのレベルが高い個体では、スキルの役割が点数に使われ、スキル確率アップやスキルレベルアップも点数に出ます。",
            "点数の基準値はLv50・Lv60の理想の個体（上位10%）なので、サブスキルなしの土台は点数が低めです。点数の伸び（%）は土台が低いほど大きく見えます。",
            "メインスキルはどれもエナジーを直接出す種類です。げんき回復などのメインスキルでは、エナジーの計算でもスキルの価値が出ません。",
            "1匹ぶんの計算です。おてつだいボーナスとげんき回復ボーナスのチームへの効果は、コラム「サブスキルの価値はタイプでどう変わる？」にあります。",
        ],
    )


COLUMN = Column(slug="score-vs-energy", order=42, title="点数（役割ごと）と実際のエナジーがずれるのはどんなとき？",
                generate=generate, build=build, uses_engine=True)
