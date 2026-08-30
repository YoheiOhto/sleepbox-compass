from __future__ import annotations

import difflib
import itertools
import json
import math
import os
import platform
import re
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .localization import names, normalize_individual, to_japanese

ROOT = Path(__file__).parents[2]
VISION_SOURCE = ROOT / "engine/macos/vision_ocr.swift"
VISION_BINARY = ROOT / ".cache/vision-ocr"
VISION_VOCABULARY = ROOT / ".cache/vision-vocabulary.json"
DEMO_SOURCE = ROOT / "engine/macos/make_demo.swift"
VENDOR_COMMON = ROOT / "engine/vendor/nerolis-lab/common/src"

# Fields that identify which individual a frame is showing. A scrolling capture
# keeps them stable while the rest of the screen changes.
IDENTITY_KEYS = ("species", "nature", "sp")
# Pokemon Sleep unlocks the five subskill rows at these levels. They must match
# the pinned engine's own `limitSubSkillsToLevel` (common/src/utils/
# subskill-utils), which counts a row as active at 10/25/50/70/80; recording
# 75/100 instead left the fourth and fifth rows permanently locked in every
# Lv70/Lv80 evaluation.
SUBSKILL_UNLOCKS = (10, 25, 50, 70, 80)
MAX_SUBSKILLS = len(SUBSKILL_UNLOCKS)
INGREDIENT_SLOTS = 3
# Ingredient amounts sit on one screen row. The band is prior knowledge used to
# rank candidate rows, not a hard crop, so a differently cropped or scrolled
# capture still resolves.
INGREDIENT_ROW_CENTER = .705
INGREDIENT_ROW_MIN = .50
INGREDIENT_ROW_MAX = .88
INGREDIENT_ROW_TOLERANCE = .04

# Vision occasionally replaces one katakana stroke in the compact species
# header.  These aliases are deliberately limited to observed near-misses of
# names in the game's closed vocabulary; they are not broad fuzzy guesses.
SPECIES_OCR_ALIASES = {
    "ワーノコ": "TOTODILE",
    "カゲボウス": "SHUPPET",
    "ーンフィア": "SYLVEON",
}


def ensure_vision_binary() -> Path:
    if VISION_BINARY.exists() and VISION_BINARY.stat().st_mtime >= VISION_SOURCE.stat().st_mtime:
        return VISION_BINARY
    VISION_BINARY.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, CLANG_MODULE_CACHE_PATH=str(ROOT / ".cache/clang"))
    Path(env["CLANG_MODULE_CACHE_PATH"]).mkdir(parents=True, exist_ok=True)
    architecture = "arm64" if platform.machine() == "arm64" else "x86_64"
    proc = subprocess.run([
        "swiftc", "-parse-as-library", "-target", f"{architecture}-apple-macosx15.0",
        "-O", str(VISION_SOURCE), "-o", str(VISION_BINARY),
        "-framework", "Vision", "-framework", "AVFoundation", "-framework", "ImageIO",
    ], capture_output=True, text=True, check=False, env=env)
    if proc.returncode:
        raise RuntimeError("macOS Vision OCRのビルドに失敗しました: " + proc.stderr.strip())
    return VISION_BINARY


def write_vision_vocabulary() -> Path:
    """Write the closed game vocabulary Vision should prefer while recognizing.

    Vision's Japanese language correction rewrites unfamiliar katakana into
    ordinary words, which silently turns species and subskill names into
    near-miss text. Supplying the known names as custom words keeps the
    correction inside the vocabulary this project can actually resolve.
    """
    table = names()
    words = sorted({japanese
                    for category in ("species", "natures", "mainskills", "berries",
                                     "ingredients", "subskills")
                    for japanese in table.get(category, {}).values() if japanese})
    payload = json.dumps(words, ensure_ascii=False)
    VISION_VOCABULARY.parent.mkdir(parents=True, exist_ok=True)
    if not VISION_VOCABULARY.exists() or VISION_VOCABULARY.read_text(encoding="utf-8") != payload:
        VISION_VOCABULARY.write_text(payload, encoding="utf-8")
    return VISION_VOCABULARY


def recognize_path(path: Path, interval: float = .8,
                   on_progress: Optional[Callable[[str], None]] = None) -> List[Dict[str, Any]]:
    binary = ensure_vision_binary()
    vocabulary = write_vision_vocabulary()
    proc = subprocess.Popen([str(binary), str(path), str(interval), str(vocabulary)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    # The binary writes the full JSON result to stdout only once, at the very
    # end. Draining it concurrently avoids a deadlock: without a reader, a
    # large result can fill the stdout pipe buffer and block the child while
    # we are still waiting on stderr progress lines below.
    stdout_chunks: List[str] = []
    reader = threading.Thread(target=lambda: stdout_chunks.append(proc.stdout.read()), daemon=True)
    reader.start()
    stderr_lines: List[str] = []
    last_percent = -1
    for raw_line in proc.stderr:
        line = raw_line.rstrip("\n")
        if line.startswith("PROGRESS "):
            # Progress lines are never part of a failure message; dropping them
            # keeps a per-frame counter from burying the real OCR error below.
            fields = line.split(" ")
            if on_progress and len(fields) == 4 and all(x.isdigit() for x in fields[1:]):
                frame, total, seconds = (int(x) for x in fields[1:])
                percent = min(100, round(frame / total * 100)) if total else 0
                if percent != last_percent:
                    last_percent = percent
                    on_progress(f"{percent}%（{seconds}秒経過, フレーム{frame}/{total}）")
        elif line:
            stderr_lines.append(line)
    reader.join()
    proc.wait()
    if proc.returncode:
        raise RuntimeError("\n".join(stderr_lines).strip() or f"OCRに失敗しました: {path}")
    return json.loads("".join(stdout_chunks))


def _compact(value: str) -> str:
    compact = re.sub(r"[\s・･:：._\-]", "", value).lower()
    # Vision renders the Latin size suffix of a subskill as the katakana it looks
    # like, turning "最大所持数アップL" into "最大所持数アップレ". No name in the
    # game vocabulary ends in レ, so only a trailing one is rewritten.
    return re.sub(r"レ$", "l", compact)


def _best_match(texts: Sequence[str], category: str,
                cutoff: float) -> Tuple[Optional[Tuple[str, float]], bool]:
    """Look one OCR reading up in the closed game vocabulary.

    Args:
        texts: Candidate OCR strings for a single field.
        category: Vocabulary table name, such as "subskills".
        cutoff: Minimum score to accept at all.

    Returns:
        The best (english_key, score) pair or None, plus whether a different
        entry scored exactly as well. A subskill whose trailing size letter was
        dropped fits S, M and L identically, and the caller must not present
        whichever one the table happened to list first as a read value.
    """
    table = names()[category]
    best: Optional[Tuple[str, float]] = None
    best_rank: Tuple[float, int] = (0.0, 0)
    ambiguous = False
    for raw in texts:
        compact = _compact(raw)
        if category == "species":
            alias = next((canonical for miss, canonical in SPECIES_OCR_ALIASES.items()
                          if compact == miss or compact.endswith(miss)), None)
            if alias:
                return (alias, .95), False
        for english, japanese in table.items():
            target = _compact(japanese)
            if target and target in compact:
                # Labels such as "きのみ ドリ" contain a very short canonical
                # value. Exact containment is stronger evidence than its length.
                # When several names are contained, the longest one is the name
                # actually printed: 26 species names end in a shorter species
                # name, so without this "コラッタ" resolved to RATICATE and
                # "ブラッキー" to CHANSEY, at full confidence.
                score, specificity = .96, len(target)
            else:
                score, specificity = difflib.SequenceMatcher(None, compact, target).ratio(), 0
            if score < cutoff:
                continue
            rank = (score, specificity)
            if best is None or rank > best_rank:
                best, best_rank, ambiguous = (english, score), rank, False
            elif rank == best_rank and english != best[0]:
                ambiguous = True
    return best, ambiguous


def _match(texts: Sequence[str], category: str, cutoff: float = .72) -> Optional[Tuple[str, float]]:
    return _best_match(texts, category, cutoff)[0]


def parse_dex_friendship(observations: Sequence[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """Extract one species' befriending state from a Sleep Style Dex capture.

    OCR is deliberately conservative: the user confirms the resulting JSON
    before import because badge artwork is not reliably readable text.
    """
    texts = [str(row.get("text", "")) for row in observations]
    species_match = _match(texts, "species")
    joined = " ".join(texts)
    level = re.search(r"(?:なかよし|フレンド)(?:レベル)?\s*(?:Lv\.?|レベル)?\s*(\d+)", joined, re.I)
    if not species_match or not level:
        return None
    badge = "gold" if re.search(r"ゴールド|金", joined) else (
        "silver" if re.search(r"シルバー|銀", joined) else (
            "bronze" if re.search(r"ブロンズ|銅", joined) else "none"))
    return {"species": species_match[0], "friendship_level": int(level.group(1)),
            "badge": badge, "source": "dex-ocr", "confidence": species_match[1]}


def ingredient_amount_row(observations: Sequence[Mapping[str, Any]]) -> List[int]:
    """Read the ingredient amounts from whichever screen row actually holds them.

    In the game screen ingredient names are icons, so only the "x n" amounts are
    OCR text and the row has to be located by layout. A fixed coordinate band
    silently drops every amount as soon as the capture is cropped, rotated or
    scrolled differently, so the amount observations are clustered into screen
    rows and the row that looks like the ingredient row is chosen. Rows are only
    accepted with positive evidence, keeping an unrelated "x2" elsewhere on the
    screen from being read as an ingredient.

    Args:
        observations: Vision text observations for a single frame.

    Returns:
        Up to three amounts in left-to-right order, or an empty list when no
        row can be identified.
    """
    marks: List[Tuple[float, float, int, bool]] = []
    for observation in observations:
        if "y" not in observation:
            continue  # sidecar payloads carry no layout, so the row is unverifiable
        text = str(observation.get("text", ""))
        amount = re.search(r"[x×]\s*(\d{1,2})", text, re.I)
        if not amount:
            continue
        slot_badge = re.search(r"(?:Lv\.?|レベル)\s*(?:1|30|60)\b", text, re.I)
        marks.append((float(observation["y"]), float(observation.get("x", 0)),
                      int(amount.group(1)), bool(slot_badge)))
    if not marks:
        return []
    rows: List[List[Tuple[float, float, int, bool]]] = []
    for mark in sorted(marks, key=lambda item: -item[0]):
        if rows and abs(rows[-1][0][0] - mark[0]) <= INGREDIENT_ROW_TOLERANCE:
            rows[-1].append(mark)
        else:
            rows.append([mark])
    plausible = []
    for row in rows:
        if len(row) > INGREDIENT_SLOTS:
            continue  # a row with more than three amounts is not the ingredient row
        center = sum(mark[0] for mark in row) / len(row)
        badged = any(mark[3] for mark in row)
        if badged or INGREDIENT_ROW_MIN <= center <= INGREDIENT_ROW_MAX:
            plausible.append((badged, -abs(center - INGREDIENT_ROW_CENTER), row))
    if not plausible:
        return []
    best = max(plausible, key=lambda item: item[:2])[2]
    return [amount for _, _, amount, _ in sorted(best, key=lambda mark: mark[1])]


# The detail header prints "Lv.n" and the species name in the left column just
# under the SP line. The same "Lv.n" text also appears on the Lv.30/Lv.60
# ingredient badges in the right half of that same band, and on the subskill
# unlock badges further down the screen, so the header is located by position.
HEADER_LEVEL_MAX_X = .5
HEADER_LEVEL_MAX_DY = .06
HEADER_LEVEL_PATTERN = re.compile(r"^\W{0,3}(?:Lv\.?|レベル)\s*(\d{1,3})\b", re.I)
SP_ANCHOR_PATTERN = re.compile(r"^\W{0,3}(?:SP|RP)\b", re.I)
# Heading that separates the subskill block from the nature panel below it.
DETAILS_HEADING = "詳細ステータス"
# A subskill's unlock badge is drawn just above its name, in the same column,
# and Vision reports the two as separate observations.
SUBSKILL_BADGE_PATTERN = re.compile(r"(?:Lv\.?|レベル)\s*(\d{1,3})\b", re.I)
SUBSKILL_BADGE_MAX_DY = .05
SUBSKILL_BADGE_MAX_DX = .18


def subskill_badges(observations: Sequence[Mapping[str, Any]]) -> List[Tuple[float, float, int]]:
    """Collect (y, x, level) for every readable unlock badge on the frame."""
    result = []
    for observation in observations:
        if "y" not in observation:
            continue
        text = str(observation.get("text", ""))
        found = SUBSKILL_BADGE_PATTERN.search(text)
        if found and not _match([text], "subskills", .78):
            result.append((float(observation["y"]), float(observation.get("x", 0.0)),
                           int(found.group(1))))
    return result


def badge_for_row(badges: Sequence[Tuple[float, float, int]],
                  observation: Mapping[str, Any]) -> int:
    """Return the unlock level printed above one subskill name, or 0.

    Pairing by position keeps the five rows correct when a name is unreadable:
    the previous reading-order fallback shifted every later row's unlock level
    as soon as one row was dropped.
    """
    if "y" not in observation:
        return 0
    row_y, row_x = float(observation["y"]), float(observation.get("x", 0.0))
    candidates = [(y - row_y, level) for y, x, level in badges
                  if 0 < y - row_y <= SUBSKILL_BADGE_MAX_DY
                  and abs(x - row_x) <= SUBSKILL_BADGE_MAX_DX]
    return min(candidates)[1] if candidates else 0


def header_level(observations: Sequence[Mapping[str, Any]]) -> Optional[int]:
    """Read the current level from the SP header using the screen layout.

    Anchoring on the species name does not work in practice: Vision often emits
    the level and the name as two observations ("Lv.14", "ムンナ"), and the name
    itself is sometimes a near-miss that only `SPECIES_OCR_ALIASES` recovers, so
    the canonical name the pattern searches for is not in the text at all. The
    header's position relative to the SP line survives both cases.

    Args:
        observations: Vision text observations for a single frame.

    Returns:
        The level printed in the header, or None when the frame carries no
        layout or no header could be located.
    """
    anchors = [float(x["y"]) for x in observations
               if "y" in x and SP_ANCHOR_PATTERN.match(str(x.get("text", "")).strip())]
    if not anchors:
        return None  # sidecar payloads carry no layout, so the caller falls back to text
    sp_y = max(anchors)
    best: Optional[Tuple[float, int]] = None
    for observation in observations:
        if "y" not in observation or float(observation.get("x", 1.0)) >= HEADER_LEVEL_MAX_X:
            continue
        found = HEADER_LEVEL_PATTERN.match(str(observation.get("text", "")).strip())
        distance = abs(float(observation["y"]) - sp_y)
        if found and distance <= HEADER_LEVEL_MAX_DY and (best is None or distance < best[0]):
            best = (distance, int(found.group(1)))
    return best[1] if best else None


def parse_frame(frame: Mapping[str, Any]) -> Dict[str, Any]:
    observations = frame.get("observations", [])
    texts = [str(x.get("text", "")) for x in observations]
    joined = "\n".join(texts)
    result: Dict[str, Any] = {"ocr_frame": frame.get("frame"),
                              "ocr_seconds": frame.get("seconds"), "field_confidence": {}}
    for field, category, cutoff in (("species", "species", .76), ("nature", "natures", .78),
                                    ("main_skill", "mainskills", .68), ("berry", "berries", .84)):
        found = _match(texts, category, cutoff)
        if found:
            result[field], result["field_confidence"][field] = found
    # Current level must be anchored to the SP header. Unanchored Lv.30/Lv.60
    # labels belong to ingredient slots, and subskill unlock badges are also
    # visible after scrolling the header off screen.
    level = header_level(observations)
    if level is None and result.get("species"):
        species_ja = re.escape(to_japanese("species", result["species"]))
        # Vision emits the header level and the name as separate observations,
        # which `_compact`-free joining turns into a line break between them.
        patterns = (
            rf"(?:SP|RP)\s*[:：]?\s*[0-9,]{{3,6}}[\s\S]{{0,45}}?(?:Lv\.?|レベル)\s*(\d{{1,3}})[\s\S]{{0,16}}{species_ja}",
            rf"{species_ja}[\s\S]{{0,45}}?(?:Lv\.?|レベル)\s*(\d{{1,3}})[\s\S]{{0,35}}?(?:SP|RP)\s*[:：]?\s*[0-9,]{{3,6}}",
            rf"(?:Lv\.?|レベル)\s*(\d{{1,3}})[\s\S]{{0,35}}?(?:SP|RP)\s*[:：]?\s*[0-9,]{{3,6}}[\s\S]{{0,35}}?{species_ja}",
        )
        found = next((match for pattern in patterns
                      if (match := re.search(pattern, joined, re.I))), None)
        level = int(found.group(1)) if found else None
    if level is not None:
        result["level"] = level
    # Vision may emit the header label and number as separate observations
    # ("SP." on one line, then "473").  Treat punctuation and line breaks as
    # separators so two otherwise-identical species are not merged.
    # The rounded game font's final 3 is occasionally recognized as hiragana
    # ろ (observed as "SP 63ろ"). It is safe to normalize only inside the
    # tightly anchored numeric SP token.
    sp = re.search(r"(?:SP|RP)[\s:：.．]*([0-9,ろロ]{3,6})", joined, re.I)
    if sp:
        parsed_sp = int(sp.group(1).replace(",", "").replace("ろ", "3").replace("ロ", "3"))
        if parsed_sp >= 200:
            result["sp"] = parsed_sp
    main_skill_ja = (to_japanese("mainskills", result["main_skill"])
                     if result.get("main_skill") else None)
    skill_level = (re.search(rf"{re.escape(main_skill_ja)}[\s\S]{{0,35}}?Lv\.?\s*(\d+)", joined, re.I)
                   if main_skill_ja else None)
    if skill_level:
        result["skill_level"] = int(skill_level.group(1))

    # The unlock badge is kept raw here (0 when it was not readable). Assigning
    # the fixed unlock levels is deferred to `finalize_candidate`, so a badge
    # read in any frame of a scroll wins over a positional guess.
    #
    # Everything below the 詳細ステータス heading is the nature panel, whose stat
    # labels ("おてつだいスピード ▲") are one character away from real subskill
    # names ("おてつだいスピードS") and fuzzy-match them. Left in, that phantom
    # row competes for one of the five slots, and in a video it collects a vote
    # from every frame that shows the panel.
    details_y = next((float(x["y"]) for x in observations
                      if "y" in x and DETAILS_HEADING in str(x.get("text", ""))), None)
    badges = subskill_badges(observations)
    subskills: List[List[Any]] = []
    subskill_rows: Dict[str, List[Any]] = {}
    for observation in observations:
        text = str(observation.get("text", ""))
        if details_y is not None and float(observation.get("y", 1.0)) < details_y:
            continue
        found, ambiguous = _best_match([text], "subskills", .78)
        if not found:
            continue
        if ambiguous:
            # The size letter was dropped, so S/M/L fit equally well. Keep the
            # row so the other four keep their slots, but make review mandatory.
            result["subskills_ambiguous"] = True
        badge = re.search(r"Lv\.?\s*(\d{1,3})", text, re.I)
        unlock = int(badge.group(1)) if badge else badge_for_row(badges, observation)
        if ambiguous:
            ambiguous_rows = result.setdefault("subskills_ambiguous_rows", [])
            marker = {"unlock": unlock, "value": found[0]}
            if marker not in ambiguous_rows:
                ambiguous_rows.append(marker)
        row = subskill_rows.get(found[0])
        if row is None:
            row = [found[0], unlock]
            subskill_rows[found[0]] = row
            subskills.append(row)
        elif unlock and not row[1]:
            row[1] = unlock
    if subskills:
        result["subskills"] = subskills

    ingredients = []
    for text in texts:
        found = _match([text], "ingredients", .78)
        if found:
            amount = re.search(r"[x×]\s*(\d{1,2})", text, re.I)
            ingredients.append([found[0], int(amount.group(1)) if amount else 0])
    if ingredients:
        result["ingredients"] = ingredients
    amounts = ingredient_amount_row(observations)
    if amounts:
        result["ingredient_amounts"] = amounts
    confidences = [float(x.get("confidence", 0)) for x in observations]
    result["ocr_confidence"] = sum(confidences) / len(confidences) if confidences else 0
    return result


def identity_conflict(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Report whether two frames disagree about which individual they show."""
    return any(left.get(key) and right.get(key) and left[key] != right[key]
               for key in IDENTITY_KEYS)


def identity_agrees(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Report whether two frames positively confirm the same individual."""
    if identity_conflict(left, right):
        return False
    return any(left.get(key) and right.get(key) and left[key] == right[key]
               for key in IDENTITY_KEYS)


def merge_subskills(existing: Sequence[Sequence[Any]],
                    incoming: Sequence[Sequence[Any]]) -> List[List[Any]]:
    """Union subskill rows by name, counting how many frames saw each row.

    A scrolling capture shows a different subset of the five rows per frame, so
    replacing the list with whichever frame saw the most rows drops rows that
    only appear in another frame. The vote count keeps a single misread from
    permanently occupying one of the five slots.
    """
    merged = [[entry[0], entry[1], entry[2] if len(entry) > 2 else 1] for entry in existing]
    by_name = {entry[0]: entry for entry in merged}
    for entry in incoming:
        votes = entry[2] if len(entry) > 2 else 1
        row = by_name.get(entry[0])
        if row is None:
            row = [entry[0], entry[1], votes]
            by_name[entry[0]] = row
            merged.append(row)
            continue
        row[2] += votes
        if entry[1] and not row[1]:
            row[1] = entry[1]
    return merged


def merge_ingredients(existing: Sequence[Sequence[Any]],
                      incoming: Sequence[Sequence[Any]]) -> List[List[Any]]:
    """Union ingredient rows by name and amount, counting frames per row.

    Unlike subskills the same ingredient legitimately fills two slots at
    different amounts, so the amount is part of the row identity.
    """
    merged = [[entry[0], entry[1], entry[2] if len(entry) > 2 else 1] for entry in existing]
    by_slot = {(entry[0], entry[1]): entry for entry in merged}
    for entry in incoming:
        votes = entry[2] if len(entry) > 2 else 1
        row = by_slot.get((entry[0], entry[1]))
        if row is None:
            row = [entry[0], entry[1], votes]
            by_slot[(entry[0], entry[1])] = row
            merged.append(row)
        else:
            row[2] += votes
    return merged


def resolve_subskill_unlocks(entries: Sequence[Sequence[Any]]) -> List[List[Any]]:
    """Assign the fixed unlock levels, keeping badges actually read from a frame.

    Rows seen in more frames win the five slots; rows whose badge was never
    readable take the unlock levels the readable rows did not claim.
    """
    ranked = sorted(entries, key=lambda entry: -(entry[2] if len(entry) > 2 else 1))[:MAX_SUBSKILLS]
    known = sorted(([entry[0], int(entry[1])] for entry in ranked if entry[1]),
                   key=lambda entry: entry[1])
    claimed = {entry[1] for entry in known}
    spare = [level for level in SUBSKILL_UNLOCKS if level not in claimed]
    unknown = [entry[0] for entry in ranked if not entry[1]]
    filled = known + [[name, spare[index]] for index, name in enumerate(unknown)
                      if index < len(spare)]
    return sorted(filled, key=lambda entry: entry[1])


def resolve_ingredient_slots(entries: Sequence[Sequence[Any]]) -> List[List[Any]]:
    """Keep the three ingredient rows seen in the most frames, in screen order."""
    indexed = list(enumerate(entries))
    ranked = sorted(indexed, key=lambda pair: (-(pair[1][2] if len(pair[1]) > 2 else 1), pair[0]))
    kept = sorted(ranked[:INGREDIENT_SLOTS], key=lambda pair: pair[0])
    return [[entry[0], entry[1]] for _, entry in kept]


def _field_score(part: Mapping[str, Any], field: str) -> float:
    """Score how much a frame should be trusted for one field."""
    confidence = part.get("field_confidence", {})
    if field in confidence:
        return float(confidence[field])
    return float(part.get("ocr_confidence", 0.0))


def _absorb(current: Dict[str, Any], scores: Dict[str, float], part: Mapping[str, Any]) -> None:
    """Fold one frame into the individual being assembled.

    Scalar fields keep the reading with the highest confidence rather than the
    last one seen, because the final frames of a scroll are systematically the
    blurriest. Slot fields are unioned instead of replaced.
    """
    for key, value in part.items():
        if key == "field_confidence":
            merged = current.setdefault(key, {})
            for field, score in value.items():
                merged[field] = max(float(score), float(merged.get(field, 0.0)))
        elif key == "subskills":
            current[key] = merge_subskills(current.get(key, []), value)
        elif key == "ingredients":
            current[key] = merge_ingredients(current.get(key, []), value)
        elif key == "ingredient_amounts":
            if len(value) > len(current.get(key, [])):
                current[key] = value
        elif value not in (None, "", [], {}):
            score = _field_score(part, key)
            if key not in current or score >= scores.get(key, -1.0):
                current[key] = value
                scores[key] = score


def merge_frames(frames: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Group consecutive frames into individuals and fold each group into one row.

    A boundary is only accepted once two consecutive frames agree on the new
    identity. Splitting on the first disagreement turned a single misread frame
    mid-scroll into a phantom individual, which left both halves with virtual
    gaps in their ingredients and subskills.
    """
    partials = [parse_frame(frame) for frame in frames]
    groups: List[Dict[str, Any]] = []
    current: Dict[str, Any] = {}
    scores: Dict[str, float] = {}
    held: Optional[Dict[str, Any]] = None
    for part in partials:
        if held is not None:
            if identity_agrees(held, part):
                if current:
                    groups.append(current)
                current, scores = {}, {}
                _absorb(current, scores, held)
            # A held frame that agrees with neither neighbour is a transient
            # misread and is dropped rather than started as an individual.
            held = None
        if current and identity_conflict(current, part):
            held = part
            continue
        _absorb(current, scores, part)
    if held is not None:
        # A capture can end one frame into the next individual. Keeping it costs
        # one row that review already flags, while dropping it loses real data.
        if current:
            groups.append(current)
        current, scores = {}, {}
        _absorb(current, scores, held)
    if current:
        groups.append(current)
    return [finalize_candidate(x, index + 1) for index, x in enumerate(groups)]


def finalize_candidate(item: Dict[str, Any], box_index: int) -> Dict[str, Any]:
    missing = [key for key in ("species", "nature", "ingredients", "subskills", "main_skill")
               if not item.get(key)]
    if item.get("subskills_ambiguous") and "subskills" not in missing:
        missing.append("subskills")
    # The frame-wide Vision mean sits near .65 on every capture because it
    # averages in the status bar and the UI chrome, so folding it in made every
    # individual look equally unreliable. Confidence is the weakest vocabulary
    # match behind the values actually kept; the raw mean stays in
    # `ocr_confidence` for anyone who wants it.
    field_scores = list(item.get("field_confidence", {}).values())
    confidence = min(field_scores) if field_scores else float(item.get("ocr_confidence", 0) or 0)
    result = {**item, "box_index": box_index, "confidence": round(confidence, 4),
              "verified": False, "ocr_missing": missing}
    result.setdefault("nature", "Hardy")
    result.setdefault("main_skill", "Metronome")
    result.setdefault("skill_level", 1)
    result.setdefault("species", "UNKNOWN")
    result["ingredients"] = resolve_ingredient_slots(result.get("ingredients", []))
    result["subskills"] = resolve_subskill_unlocks(result.get("subskills", []))
    result["species_ja"] = to_japanese("species", result["species"])
    return normalize_individual(result)


def enrich_with_species_data(items: List[Dict[str, Any]],
                             engine: str = "engine/bin/pokesleep-engine",
                             pokemon_data: Optional[Mapping[str, Any]] = None) -> List[Dict[str, Any]]:
    """Resolve deterministic berry and constrain icon-only ingredient fields."""
    if pokemon_data is None:
        try:
            from .engine import run_engine
            pokemon_data = run_engine({"mode": "metadata"}, engine).get("pokemon", {})
        except Exception:
            pokemon_data = load_source_metadata()
    if not pokemon_data:
        return items
    for item in items:
        ingredients_need_review = "ingredients" in item.get("ocr_missing", [])
        metadata = pokemon_data.get(item.get("species"), {})
        # Box slots aren't captured by OCR (the game screen doesn't show one),
        # so display order follows the National Pokédex number instead of scan
        # order.
        if metadata.get("pokedex_number") is not None:
            item["box_index"] = metadata["pokedex_number"]
        if metadata.get("berry"):
            item["berry"] = metadata["berry"]
            item["berry_ja"] = to_japanese("berries", metadata["berry"])
            item.setdefault("field_confidence", {})["berry"] = 1.0
        if metadata.get("main_skill") and not item.get("main_skill"):
            item["main_skill"] = metadata["main_skill"]
            item.setdefault("field_confidence", {})["main_skill"] = 1.0
        options = []
        for slot in metadata.get("ingredients", []):
            choices = [[name, amount, to_japanese("ingredients", name)]
                       for name, amount in slot.get("choices", [])]
            options.append({"level": slot.get("level"), "choices": choices})
        if options:
            item["ingredient_options"] = options
            # Slots are positional (Lv1/Lv30/Lv60), so they are resolved by
            # index. Growing the list by append dropped a slot the amount row
            # had already identified whenever an earlier slot stayed ambiguous:
            # amounts 1/2/3 on a Totodile resolve Lv60 to Oil x3, but the
            # ambiguous Lv30 slot left the list one short, so Lv60 fell back to
            # the species' first option (Sausage x4) and SP never matched.
            slots: List[Optional[List[Any]]] = list(item.get("ingredients", []))[:len(options)]
            slots += [None] * (len(options) - len(slots))
            amounts = item.get("ingredient_amounts", [])
            inferred: List[Optional[List[Any]]] = []
            for index, amount in enumerate(amounts[:len(options)]):
                matches = [choice for choice in options[index]["choices"] if choice[1] == amount]
                inferred.append(list(matches[0][:2]) if len(matches) == 1 else None)
                if inferred[index]:
                    slots[index] = inferred[index]
            if len(inferred) == len(options) and all(inferred):
                ingredients_need_review = False
            # Keep calculations runnable before review by filling unresolved
            # slots with the species' first legal option. `ocr_missing` remains
            # set, so these assumptions are always shown as provisional.
            for index, slot in enumerate(options):
                if slots[index] is None and slot["choices"]:
                    slots[index] = list(slot["choices"][0][:2])
            ingredients = []
            for value in slots:
                if value is None:
                    break  # a slot with no legal option ends the positional list
                ingredients.append(value)
            item["ingredients"] = ingredients
        missing = [key for key in item.get("ocr_missing", [])
                   if key not in ("berry", "ingredients", "main_skill")]
        missing.extend(key for key in ("species", "nature", "subskills", "main_skill")
                       if not item.get(key) and key not in missing)
        if (ingredients_need_review or
                len(item.get("ingredients", [])) < len(options or [None, None, None])):
            missing.append("ingredients")
        item["ocr_missing"] = missing
    return items


def resolve_ingredients_by_sp(items: List[Dict[str, Any]],
                              engine_command: str = "engine/bin/pokesleep-engine",
                              runner: Optional[Callable[..., Dict[str, Any]]] = None
                              ) -> List[Dict[str, Any]]:
    """Resolve ambiguous ingredient slots with one batched exact-SP check.

    Ingredient icons are not text, but every legal combination is known from
    species metadata.  Trying those combinations against the locally pinned
    RP calculator turns the displayed SP into a checksum.  We only accept a
    combination when exactly one candidate matches, so this can never replace
    review with an arbitrary guess.
    """
    if runner is None:
        from .engine import run_engine
        runner = run_engine
    from .engine import individual_to_engine
    candidates: List[Dict[str, Any]] = []
    candidate_map: Dict[str, Tuple[Dict[str, Any], List[List[Any]]]] = {}
    for item_index, item in enumerate(items):
        if ("ingredients" not in item.get("ocr_missing", []) or
                item.get("sp") is None or item.get("species") in (None, "UNKNOWN")):
            continue
        if any(item.get(key) is None for key in ("level", "nature", "main_skill", "skill_level")):
            continue
        options = item.get("ingredient_options", [])
        if not options or any(not slot.get("choices") for slot in options):
            continue
        combinations = itertools.product(*(slot["choices"] for slot in options))
        for combination_index, combination in enumerate(combinations):
            ingredients = [list(choice[:2]) for choice in combination]
            candidate = {**item, "ingredients": ingredients}
            candidate["ingredients_json"] = json.dumps(ingredients)
            candidate["subskills_json"] = json.dumps(item.get("subskills", []))
            uid = f"ingredient-{item_index}-{combination_index}"
            candidates.append({"uid": uid, "displayedSp": item["sp"],
                               "instance": individual_to_engine(candidate)})
            candidate_map[uid] = (item, ingredients)
    if not candidates:
        return items
    try:
        response = runner({"mode": "verify", "tolerance": 0, "strictBelowLevel": 101,
                           "instances": candidates}, engine_command)
    except Exception:
        # OCR and manual review remain usable when the optional Node bridge has
        # not been built yet.
        return items
    matches: Dict[int, List[List[List[Any]]]] = {}
    for result in response.get("results", []):
        if not result.get("match") or result.get("diff") != 0:
            continue
        mapped = candidate_map.get(result.get("uid"))
        if mapped:
            item, ingredients = mapped
            matches.setdefault(id(item), []).append(ingredients)
    for item in items:
        exact = matches.get(id(item), [])
        if not exact:
            continue
        # At Lv30-59 the Lv60 ingredient does not affect SP, so several exact
        # candidates can remain while every candidate agrees on the active
        # Lv1/Lv30 prefix. Commit only positions shared by all exact matches.
        # This recovers useful information without pretending SP can reveal a
        # locked future icon.
        shared = [index for index in range(len(exact[0]))
                  if all(candidate[index] == exact[0][index] for candidate in exact[1:])]
        current = list(item.get("ingredients", []))
        current += [None] * (len(exact[0]) - len(current))
        for index in shared:
            current[index] = exact[0][index]
        item["ingredients"] = [value for value in current if value is not None]
        if shared:
            item["ingredient_slots_resolved_by_sp"] = shared
        if len(shared) == len(exact[0]):
            item["ocr_missing"] = [key for key in item.get("ocr_missing", [])
                                   if key != "ingredients"]
            item["ingredients_resolved_by"] = "sp_exact"
    return items


def resolve_species_variant_by_sp(items: List[Dict[str, Any]],
                                  engine_command: str = "engine/bin/pokesleep-engine",
                                  pokemon_data: Optional[Mapping[str, Any]] = None,
                                  runner: Optional[Callable[..., Dict[str, Any]]] = None
                                  ) -> List[Dict[str, Any]]:
    """Resolve text-identical forms when exact SP identifies one form only."""
    if pokemon_data is None:
        pokemon_data = load_source_metadata()
    if runner is None:
        from .engine import run_engine
        runner = run_engine
    from .engine import individual_to_engine
    candidates: List[Dict[str, Any]] = []
    candidate_map: Dict[str, Tuple[Dict[str, Any], str]] = {}
    for item_index, item in enumerate(items):
        species = item.get("species")
        if (not species or species == "UNKNOWN" or item.get("sp") is None or
                any(item.get(key) is None for key in ("level", "nature", "main_skill", "skill_level"))):
            continue
        family = [name for name in pokemon_data
                  if name == species or name.startswith(species + "_") or species.startswith(name + "_")]
        if len(family) < 2:
            continue
        candidate_index = 0
        for variant in family:
            slots = pokemon_data[variant].get("ingredients", [])
            if not slots or any(not slot.get("choices") for slot in slots):
                continue
            for combination in itertools.product(*(slot["choices"] for slot in slots)):
                ingredients = [list(choice[:2]) for choice in combination]
                candidate = {**item, "species": variant,
                             "ingredients_json": json.dumps(ingredients),
                             "subskills_json": json.dumps(item.get("subskills", []))}
                uid = f"variant-{item_index}-{candidate_index}"
                candidate_index += 1
                candidates.append({"uid": uid, "displayedSp": item["sp"],
                                   "instance": individual_to_engine(candidate)})
                candidate_map[uid] = (item, variant)
    if not candidates:
        return items
    try:
        response = runner({"mode": "verify", "tolerance": 0, "strictBelowLevel": 101,
                           "instances": candidates}, engine_command)
    except Exception:
        return items
    matches: Dict[int, set[str]] = {}
    for result in response.get("results", []):
        if result.get("match") and result.get("diff") == 0 and result.get("uid") in candidate_map:
            item, variant = candidate_map[result["uid"]]
            matches.setdefault(id(item), set()).add(variant)
    changed = []
    for item in items:
        variants = matches.get(id(item), set())
        if len(variants) == 1 and next(iter(variants)) != item.get("species"):
            item["species"] = next(iter(variants))
            item["species_ja"] = to_japanese("species", item["species"])
            item["species_resolved_by"] = "sp_exact"
            item.pop("ingredient_options", None)
            item["ingredients"] = []
            if "ingredients" not in item.setdefault("ocr_missing", []):
                item["ocr_missing"].append("ingredients")
            changed.append(item)
    if changed:
        enrich_with_species_data(changed, pokemon_data=pokemon_data)
    return items


def load_source_metadata() -> Dict[str, Any]:
    """Read the pinned Neroli source when Node's compiled bundle is unavailable."""
    ingredient_file = VENDOR_COMMON / "types/ingredient/ingredients.ts"
    pokemon_dir = VENDOR_COMMON / "types/pokemon"
    if not ingredient_file.exists():
        return {}
    ingredient_source = ingredient_file.read_text(encoding="utf-8")
    ingredient_defs = {constant: (name, int(value)) for constant, name, value in re.findall(
        r"export const (\w+): Ingredient = createIngredient\(\{\s*name: '([^']+)',\s*value: (\d+)",
        ingredient_source)}
    declarations = []
    pattern = re.compile(
        r"export const (\w+): Pokemon = (create(?:Berry|Ingredient|Skill)Specialist|evolvedPokemon|preEvolvedPokemon)"
        r"\((?:(\w+),\s*)?\{([\s\S]*?)\n\}\);"
    )
    for filename in ("berry-pokemon.ts", "ingredient-pokemon.ts", "skill-pokemon.ts"):
        source = (pokemon_dir / filename).read_text(encoding="utf-8")
        declarations.extend(pattern.findall(source))
    result: Dict[str, Any] = {}
    pending = list(declarations)
    while pending:
        next_pending = []
        progressed = False
        for constant, constructor, relative, body in pending:
            name_match = re.search(r"name:\s*'([^']+)'", body)
            name = name_match.group(1) if name_match else constant
            pokedex_match = re.search(r"pokedexNumber:\s*(\d+)", body)
            pokedex_number = int(pokedex_match.group(1)) if pokedex_match else None
            if constructor in ("evolvedPokemon", "preEvolvedPokemon"):
                base = result.get(relative)
                if not base:
                    next_pending.append((constant, constructor, relative, body))
                    continue
                result[constant] = {**base, "name": name, "pokedex_number": pokedex_number}
                progressed = True
                continue
            berry_match = re.search(r"berry:\s*(\w+)", body)
            ingredient_match = re.search(
                r"ingredients:\s*\{\s*a:\s*(\w+),\s*b:\s*(\w+)(?:,\s*c:\s*(\w+))?\s*\}", body)
            if not berry_match or not ingredient_match:
                continue
            constants = [x for x in ingredient_match.groups() if x]
            if any(x not in ingredient_defs for x in constants):
                continue
            specialty_factor = 2 if constructor == "createIngredientSpecialist" else 1
            first_name, first_value = ingredient_defs[constants[0]]
            slots = []
            for level, factor, choices in ((1, 1, constants[:1]), (30, 2.25, constants[:2]),
                                            (60, 3.6, constants)):
                values = []
                for ingredient in choices:
                    ingredient_name, ingredient_value = ingredient_defs[ingredient]
                    amount = math.floor(first_value * factor * specialty_factor / ingredient_value + .5)
                    values.append([ingredient_name, amount])
                slots.append({"level": level, "choices": values})
            result[constant] = {"name": name, "berry": berry_match.group(1),
                                "ingredients": slots, "pokedex_number": pokedex_number}
            progressed = True
        if not progressed:
            break
        pending = next_pending
    return {value["name"]: {"berry": value["berry"], "ingredients": value["ingredients"],
                            "pokedex_number": value.get("pokedex_number")}
            for value in result.values()}


def scan(path: Path, interval: float = .8,
        on_progress: Optional[Callable[[str], None]] = None,
        resolve_sp: bool = True) -> List[Dict[str, Any]]:
    items = enrich_with_species_data(merge_frames(recognize_path(path, interval, on_progress)))
    if not resolve_sp:
        return items
    resolve_species_variant_by_sp(items)
    return resolve_ingredients_by_sp(items)


def make_demo(path: Path) -> Path:
    cache = ROOT / ".cache/clang"
    cache.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, CLANG_MODULE_CACHE_PATH=str(cache))
    proc = subprocess.run(["swift", str(DEMO_SOURCE), str(path)], capture_output=True,
                          text=True, check=False, env=env)
    if proc.returncode:
        raise RuntimeError("OCRデモ画像の生成に失敗しました: " + proc.stderr.strip())
    return path
