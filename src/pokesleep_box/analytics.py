from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Optional, Sequence

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
            encounters: Mapping[str, Any] = {}) -> Dict[str, Any]:
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

    general, tailored, favorites, by_island, encounter_views = capture_recommendations(
        items, forecasts, benchmarks, encounters)
    quality = {"total": len(items), "verified": sum(bool(x.get("verified")) for x in items),
               "unverified": sum(not x.get("verified") for x in items),
               "low_confidence": sum(not x.get("verified") and
                                     float(x.get("confidence", 0) or 0) < .8 for x in items),
               "sp_match": sum(x.get("sp_diff") == 0 and x.get("sp_computed") is not None for x in items)}
    return {"forecasts": forecasts, "growth": growth, "coverage": coverage,
            "capture": {"general": general, "tailored": tailored, "favorites": favorites,
                        "by_island": by_island, "encounter_views": encounter_views,
                        "encounter_updated": encounters.get("updated")}, "quality": quality}


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


def capture_recommendations(items, forecasts, benchmarks, encounters={}):
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

    def normalized_name(value):
        return "".join(ch for ch in str(value or "").lower() if ch.isalnum())

    # What to catch tonight: restrict the candidates to unevolved Pokémon that
    # actually appear at the selected research area and sleep type. A seed with
    # branching evolutions is shown once, using its best useful destination.
    encounter_views = []
    fields = encounters.get("fields", {}) if isinstance(encounters, Mapping) else {}
    for sleeping_field, sleep_groups in fields.items():
        groups = []
        for sleep_type, encounter_names in sleep_groups.items():
            available_seeds = {normalized_name(x) for x in encounter_names}
            seed_rows = {}
            skipped_seeds = {}
            for candidate in benchmarks:
                base_en = candidate.get("base_species_en")
                if not base_en or normalized_name(base_en) not in available_seeds:
                    continue
                base_key = candidate.get("base_species") or candidate["species"]
                base_name = (candidate.get("base_species_ja") or
                             to_japanese("species", base_key))
                best = None
                reject_reason = None
                for target_field in ISLANDS:
                    metric = _metric(candidate.get("island_scores", {}).get(target_field, {}).get("60"))
                    if not metric:
                        continue
                    daily = round(metric["expected"])
                    peers = [(row, _metric(row.get("island_scores", {}).get(target_field, {}).get("60")))
                             for row in benchmarks if slot(row) == slot(candidate)]
                    peers = [(row, score) for row, score in peers if score]
                    leader, leader_metric = max(peers, key=lambda x: x[1]["expected"])
                    if leader["species"] != candidate["species"]:
                        reject_reason = (f"同枠では{leader.get('species_ja') or to_japanese('species', leader['species'])}が上")
                        continue
                    family_owned = best_owned(candidate["species"], target_field)
                    if family_owned and float(family_owned.get("absolute_score") or 0) >= 80:
                        reject_reason = "進化先と同じ系統に80点以上を所持"
                        continue
                    slot_owned = best_owned_in_slot(candidate, target_field)
                    owned_daily = (_item_metric(slot_owned, target_field, "60")["expected"]
                                   if slot_owned else 0)
                    if owned_daily >= daily * .9:
                        reject_reason = f"手持ちの同枠戦力が理想値の{round(owned_daily/daily*100)}%"
                        continue
                    row = {"species": base_name, "species_key": base_key,
                           "evolves_to": candidate.get("species_ja") or to_japanese("species", candidate["species"]),
                           "target_island": target_field, "daily": daily,
                           "improvement": round(max(0, daily - owned_daily)),
                           "specialty": candidate.get("specialty"), "berry": candidate.get("berry"),
                           "external_tier": tier_by_species.get(candidate["species"]),
                           "tier_rank": tier_rank(candidate["species"]),
                           "own_score": slot_owned.get("absolute_score") if slot_owned else None}
                    ranking = (-row["improvement"], tier_rank(candidate["species"]), -daily)
                    if best is None or ranking < best[0]:
                        best = (ranking, row)
                if best:
                    current = seed_rows.get(base_key)
                    if current is None or best[0] < current[0]:
                        seed_rows[base_key] = best
                elif reject_reason:
                    skipped_seeds[base_key] = {"species": base_name, "reason": reject_reason}
            priorities = [x[1] for x in seed_rows.values()]
            priorities.sort(key=lambda x: (-x["improvement"], x["tier_rank"], -x["daily"]))
            groups.append({"sleep_type": sleep_type, "priority": priorities[:5],
                           "skip": list(skipped_seeds.values())[:5]})
        encounter_views.append({"field": sleeping_field, "sleep_types": groups})
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
    return general, tailored, favorites, by_island, encounter_views
