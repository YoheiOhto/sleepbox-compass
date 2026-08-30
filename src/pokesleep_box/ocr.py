from __future__ import annotations

import difflib
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
# Pokemon Sleep unlocks the five subskill rows at these levels.
SUBSKILL_UNLOCKS = (10, 25, 50, 75, 100)
MAX_SUBSKILLS = len(SUBSKILL_UNLOCKS)
INGREDIENT_SLOTS = 3
# Ingredient amounts sit on one screen row. The band is prior knowledge used to
# rank candidate rows, not a hard crop, so a differently cropped or scrolled
# capture still resolves.
INGREDIENT_ROW_CENTER = .705
INGREDIENT_ROW_MIN = .50
INGREDIENT_ROW_MAX = .88
INGREDIENT_ROW_TOLERANCE = .04


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
        if line.startswith("PROGRESS ") and on_progress:
            frame, total, seconds = (int(x) for x in line.split(" ")[1:])
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
    return re.sub(r"[\s・･:：._\-]", "", value).lower()


def _match(texts: Sequence[str], category: str, cutoff: float = .72) -> Optional[Tuple[str, float]]:
    table = names()[category]
    best: Optional[Tuple[str, float]] = None
    for raw in texts:
        compact = _compact(raw)
        for english, japanese in table.items():
            target = _compact(japanese)
            if target and target in compact:
                # Labels such as "きのみ ドリ" contain a very short canonical
                # value. Exact containment is stronger evidence than its length.
                score = .96
            else:
                score = difflib.SequenceMatcher(None, compact, target).ratio()
            if score >= cutoff and (best is None or score > best[1]):
                best = (english, score)
    return best


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
    header_level = None
    if result.get("species"):
        species_ja = re.escape(to_japanese("species", result["species"]))
        patterns = (
            rf"(?:SP|RP)\s*[:：]?\s*[0-9,]{{3,6}}[\s\S]{{0,45}}?(?:Lv\.?|レベル)\s*(\d{{1,3}})[^\n]{{0,16}}{species_ja}",
            rf"{species_ja}[\s\S]{{0,45}}?(?:Lv\.?|レベル)\s*(\d{{1,3}})[\s\S]{{0,35}}?(?:SP|RP)\s*[:：]?\s*[0-9,]{{3,6}}",
            rf"(?:Lv\.?|レベル)\s*(\d{{1,3}})[\s\S]{{0,35}}?(?:SP|RP)\s*[:：]?\s*[0-9,]{{3,6}}[\s\S]{{0,35}}?{species_ja}",
        )
        header_level = next((match for pattern in patterns
                             if (match := re.search(pattern, joined, re.I))), None)
    if header_level:
        result["level"] = int(header_level.group(1))
    sp = re.search(r"(?:SP|RP)\s*[:：]?\s*([0-9,]{3,6})", joined, re.I)
    if sp:
        parsed_sp = int(sp.group(1).replace(",", ""))
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
    subskills: List[List[Any]] = []
    subskill_rows: Dict[str, List[Any]] = {}
    for text in texts:
        found = _match([text], "subskills", .78)
        if not found:
            continue
        badge = re.search(r"Lv\.?\s*(\d{1,3})", text, re.I)
        unlock = int(badge.group(1)) if badge else 0
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
    confidence = min([item.get("ocr_confidence", 0), *item.get("field_confidence", {}).values()] or [0])
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
            # The Lv1 ingredient is normally species-fixed and can be filled safely.
            if not item.get("ingredients") and len(options[0]["choices"]) == 1:
                name, amount, _ = options[0]["choices"][0]
                item["ingredients"] = [[name, amount]]
            amounts = item.get("ingredient_amounts", [])
            inferred = []
            for index, amount in enumerate(amounts[:len(options)]):
                matches = [choice for choice in options[index]["choices"] if choice[1] == amount]
                if len(matches) == 1:
                    inferred.append(matches[0][:2])
                else:
                    inferred.append(None)
            if inferred:
                ingredients = list(item.get("ingredients", []))
                for index, value in enumerate(inferred):
                    if value and index < len(ingredients):
                        ingredients[index] = value
                    elif value and index == len(ingredients):
                        ingredients.append(value)
                item["ingredients"] = ingredients
                if len(inferred) == len(options) and all(inferred):
                    ingredients_need_review = False
            # Keep calculations runnable before review by filling unresolved
            # slots with the species' first legal option. `ocr_missing` remains
            # set, so these assumptions are always shown as provisional.
            ingredients = list(item.get("ingredients", []))
            while len(ingredients) < len(options):
                choices = options[len(ingredients)]["choices"]
                if not choices:
                    break
                ingredients.append(choices[0][:2])
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
        on_progress: Optional[Callable[[str], None]] = None) -> List[Dict[str, Any]]:
    return enrich_with_species_data(merge_frames(recognize_path(path, interval, on_progress)))


def make_demo(path: Path) -> Path:
    cache = ROOT / ".cache/clang"
    cache.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, CLANG_MODULE_CACHE_PATH=str(cache))
    proc = subprocess.run(["swift", str(DEMO_SOURCE), str(path)], capture_output=True,
                          text=True, check=False, env=env)
    if proc.returncode:
        raise RuntimeError("OCRデモ画像の生成に失敗しました: " + proc.stderr.strip())
    return path
