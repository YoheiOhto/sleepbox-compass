"""Candy and dream-shard cost of raising a helper to a target level.

The tables live in ``training_costs.json`` next to this module; its
``_comment`` header lists the public sources and which values are only
single-sourced. Game rules follow Ver.3.6.0 (level cap 70).

Assumptions:
- The helper starts at the beginning of ``level`` (EXP already earned inside
  the current level is unknown and treated as 0), so costs are upper bounds.
- Only candies are used. Sleep/nap EXP, handy candies, and the regular
  (non-mini) candy boost are not modelled. Handy candies convert to species
  candies, so they lower the candy count you must farm but not the shard cost.
- EXP left over after a level-up carries into the next level, as in game.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from .localization import names, to_english


DATA_PATH = Path(__file__).with_name("training_costs.json")
NORMAL_EXP_TYPE = 600


@lru_cache(maxsize=1)
def tables() -> Dict[str, Any]:
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def max_level() -> int:
    return int(tables()["max_level"])


def _round_half_up(value: float) -> int:
    return int(math.floor(value + 0.5))


def known_species(species: str) -> bool:
    key = str(species or "").upper()
    return key in names().get("species", {}) or key in tables()["species_exp_type"]


def exp_type(species: str) -> Optional[int]:
    """EXP type (600/900/1080/1320) of a species key like ``DRAGONITE``.

    Returns None when the species is not in the known species list.
    """
    key = str(species or "").upper()
    if not known_species(key):
        return None
    return int(tables()["species_exp_type"].get(key, NORMAL_EXP_TYPE))


def nature_exp_effect(nature: Optional[str]) -> Optional[str]:
    """'up', 'down', 'neutral', or None if the nature is not recognised.

    Accepts English (``Timid``) or Japanese (``おくびょう``) names.
    """
    if not nature:
        return None
    english = to_english("natures", str(nature))
    groups = tables()["exp_natures"]
    if english in groups["up"]:
        return "up"
    if english in groups["down"]:
        return "down"
    if english in names().get("natures", {}):
        return "neutral"
    return None


def cumulative_exp(level: int, type_: int = NORMAL_EXP_TYPE) -> int:
    """Total EXP from Lv1 to reach ``level`` for an EXP type."""
    multiplier = tables()["exp_types"][str(type_)]["multiplier"]
    return _round_half_up(tables()["cumulative_exp"][level - 1] * multiplier)


def candy_exp(level: int, effect: str = "neutral", boost: bool = False) -> int:
    """EXP one candy gives to a helper currently at ``level``."""
    for bracket in tables()["candy_exp"]:
        if bracket["below_level"] is None or level < bracket["below_level"]:
            value = int(bracket[effect])
            break
    if boost:
        value *= int(tables()["mini_candy_boost"]["exp_factor"])
    return value


def shards_per_candy(level: int, boost: bool = False) -> int:
    """Dream shards per candy while the helper is at ``level``."""
    value = int(tables()["shards_per_candy"][level - 1])
    if boost:
        value *= int(tables()["mini_candy_boost"]["shard_factor"])
    return value


def training_cost(species: str, nature: Optional[str], level: int, target: int,
                  mini_boost: bool = False) -> Optional[Dict[str, Any]]:
    """Candies, dream shards, and EXP needed to go from ``level`` to ``target``.

    Returns None for an unknown species or a level outside 1..max_level().
    ``level >= target`` costs nothing. An unknown nature is treated as
    neutral and reported in ``notes``. ``mini_boost`` applies the mini candy
    boost to every candy (x2 EXP, x4 shards) and ignores its daily cap.
    """
    type_ = exp_type(species)
    cap = max_level()
    try:
        level, target = int(level), int(target)
    except (TypeError, ValueError):
        return None
    if type_ is None or not (1 <= level <= cap and 1 <= target <= cap):
        return None

    notes: List[str] = ["現在のレベル内で獲得済みのEXPは0とみなしています（必要量は多めに出ます）。"]
    effect = nature_exp_effect(nature)
    if effect is None:
        effect = "neutral"
        notes.append("性格が不明なため、EXP補正なしで計算しています。")
    if type_ != NORMAL_EXP_TYPE:
        label = tables()["exp_types"][str(type_)]["label"]
        notes.append(f"{label}のため、必要EXPが通常の"
                     f"{tables()['exp_types'][str(type_)]['multiplier']:g}倍です。")
    if mini_boost:
        notes.append("ミニアメブーストを全てのアメに使う前提です（1日50個の上限は考慮していません）。")
    if type_ == 1080 and str(species).upper() == "MEWTWO":
        notes.append("ミュウツーのEXPタイプは1つの情報源でしか確認できていません。")
    if target > 65:
        notes.append("Lv66以降の必要EXPとかけら数はコミュニティの実測値です。")

    candies = shards = 0
    carry = 0
    for current in range(level, max(level, target)):
        need = cumulative_exp(current + 1, type_) - cumulative_exp(current, type_) - carry
        per_candy = candy_exp(current, effect, mini_boost)
        used = max(0, math.ceil(need / per_candy))
        candies += used
        shards += used * shards_per_candy(current, mini_boost)
        carry = used * per_candy - need

    return {
        "candies": candies,
        "dream_shards": shards,
        "exp": max(0, cumulative_exp(target, type_) - cumulative_exp(level, type_)),
        "exp_type": type_,
        "nature_effect": effect,
        "mini_boost": bool(mini_boost),
        "notes": notes,
    }
