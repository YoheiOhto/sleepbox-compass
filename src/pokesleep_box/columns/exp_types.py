"""Column: how much do EXP types and EXP natures change training cost?"""

from __future__ import annotations

from typing import Any, Dict

from .. import costs
from ..localization import names
from . import Article, Column, Section
from .page import figure, table
from .svg import bar_chart, fmt_int, fmt_man, legend

# A species of each EXP type; costs depend only on the type and nature.
REPRESENTATIVE = {600: "PIKACHU", 900: "DRATINI", 1080: "RAIKOU", 1320: "DARKRAI"}
NATURES = {"up": "Timid", "neutral": "Serious", "down": "Quiet"}
EFFECT_JA = {"up": "EXPアップ", "neutral": "補正なし", "down": "EXPダウン"}
SPANS = [(1, 50), (1, 60), (50, 60)]


def generate(ctx) -> Dict[str, Any]:
    tables = costs.tables()
    rows = []
    for type_, species in REPRESENTATIVE.items():
        for effect, nature in NATURES.items():
            for start, end in SPANS:
                cost = costs.training_cost(species, nature, start, end)
                rows.append({"exp_type": type_, "effect": effect, "from": start, "to": end,
                             "candies": cost["candies"], "shards": cost["dream_shards"],
                             "exp": cost["exp"]})
    species_ja = names()["species"]
    members = {str(t): [] for t in REPRESENTATIVE if t != costs.NORMAL_EXP_TYPE}
    for key, type_ in tables["species_exp_type"].items():
        members[str(type_)].append(species_ja.get(key, key))
    natures_ja = names()["natures"]
    return {
        "data": {
            "types": [{"exp_type": t, "label": tables["exp_types"][str(t)]["label"],
                       "multiplier": tables["exp_types"][str(t)]["multiplier"]}
                      for t in REPRESENTATIVE],
            "natures": {effect: [natures_ja.get(n, n) for n in tables["exp_natures"][effect]]
                        for effect in ("up", "down")},
            "candy_exp": tables["candy_exp"],
            "members": members,
            "costs": rows,
        },
        "assumptions": [
            "Lv1から（または今のレベルになったばかりの状態から）アメだけで育てた量です。睡眠EXP、ミニアメブーストは入れていません。",
            "EXPアップの性格はアメ1個のEXPが約1.18倍、EXPダウンは約0.82倍です（端数はゲームの表どおり）。",
            "伝説・幻のポケモンはLv1で出会うことはありませんが、比べやすいようにLv1からの量も載せています。",
            "ミュウツーのEXPタイプは1つの情報源でしか確かめられていません。",
        ],
        "sources": ["src/pokesleep_box/training_costs.json（EXPタイプ・性格・アメのEXP・かけらの表。"
                    "公開データの出典はファイル内の _comment）"],
    }


def build(doc: Dict[str, Any]) -> Article:
    data = doc["data"]
    types = data["types"]
    cost = {(r["exp_type"], r["effect"], r["from"], r["to"]): r for r in data["costs"]}
    label = {t["exp_type"]: t["label"] for t in types}
    normal60 = cost[(600, "neutral", 1, 60)]
    dragon60 = cost[(900, "neutral", 1, 60)]
    myth60 = cost[(1320, "neutral", 1, 60)]
    up60 = cost[(600, "up", 1, 60)]
    down60 = cost[(600, "down", 1, 60)]
    dragon_down = cost[(900, "down", 50, 60)]
    normal_up = cost[(600, "up", 50, 60)]

    conclusion = [
        f"Lv1→60のアメは、通常タイプ{fmt_int(normal60['candies'])}個に対し、600族は"
        f"{fmt_int(dragon60['candies'])}個（{dragon60['candies'] / normal60['candies']:.2f}倍）、"
        f"幻は{fmt_int(myth60['candies'])}個（{myth60['candies'] / normal60['candies']:.2f}倍）です。",
        f"かけらもほぼ同じ割合で増えます。Lv1→60で通常{fmt_man(normal60['shards'])}、"
        f"600族{fmt_man(dragon60['shards'])}、幻{fmt_man(myth60['shards'])}です。",
        f"性格の差も大きく、通常タイプのLv1→60でEXPアップなら{fmt_int(up60['candies'])}個"
        f"（{(up60['candies'] / normal60['candies'] - 1) * 100:+.0f}%）、EXPダウンなら"
        f"{fmt_int(down60['candies'])}個（{(down60['candies'] / normal60['candies'] - 1) * 100:+.0f}%）です。",
        f"いちばん差が開く組み合わせでは、Lv50→60のかけらが通常タイプ・EXPアップで"
        f"{fmt_man(normal_up['shards'])}、600族・EXPダウンで{fmt_man(dragon_down['shards'])}"
        f"（{dragon_down['shards'] / normal_up['shards']:.1f}倍）になります。",
    ]

    type_names = [f"{t['label']}（{t['exp_type']}）" for t in types]
    shard_series = [
        {"name": "Lv1→50", "slot": 1,
         "values": [cost[(t["exp_type"], "neutral", 1, 50)]["shards"] for t in types]},
        {"name": "Lv50→60", "slot": 2,
         "values": [cost[(t["exp_type"], "neutral", 50, 60)]["shards"] for t in types]},
    ]
    shard_chart = bar_chart(
        type_names, shard_series, title="EXPタイプごとのゆめのかけら（性格補正なし）",
        desc="通常・600族・伝説・幻の4タイプで、Lv1→50とLv50→60に必要なかけらを並べた棒グラフ。",
        y_label="かけら")
    effect_series = [{"name": EFFECT_JA[e], "slot": i + 1,
                      "values": [cost[(t["exp_type"], e, 1, 60)]["candies"] for t in types]}
                     for i, e in enumerate(("up", "neutral", "down"))]
    candy_chart = bar_chart(
        type_names, effect_series, title="Lv1→60に必要なアメ（性格別）",
        desc="EXPタイプごとに、EXPアップ・補正なし・EXPダウンの性格で必要なアメの数を並べた棒グラフ。",
        y_label="アメ", y_format=fmt_int)

    main_rows = []
    for t in types:
        for effect in ("up", "neutral", "down"):
            a, b, c = (cost[(t["exp_type"], effect, s, e)] for s, e in SPANS)
            main_rows.append([f"{t['label']}（{t['exp_type']}）", EFFECT_JA[effect],
                              fmt_int(a["candies"]), fmt_int(a["shards"]),
                              fmt_int(b["candies"]), fmt_int(b["shards"]),
                              fmt_int(c["candies"]), fmt_int(c["shards"])])
    main_table = table(
        ["EXPタイプ", "性格", "Lv1→50 アメ", "かけら", "Lv1→60 アメ", "かけら", "Lv50→60 アメ", "かけら"],
        main_rows, numeric=[2, 3, 4, 5, 6, 7],
        highlight=[i for i, row in enumerate(main_rows) if row[1] == EFFECT_JA["neutral"]])

    member_rows = [[f"{t['label']}（{t['exp_type']}）", f"{t['multiplier']:g}倍",
                    "、".join(data["members"].get(str(t["exp_type"]), [])) or "上記以外のすべて"]
                   for t in types]
    member_table = table(["EXPタイプ", "必要EXP", "ポケモン"], member_rows, numeric=[1])
    nature_table = table(
        ["性格の補正", "性格"],
        [[EFFECT_JA[e], "、".join(data["natures"][e])] for e in ("up", "down")])
    candy_rows = []
    lower = 1
    for bracket in data["candy_exp"]:
        upper = bracket["below_level"]
        span = f"Lv{lower}〜{upper - 1}" if upper else f"Lv{lower}〜"
        candy_rows.append([span, bracket["up"], bracket["neutral"], bracket["down"]])
        lower = upper or lower
    candy_table = table(["今のレベル", "EXPアップ", "補正なし", "EXPダウン"], candy_rows,
                        numeric=[1, 2, 3], caption="アメ1個でもらえるEXP")

    return Article(
        takeaway=(f"Lv1→60のアメは600族で{dragon60['candies'] / normal60['candies']:.2f}倍、"
                  f"幻で{myth60['candies'] / normal60['candies']:.2f}倍。性格でも±2割ほど変わる。"),
        conclusion=conclusion,
        sections=[
            Section("タイプ別のかけら",
                    figure(shard_chart, "", legend(shard_series))),
            Section("性格によるアメの差", figure(candy_chart, "", legend(effect_series))),
            Section("くわしい数字", main_table),
            Section("どのポケモンがどのタイプ？", member_table + nature_table + candy_table),
        ],
        how_to_read=[
            "EXPタイプは、レベルアップに必要なEXPの多さです。600族は通常の1.5倍、伝説は1.8倍、幻は2.2倍のEXPが要ります。",
            "かけらは「アメの数×アメ1個のかけら」なので、アメが増えるとほぼ同じ割合で増えます。",
            "性格のEXP補正はアメ1個のEXPを変えます。EXPダウンの性格は、同じレベルにするのにアメが約2割多く要ります。",
            "表の色つきの行は、性格補正なしの基準の行です。",
        ],
        limits=["今のレベル内ですでに持っているEXPは0として数えるので、実際より少し多めに出ます。"],
    )


COLUMN = Column(slug="exp-types", order=30, title="経験値タイプで育成コストはどれだけ変わる？",
                generate=generate, build=build)
