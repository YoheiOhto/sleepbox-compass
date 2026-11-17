"""Shared engine runs for the subskill columns (no ``COLUMN`` here).

Every subskill is tried one at a time on a plain build (neutral nature, no
other subskills) of a few representative species, so its effect on the
engine's daily energy and on the app's role score can be read directly.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..analytics import ISLANDS
from ..core import absolute_role_scores
from ..localization import names

# Two common final evolutions per specialty, all with a strength-type main
# skill so the engine's skill energy is comparable (same as lv60-worth).
SPECIES = ["RAICHU", "HOUNDOOM", "GENGAR", "QUAQUAVAL", "AMPHAROS", "ESPEON"]
SUBSKILLS = [
    "Helping Speed M", "Helping Speed S", "Berry Finding S",
    "Ingredient Finder M", "Ingredient Finder S",
    "Skill Trigger M", "Skill Trigger S", "Skill Level Up M", "Skill Level Up S",
    "Inventory Up L", "Inventory Up M", "Inventory Up S",
    "Helping Bonus", "Energy Recovery Bonus",
    "Dream Shard Bonus", "Research EXP Bonus", "Sleep EXP Bonus",
]
# Subskills whose main effect reaches the whole team.
TEAM_SUBSKILLS = ["Helping Bonus", "Energy Recovery Bonus"]
# Subskills with no effect on energy at all.
NO_ENERGY_SUBSKILLS = ["Dream Shard Bonus", "Research EXP Bonus", "Sleep EXP Bonus"]
NATURE = "Bashful"          # neutral nature
BASE_SKILL_LEVEL = 3        # e.g. caught at skill Lv1 and evolved twice
SKILL_LEVEL_BONUS = {"Skill Level Up M": 2, "Skill Level Up S": 1}
LEVELS = [50, 60]
ITERATIONS = 1000
SPECIALTY_JA = {"berry": "きのみタイプ", "ingredient": "食材タイプ", "skill": "スキルタイプ"}


def favored_island(berry: str) -> str:
    return next(name for name, berries in ISLANDS.items() if berry in berries)


def plain_instance(info: Dict[str, Any], species: str, subskill: Optional[str],
                   level: int = LEVELS[0], max_skill: int = 99) -> Dict[str, Any]:
    """A plain build with at most one subskill in the Lv10 slot.

    The engine treats the given skill level as already including Skill Level
    Up subskills unlocked at or below ``level``, so their bonus is added here.
    """
    skill = min(max_skill, BASE_SKILL_LEVEL + SKILL_LEVEL_BONUS.get(subskill or "", 0))
    return {"species": species, "level": level, "nature": NATURE,
            "subskills": [[subskill, 10]] if subskill else [],
            "ingredients": [slot["choices"][0] for slot in info["ingredients"]],
            "skillLevel": skill, "ribbon": 0}


def single_subskill_grid(ctx) -> Dict[str, Any]:
    """Energy and role scores at Lv50/60 for each species x (none + each subskill)."""
    meta = ctx.engine({"mode": "metadata"})["pokemon"]
    ja = names()
    rows = []
    for species in SPECIES:
        info = meta[species]
        island = favored_island(info["berry"])
        variants = [None, *SUBSKILLS]
        # Skill max level is only known from an evaluate result; ask once.
        probe = ctx.engine({"mode": "evaluate", "anchors": LEVELS, "iterations": 10,
                            "instances": [{"uid": "probe", "instance": plain_instance(info, species, None)}]})
        max_skill = probe["results"][0]["mainSkillMaxLevel"]
        response = ctx.engine({
            "mode": "evaluate", "anchors": LEVELS, "iterations": ITERATIONS,
            "islands": {island: list(ISLANDS[island])},
            "instances": [{"uid": v or "none", "instance": plain_instance(info, species, v, max_skill=max_skill)}
                          for v in variants]})
        results = {}
        for variant, result in zip(variants, response["results"]):
            energy = result["energyScores"][island]
            roles = {int(level): values for level, values in result["scores"].items()}
            role_scores = absolute_role_scores(roles)
            results[variant or "none"] = {
                "energy": {str(level): {k: energy[str(level)][k]
                                        for k in ("berry", "ingredient", "direct_skill", "expected")}
                           for level in LEVELS},
                "rp": {str(level): {k: round(v, 1) for k, v in roles[level].items()} for level in LEVELS},
                "role_scores": role_scores,
                "score": max(role_scores.values()),
            }
        rows.append({
            "species": species,
            "species_ja": ja["species"].get(species, species),
            "specialty": info["specialty"],
            "main_skill": ja["mainskills"].get(info["main_skill"], info["main_skill"]),
            "skill_max_level": max_skill,
            "berry": ja["berries"].get(info["berry"], info["berry"]),
            "island": island,
            "ingredients": [[ja["ingredients"].get(n, n), a] for n, a in
                            (slot["choices"][0] for slot in info["ingredients"])],
            "results": results,
        })
    return {"species": rows, "subskills": SUBSKILLS,
            "subskills_ja": {s: ja["subskills"].get(s, s) for s in SUBSKILLS},
            "nature": ja["natures"].get(NATURE, NATURE), "base_skill_level": BASE_SKILL_LEVEL,
            "levels": LEVELS, "iterations": ITERATIONS}


def gain(row: Dict[str, Any], subskill: str, level: int, key: str = "expected") -> int:
    res = row["results"]
    return res[subskill]["energy"][str(level)][key] - res["none"]["energy"][str(level)][key]


def gain_pct(row: Dict[str, Any], subskill: str, level: int) -> float:
    base = row["results"]["none"]["energy"][str(level)]["expected"]
    return gain(row, subskill, level) / base * 100 if base else 0.0


def by_specialty(rows: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        out.setdefault(row["specialty"], []).append(row)
    return out
