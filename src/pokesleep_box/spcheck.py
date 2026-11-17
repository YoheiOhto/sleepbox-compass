"""Confirm freshly scanned individuals from the SP shown on screen.

Neroli's Lab's ``RP`` (``common/src/utils/rp-utils/rp.ts``) computes the SP
from the species' base data, the current level, the nature, the main skill
level, the subskills unlocked at the current level (unlock <= level) and the
ingredient slots unlocked at the current level (Lv1/30/60 <= level). Locked
subskills and ingredient slots do not change the SP, and neither does the
main skill name (it follows the species). So an exact SP match confirms only
the unlocked part of a read; the locked part is confirmed only when the OCR
read it cleanly. Policy chosen by the user: "解放済みのみ確定".

Only an exact match below Lv56 (``verify_mode=strict``) can auto-verify; at
Lv56 and above the engine's SP is approximate, so even an exact match leaves
the individual unverified.

Rows a human edited on the review page (or imported with `import-json`) but
did not tick 「照合済み」 (``review_confirmed``) go through the same rule. The
review page saves every row with its scan-time ``ocr_missing`` unchanged and
records in ``human_edited`` the fields whose values the human changed; those
fields count as resolved (see ``unresolved_fields``). Fields left untouched
keep their OCR status, so saving the page does not by itself confirm them.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

INGREDIENT_UNLOCKS = (1, 30, 60)
# Natures without any effect; the SP cannot tell them apart.
NEUTRAL_NATURES = {"Hardy", "Docile", "Serious", "Bashful", "Quirky"}
# Subskills the SP cannot identify: Skill Level Up S/M add nothing to the RP
# formula, and these bonuses all add the same 0.221 factor.
SP_BLIND_SUBSKILLS = {"Skill Level Up S", "Skill Level Up M", "Dream Shard Bonus",
                      "Energy Recovery Bonus", "Helping Bonus", "Research EXP Bonus",
                      "Sleep EXP Bonus"}

MATCH, MISMATCH, PARTIAL, UNCHECKED, HUMAN = "match", "mismatch", "partial", "unchecked", "human"


def unresolved_fields(item: Mapping[str, Any]) -> List[str]:
    """``ocr_missing`` without the fields a human changed on the review page."""
    edited = set(item.get("human_edited") or [])
    return [field for field in item.get("ocr_missing", []) if field not in edited]


def _locked_ingredient_slots_clean(row: Mapping[str, Any], item: Mapping[str, Any],
                                   level: int) -> bool:
    """Whether every ingredient slot above ``level`` was read without guessing.

    With species options, a slot is clean when the read amount (or matched
    icon) resolves it to exactly the stored ingredient, as in
    ``ocr.resolve_ingredient_slots``; a provisional fallback is not clean.
    Ingredients a human changed on the review page are clean.
    """
    from .ocr import align_amounts, resolve_ingredient_slots

    stored = json.loads(row["ingredients_json"] or "[]")
    missing = set(unresolved_fields(item))
    options = item.get("ingredient_options") or []
    slots = len(options) or len(INGREDIENT_UNLOCKS)
    if len(stored) < slots:
        return False
    if "ingredients" in (item.get("human_edited") or []):
        return True
    unlocks = [(options[index].get("level") if options else None) or INGREDIENT_UNLOCKS[index]
               for index in range(slots)]
    locked = [index for index in range(slots) if unlocks[index] > level]
    if not locked:
        return True
    if not options:
        return "ingredients" not in missing
    confident, _ = resolve_ingredient_slots(
        options, align_amounts(item.get("ingredient_amounts", []), options),
        item.get("ingredient_names", []))
    return all(index < len(confident) and confident[index] is not None and
               list(confident[index][:2]) == list(stored[index][:2]) for index in locked)


def assess(row: Mapping[str, Any], item: Mapping[str, Any],
           result: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Apply the SP rule to one stored individual and the scan item behind it.

    Returns ``status`` (match: auto-verified, partial: SP matches but some
    locked or SP-blind fields remain unclear, mismatch, unchecked, human),
    ``review_fields`` (the fields still to check by hand) and ``flag``.
    """
    missing = unresolved_fields(item)
    if row["review_confirmed"]:
        return {"status": HUMAN, "review_fields": [], "flag": None}
    if not result:
        return {"status": UNCHECKED, "review_fields": missing, "flag": None}
    if not result.get("match"):
        return {"status": MISMATCH, "review_fields": missing,
                "flag": f"SP不一致（画面 {row['sp']} / 計算 {result.get('computedSp')}）"}
    if result.get("mode") != "strict":
        return {"status": UNCHECKED, "review_fields": missing,
                "flag": "Lv56以上はSPの計算が近似のため自動確定しません"}
    level = int(row["level"])
    remaining: List[str] = []
    if "nature" in missing and row["nature"] in NEUTRAL_NATURES:
        remaining.append("nature")
    if "subskills" in missing:
        subskills = json.loads(row["subskills_json"] or "[]")
        if len(subskills) < 5 or any(unlock > level or name in SP_BLIND_SUBSKILLS
                                     for name, unlock, *_ in subskills):
            remaining.append("subskills")
    if not _locked_ingredient_slots_clean(row, item, level):
        remaining.append("ingredients")
    if "main_skill" in missing:
        remaining.append("main_skill")
    return {"status": PARTIAL if remaining else MATCH, "review_fields": remaining, "flag": None}


def check_items(db: sqlite3.Connection, items: Sequence[Dict[str, Any]], command: str,
                tolerance: int = 0, strict_below_level: int = 56,
                on_progress: Optional[Callable[[str], None]] = None) -> Dict[str, int]:
    """SP-check only the individuals stored from ``items`` and annotate them.

    Each item needs ``stored_uid`` (set by ``core.import_individuals``). The
    engine runs once for all computable rows. Rows are only ever upgraded to
    verified; a mismatch drops a verification that no human confirmed. Items
    get ``sp_check``, ``sp_flag``, ``review_fields`` and, when confirmed,
    ``verified``/``auto_verified``. Raises ``engine.EngineUnavailable``.
    """
    from .engine import individual_to_engine, run_engine

    by_uid: Dict[str, Dict[str, Any]] = {}
    for item in items:
        if item.get("stored_uid"):
            by_uid[item["stored_uid"]] = item
    rows = {row["uid"]: row for row in db.execute(
        "SELECT * FROM individual WHERE archived=0").fetchall() if row["uid"] in by_uid}
    computable = [row for row in rows.values()
                  if row["species"] != "UNKNOWN" and row["sp"] is not None
                  and row["level"] is not None and not row["review_confirmed"]]
    results: Dict[str, Mapping[str, Any]] = {}
    if computable:
        response = run_engine({"mode": "verify", "tolerance": tolerance,
                               "strictBelowLevel": strict_below_level,
                               "instances": [{"uid": row["uid"], "displayedSp": row["sp"],
                                              "instance": individual_to_engine(dict(row))}
                                             for row in computable]}, command, on_progress)
        results = {result["uid"]: result for result in response.get("results", [])}
    counts = {MATCH: 0, MISMATCH: 0, PARTIAL: 0, UNCHECKED: 0, HUMAN: 0}
    for uid, item in by_uid.items():
        row = rows.get(uid)
        if row is None:
            continue
        result = results.get(uid)
        outcome = assess(row, item, result)
        status = outcome["status"]
        counts[status] += 1
        item["sp_check"] = status
        item["sp_flag"] = outcome["flag"]
        item["review_fields"] = outcome["review_fields"]
        if status == HUMAN:
            continue
        if result:
            mode = result.get("mode")
            db.execute("UPDATE individual SET sp_computed=?,sp_diff=?,verify_mode=? WHERE uid=?",
                       (result.get("computedSp"), result.get("diff"),
                        mode if mode in ("strict", "tolerant") else "failed", uid))
        if status == MATCH:
            db.execute("UPDATE individual SET verified=1,auto_verified=1,sp_flag=NULL WHERE uid=?",
                       (uid,))
            item["verified"] = True
            item["auto_verified"] = True
        elif status == MISMATCH:
            db.execute("""UPDATE individual SET verified=review_confirmed,auto_verified=0,sp_flag=?
                          WHERE uid=?""", (outcome["flag"], uid))
        else:
            db.execute("UPDATE individual SET sp_flag=? WHERE uid=?", (outcome["flag"], uid))
    db.commit()
    return counts


def review_order(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """SP mismatches first, then other unverified items, then confirmed ones."""
    def rank(item: Mapping[str, Any]) -> int:
        if item.get("sp_check") == MISMATCH:
            return 0
        return 2 if item.get("verified") else 1
    return sorted(items, key=rank)
