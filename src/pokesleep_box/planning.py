"""Small, explicit decision helpers that never invent game-resource costs."""
from __future__ import annotations

from math import ceil, log
from typing import Any, Mapping, Sequence


def catches_for_target(per_catch_probability: float, target_probability: float = .9) -> int:
    """Independent-trial catch count required to reach a target chance.

    The chance must come from a model the player trusts; presenting a made-up
    probability for hidden Pokémon Sleep rolls would be less useful than no
    answer at all.
    """
    p, target = float(per_catch_probability), float(target_probability)
    if not 0 < p <= 1 or not 0 < target < 1:
        raise ValueError("1回の確率は0〜100%、目標は0〜100%未満で指定してください")
    return ceil(log(1 - target) / log(1 - p)) if p < 1 else 1


def resource_plan(subskill_plans: Sequence[Mapping[str, Any]],
                  mainskill_plans: Sequence[Mapping[str, Any]],
                  resources: Mapping[str, Any] = {}) -> list[dict[str, Any]]:
    """Choose known one-seed upgrades within user-declared seed budgets."""
    budget = {"main": max(0, int(resources.get("mainSkillSeeds", 0) or 0)),
              "sub": max(0, int(resources.get("subSkillSeeds", 0) or 0))}
    candidates = ([{**dict(x), "resource": "sub", "label": "サブスキルのたね"}
                   for x in subskill_plans] +
                  [{**dict(x), "resource": "main", "label": "メインスキルのたね"}
                   for x in mainskill_plans])
    result = []
    for item in sorted(candidates, key=lambda x: (-float(x.get("gain", 0)), -float(x.get("score", 0)))):
        kind = item["resource"]
        if budget[kind] <= 0:
            continue
        budget[kind] -= 1
        result.append(item | {"remaining": budget[kind]})
    return result
