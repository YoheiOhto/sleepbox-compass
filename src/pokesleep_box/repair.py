"""Repair SP mismatches: "fast model first, verify with SP, repair or escalate".

The fast default VLM sometimes misreads the small level digits (e.g. Lv.14 as
16). After the SP check, each mismatched row that no human confirmed goes
through two steps:

1. Level repair: the SP is recomputed with the level shifted by -2..+2 (other
   fields unchanged) in one batched engine call. When exactly one shift gives
   the shown SP exactly, the level is corrected, ``repaired`` is set,
   ``repair_note`` records 「Lv 16→14（SPで補正）」 and the row is SP-checked
   again with the normal rules (so Lv56+ rows are still never auto-verified).
   No match or several matches leave the row unchanged.
2. Escalation: rows still mismatched whose source is a still image read by
   Qwen are read again with a larger fallback model (same cache directory),
   re-imported onto the same row, SP-checked and level-repaired again.
   Videos are not escalated.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from .spcheck import MISMATCH, check_items

LEVEL_SHIFTS = (-2, -1, 1, 2)
# Fields assumed unread for a stored row whose scan-time read is unknown
# (`sync --recheck` without a cached read): the SP check then confirms only
# what the SP can prove and leaves the rest for review.
UNKNOWN_READ_MISSING = ["nature", "subskills", "ingredients", "main_skill"]
# Fields that a re-read may fail to see; the stored value is kept then.
KEEP_WHEN_MISSING = ("nature", "ingredients", "subskills", "main_skill")

Progress = Callable[[str], None]


def _add_note(item: Dict[str, Any], note: str) -> str:
    previous = item.get("repair_note")
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


def repair_levels(db: sqlite3.Connection, items: Sequence[Dict[str, Any]], command: str,
                  strict_below_level: int = 56,
                  on_progress: Optional[Progress] = None) -> List[Dict[str, Any]]:
    """Correct the level of SP-mismatched items when one level shift fits exactly.

    Returns the repaired items, already SP-checked again (``check_items``).
    Raises ``engine.EngineUnavailable``.
    """
    from .engine import individual_to_engine, run_engine

    candidates = _mismatched_rows(db, items)
    instances = []
    for uid, (row, _) in candidates.items():
        level = int(row["level"])
        trials = sorted({min(100, max(1, level + shift)) for shift in LEVEL_SHIFTS} - {level})
        for trial in trials:
            instances.append({"uid": f"{uid}|{trial - level}", "displayedSp": row["sp"],
                              "instance": individual_to_engine(dict(row, level=trial))})
    if not instances:
        return []
    response = run_engine({"mode": "verify", "tolerance": 0,
                           "strictBelowLevel": strict_below_level,
                           "instances": instances}, command, on_progress)
    fits: Dict[str, List[int]] = {}
    for result in response.get("results", []):
        uid, _, delta = str(result.get("uid", "")).rpartition("|")
        if uid in candidates and result.get("diff") == 0:
            fits.setdefault(uid, []).append(int(candidates[uid][0]["level"]) + int(delta))
    repaired = []
    for uid, levels in fits.items():
        if len(levels) != 1:
            continue
        row, item = candidates[uid]
        old, new = int(row["level"]), levels[0]
        item["level_read"] = old
        item["level"] = new
        note = _add_note(item, f"Lv {old}→{new}（SPで補正）")
        db.execute("UPDATE individual SET level=?,repaired=1,repair_note=? WHERE uid=?",
                   (new, note, uid))
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
    db.execute("UPDATE individual SET repair_note=? WHERE uid=?", (new["repair_note"], uid))
    db.commit()
    return new


def repair_mismatches(db: sqlite3.Connection, items: List[Dict[str, Any]], command: str,
                      images: Mapping[str, Path], fallback: Optional[Callable[[], Any]],
                      frames: Path, on_progress: Optional[Progress] = None) -> Dict[str, Any]:
    """Run level repair, then escalation, on the SP-mismatched ``items``.

    ``images`` maps a stored uid to its still image (only rows eligible for
    a re-read); ``fallback`` returns the fallback reader and is called at
    most once, only when a row needs it. ``items`` is updated in place: an
    escalated item is replaced by its re-read. Returns the counts
    ``level_repaired``, ``escalated``, ``escalated_fixed``, ``still_mismatch``
    and, when the fallback reader is unusable, ``note``.
    """
    from .vlm import VLMUnavailable

    stats: Dict[str, Any] = {"level_repaired": 0, "escalated": 0, "escalated_fixed": 0}
    stats["level_repaired"] = len(repair_levels(db, items, command, on_progress=on_progress))
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
            repair_levels(db, rereads, command, on_progress=on_progress)
        stats["escalated"] = len(rereads)
        stats["escalated_fixed"] = sum(1 for item in rereads if item.get("sp_check") != MISMATCH)
    stats["still_mismatch"] = sum(1 for item in items if item.get("sp_check") == MISMATCH)
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


def flagged_items(db: sqlite3.Connection, source: Path, vlm_cache: Optional[Path],
                  fallback_model: Optional[str], frames: Path) -> Dict[str, Any]:
    """Collect the SP-mismatched and never-checked rows for `sync --recheck`.

    Returns ``items`` (one per row; see ``row_item``) and ``images`` (stored
    uid -> still image for rows eligible for a re-read). A still row
    (``capture-<sha>`` uid) is mapped to its capture through ``scan_source``
    (SHA-256 + name) and found in ``source``. When the original Qwen read is
    in the cache, its scan-time metadata (unread fields, ingredient options
    and amounts) replaces the conservative ``UNKNOWN_READ_MISSING``; the
    stored field values always win. Only captures read by a Qwen model other
    than ``fallback_model`` are eligible for a re-read.
    """
    # SP mismatches, plus rows that were never SP-checked (e.g. imported before
    # the automatic check existed); `check_items` checks them first and only
    # the mismatches go on to repair.
    rows = db.execute("""SELECT * FROM individual WHERE archived=0 AND review_confirmed=0
                         AND verified=0 AND (sp_flag LIKE 'SP不一致%' OR sp_computed IS NULL)
                         ORDER BY box_index, uid""").fetchall()
    sources = {row["sha256"][:20]: row for row in db.execute("SELECT * FROM scan_source")}
    items, images = [], {}
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
        item["capture_sha256"] = source_row["sha256"]
        item["ocr_sources"] = [source_row["name"]]
        reader = str(source_row["reader"])
        if not reader.startswith("qwen:"):
            continue
        model = reader[len("qwen:"):]
        _attach_cached_read(item, row, image, model, vlm_cache, frames)
        if fallback_model and model != fallback_model:
            images[uid] = image
    return {"items": items, "images": images}


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
    return (f"SP不一致の補正: レベル補正 {stats.get('level_repaired', 0)}匹 / "
            f"読み直しで解消 {stats.get('escalated_fixed', 0)}匹"
            f"（読み直し {stats.get('escalated', 0)}匹） / "
            f"不一致のまま {stats.get('still_mismatch', 0)}匹")
