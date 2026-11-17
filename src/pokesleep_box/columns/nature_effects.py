"""Column: how much does a nature change daily energy, per specialty?

Runs the engine's per-individual production (``evaluate`` mode,
``energyScores``) for two example species of each specialty with every
non-neutral nature and one neutral nature, at Lv50 and Lv60. EXP natures
change only the training cost, which comes from the cost tables.
"""

from __future__ import annotations

from statistics import mean
from typing import Any, Dict, List, Sequence

from .. import costs
from ..localization import SUBSKILL_UNLOCKS, names
from . import Article, Column, Section
from .lv60_worth import BUILDS
from .page import figure, table
from .svg import WIDTH, _frame, _Plot, _uid, esc, fmt_int, legend

# Two final evolutions per specialty, all with a strength-type main skill.
SPECIES = ["RAICHU", "HOUNDOOM", "GENGAR", "ABSOL", "AMPHAROS", "ESPEON"]
SPECIALTIES = ["berry", "ingredient", "skill"]
SPECIALTY_JA = {"berry": "きのみタイプ", "ingredient": "食材タイプ", "skill": "スキルタイプ"}
# (stat raised, stat lowered) for every nature with an effect, as in the
# engine's nature table (Neroli's Lab common/src/types/nature/nature.ts).
NATURES = {
    "Lonely": ("speed", "energy"), "Adamant": ("speed", "ingredient"),
    "Naughty": ("speed", "skill"), "Brave": ("speed", "exp"),
    "Bold": ("energy", "speed"), "Impish": ("energy", "ingredient"),
    "Lax": ("energy", "skill"), "Relaxed": ("energy", "exp"),
    "Modest": ("ingredient", "speed"), "Mild": ("ingredient", "energy"),
    "Rash": ("ingredient", "skill"), "Quiet": ("ingredient", "exp"),
    "Calm": ("skill", "speed"), "Gentle": ("skill", "energy"),
    "Careful": ("skill", "ingredient"), "Sassy": ("skill", "exp"),
    "Timid": ("exp", "speed"), "Hasty": ("exp", "energy"),
    "Jolly": ("exp", "ingredient"), "Naive": ("exp", "skill"),
}
NEUTRAL = "Serious"
STATS = ["speed", "ingredient", "skill", "energy", "exp"]
STAT_JA = {"speed": "おてつだいスピード", "ingredient": "食材おてつだい確率",
           "skill": "メインスキル発生確率", "energy": "げんき回復量", "exp": "EXP獲得量"}
STAT_SHORT = {"speed": "スピード", "ingredient": "食材確率", "skill": "スキル確率",
              "energy": "げんき回復", "exp": "EXP"}
# How much each stat moves in the game (engine nature table), for the text.
STAT_SIZE = {"speed": ("おてつだい時間 -10%", "おてつだい時間 +7.5%"),
             "ingredient": ("+20%", "-20%"), "skill": ("+20%", "-20%"),
             "energy": ("+20%", "-12%"), "exp": ("+18%", "-18%")}
LEVELS = [50, 60]
ITERATIONS = 1000   # engine runs; only species whose skill is simulated randomly vary (about +-2% between runs)


def generate(ctx) -> Dict[str, Any]:
    meta = ctx.engine({"mode": "metadata"})["pokemon"]
    natures = [NEUTRAL, *NATURES]
    instances = []
    for species in SPECIES:
        info = meta[species]
        _, subskills = BUILDS[info["specialty"]]
        for nature in natures:
            instances.append({"uid": f"{species}:{nature}", "instance": {
                "species": species, "level": LEVELS[0], "nature": nature,
                "subskills": [[name, unlock] for name, unlock in zip(subskills, SUBSKILL_UNLOCKS)],
                "ingredients": [slot["choices"][0] for slot in info["ingredients"]],
                "skillLevel": 99, "ribbon": 0}})
    # Every species makes only its own berry, so one island that likes all
    # of their berries is the same as each one's favorite island.
    favorite = sorted({meta[s]["berry"] for s in SPECIES})
    response = ctx.engine({"mode": "evaluate", "anchors": LEVELS, "iterations": ITERATIONS,
                           "islands": {"favorite": favorite}, "instances": instances})
    by_uid = {r["uid"]: r for r in response["results"]}
    ja = names()
    rows = []
    for species in SPECIES:
        info = meta[species]
        nature0, subskills = BUILDS[info["specialty"]]
        energy = {}
        for nature in natures:
            scores = by_uid[f"{species}:{nature}"]["energyScores"]["favorite"]
            energy[nature] = {str(level): {k: scores[str(level)][k]
                                           for k in ("berry", "ingredient", "direct_skill", "expected")}
                              for level in LEVELS}
        rows.append({
            "species": species,
            "species_ja": ja["species"].get(species, species),
            "specialty": info["specialty"],
            "main_skill": ja["mainskills"].get(info["main_skill"], info["main_skill"]),
            "berry": ja["berries"].get(info["berry"], info["berry"]),
            "subskills": [[ja["subskills"].get(n, n), u] for n, u in zip(subskills, SUBSKILL_UNLOCKS)],
            "ingredients": [[ja["ingredients"].get(n, n), a]
                            for n, a in (slot["choices"][0] for slot in info["ingredients"])],
            "energy": energy,
        })
    exp_costs = {}
    for nature in (NEUTRAL, "Timid", "Quiet"):
        exp_costs[nature] = {f"{a}-{b}": {k: v for k, v in costs.training_cost("RAICHU", nature, a, b).items()
                                          if k in ("candies", "dream_shards")}
                             for a, b in ((1, 50), (50, 60), (1, 60))}
    return {
        "data": {
            "levels": LEVELS, "iterations": ITERATIONS, "neutral": NEUTRAL,
            "natures": {n: {"up": up, "down": down, "ja": ja["natures"].get(n, n)}
                        for n, (up, down) in NATURES.items()},
            "neutral_ja": ja["natures"].get(NEUTRAL, NEUTRAL),
            "species": rows,
            "exp_costs": exp_costs,
        },
        "assumptions": [
            "1日のエナジーは計算エンジン（Neroli's Lab）の1匹ぶんの見積もりです。きのみ＋食材（素材の基本エナジー）＋メインスキルのエナジーの合計です。",
            "きのみが好物になる島（好物2倍）、睡眠は22:00〜6:00、リボンなし、おてつだいサポートなしで計算しています。",
            "性格以外（サブスキル、食材、スキルレベル最大）は同じにして、性格だけを21通り変えています。補正なしの性格はまじめで代表させています（補正なしの5つは同じ値です）。",
            "食材は料理にしたときの倍率を入れていません。料理まで考えると、食材タイプにとっての食材確率の重みはもっと大きくなります。",
            "げんき回復の補正は、この1匹自身のげんきの戻り方だけに効くものとして計算されています。",
            "EXPの補正は1日のエナジーには効きません。育てるのに使うアメとかけらの量だけが変わります（アメだけで育てたとき）。",
            "ここで使った6匹（メインスキルはエナジーチャージ）は、同じ条件なら毎回同じ数字になります（乱数のぶれはありません）。ただし1%前後の小さな差は、サブスキルや食材を少し変えると入れかわることがあります。",
        ],
        "sources": [
            "Neroli's Lab（計算エンジン、engine/src/cli.mjs の evaluate / metadata モード）",
            "Neroli's Lab の性格の表（common/src/types/nature/nature.ts）",
            "src/pokesleep_box/training_costs.json（アメ・かけらの表）",
        ],
    }


def _pct(row: Dict[str, Any], nature: str, level: int, neutral: str) -> float:
    base = row["energy"][neutral][str(level)]["expected"]
    return (row["energy"][nature][str(level)]["expected"] / base - 1) * 100


def _avg_pct(rows: List[Dict[str, Any]], nature: str, level: int, neutral: str) -> float:
    return mean(_pct(r, nature, level, neutral) for r in rows)


def diverging_bar_chart(categories: Sequence[str], series: Sequence[Dict], *, title: str,
                        desc: str, y_label: str, height: int = 320) -> str:
    """Grouped bars that start at zero and may go below it (``bar_chart``
    always starts at the bottom of the axis). Values are percent changes."""
    values = [v for s in series for v in s["values"]]
    plot = _Plot(height, max(max(values), 0.5), min(min(values), 0))
    zero = plot.y(0)
    group_w = plot.inner_w / len(categories)
    gap = 2
    bar_w = (group_w * 0.78) / len(series) - gap
    parts = [plot.grid(lambda v: f"{v:+.0f}" if v else "0", y_label),
             f'<line class="marker" x1="{plot.left}" x2="{WIDTH - plot.right}" '
             f'y1="{zero:.1f}" y2="{zero:.1f}" style="stroke-dasharray:none"/>']
    for index, category in enumerate(categories):
        x0 = plot.left + group_w * index + (group_w - (bar_w + gap) * len(series) + gap) / 2
        for s_index, s in enumerate(series):
            value = s["values"][index]
            x = x0 + s_index * (bar_w + gap)
            top, bottom = sorted((plot.y(value), zero))
            parts.append(f'<rect class="bar s{int(s.get("slot", 1))}" x="{x:.1f}" y="{top:.1f}" '
                         f'width="{bar_w:.1f}" height="{bottom - top:.1f}" rx="3">'
                         f'<title>{esc(category)} {esc(s["name"])}: {esc(_signed(value))}</title></rect>')
            label_y = top - 5 if value >= 0 else bottom + 14
            parts.append(f'<text class="value" x="{x + bar_w / 2:.1f}" y="{label_y:.1f}" '
                         f'text-anchor="middle">{esc(f"{value:+.1f}" if round(value, 1) else "0")}</text>')
        parts.append(f'<text class="tick" x="{plot.left + group_w * (index + .5):.1f}" '
                     f'y="{plot.top + plot.inner_h + 18}" text-anchor="middle">{esc(category)}</text>')
    return _frame(title, desc, height, "".join(parts), _uid(title))


def _signed(value: float) -> str:
    """'+1.2%' / '-0.4%', with no '-0.0%'."""
    rounded = round(value, 1) or 0.0
    return f"{rounded:+.1f}%"


def _pure(natures: Dict[str, Dict[str, str]], stat: str, side: str) -> str:
    """The nature that moves only ``stat`` (its other side is EXP, which does
    not change energy)."""
    other = "down" if side == "up" else "up"
    return next(n for n, v in natures.items() if v[side] == stat and v[other] == "exp")


def build(doc: Dict[str, Any]) -> Article:
    data = doc["data"]
    neutral = data["neutral"]
    natures = data["natures"]
    rows = data["species"]
    by_spec = {s: [r for r in rows if r["specialty"] == s] for s in SPECIALTIES}
    energy_stats = [s for s in STATS if s != "exp"]

    def ja(n: str) -> str:
        return data["neutral_ja"] if n == neutral else natures[n]["ja"]

    def label(n: str) -> str:
        if n == neutral:
            return f"{ja(n)}（補正なし）"
        return f"{ja(n)}（{STAT_SHORT[natures[n]['up']]}↑ {STAT_SHORT[natures[n]['down']]}↓）"

    # Effect of raising / lowering one stat alone, from the natures paired with EXP.
    up = {s: {st: _avg_pct(by_spec[s], _pure(natures, st, "up"), 60, neutral) for st in energy_stats}
          for s in SPECIALTIES}
    down = {s: {st: -_avg_pct(by_spec[s], _pure(natures, st, "down"), 60, neutral) for st in energy_stats}
            for s in SPECIALTIES}
    ranked = {s: sorted(natures, key=lambda n: _avg_pct(by_spec[s], n, 60, neutral), reverse=True)
              for s in SPECIALTIES}
    best = {s: ranked[s][0] for s in SPECIALTIES}
    worst = {s: ranked[s][-1] for s in SPECIALTIES}
    spread = {s: _avg_pct(by_spec[s], best[s], 60, neutral) - _avg_pct(by_spec[s], worst[s], 60, neutral)
              for s in SPECIALTIES}
    lv_gap = max(abs(_avg_pct(by_spec[s], n, 50, neutral) - _avg_pct(by_spec[s], n, 60, neutral))
                 for s in SPECIALTIES for n in natures)

    def top_up(s: str) -> str:
        st = max(energy_stats, key=lambda x: up[s][x])
        return STAT_JA[st]

    exp = data["exp_costs"]
    shards = {n: exp[n]["50-60"]["dream_shards"] for n in exp}
    candies = {n: exp[n]["1-60"]["candies"] for n in exp}

    def avg(s: str, n: str) -> float:
        return _avg_pct(by_spec[s], n, 60, neutral)

    speed_loss = [down[s]["speed"] for s in SPECIALTIES]
    energy_moves = [v for s in SPECIALTIES for v in (up[s]["energy"], -down[s]["energy"])]
    quiet = _pure(natures, "ingredient", "up")
    conclusion = [
        f"いちばん良い性格と悪い性格の差（Lv60）は、きのみタイプ{spread['berry']:.0f}%、"
        f"食材タイプ{spread['ingredient']:.0f}%、スキルタイプ{spread['skill']:.0f}%です。",
        f"きのみタイプは{label(best['berry'])}が1位（補正なしより{avg('berry', best['berry']):+.0f}%）。"
        f"いちばん効くのは{top_up('berry')}です。食材確率が下がるのはきのみタイプにはむしろ得です"
        "（食材の代わりにきのみを持ち帰るため）。",
        f"食材タイプも、料理の倍率を入れない1日のエナジーでは{label(best['ingredient'])}が1位"
        f"（{avg('ingredient', best['ingredient']):+.0f}%）で、食材確率だけが上がる{label(quiet)}は"
        f"{avg('ingredient', quiet):+.0f}%です。料理に使う食材の価値を重く見るなら、食材確率↑の性格の順位は上がります。",
        f"スキルタイプは{label(best['skill'])}が1位（{avg('skill', best['skill']):+.0f}%）。"
        f"{top_up('skill')}が上がる性格が上位で、これが下がる性格は{avg('skill', worst['skill']):+.0f}%まで落ちます。",
        f"どのタイプでも、おてつだいスピードが下がる性格は{min(speed_loss):.0f}〜{max(speed_loss):.0f}%の損です。"
        f"げんき回復の上下は、1匹だけで見ると{min(energy_moves):+.0f}〜{max(energy_moves):+.0f}%です。",
        f"EXPの補正はエナジーには効かず、育成コストだけが変わります。Lv50→60のかけらは補正なし{fmt_int(shards[neutral])}、"
        f"EXPアップ{fmt_int(shards['Timid'])}、EXPダウン{fmt_int(shards['Quiet'])}です。",
        f"性格による差の割合は、Lv50とLv60でほとんど同じです（ずれは最大{lv_gap:.1f}ポイント）。",
    ]

    def effect_chart(values: Dict[str, Dict[str, float]], sign: int, title: str, desc: str) -> str:
        series = [{"name": SPECIALTY_JA[s], "slot": i + 1,
                   "values": [round(sign * values[s][st], 1) for st in energy_stats]}
                  for i, s in enumerate(SPECIALTIES)]
        svg = diverging_bar_chart([STAT_SHORT[st] for st in energy_stats], series, title=title,
                                  desc=desc, y_label="補正なしと比べた変化（%）")
        return figure(svg, "", legend(series))

    up_html = effect_chart(up, 1, "1つの力だけが上がったとき（Lv60）",
                           "4つの力それぞれについて、それだけが上がる性格で1日のエナジーが何%変わるかを、タイプ別に比べた棒グラフ。")
    down_html = effect_chart(down, -1, "1つの力だけが下がったとき（Lv60）",
                             "4つの力それぞれについて、それだけが下がる性格で1日のエナジーが何%変わるかを、タイプ別に比べた棒グラフ。")
    pure_table = ""
    for side, values, sign, caption in (
            ("up", up, 1, "1つの力だけが上がる性格（EXPが下がる性格）での1日のエナジーの変化（Lv60、2匹の平均）"),
            ("down", down, -1, "1つの力だけが下がる性格（EXPが上がる性格）での1日のエナジーの変化（Lv60、2匹の平均）")):
        side_rows = [[STAT_JA[st], STAT_SIZE[st][0 if side == "up" else 1], ja(_pure(natures, st, side)),
                      *[_signed(sign * values[s][st]) for s in SPECIALTIES]] for st in energy_stats]
        pure_table += table(["力", "変わる幅", "性格", "きのみタイプ", "食材タイプ", "スキルタイプ"], side_rows,
                            numeric=[3, 4, 5], caption=caption)

    rank_sections = []
    for s in SPECIALTIES:
        group = by_spec[s]
        order = [*ranked[s][:len(natures)]]
        neutral_index = next(i for i, n in enumerate(order)
                             if _avg_pct(group, n, 60, neutral) < 0)
        order.insert(neutral_index, neutral)
        table_rows = []
        for n in order:
            row = [label(n), _signed(_avg_pct(group, n, 50, neutral)),
                   _signed(_avg_pct(group, n, 60, neutral))]
            row += [fmt_int(r["energy"][n]["60"]["expected"]) for r in group]
            table_rows.append(row)
        rank_sections.append(Section(
            f"{SPECIALTY_JA[s]}の性格ランキング",
            table(["性格", "Lv50", "Lv60", *[f"{r['species_ja']} Lv60" for r in group]], table_rows,
                  numeric=list(range(1, 3 + len(group))),
                  highlight=[order.index(neutral)],
                  caption="補正なしと比べた1日のエナジーの変化（2匹の平均）と、各ポケモンのLv60の1日のエナジー")))

    exp_rows = []
    for n, effect in ((neutral, "補正なし"), ("Timid", "EXPアップ"), ("Quiet", "EXPダウン")):
        c = exp[n]
        exp_rows.append([effect, ja(n), fmt_int(c["1-60"]["candies"]), fmt_int(c["50-60"]["candies"]),
                         fmt_int(c["50-60"]["dream_shards"]), fmt_int(c["1-60"]["dream_shards"])])
    exp_table = table(["EXP補正", "性格の例", "アメ Lv1→60", "アメ Lv50→60", "かけら Lv50→60", "かけら Lv1→60"],
                      exp_rows, numeric=[2, 3, 4, 5],
                      caption="通常EXPタイプのポケモンをアメだけで育てたとき")

    build_rows = [[r["species_ja"], SPECIALTY_JA[r["specialty"]], r["main_skill"], r["berry"],
                   "、".join(f"{n}(Lv{u})" for n, u in r["subskills"]),
                   "・".join(f"{n}×{a}" for n, a in r["ingredients"])] for r in rows]
    builds = table(["ポケモン", "タイプ", "メインスキル", "きのみ", "サブスキル（開くレベル）", "食材（Lv1/30/60）"],
                   build_rows)

    return Article(
        takeaway=(f"良い性格と悪い性格の差はLv60で{min(spread.values()):.0f}〜{max(spread.values()):.0f}%。"
                  "スピードを下げない性格を選ぶのが基本です。"),
        conclusion=conclusion,
        sections=[
            Section("1つの力だけを動かしたとき", up_html + down_html + pure_table),
            *rank_sections,
            Section("EXP補正は育成コストに効く", exp_table + "<p>EXPアップの性格は、同じかけらで少し早くLv60に届きます。"
                    f"Lv1→60のアメは補正なし{fmt_int(candies[neutral])}個に対して、EXPアップは{fmt_int(candies['Timid'])}個です。"
                    "ただし、EXPアップの性格はほかの力が1つ下がります（たとえばおくびょうはスピードが下がります）。</p>"),
            Section("計算に使った個体", builds + "<p>性格以外はすべて同じにしています。</p>"),
        ],
        how_to_read=[
            "性格は1つの力を上げ、別の1つを下げます。EXPは1日のエナジーに関係しないので、「EXPが下がる性格」を見れば上がる側だけの効果が、「EXPが上がる性格」を見れば下がる側だけの効果がわかります。1つめと2つめの図はこの考え方で作っています。",
            "棒が0の線から上なら1日のエナジーが増え、下なら減ります。長い棒ほど、その力の上下がよく効きます。きのみタイプで食材確率が下がると増えるのは、食材の代わりにきのみを持ち帰るためです。",
            "ランキングの表の％は、補正なし（色つきの行）と比べた1日のエナジーの増減です。同じタイプの2匹の平均です。",
            "表の右側は、それぞれのポケモンのLv60での1日のエナジー（きのみ＋食材＋スキル）です。",
        ],
        limits=[
            "1匹だけの見積もりです。チームの中での役割（げんき回復やおてつだいサポート）は入っていません。げんき回復の補正は、チーム全体のげんきの持ち方によって効き方が変わります。",
            "食材の価値は素材の基本エナジーで数えています。料理のレシピの倍率を入れると、食材確率の上下は食材タイプでもっと大きく効きます。",
            "サブスキルの組み合わせが違えば、性格の効き方も少し変わります。",
        ],
    )


COLUMN = Column(slug="nature-effects", order=50, title="性格はどれだけ効く？",
                generate=generate, build=build, uses_engine=True)
