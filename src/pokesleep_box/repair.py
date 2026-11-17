"""Repair SP mismatches: "fast model first, verify with SP, repair or escalate".

The fast default VLM sometimes misreads small text: the level digits (e.g.
Lv.14 as 16), a nature, a subskill grade (S/M) or name, or the skill level.
After the SP check, each mismatched row that no human confirmed goes through:

1. SP repair (a rule-based candidate search, see ``build_candidates``):
   alternative builds near the read are SP-checked in one batched engine call
   per round. Round 1 changes exactly one field (level -2..+2, any other
   nature, any other subskill in one unlocked slot, skill level +-1). Only
   when no round-1 candidate fits, round 2 changes the level plus one other
   field. A candidate is accepted only when it is the ONLY candidate with the
   smallest change count whose SP equals the shown SP exactly; the row is
   then corrected, ``repaired`` is set, ``repair_note`` records e.g.
   「性格 さみしがり→いじっぱり（SPで補正）」 and the row is SP-checked again
   with the normal rules. Every SP-equal alternative of a changed field is
   among the candidates (all natures, all subskills), so SP-indistinguishable
   choices (neutral natures, SP-blind subskills) always tie and are never
   chosen; a winner that touches an SP-blind value is rejected as well.
2. Escalation: rows still mismatched whose source is a still image read by
   Qwen are read again with a larger fallback model (same cache directory),
   re-imported onto the same row, SP-checked and SP-repaired again.
   Videos are not escalated.

Rows whose SP matches but some fields stay unclear (status partial) go
through ``confirm_partials``: a second read by the fallback model that agrees
with every unclear stored field resolves them (two-model agreement).
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from .spcheck import (MISMATCH, NEUTRAL_NATURES, PARTIAL, SP_BLIND_SUBSKILLS,
                      STRICT_BELOW_LEVEL, check_items)

LEVEL_SHIFTS = (-2, -1, 1, 2)
SKILL_LEVEL_SHIFTS = (-1, 1)
# Grades of one subskill family. Used only when a round would exceed the cap
# with every subskill (it does not with the current 17 subskills).
SUBSKILL_FAMILIES = (("Helping Speed S", "Helping Speed M"),
                     ("Skill Trigger S", "Skill Trigger M"),
                     ("Ingredient Finder S", "Ingredient Finder M"),
                     ("Inventory Up S", "Inventory Up M", "Inventory Up L"),
                     ("Skill Level Up S", "Skill Level Up M"))
# Engine instances per row and round. Round 1 needs at most ~110 and
# round 2 at most ~430 with 25 natures and 17 subskills.
MAX_CANDIDATES = 500
# Fields assumed unread for a stored row whose scan-time read is unknown
# (`sync --recheck` without a cached read): the SP check then confirms only
# what the SP can prove and leaves the rest for review.
UNKNOWN_READ_MISSING = ["nature", "subskills", "ingredients", "main_skill"]
# Fields that a re-read may fail to see; the stored value is kept then.
KEEP_WHEN_MISSING = ("nature", "ingredients", "subskills", "main_skill")

Progress = Callable[[str], None]


def _add_note(item: Dict[str, Any], note: str) -> str:
    previous = item.get("repair_note")
    if previous and note in previous.split("、"):
        return previous
    item["repair_note"] = f"{previous}、{note}" if previous else note
    return item["repair_note"]


def _mismatched_rows(db: sqlite3.Connection, items: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    rows = {}
    for item in items:
        uid = item.get("stored_uid")
        if item.get("sp_check") != MISMATCH or not uid:
            continue
        row = db.execute("SELECT * FROM individual WHERE uid=? AND archived=0", (uid,)).fetchone()
        if (row is None or row["review_confirmed"] or row["species"] == "UNKNOWN"
                or row["sp"] is None or row["level"] is None):
            continue
        rows[uid] = (row, item)
    return rows


def _all_natures() -> List[str]:
    from .localization import names
    return list(names()["natures"])


def _all_subskills() -> List[str]:
    from .localization import names
    return list(names()["subskills"])


def _family(name: str) -> List[str]:
    for family in SUBSKILL_FAMILIES:
        if name in family:
            return list(family)
    return [name]


def _one_field_changes(row: Mapping[str, Any], level: int, full: bool) -> List[Dict[str, Any]]:
    """Changes of one field other than the level, valid for a build at ``level``.

    Subskill slots still locked at ``level`` are skipped: they do not change
    the SP. ``full`` tries every subskill in a slot, otherwise only the other
    grades of the read subskill's family. A subskill read in another slot is
    never tried (an individual has no duplicate subskills).
    """
    changes: List[Dict[str, Any]] = [{"nature": nature} for nature in _all_natures()
                                     if nature != row["nature"]]
    subskills = json.loads(row["subskills_json"] or "[]")
    names_read = [entry[0] for entry in subskills]
    for slot, (name, unlock, *_) in enumerate(subskills):
        if unlock is None or int(unlock) > level:
            continue
        pool = _all_subskills() if full else _family(name)
        changes += [{"subskill": [slot, alt]} for alt in pool
                    if alt != name and alt not in names_read]
    skill_level = int(row["skill_level"] or 1)
    changes += [{"skill_level": skill_level + shift} for shift in SKILL_LEVEL_SHIFTS
                if skill_level + shift >= 1]
    return changes


def build_candidates(row: Mapping[str, Any], round_number: int,
                     cap: int = MAX_CANDIDATES) -> List[Dict[str, Any]]:
    """Alternative builds for an SP-mismatched row, as dicts of changed fields.

    Round 1: one changed field (level shift, nature, one subskill slot or
    skill level). Round 2: a level shift plus one other field. At most ``cap``
    candidates; over the cap, subskills fall back to grade swaps.
    """
    level = int(row["level"])
    levels = sorted({min(100, max(1, level + shift)) for shift in LEVEL_SHIFTS} - {level})
    candidates: List[Dict[str, Any]] = []
    for full in (True, False):
        if round_number == 1:
            candidates = [{"level": trial} for trial in levels] + _one_field_changes(row, level, full)
        else:
            candidates = [dict(change, level=trial) for trial in levels
                          for change in _one_field_changes(row, trial, full)]
        if len(candidates) <= cap:
            break
    return candidates[:cap]


def apply_change(row: Mapping[str, Any], change: Mapping[str, Any]) -> Dict[str, Any]:
    """The stored row with ``change`` applied (DB column names)."""
    data = dict(row)
    for key in ("level", "nature", "skill_level"):
        if key in change:
            data[key] = change[key]
    if "subskill" in change:
        slot, name = change["subskill"]
        subskills = json.loads(row["subskills_json"] or "[]")
        subskills[slot] = [name, *subskills[slot][1:]]
        data["subskills_json"] = json.dumps(subskills, ensure_ascii=False)
    return data


def _sp_blind(row: Mapping[str, Any], change: Mapping[str, Any]) -> bool:
    """Whether ``change`` moves into or out of a value the SP cannot single out."""
    if change.get("nature") in NEUTRAL_NATURES:
        return True
    if "subskill" in change:
        slot, name = change["subskill"]
        old = json.loads(row["subskills_json"] or "[]")[slot][0]
        return name in SP_BLIND_SUBSKILLS or old in SP_BLIND_SUBSKILLS
    return False


def describe_change(row: Mapping[str, Any], change: Mapping[str, Any]) -> str:
    """Japanese repair note, e.g. 「Lv 16→14、性格 さみしがり→いじっぱり（SPで補正）」."""
    from .localization import to_japanese

    parts = []
    if "level" in change:
        parts.append(f"Lv {row['level']}→{change['level']}")
    if "nature" in change:
        parts.append(f"性格 {to_japanese('natures', row['nature'])}→"
                     f"{to_japanese('natures', change['nature'])}")
    if "subskill" in change:
        slot, name = change["subskill"]
        old = json.loads(row["subskills_json"] or "[]")[slot][0]
        parts.append(f"サブスキル{slot + 1} {to_japanese('subskills', old)}→"
                     f"{to_japanese('subskills', name)}")
    if "skill_level" in change:
        parts.append(f"スキルLv {row['skill_level']}→{change['skill_level']}")
    return "、".join(parts) + "（SPで補正）"


def _search_round(rows: Mapping[str, Any], round_number: int, command: str,
                  strict_below_level: int, on_progress: Optional[Progress]
                  ) -> Dict[str, List[Dict[str, Any]]]:
    """SP-check every row's candidates of one round in one engine call.

    Returns uid -> the candidates whose SP equals the shown SP exactly.
    """
    from .engine import individual_to_engine, run_engine

    candidates: Dict[str, List[Dict[str, Any]]] = {}
    instances = []
    for uid, (row, _) in rows.items():
        candidates[uid] = build_candidates(row, round_number)
        for index, change in enumerate(candidates[uid]):
            instances.append({"uid": f"{uid}|{index}", "displayedSp": row["sp"],
                              "instance": individual_to_engine(apply_change(row, change))})
    if not instances:
        return {}
    if on_progress:
        on_progress(f"SP補正の候補 {len(instances)}件を計算（{len(rows)}匹・{round_number}段目）")
    response = run_engine({"mode": "verify", "tolerance": 0,
                           "strictBelowLevel": strict_below_level,
                           "instances": instances}, command, on_progress)
    fits: Dict[str, List[Dict[str, Any]]] = {uid: [] for uid in rows}
    for result in response.get("results", []):
        uid, _, index = str(result.get("uid", "")).rpartition("|")
        if uid not in candidates or result.get("diff") != 0:
            continue
        change = candidates[uid][int(index)]
        top = result.get("skillMaxLevel")
        # Above the species' maximum the skill level is clamped: not a real build.
        if "skill_level" in change and top is not None and change["skill_level"] > int(top):
            continue
        fits[uid].append(change)
    return fits


def repair_builds(db: sqlite3.Connection, items: Sequence[Dict[str, Any]], command: str,
                  strict_below_level: int = STRICT_BELOW_LEVEL,
                  on_progress: Optional[Progress] = None) -> List[Dict[str, Any]]:
    """Correct SP-mismatched items when exactly one nearby build fits the SP.

    Returns the repaired items, already SP-checked again (``check_items``).
    Raises ``engine.EngineUnavailable``.
    """
    rows = _mismatched_rows(db, items)
    pending = dict(rows)
    chosen: Dict[str, Dict[str, Any]] = {}
    for round_number in (1, 2):
        if not pending:
            break
        fits = _search_round(pending, round_number, command, strict_below_level, on_progress)
        undecided = {}
        for uid, entry in pending.items():
            hits = fits.get(uid, [])
            if not hits:
                undecided[uid] = entry
            elif len(hits) == 1 and not _sp_blind(entry[0], hits[0]):
                chosen[uid] = hits[0]
            # Several fits with the same change count: ambiguous, not repaired.
        pending = undecided
    repaired = []
    for uid, change in chosen.items():
        row, item = rows[uid]
        new = apply_change(row, change)
        if "level" in change:
            item["level_read"] = int(row["level"])
        item.update(level=new["level"], nature=new["nature"], skill_level=new["skill_level"],
                    subskills=json.loads(new["subskills_json"] or "[]"), repaired=True)
        # A unique repair proves the changed fields from the SP. A repaired
        # nature is never neutral (see ``_sp_blind``), so it is resolved; a
        # repaired subskill slot is unlocked, which the SP rule covers anyway.
        if "nature" in change:
            item["sp_resolved"] = sorted(set(item.get("sp_resolved") or []) | {"nature"})
        note = _add_note(item, describe_change(row, change))
        db.execute("""UPDATE individual SET level=?,nature=?,subskills_json=?,skill_level=?,
                      repaired=1,repair_note=? WHERE uid=?""",
                   (new["level"], new["nature"], new["subskills_json"], new["skill_level"],
                    note, uid))
        repaired.append(item)
    db.commit()
    if repaired:
        check_items(db, repaired, command, strict_below_level=strict_below_level,
                    on_progress=on_progress)
    return repaired


def reread(db: sqlite3.Connection, item: Mapping[str, Any], image: Path, reader: Any,
           frames: Path) -> Optional[Dict[str, Any]]:
    """Read ``image`` again with ``reader`` and re-import it onto the same row.

    Runs the full per-image pipeline (merge, finalize, species data and icon
    matching). Returns the new item (with ``stored_uid``), or ``None`` when
    the read does not yield exactly one individual of a known species.
    """
    from .core import import_individuals
    from .vlm import scan as vlm_scan

    uid = item["stored_uid"]
    row = db.execute("SELECT * FROM individual WHERE uid=?", (uid,)).fetchone()
    found = vlm_scan(image, reader, frames)
    if row is None or len(found) != 1 or found[0].get("species") in (None, "UNKNOWN"):
        return None
    new = found[0]
    missing = set(new.get("ocr_missing", []))
    stored = {"nature": row["nature"], "ingredients": json.loads(row["ingredients_json"]),
              "subskills": json.loads(row["subskills_json"]), "main_skill": row["main_skill"]}
    for field in KEEP_WHEN_MISSING:
        if field in missing:
            new[field] = stored[field]
            if field == "main_skill":
                new["skill_level"] = row["skill_level"]
    # The upsert onto the stored uid replaces the read; it never runs the
    # recapture rule ("level only goes up"), which would keep a misread level.
    new.update(uid=uid, box_index=row["box_index"], ocr_sources=[image.name])
    if item.get("capture_sha256"):
        new["capture_sha256"] = item["capture_sha256"]
    if not new.get("display_name"):
        new["display_name"] = row["display_name"]
    import_individuals(db, [new])
    short = str(getattr(reader, "model_name", "fallback")).rsplit("/", 1)[-1]
    note = f"{short}で読み直し"
    if row["level"] is not None and new.get("level") is not None and int(new["level"]) != int(row["level"]):
        note += f"（Lv {row['level']}→{new['level']}）"
    _add_note(new, note)
    # The stored fields now come from this reader: a later agreement check
    # with the same model would only agree with itself.
    new["reread_model"] = str(getattr(reader, "model_name", ""))
    db.execute("UPDATE individual SET repair_note=? WHERE uid=?", (new["repair_note"], uid))
    db.commit()
    return new


def repair_mismatches(db: sqlite3.Connection, items: List[Dict[str, Any]], command: str,
                      images: Mapping[str, Path], fallback: Optional[Callable[[], Any]],
                      frames: Path, on_progress: Optional[Progress] = None) -> Dict[str, Any]:
    """Run SP repair, then escalation, on the SP-mismatched ``items``.

    ``images`` maps a stored uid to its still image (only rows eligible for
    a re-read); ``fallback`` returns the fallback reader and is called at
    most once, only when a row needs it. ``items`` is updated in place: an
    escalated item is replaced by its re-read. Returns the counts
    ``sp_repaired``, ``escalated``, ``escalated_fixed``, ``still_mismatch``
    and, when the fallback reader is unusable, ``note``.
    """
    from .vlm import VLMUnavailable

    stats: Dict[str, Any] = {"sp_repaired": 0, "escalated": 0, "escalated_fixed": 0}
    stats["sp_repaired"] = len(repair_builds(db, items, command, on_progress=on_progress))
    remaining = [index for index, item in enumerate(items)
                 if item.get("sp_check") == MISMATCH and item.get("stored_uid") in images]
    if remaining and fallback is not None:
        reader = None
        rereads = []
        try:
            for position, index in enumerate(remaining, start=1):
                item = items[index]
                if reader is None:
                    reader = fallback()
                if on_progress:
                    on_progress(f"読み直し {position}/{len(remaining)}: {images[item['stored_uid']].name}")
                new = reread(db, item, images[item["stored_uid"]], reader, frames)
                if new is not None:
                    items[index] = new
                    rereads.append(new)
        except VLMUnavailable as exc:
            stats["note"] = f"読み直し用のモデルを使えないため、読み直しを省略しました: {exc}"
        if rereads:
            check_items(db, rereads, command, on_progress=on_progress)
            # Counted in ``escalated_fixed`` when the re-read then fits.
            repair_builds(db, rereads, command, on_progress=on_progress)
        stats["escalated"] = len(rereads)
        stats["escalated_fixed"] = sum(1 for item in rereads if item.get("sp_check") != MISMATCH)
    stats["still_mismatch"] = sum(1 for item in items if item.get("sp_check") == MISMATCH)
    return stats


FIELD_LABELS = {"nature": "性格", "subskills": "サブスキル", "ingredients": "食材",
                "main_skill": "メインスキル"}


def _model_label(model: Optional[str], default: str) -> str:
    """Short model label for notes, e.g. "4B" for mlx-community/Qwen3.5-4B-4bit."""
    match = re.search(r"(\d+(?:\.\d+)?B)(?![a-z])", str(model or ""))
    return match.group(1) if match else default


def _field_text(field: str, values: Mapping[str, Any]) -> str:
    """Japanese text of one field's value, for disagreement notes."""
    from .localization import to_japanese

    if field == "nature":
        return to_japanese("natures", str(values.get("nature")))
    if field == "subskills":
        return "/".join(to_japanese("subskills", str(entry[0]))
                        for entry in values.get("subskills") or [] if entry)
    if field == "ingredients":
        return "/".join(to_japanese("ingredients", str(entry[0]))
                        for entry in values.get("ingredients") or [] if entry)
    if field == "main_skill":
        return to_japanese("mainskills", str(values.get("main_skill")))
    return str(values.get(field))


def _agrees(field: str, row: Mapping[str, Any], read: Mapping[str, Any]) -> Optional[bool]:
    """Whether a second read agrees with the stored value of ``field``.

    ``None`` when the second read did not see the field (no evidence either
    way). Ingredients agree when every locked slot of the second read
    resolves cleanly to the stored ingredient, as in the SP rule
    (``spcheck._locked_ingredient_slots_clean``).
    """
    from .spcheck import _locked_ingredient_slots_clean

    missing = set(read.get("ocr_missing") or [])
    if field == "ingredients":
        if not read.get("ingredient_options"):
            return None
        probe = dict(read, human_edited=[], model_agreed=[], sp_resolved=[])
        if _locked_ingredient_slots_clean(row, probe, int(row["level"])):
            return True
        stored = [entry[0] for entry in json.loads(row["ingredients_json"] or "[]")]
        names_read = [entry[0] for entry in read.get("ingredients") or []]
        # A provisional (guessed) slot is no evidence; a clean other choice is.
        return None if "ingredients" in missing and names_read == stored else False
    if field in missing:
        return None
    if field == "nature":
        return read.get("nature") == row["nature"]
    if field == "subskills":
        stored = [entry[0] for entry in json.loads(row["subskills_json"] or "[]")]
        return [entry[0] for entry in read.get("subskills") or []] == stored
    if field == "main_skill":
        return read.get("main_skill") == row["main_skill"]
    return None


def confirm_partials(db: sqlite3.Connection, items: List[Dict[str, Any]], command: str,
                     images: Mapping[str, Path], fallback: Optional[Callable[[], Any]],
                     frames: Path, fallback_model: Optional[str],
                     on_progress: Optional[Progress] = None) -> Dict[str, Any]:
    """Two-model agreement for SP-matched rows with unclear fields (partial).

    Each partial item whose still image is in ``images`` is read again with
    the fallback reader (cache-first; ``fallback`` is called at most once).
    Every unclear field (``review_fields``) on which the second read agrees
    with the stored value (same nature, same subskills in the same order,
    same locked ingredients, same main skill) is added to ``model_agreed``,
    which ``spcheck.unresolved_fields`` treats as resolved, and the item is
    SP-checked again: with nothing left it is auto-verified. Other fields
    stay for review and are noted with both readings, e.g.
    「性格: 4B いじっぱり / 9B さみしがり」. Rows whose stored fields already
    come from ``fallback_model`` (its original read or an earlier re-read)
    are skipped: a model cannot agree with itself. Returns ``checked``,
    ``confirmed``, ``disagreed`` and, when the reader is unusable, ``note``.
    """
    from .vlm import VLMUnavailable, scan as vlm_scan

    stats: Dict[str, Any] = {"checked": 0, "confirmed": 0, "disagreed": 0}
    short = str(fallback_model or "").rsplit("/", 1)[-1]
    targets = []
    for item in items:
        uid = item.get("stored_uid")
        if item.get("sp_check") != PARTIAL or uid not in images or not item.get("review_fields"):
            continue
        if fallback_model and fallback_model in (item.get("read_model"), item.get("reread_model")):
            continue
        row = db.execute("SELECT * FROM individual WHERE uid=? AND archived=0", (uid,)).fetchone()
        if row is None or row["review_confirmed"] or row["level"] is None:
            continue
        if short and f"{short}で読み直し" in str(row["repair_note"] or ""):
            continue
        targets.append((item, row))
    if not targets or fallback is None:
        return stats
    reader = None
    agreed_items = []
    try:
        for position, (item, row) in enumerate(targets, start=1):
            if reader is None:
                reader = fallback()
            image = images[item["stored_uid"]]
            if on_progress:
                on_progress(f"2モデルで確認 {position}/{len(targets)}: {image.name}")
            try:
                found = vlm_scan(image, reader, frames)
            except VLMUnavailable:
                raise
            except Exception:  # noqa: BLE001 - an unreadable image stays for review
                continue
            stats["checked"] += 1
            if len(found) != 1 or found[0].get("species") != row["species"]:
                continue
            first = _model_label(item.get("read_model"), "元の読み")
            second = _model_label(getattr(reader, "model_name", fallback_model), "読み直し")
            stored = {"nature": row["nature"], "main_skill": row["main_skill"],
                      "subskills": json.loads(row["subskills_json"] or "[]"),
                      "ingredients": json.loads(row["ingredients_json"] or "[]")}
            agreed, notes = [], []
            for field in item["review_fields"]:
                verdict = _agrees(field, row, found[0])
                label = FIELD_LABELS.get(field, field)
                if verdict:
                    agreed.append(field)
                elif verdict is None:
                    notes.append(f"{label}: {second}で読めず")
                else:
                    notes.append(f"{label}: {first} {_field_text(field, stored)} / "
                                 f"{second} {_field_text(field, found[0])}")
            if agreed:
                item["model_agreed"] = sorted(set(item.get("model_agreed") or []) | set(agreed))
                agreed_items.append(item)
            if notes:
                stats["disagreed"] += 1
                for note in notes:
                    _add_note(item, note)
                db.execute("UPDATE individual SET repair_note=? WHERE uid=?",
                           (item["repair_note"], item["stored_uid"]))
        db.commit()
    except VLMUnavailable as exc:
        db.commit()
        stats["note"] = f"確認用のモデルを使えないため、2モデルでの確認を省略しました: {exc}"
    if agreed_items:
        check_items(db, agreed_items, command, on_progress=on_progress)
        stats["confirmed"] = sum(1 for item in agreed_items if item.get("auto_verified"))
    return stats


def still_image(source: Path, name: str, digest: str) -> Optional[Path]:
    """Find the still image ``name`` with SHA-256 ``digest`` in ``source``.

    ``source`` is a folder or a single file. A renamed file is found by its
    hash among the folder's images.
    """
    from .ingest import IMAGE_SUFFIXES, discover_inputs, file_sha256

    if Path(name).suffix.lower() not in IMAGE_SUFFIXES or not source.exists():
        return None
    if source.is_file():
        return source if file_sha256(source) == digest else None
    candidate = source / name
    if candidate.is_file() and file_sha256(candidate) == digest:
        return candidate
    for path in discover_inputs(source):
        if path.suffix.lower() in IMAGE_SUFFIXES and file_sha256(path) == digest:
            return path
    return None


def scanned_images(items: Sequence[Mapping[str, Any]], source: Path) -> Dict[str, Path]:
    """Map the stored uid of each still-image item of this run to its image."""
    images = {}
    for item in items:
        names = item.get("ocr_sources") or []
        if item.get("stored_uid") and item.get("capture_sha256") and len(names) == 1:
            path = still_image(source, names[0], item["capture_sha256"])
            if path is not None:
                images[item["stored_uid"]] = path
    return images


def row_item(row: Mapping[str, Any]) -> Dict[str, Any]:
    """A scan-like item for a stored row (for re-checking and the review page)."""
    from .localization import to_japanese

    return {"uid": row["uid"], "stored_uid": row["uid"], "species": row["species"],
            "species_ja": to_japanese("species", row["species"]),
            "display_name": row["display_name"], "level": row["level"], "sp": row["sp"],
            "nature": row["nature"], "berry": row["berry"],
            "ingredients": json.loads(row["ingredients_json"] or "[]"),
            "subskills": json.loads(row["subskills_json"] or "[]"),
            "main_skill": row["main_skill"], "skill_level": row["skill_level"],
            "box_index": row["box_index"], "confidence": row["confidence"], "verified": False,
            "ocr_missing": list(UNKNOWN_READ_MISSING), "sp_check": MISMATCH,
            "sp_flag": row["sp_flag"], "repair_note": row["repair_note"]}


def icon_downgrades(db: sqlite3.Connection, source: Path, engine: str,
                    identify: Optional[Callable[[Sequence[Path], Sequence[Mapping[str, Any]]],
                                                List[Optional[str]]]] = None,
                    on_progress: Optional[Progress] = None) -> Dict[str, Any]:
    """Un-verify auto-verified rows whose locked ingredient contradicts its icon.

    Part of `sync --recheck`; the only check that looks at verified rows.
    Only rows that are auto-verified (never ``review_confirmed`` ones), are
    still captures found in ``source`` (via ``scan_source``) and have a
    locked ingredient slot with more than one legal option are looked at;
    icons are matched on that image once per row. A row whose stored choice
    differs from a confident icon match loses ``verified``/``auto_verified``
    and gets a note like 「Lv60食材: 登録値→モーモーミルク×6 /
    アイコン→リラックスカカオ」, so the recheck that follows lists it for
    review. Without icon matching (no sprites, NumPy/Pillow or Vision) nothing
    changes. Returns ``checked``, ``downgraded`` and per uid ``names`` (the
    icon matches) and ``notes``.
    """
    from .localization import to_japanese
    from .ocr import ingredient_options, species_metadata
    from .spcheck import INGREDIENT_UNLOCKS

    stats: Dict[str, Any] = {"checked": 0, "downgraded": 0, "names": {}, "notes": {}}
    rows = [row for row in db.execute(
        """SELECT * FROM individual WHERE archived=0 AND review_confirmed=0 AND verified=1
           AND auto_verified=1 AND uid LIKE 'capture-%' ORDER BY box_index, uid""")
        if row["level"] is not None and int(row["level"]) < max(INGREDIENT_UNLOCKS)]
    if not rows:
        return stats
    if identify is None:
        from . import icons
        if not icons.available():
            return stats
        identify = icons.identify
    sources = {row["sha256"][:20]: row for row in db.execute("SELECT * FROM scan_source")}
    metadata = species_metadata(engine)
    for row in rows:
        source_row = sources.get(row["uid"][len("capture-"):])
        options = ingredient_options(metadata.get(row["species"], {}))
        level = int(row["level"])
        locked = [index for index, slot in enumerate(options)
                  if (slot.get("level") or INGREDIENT_UNLOCKS[index]) > level
                  and len(slot["choices"]) > 1]
        if source_row is None or not locked:
            continue
        image = still_image(source, source_row["name"], source_row["sha256"])
        if image is None:
            continue
        stats["checked"] += 1
        if on_progress:
            on_progress(f"食材アイコンの確認 {stats['checked']}: {image.name}")
        names = list(identify([image], options) or [])
        stored = json.loads(row["ingredients_json"] or "[]")
        notes = []
        for index in locked:
            name = names[index] if index < len(names) else None
            if name and index < len(stored) and stored[index][0] != name:
                notes.append(f"Lv{options[index]['level']}食材: 登録値→"
                             f"{to_japanese('ingredients', stored[index][0])}×{stored[index][1]} / "
                             f"アイコン→{to_japanese('ingredients', name)}")
        if not notes:
            continue
        note = row["repair_note"]
        for text in notes:
            note = f"{note}、{text}" if note and text not in note.split("、") else note or text
        db.execute("""UPDATE individual SET verified=0,auto_verified=0,repair_note=?
                      WHERE uid=? AND review_confirmed=0""", (note, row["uid"]))
        stats["downgraded"] += 1
        stats["names"][row["uid"]] = names
        stats["notes"][row["uid"]] = notes
    db.commit()
    return stats


def flagged_items(db: sqlite3.Connection, source: Path, vlm_cache: Optional[Path],
                  fallback_model: Optional[str], frames: Path) -> Dict[str, Any]:
    """Collect the unverified, unconfirmed rows for `sync --recheck`.

    Returns ``items`` (one per row; see ``row_item``), ``images`` (stored
    uid -> still image for rows eligible for a re-read) and ``stills``
    (stored uid -> still image for every row whose capture was found). A still row
    (``capture-<sha>`` uid) is mapped to its capture through ``scan_source``
    (SHA-256 + name) and found in ``source``. When the original Qwen read is
    in the cache, its scan-time metadata (unread fields, ingredient options
    and amounts) replaces the conservative ``UNKNOWN_READ_MISSING``; the
    stored field values always win. Only captures read by a Qwen model other
    than ``fallback_model`` are eligible for a re-read.
    """
    # Every unverified row: SP mismatches, rows never SP-checked (imported
    # before the automatic check existed) and partial matches, so a rule change
    # (e.g. exact matches at Lv56+) reaches stored rows. `check_items` checks
    # them first and only the mismatches go on to repair.
    rows = db.execute("""SELECT * FROM individual WHERE archived=0 AND review_confirmed=0
                         AND verified=0 ORDER BY box_index, uid""").fetchall()
    sources = {row["sha256"][:20]: row for row in db.execute("SELECT * FROM scan_source")}
    items, images, stills = [], {}, {}
    for row in rows:
        item = row_item(row)
        items.append(item)
        uid = row["uid"]
        if not uid.startswith("capture-") or uid[len("capture-"):] not in sources:
            continue
        source_row = sources[uid[len("capture-"):]]
        image = still_image(source, source_row["name"], source_row["sha256"])
        if image is None:
            continue
        stills[uid] = image
        item["capture_sha256"] = source_row["sha256"]
        item["ocr_sources"] = [source_row["name"]]
        reader = str(source_row["reader"])
        if not reader.startswith("qwen:"):
            continue
        model = reader[len("qwen:"):]
        item["read_model"] = model
        _attach_cached_read(item, row, image, model, vlm_cache, frames)
        if fallback_model and model != fallback_model:
            images[uid] = image
    return {"items": items, "images": images, "stills": stills}


def _attach_cached_read(item: Dict[str, Any], row: Mapping[str, Any], image: Path, model: str,
                        vlm_cache: Optional[Path], frames: Path) -> None:
    """Add the scan-time metadata of a cached Qwen read; never loads a model."""
    from .ingest import file_sha256
    from .vlm import QwenReader, scan as vlm_scan

    reader = QwenReader(model, vlm_cache)
    cache = reader._cache_path(file_sha256(image))
    if cache is None or not cache.exists():
        return
    try:
        found = vlm_scan(image, reader, frames)
    except Exception:  # noqa: BLE001 - the metadata is optional
        return
    if len(found) != 1 or found[0].get("species") != row["species"]:
        return
    for key in ("ocr_missing", "ingredient_options", "ingredient_amounts", "ingredient_names",
                "berry_ja", "field_confidence"):
        if key in found[0]:
            item[key] = found[0][key]


def count_summary(stats: Mapping[str, Any]) -> str:
    """One Japanese summary line for ``repair_mismatches`` counts."""
    return (f"SP不一致の補正: SPで補正 {stats.get('sp_repaired', 0)}匹 / "
            f"読み直しで解消 {stats.get('escalated_fixed', 0)}匹"
            f"（読み直し {stats.get('escalated', 0)}匹） / "
            f"不一致のまま {stats.get('still_mismatch', 0)}匹")


def agreement_summary(stats: Mapping[str, Any]) -> str:
    """One Japanese summary line for ``confirm_partials`` counts."""
    return (f"2モデルでの確認: 確認 {stats.get('checked', 0)}匹 / "
            f"一致して確定 {stats.get('confirmed', 0)}匹 / "
            f"読みが食い違い {stats.get('disagreed', 0)}匹")
