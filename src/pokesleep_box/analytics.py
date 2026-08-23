from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Optional, Sequence

from .localization import to_japanese

ISLANDS = {
    "シアンの砂浜": ("ORAN", "PAMTRE", "PECHA"),
    "トープ洞窟": ("FIGY", "LEPPA", "SITRUS"),
    "ウノハナ雪原": ("PERSIM", "RAWST", "WIKI"),
    "ラピスラズリ湖畔": ("CHERI", "DURIN", "MAGO"),
    "ゴールド旧発電所": ("BELUE", "BLUK", "GREPA"),
    "アンバー渓谷": ("CHESTO", "LUM", "YACHE"),
}
MODES = ("current", "50", "60", "70", "80")


def _metric(value: Any) -> Optional[Dict[str, float]]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        expected = float(value)
        return {"expected": expected, "low": expected, "high": expected,
                "berry": expected, "skill": 0.0}
    if not isinstance(value, Mapping):
        return None
    # Explicit cooking-free energy takes precedence. Ingredients and cooking are
    # deliberately never added here.
    berry = float(value.get("berry", 0) or 0)
    skill = float(value.get("direct_skill", value.get("skill", 0)) or 0)
    expected = float(value.get("expected", value.get("energy", berry + skill)) or 0)
    spread = float(value.get("spread", 0) or 0)
    return {"expected": expected, "low": float(value.get("low", expected - spread) or 0),
            "high": float(value.get("high", expected + spread) or 0),
            "berry": berry, "skill": skill}


def _item_metric(item: Mapping[str, Any], island: str, mode: str) -> Optional[Dict[str, float]]:
    energy = item.get("energy_scores", {}).get(island, {}).get(mode)
    return _metric(energy)


def analyze(items: Sequence[Mapping[str, Any]], settings: Mapping[str, Any] = {},
            benchmarks: Sequence[Mapping[str, Any]] = (), team_size: int = 5) -> Dict[str, Any]:
    bonuses = settings.get("areaBonusByIsland", {})
    default_bonus = float(settings.get("areaBonus", 0) or 0)
    forecasts = []
    membership: Dict[str, set] = {}
    for island in ISLANDS:
        modes = {}
        bonus = float(bonuses.get(island, default_bonus) or 0)
        factor = 1 + bonus / 100
        for mode in MODES:
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
                          for key in ("expected", "low", "high", "berry", "skill")}
                modes[mode] = {"daily": totals, "weekly": {k: v * 7 for k, v in totals.items()},
                               "provisional": any(not x[1].get("verified") for x in selected),
                               "members": [{"uid": x[1]["uid"],
                                            "name": x[1].get("display_name") or x[1].get("species_ja") or x[1]["species"],
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
                row = {"uid": item["uid"], "name": item.get("display_name") or item.get("species_ja") or item["species"],
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
                        "name": item.get("display_name") or item.get("species_ja") or item["species"],
                        "species": item.get("species_ja") or item["species"],
                        "islands": sorted(membership.get(item["uid"], set())),
                        "count": len(membership.get(item["uid"], set()))} for item in items],
                      key=lambda x: (-x["count"], x["name"]))

    general, tailored = capture_recommendations(items, forecasts, benchmarks)
    quality = {"total": len(items), "verified": sum(bool(x.get("verified")) for x in items),
               "unverified": sum(not x.get("verified") for x in items),
               "low_confidence": sum(float(x.get("confidence", 0) or 0) < .995 for x in items),
               "sp_match": sum(x.get("sp_diff") == 0 and x.get("sp_computed") is not None for x in items)}
    return {"forecasts": forecasts, "growth": growth, "coverage": coverage,
            "capture": {"general": general, "tailored": tailored}, "quality": quality}


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
    general = []
    for island in ISLANDS:
        ranked = []
        for row in benchmarks:
            metric = _metric(row.get("island_scores", {}).get(island, {}).get("60"))
            if metric:
                ranked.append({"species": row.get("species_ja") or to_japanese("species", row["species"]),
                               "species_key": row["species"], "island": island,
                               "daily": round(metric["expected"]), "sample": bool(row.get("sample")),
                               "reason": "理想個体のLv60非料理エナジー上位"})
        general.extend(sorted(ranked, key=lambda x: -x["daily"])[:3])
    weakest = sorted(forecasts, key=lambda x: x["modes"].get("60", {}).get("weekly", {}).get("expected", 0))
    tailored = []
    for forecast in weakest:
        candidates = []
        for candidate in (x for x in general if x["island"] == forecast["island"]):
            owned_values = [_item_metric(x, forecast["island"], "60")["expected"]
                            for x in items if x["species"] == candidate["species_key"]
                            and _item_metric(x, forecast["island"], "60")]
            best_owned = max(owned_values, default=0)
            if not best_owned or best_owned < candidate["daily"] * .8:
                reason = (f"{forecast['island']}の戦力穴を埋める未所持候補" if not best_owned else
                          f"所持個体が理想値の{round(best_owned/candidate['daily']*100)}%のため更新候補")
                candidates.append({**candidate, "reason": reason,
                                   "improvement": round(max(0, candidate["daily"] - best_owned))})
        tailored.extend(sorted(candidates, key=lambda x: -x["improvement"])[:2])
    return general, tailored
