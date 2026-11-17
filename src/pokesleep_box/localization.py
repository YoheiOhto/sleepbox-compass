from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict


# Subskill slot unlock levels since game Ver.3.6.0 (2026-06-25): the last
# two slots moved from Lv75/Lv100 to Lv70/Lv80.
SUBSKILL_UNLOCKS = (10, 25, 50, 70, 80)
LEGACY_UNLOCKS = {75: 70, 100: 80}


def current_unlock(level: Any) -> Any:
    """Map a pre-3.6.0 subskill unlock level to the current one."""
    return LEGACY_UNLOCKS.get(level, level)


def slot_unlocks(subskills: Any) -> list:
    """Subskill entries with the unlock level fixed by the game rules.

    Since Ver.3.6.0 every individual has five subskill slots unlocked at
    Lv10/25/50/70/80 by position, so a complete five-slot list always gets
    those levels and a misread unlock badge is ignored. A shorter list does
    not tell which slots are missing, so its read unlocks are kept and only
    the pre-3.6.0 Lv75/Lv100 values are mapped (``current_unlock``).
    """
    entries = list(subskills or [])
    if len(entries) == len(SUBSKILL_UNLOCKS):
        return [[entry[0], SUBSKILL_UNLOCKS[index], *entry[2:]]
                for index, entry in enumerate(entries)]
    return [[entry[0], current_unlock(entry[1]), *entry[2:]] for entry in entries]


def base_main_skill(name: str) -> str:
    """'Psystrike (Berry Zone)' or 'マジカルフレイム（料理アドバンスS）' -> the base skill."""
    match = re.search(r"[(（]([^()（）]+)[)）]\s*$", name)
    return match.group(1).strip() if match else name


@lru_cache(maxsize=1)
def names() -> Dict[str, Any]:
    path = Path(__file__).with_name("names_ja.json")
    return json.loads(path.read_text(encoding="utf-8"))


def to_english(category: str, value: str) -> str:
    table = names().get(category, {})
    if value in table:
        return value
    reverse = {japanese: english for english, japanese in table.items()}
    return reverse.get(value, value)


def to_japanese(category: str, value: str) -> str:
    return names().get(category, {}).get(value, value)


def normalize_individual(item: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(item)
    result["species"] = to_english("species", str(item["species"]))
    result["nature"] = to_english("natures", str(item["nature"]))
    result["main_skill"] = to_english("mainskills", base_main_skill(str(item["main_skill"])))
    berry = item.get("berry")
    result["berry"] = to_english("berries", str(berry)) if berry else None
    result["ingredients"] = [
        [to_english("ingredients", str(entry[0])), entry[1], *entry[2:]]
        for entry in item.get("ingredients", [])
    ]
    result["subskills"] = slot_unlocks(
        [to_english("subskills", str(entry[0])), *entry[1:]] for entry in item.get("subskills", []))
    result.setdefault("species_ja", to_japanese("species", result["species"]))
    return result
