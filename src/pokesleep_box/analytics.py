from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .core import EXTERNAL_TIER_JA
from .localization import to_english, to_japanese

# Mirrors the SS/S/A/B/C/D scale used by EXTERNAL_TIER_JA (core.py). Species
# missing from that snapshot rank last, since capture priority should favor
# species the external tier actually vouches for.
TIER_ORDER = ("SS", "S", "A", "B", "C", "D")

ISLANDS = {
    "シアンの砂浜": ("ORAN", "PAMTRE", "PECHA"),
    "トープ洞窟": ("FIGY", "LEPPA", "SITRUS"),
    "ウノハナ雪原": ("PERSIM", "RAWST", "WIKI"),
    "ラピスラズリ湖畔": ("CHERI", "DURIN", "MAGO"),
    "ゴールド旧発電所": ("BELUE", "BLUK", "GREPA"),
    "アンバー渓谷": ("CHESTO", "LUM", "YACHE"),
}
MODES = ("current", "50", "60", "70", "80")


def individual_label(item: Mapping[str, Any]) -> str:
    """Human-traceable label shared by every view that mentions an individual."""
    name = item.get("display_name") or item.get("species_ja") or item.get("species") or "不明"
    parts = [name]
    if item.get("box_index") is not None:
        parts.append(f"取込#{item['box_index']}")
    if item.get("level") is not None:
        parts.append(f"Lv{item['level']}")
    if item.get("sp") is not None:
        parts.append(f"SP {item['sp']}")
    return " · ".join(parts)


def _metric(value: Any) -> Optional[Dict[str, float]]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        expected = float(value)
        return {"expected": expected, "low": expected, "high": expected,
                "berry": expected, "ingredient": 0.0, "skill": 0.0}
    if not isinstance(value, Mapping):
        return None
    # Ingredient base energy can be included without assuming a particular
    # recipe. Recipe bonuses remain separate until recipe settings are known.
    berry = float(value.get("berry", 0) or 0)
    ingredient = float(value.get("ingredient", 0) or 0)
    skill = float(value.get("direct_skill", value.get("skill", 0)) or 0)
    expected = float(value.get("expected", value.get("energy", berry + ingredient + skill)) or 0)
    spread = float(value.get("spread", 0) or 0)
    return {"expected": expected, "low": float(value.get("low", expected - spread) or 0),
            "high": float(value.get("high", expected + spread) or 0),
            "berry": berry, "ingredient": ingredient, "skill": skill}


def _item_metric(item: Mapping[str, Any], island: str, mode: str) -> Optional[Dict[str, float]]:
    energy = item.get("energy_scores", {}).get(island, {}).get(mode)
    return _metric(energy)


def analyze(items: Sequence[Mapping[str, Any]], settings: Mapping[str, Any] = {},
            benchmarks: Sequence[Mapping[str, Any]] = (), team_size: int = 5,
            team_plans: Sequence[Mapping[str, Any]] = (),
            encounters: Mapping[str, Any] = {},
            capture_plans: Mapping[str, Any] = {}) -> Dict[str, Any]:
    bonuses = settings.get("areaBonusByIsland", {})
    default_bonus = float(settings.get("areaBonus", 0) or 0)
    forecasts = []
    membership: Dict[str, set] = {}
    for island in ISLANDS:
        modes = {}
        bonus = float(bonuses.get(island, default_bonus) or 0)
        factor = 1 + bonus / 100
        for mode in MODES:
            team_plan = next((p for p in team_plans if p.get("island") == island
                              and str(p.get("mode")) == mode and "total_energy" in p), None)
            if team_plan:
                members_by_uid = {x["uid"]: x for x in items}
                for member in team_plan.get("members", []):
                    membership.setdefault(member["uid"], set()).add(island)
                total = float(team_plan["total_energy"]) * factor
                berry = sum(float(x.get("berry", 0)) for x in team_plan.get("members", [])) * factor
                ingredient = sum(float(x.get("ingredient", 0)) for x in team_plan.get("members", [])) * factor
                skill = sum(float(x.get("direct_skill", 0)) for x in team_plan.get("members", [])) * factor
                cooking = float(team_plan.get("cooking", 0) or 0) * factor
                totals = {"expected": round(total), "low": round(total), "high": round(total),
                          "berry": round(berry), "ingredient": round(ingredient), "skill": round(skill),
                          "cooking": round(cooking)}
                modes[mode] = {"daily": totals, "weekly": {k: v * 7 for k, v in totals.items()},
                               "provisional": bool(team_plan.get("provisional")), "team_aware": True,
                               "synergy_gain": round(float(team_plan.get("synergy_gain", 0)) * factor),
                               "members": [{"uid": m["uid"],
                                            "name": (individual_label(members_by_uid[m["uid"]])
                                                     if m["uid"] in members_by_uid else m["uid"]),
                                            "energy": round(float(m.get("energy", 0)) * factor),
                                            "marginal": round(float(m.get("marginal", 0)) * factor),
                                            "recovery": m.get("recovery", 0),
                                            "team_help_support": m.get("team_help_support", 0),
                                            "subskills": m.get("subskills", [])}
                                           for m in team_plan.get("members", [])]}
                continue
            candidates = []
            for item in items:
                metric = _item_metric(item, island, mode)
                if metric:
                    candidates.append((metric["expected"], item, metric))
            selected = sorted(candidates, key=lambda x: (-x[0], x[1]["uid"]))[:team_size]
            if selected:
                for _, item, _ in selected:
                    membership.setdefault(item["uid"], set()).add(island)
                totals = {key: round(sum(m[key] for _, _, m in selected) * factor)
                          for key in ("expected", "low", "high", "berry", "ingredient", "skill")}
                modes[mode] = {"daily": totals, "weekly": {k: v * 7 for k, v in totals.items()},
                               "provisional": any(not x[1].get("verified") for x in selected),
                               "members": [{"uid": x[1]["uid"],
                                            "name": individual_label(x[1]),
                                            "energy": round(x[0] * factor)} for x in selected]}
        current = modes.get("current", {}).get("weekly", {}).get("expected", 0)
        lv60 = modes.get("60", {}).get("weekly", {}).get("expected", 0)
        strength = "未計算" if not current else ("十分強い" if current >= 700000 else
                    "あと1匹で伸びそう" if current >= 400000 else "戦力補強を推奨")
        forecasts.append({"island": island, "berries": ISLANDS[island], "area_bonus": bonus,
                          "modes": modes, "growth_to_60": lv60 - current, "diagnosis": strength})

    growth = []
    for item in items:
        best = None
        for island in ISLANDS:
            cur, future = _item_metric(item, island, "current"), _item_metric(item, island, "60")
            if cur and future:
                row = {"uid": item["uid"], "name": individual_label(item),
                       "species": item.get("species_ja") or item["species"], "island": island,
                       "daily_gain": round(future["expected"] - cur["expected"]),
                       "weekly_gain": round((future["expected"] - cur["expected"]) * 7),
                       "reasons": explain_strength(item, island)}
                if best is None or row["weekly_gain"] > best["weekly_gain"]:
                    best = row
        if best:
            growth.append(best)
    growth.sort(key=lambda x: -x["weekly_gain"])

    coverage = sorted([{"uid": item["uid"],
                        "name": individual_label(item),
                        "species": item.get("species_ja") or item["species"],
                        "islands": sorted(membership.get(item["uid"], set())),
                        "count": len(membership.get(item["uid"], set()))} for item in items],
                      key=lambda x: (-x["count"], x["name"]))

    general, tailored, favorites, by_island = capture_recommendations(
        items, forecasts, benchmarks)
    quality = {"total": len(items), "verified": sum(bool(x.get("verified")) for x in items),
               "unverified": sum(not x.get("verified") for x in items),
               "low_confidence": sum(not x.get("verified") and
                                     float(x.get("confidence", 0) or 0) < .8 for x in items),
               "sp_match": sum(x.get("sp_diff") == 0 and x.get("sp_computed") is not None for x in items)}
    tonight = capture_tonight(capture_plans, items, encounters,
                              int(settings.get("captureLevel") or CAPTURE_LEVEL))
    return {"forecasts": forecasts, "growth": growth, "coverage": coverage,
            "capture": {"general": general, "tailored": tailored, "favorites": favorites,
                        "by_island": by_island,
                        "encounter_updated": encounters.get("updated"),
                        # Engine-simulated capture candidates (see capture_tonight).
                        "tonight": tonight,
                        # The same candidates merged over every research area.
                        "all_fields": capture_all_fields(tonight),
                        "catch_level": int(settings.get("captureLevel") or CAPTURE_LEVEL),
                        "build": capture_plans.get("build"),
                        "sample": bool(capture_plans.get("sample"))}, "quality": quality}


# Level a wild catch is assumed to have when its training cost is estimated
# (settings key ``captureLevel``). Most of the cost lies above Lv30, so the
# estimate changes little between Lv5 and Lv20.
CAPTURE_LEVEL = 10
CAPTURE_STATUS_ORDER = ("good", "lucky", "skip")
SLEEP_TYPE_ORDER = ("うとうと", "すやすや", "ぐっすり")


def capture_species_rows(capture: Mapping[str, Any], items: Sequence[Mapping[str, Any]],
                         catch_level: int = CAPTURE_LEVEL) -> Dict[str, Dict[str, Any]]:
    """One display row per final-evolution species from a stored capture-gain document.

    ``status`` is ``good`` when a typical catch beats the box's own trained
    team on some island, ``lucky`` when only the ideal build does, and
    ``skip`` otherwise. ``cost`` is the candy/dream-shard cost from
    ``catch_level`` to the target level with a neutral nature, and
    ``gain_per_10k_shards`` is the weekly gain (typical, or ideal for
    ``lucky``) per 10,000 dream shards, comparable with the 育成 tab.
    """
    from .costs import training_cost

    # Replaced members are named by their final evolution: the comparison
    # team is the box trained (and evolved) to the target level.
    labels = {item["uid"]: to_japanese("species", item.get("final_evolution") or item.get("species") or "")
              for item in items}
    species_of = {item["uid"]: {item.get("species"), item.get("final_evolution")} for item in items}
    rows: Dict[str, Dict[str, Any]] = {}
    for entry in capture.get("species", []):
        options = [o for o in entry.get("options", []) if o.get("gain") is not None]
        if not options:
            continue
        typical = max(options, key=lambda o: (o["gain"], o.get("gain_ideal") or 0))
        ideal = max(options, key=lambda o: (o.get("gain_ideal") or 0, o["gain"]))
        if typical["gain"] > 0:
            status, best, gain = "good", typical, typical["gain"]
        elif (ideal.get("gain_ideal") or 0) > 0:
            status, best, gain = "lucky", ideal, ideal["gain_ideal"]
        else:
            status, best, gain = "skip", ideal, max(ideal.get("gain_ideal") or 0, typical["gain"])
        line = set(entry.get("line") or [entry.get("base")])
        owned = [item for item in items if item.get("species") in line
                 or item.get("final_evolution") in line]
        best_owned = max(owned, key=lambda x: float(x.get("absolute_score") or 0), default=None)
        target = int(entry.get("target") or 50)
        cost = training_cost(entry["species"], "Bashful", catch_level, target)
        shards = (cost or {}).get("dream_shards") or 0
        weekly = gain * 7
        efficiency = round(weekly / shards * 10000) if shards and gain > 0 else None
        helps = [o["island"] for o in options if o["gain"] > 0 or
                 (status != "good" and (o.get("gain_ideal") or 0) > 0)]
        replaced_uid = best.get("replaced") if status == "good" else best.get("replaced_ideal")
        replaced = labels.get(replaced_uid) if replaced_uid else None
        # Say whether the catch would upgrade an owned member of the same line
        # or join the team as an extra copy of a line already owned.
        if replaced_uid and species_of.get(replaced_uid, set()) & line:
            how, fit = "upgrade", f"（手持ちの{replaced}より強い個体として入れ替え）"
        elif owned and replaced:
            how, fit = "extra", f"（2匹目として{replaced}と交代）"
        elif owned:
            how, fit = "extra", "（2匹目として）"
        else:
            how, fit = "new", (f"（{replaced}と交代）" if replaced else "")
        if status == "good":
            reason = f"ふつうの個体でも、手持ちを育てた編成より{best['island']}で週+{weekly:,}" + fit
        elif status == "lucky":
            reason = (f"ふつうの個体なら手持ちを育てた方が強い。理想の性格・サブスキルなら"
                      f"{best['island']}で週+{weekly:,}" + fit)
        else:
            reason = "理想の個体でも、手持ちを育てた編成を超えない"
        rows[entry["species"]] = {
            "species_key": entry["species"], "base_key": entry.get("base"),
            "species": to_japanese("species", entry.get("base") or entry["species"]),
            "evolves_to": to_japanese("species", entry["species"]),
            "line": sorted(line), "specialty": entry.get("specialty"), "target": target,
            "status": status, "island": best["island"], "reason": reason,
            "gain_per_week": weekly,
            "typical_per_week": typical["gain"] * 7, "typical_island": typical["island"],
            "ideal_per_week": (ideal.get("gain_ideal") or 0) * 7,
            "now_per_week": (best.get("gain_now") or 0) * 7,
            "roles": best.get("roles", []), "provides": best.get("provides", []),
            "replaced": replaced, "fit": how if status != "skip" else None,
            "helps": helps, "cost": cost,
            "gain_per_10k_shards": efficiency,
            "owned_count": len(owned),
            "owned_score": (round(float(best_owned.get("absolute_score") or 0), 1)
                            if best_owned else None)}
    return rows


def capture_tonight(capture: Mapping[str, Any], items: Sequence[Mapping[str, Any]],
                    encounters: Mapping[str, Any], catch_level: int = CAPTURE_LEVEL
                    ) -> List[Dict[str, Any]]:
    """Capture candidates per research area and sleep type, from the stored capture gains.

    Pokémon of one evolution line are shown once (its first form's name),
    using its best final evolution; ``seen`` lists the forms that appear
    there. Rows are ordered good -> lucky -> skip, then by efficiency.
    """
    rows = capture_species_rows(capture, items, catch_level)
    if not rows:
        return []
    resolved = capture.get("names") or {}
    views = []
    for field, groups in (encounters.get("fields") or {}).items():
        sleep_types = []
        for sleep_type, listed in groups.items():
            seen_keys = [key for name in listed for key in resolved.get(name, [])]
            lines: Dict[str, List[Dict[str, Any]]] = {}
            for row in rows.values():
                seen = [key for key in seen_keys if key in row["line"]]
                if seen:
                    lines.setdefault(row["base_key"] or row["species_key"], []).append(
                        {**row, "seen": [to_japanese("species", key) for key in dict.fromkeys(seen)]})
            picked = []
            for choices in lines.values():
                choices.sort(key=_capture_order)
                best = dict(choices[0])
                best["others"] = [c["evolves_to"] for c in choices[1:]]
                picked.append(best)
            picked.sort(key=_capture_order)
            sleep_types.append({"sleep_type": sleep_type,
                                "good": [r for r in picked if r["status"] == "good"],
                                "lucky": [r for r in picked if r["status"] == "lucky"],
                                "skip": [r for r in picked if r["status"] == "skip"]})
        views.append({"field": field, "sleep_types": sleep_types})
    return views


def capture_all_fields(views: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Merge the per-area capture views into one list per sleep type.

    Each evolution line appears once per sleep type, using its best row by
    the same order as the per-area lists. ``fields`` lists the research
    areas where the line appears with that sleep type, and ``seen`` and
    ``others`` are merged over those areas.
    """
    merged: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for view in views:
        for group in view.get("sleep_types", []):
            lines = merged.setdefault(group["sleep_type"], {})
            for status in CAPTURE_STATUS_ORDER:
                for row in group.get(status, []):
                    key = row.get("base_key") or row["species_key"]
                    found = lines.get(key)
                    if found is None:
                        lines[key] = found = {"rows": [], "fields": [], "seen": [], "others": []}
                    found["rows"].append(row)
                    if view["field"] not in found["fields"]:
                        found["fields"].append(view["field"])
                    found["seen"] += [s for s in row.get("seen", []) if s not in found["seen"]]
                    found["others"] += [o for o in row.get("others", []) if o not in found["others"]]
    order = {name: i for i, name in enumerate(SLEEP_TYPE_ORDER)}
    result = []
    for sleep_type, lines in sorted(merged.items(),
                                    key=lambda kv: order.get(kv[0], len(order))):
        picked = []
        for found in lines.values():
            best = dict(min(found["rows"], key=_capture_order))
            best["fields"] = found["fields"]
            best["seen"] = found["seen"]
            best["others"] = [o for o in found["others"] if o != best.get("evolves_to")]
            picked.append(best)
        picked.sort(key=_capture_order)
        result.append({"sleep_type": sleep_type,
                       **{status: [r for r in picked if r["status"] == status]
                          for status in CAPTURE_STATUS_ORDER}})
    return result


def _capture_order(row: Mapping[str, Any]) -> tuple:
    efficiency = row.get("gain_per_10k_shards")
    return (CAPTURE_STATUS_ORDER.index(row["status"]), efficiency is None, -(efficiency or 0),
            -row.get("gain_per_week", 0), row["species_key"])


def explain_strength(item: Mapping[str, Any], island: str) -> list:
    raw = item.get("subskills")
    if raw is None and item.get("subskills_json"):
        raw = json.loads(item["subskills_json"])
    names = {entry[0] for entry in (raw or [])}
    reasons = []
    if "Berry Finding S" in names:
        reasons.append("きのみの数Sで、きのみ生産が強い")
    if "Helping Speed M" in names or "Helping Speed S" in names:
        reasons.append("おてつだいスピードで生産回数が増える")
    if "Skill Trigger M" in names or "Skill Trigger S" in names:
        reasons.append("スキル確率アップで直接スキルの期待値が高い")
    if item.get("berry") in ISLANDS.get(island, ()):
        reasons.append(f"{island}の好物きのみと一致")
    return reasons or ["外部計算エンジンの非料理エナジーが上位"]


def capture_recommendations(items, forecasts, benchmarks):
    # External tier is the primary sort key throughout: species this app has no
    # independent tier signal for rank last, even if their own Lv60 ideal energy
    # looks high, since the ask is to trust the external ranking first.
    tier_by_species = {to_english("species", ja): tier for ja, tier in EXTERNAL_TIER_JA.items()}

    def tier_rank(species_key: str) -> int:
        tier = tier_by_species.get(species_key)
        return TIER_ORDER.index(tier) if tier in TIER_ORDER else len(TIER_ORDER)

    def best_owned(species_key: str, island: str):
        """Return the best verified member of the benchmark's evolution family."""
        owned = [x for x in items
                 if x.get("verified")
                 and (x.get("final_evolution") or x["species"]) == species_key
                 and _item_metric(x, island, "60")]
        return max(owned, key=lambda x: _item_metric(x, island, "60")["expected"], default=None)

    def slot(row):
        """Competition slot: production role × berry type (the field-relevant type)."""
        return (row.get("specialty") or "unknown", row.get("berry") or "unknown")

    def best_owned_in_slot(candidate, island: str):
        same_slot_species = {x["species"] for x in benchmarks if slot(x) == slot(candidate)}
        owned = [x for x in items
                 if x.get("verified")
                 and (x.get("final_evolution") or x["species"]) in same_slot_species
                 and _item_metric(x, island, "60")]
        return max(owned, key=lambda x: _item_metric(x, island, "60")["expected"], default=None)

    general = []
    for island in ISLANDS:
        ranked = []
        for row in benchmarks:
            metric = _metric(row.get("island_scores", {}).get(island, {}).get("60"))
            if metric:
                species_key = row["species"]
                owned = best_owned(species_key, island)
                ranked.append({"species": row.get("species_ja") or to_japanese("species", species_key),
                               "species_key": species_key, "island": island,
                               "daily": round(metric["expected"]), "sample": bool(row.get("sample")),
                               "external_tier": tier_by_species.get(species_key),
                               "own_score": owned.get("absolute_score") if owned else None,
                               "berry": row.get("berry"),
                               "reason": "外部Tier上位・理想個体のLv60非料理エナジー上位"})
        ranked.sort(key=lambda x: (tier_rank(x["species_key"]), -x["daily"]))
        general.extend(ranked[:5])

    # Field-specific capture board. Only the theoretical leader in each
    # specialty×berry slot remains eligible, preventing a weaker duplicate
    # species from being recommended merely because the box does not own it.
    by_island = []
    for island in ISLANDS:
        available = []
        for row in benchmarks:
            metric = _metric(row.get("island_scores", {}).get(island, {}).get("60"))
            if metric:
                available.append((row, round(metric["expected"])))
        leaders = {}
        for row, daily in available:
            key = slot(row)
            if key not in leaders or daily > leaders[key][1]:
                leaders[key] = (row, daily)

        priority, skipped = [], []
        for row, daily in available:
            species_key, key = row["species"], slot(row)
            leader, leader_daily = leaders[key]
            base = {"species": row.get("species_ja") or to_japanese("species", species_key),
                    "species_key": species_key, "island": island, "daily": daily,
                    "specialty": row.get("specialty"), "berry": row.get("berry"),
                    "fits_island": row.get("berry") in ISLANDS.get(island, ()),
                    "external_tier": tier_by_species.get(species_key),
                    "sample": bool(row.get("sample"))}
            if leader["species"] != species_key:
                gap = round((leader_daily / daily - 1) * 100) if daily else 0
                skipped.append({**base, "reason":
                                f"同じ役割×タイプでは{leader.get('species_ja') or to_japanese('species', leader['species'])}が{gap}%上"})
                continue
            family_owned = best_owned(species_key, island)
            if family_owned and float(family_owned.get("absolute_score") or 0) >= 80:
                skipped.append({**base, "own_score": family_owned.get("absolute_score"),
                                "reason": "同じ進化系に80点以上の理想級個体を所持"})
                continue
            slot_owned = best_owned_in_slot(row, island)
            owned_daily = (_item_metric(slot_owned, island, "60")["expected"]
                           if slot_owned else 0)
            if owned_daily >= daily * .9:
                skipped.append({**base, "own_score": slot_owned.get("absolute_score"),
                                "reason": f"手持ちの同じ役割×タイプが理想値の{round(owned_daily/daily*100)}%"})
                continue
            improvement = round(max(0, daily - owned_daily))
            priority.append({**base, "own_score": slot_owned.get("absolute_score") if slot_owned else None,
                             "improvement": improvement,
                             "reason": ("同枠未所持" if not slot_owned else
                                        f"同枠戦力を1日{improvement:,}改善")})
        priority.sort(key=lambda x: (not x["fits_island"], tier_rank(x["species_key"]),
                                     -x["improvement"], -x["daily"]))
        skipped.sort(key=lambda x: (not x["fits_island"], tier_rank(x["species_key"]), -x["daily"]))
        by_island.append({"island": island, "priority": priority[:5], "skip": skipped[:5]})

    weakest = sorted(forecasts, key=lambda x: x["modes"].get("60", {}).get("weekly", {}).get("expected", 0))
    # Keyed by species so the same species recommended for several weak
    # islands (e.g. it fits more than one island's forecast) surfaces once,
    # with every relevant island listed together, instead of one row per island.
    tailored_by_species: Dict[str, Dict[str, Any]] = {}
    for forecast in weakest:
        island = forecast["island"]
        island_berries = ISLANDS.get(island, ())
        candidates = []
        for candidate in (x for x in general if x["island"] == island):
            owned = best_owned(candidate["species_key"], island)
            owned_value = _item_metric(owned, island, "60")["expected"] if owned else 0
            owned_is_ideal = bool(owned and float(owned.get("absolute_score") or 0) >= 80.0)
            if not owned_is_ideal and (not owned_value or owned_value < candidate["daily"] * .8):
                fits_island = candidate.get("berry") in island_berries
                reason = (f"{island}の戦力穴を埋める未所持候補" if not owned_value else
                          f"所持個体が理想値の{round(owned_value/candidate['daily']*100)}%のため更新候補")
                if fits_island:
                    reason += f"・{island}の固定きのみに合う"
                candidates.append({**candidate, "reason": reason,
                                   "improvement": round(max(0, candidate["daily"] - owned_value)),
                                   "own_score": owned.get("absolute_score") if owned else None,
                                   "fits_island": fits_island})
        candidates.sort(key=lambda x: (tier_rank(x["species_key"]), not x["fits_island"], -x["improvement"]))
        for candidate in candidates[:2]:
            key = candidate["species_key"]
            existing = tailored_by_species.get(key)
            if existing is None:
                tailored_by_species[key] = {**candidate, "islands": [island]}
            elif candidate["improvement"] > existing["improvement"]:
                tailored_by_species[key] = {**candidate, "islands": existing["islands"] + [island]}
            else:
                existing["islands"].append(island)
    tailored = sorted(tailored_by_species.values(),
                      key=lambda x: (tier_rank(x["species_key"]), -x["improvement"]))

    # A separate, tier-independent reference: the box's own best-evaluated
    # individuals, so the external-tier-driven lists above can be sanity
    # checked against what this app itself rates highly.
    favorites = [{"uid": x["uid"], "species": x.get("species_ja") or x["species"],
                 "species_key": x["species"], "score": x["absolute_score"],
                 "reason": "本アプリの個体評価が高い手持ちです"}
                for x in sorted((i for i in items if i.get("verified") and i.get("absolute_score")),
                                key=lambda i: -i["absolute_score"])[:8]]
    return general, tailored, favorites, by_island
