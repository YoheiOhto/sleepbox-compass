from __future__ import annotations

import difflib
import json
import math
import os
import platform
import re
import subprocess
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .localization import SUBSKILL_UNLOCKS, base_main_skill, names, normalize_individual, to_japanese

ROOT = Path(__file__).parents[2]
VISION_SOURCE = ROOT / "engine/macos/vision_ocr.swift"
VISION_BINARY = ROOT / ".cache/vision-ocr"
DEMO_SOURCE = ROOT / "engine/macos/make_demo.swift"
VENDOR_COMMON = ROOT / "engine/vendor/nerolis-lab/common/src"


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


def recognize_path(path: Path, interval: float = .8,
                   on_progress: Optional[Callable[[str], None]] = None) -> List[Dict[str, Any]]:
    binary = ensure_vision_binary()
    with subprocess.Popen([str(binary), str(path), str(interval)], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True) as proc:
        # The binary writes the full JSON result to stdout only once, at the
        # very end. Draining it concurrently avoids a deadlock: without a
        # reader, a large result can fill the stdout pipe buffer and block the
        # child while we are still waiting on stderr progress lines below.
        stdout_chunks: List[str] = []
        reader = threading.Thread(target=lambda: stdout_chunks.append(proc.stdout.read()),
                                  daemon=True)
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
            rf"(?:SP|RP)\s*[:：]?\s*[0-9,]{{3,6}}[\s\S]{{0,45}}?(?:Lv\.?|レベル)\s*(\d{{1,3}})[\s\S]{{0,30}}?{species_ja}",
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

    subskills = []
    for text in texts:
        found = _match([text], "subskills", .78)
        if found:
            badge = re.search(r"Lv\.?\s*(\d{1,3})", text, re.I)
            subskills.append([found[0], int(badge.group(1)) if badge else 0])
    if subskills:
        defaults = SUBSKILL_UNLOCKS
        unique_subskills = []
        seen_subskills = set()
        for name, unlock in subskills:
            if name not in seen_subskills:
                unique_subskills.append((name, unlock))
                seen_subskills.add(name)
        result["subskills"] = [[name, unlock or defaults[i]]
                               for i, (name, unlock) in enumerate(unique_subskills[:5])]

    ingredients = []
    for text in texts:
        found = _match([text], "ingredients", .78)
        if found:
            amount = re.search(r"[x×]\s*(\d{1,2})", text, re.I)
            ingredients.append([found[0], int(amount.group(1)) if amount else 0])
    if ingredients:
        result["ingredients"] = ingredients
    # In the game screen ingredient names are icons, but their amounts are OCR
    # text. Restrict to the ingredient row by Vision's normalized coordinates.
    amount_rows = []
    for observation in observations:
        y = float(observation.get("y", 0))
        text = str(observation.get("text", ""))
        amount = re.search(r"[x×]\s*(\d{1,2})", text, re.I)
        if amount and .62 <= y <= .79:
            amount_rows.append((float(observation.get("x", 0)), int(amount.group(1))))
    if amount_rows:
        result["ingredient_amounts"] = [amount for _, amount in sorted(amount_rows)[:3]]
    confidences = [float(x.get("confidence", 0)) for x in observations]
    result["ocr_confidence"] = sum(confidences) / len(confidences) if confidences else 0
    return result


def merge_frames(frames: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    partials = [parse_frame(frame) for frame in frames]
    groups: List[Dict[str, Any]] = []
    current: Dict[str, Any] = {}
    for part in partials:
        species_changed = current.get("species") and part.get("species") and current["species"] != part["species"]
        core_conflict = (current.get("nature") and part.get("nature") and current["nature"] != part["nature"])
        identity_changed = (current.get("sp") and part.get("sp") and current["sp"] != part["sp"])
        if (species_changed or core_conflict or identity_changed) and current:
            groups.append(current)
            current = {}
        for key, value in part.items():
            if key == "field_confidence":
                current.setdefault(key, {}).update(value)
            elif key in ("subskills", "ingredients"):
                if len(value) >= len(current.get(key, [])):
                    current[key] = value
            elif value not in (None, "", [], {}):
                current[key] = value
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
    result.setdefault("ingredients", [])
    result.setdefault("subskills", [])
    result.setdefault("main_skill", "Metronome")
    result.setdefault("skill_level", 1)
    result.setdefault("species", "UNKNOWN")
    result["species_ja"] = to_japanese("species", result["species"])
    return normalize_individual(result)


def align_amounts(amounts: Sequence[Optional[int]],
                  options: Sequence[Mapping[str, Any]]) -> List[Optional[int]]:
    """Undo a one-slot shift in read ingredient amounts.

    Readers sometimes skip the first (partly hidden) ingredient or take the
    berry count as the first one, shifting every amount by one slot. Choose
    the shift whose amounts agree with the most legal options; ties keep the
    amounts as read.
    """
    amounts = list(amounts)
    if not amounts or not options:
        return amounts

    def fits(candidate: Sequence[Optional[int]]) -> int:
        return sum(1 for index, amount in enumerate(candidate[:len(options)])
                   if amount is not None and
                   any(choice[1] == amount for choice in options[index]["choices"]))

    best = amounts
    for shifted in (amounts[1:], [None] + amounts):
        if fits(shifted) > fits(best):
            best = shifted
    return best[:len(options)]


def resolve_ingredient_slots(options: Sequence[Mapping[str, Any]],
                             amounts: Sequence[Optional[int]],
                             names: Sequence[Optional[str]] = ()) -> Tuple[list, list]:
    """Return (confident, provisional) [name, amount] per ingredient slot.

    A slot is confident when its only legal option agrees with what was read,
    when the read amount fits exactly one option, or when a matched icon name
    fits an option whose amount does not contradict the read amount.
    """
    confident: list = []
    provisional: list = []
    for index, slot in enumerate(options):
        choices = slot["choices"]
        amount = amounts[index] if index < len(amounts) else None
        name = names[index] if index < len(names) else None
        matches = [c for c in choices if amount is not None and c[1] == amount]
        named = [c for c in choices if name and c[0] == name]
        if named:
            # Icon and amount must agree; a contradiction is left for review.
            value = named[0][:2] if amount is None or named[0][1] == amount else None
        elif len(choices) == 1:
            value = choices[0][:2] if amount is None or matches else None
        elif len(matches) == 1:
            value = matches[0][:2]
        else:
            value = None
        confident.append(value)
        provisional.append(value or (matches[0][:2] if matches else None))
    return confident, provisional


def enrich_with_species_data(items: List[Dict[str, Any]],
                             engine: str = "engine/bin/pokesleep-engine",
                             pokemon_data: Optional[Mapping[str, Any]] = None,
                             icon_matcher: Optional[Callable[[Dict[str, Any], list],
                                                             Sequence[Optional[str]]]] = None,
                             ) -> List[Dict[str, Any]]:
    """Resolve deterministic berry and constrain icon-only ingredient fields.

    ``icon_matcher(item, options)`` may return the ingredient name shown in
    each slot; it is only consulted when the amounts leave a slot ambiguous.
    """
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
            item["main_skill"] = base_main_skill(metadata["main_skill"])
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
            amounts = align_amounts(item.get("ingredient_amounts", []), options)
            hints = list(item.get("ingredient_names", []))
            inferred, provisional = resolve_ingredient_slots(options, amounts, hints)
            if icon_matcher and not all(inferred) and not hints:
                hints = list(icon_matcher(item, options) or [])
                if any(hints):
                    item["ingredient_names"] = hints
                    inferred, provisional = resolve_ingredient_slots(options, amounts, hints)
            if all(inferred):
                ingredients_need_review = False
            # Resolve slot by slot: a confident read wins, then what OCR text
            # gave, then an option agreeing with the read amount, and finally
            # the species' first legal option so calculations stay runnable.
            # `ocr_missing` keeps every unresolved slot marked provisional.
            existing = list(item.get("ingredients", []))
            ingredients = []
            for index, slot in enumerate(options):
                value = (inferred[index] if index < len(inferred) else None) or \
                        (existing[index] if index < len(existing) else None) or \
                        (provisional[index] if index < len(provisional) else None) or \
                        (slot["choices"][0][:2] if slot["choices"] else None)
                if value is None:
                    break
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
    # Evolution links by constant: evolvedPokemon(BASE) evolves from BASE and
    # preEvolvedPokemon(NEXT) is what NEXT evolves from.
    evolves_from: Dict[str, str] = {}
    for constant, constructor, relative, _ in declarations:
        if constructor == "evolvedPokemon":
            evolves_from[constant] = relative
        elif constructor == "preEvolvedPokemon":
            evolves_from[relative] = constant
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
                result[constant] = {**base, "name": name, "pokedex_number": pokedex_number,
                                    "constant": constant}
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
            specialty = {"createBerrySpecialist": "berry", "createIngredientSpecialist": "ingredient",
                         "createSkillSpecialist": "skill"}[constructor]
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
                                "ingredients": slots, "pokedex_number": pokedex_number,
                                "specialty": specialty, "constant": constant}
            progressed = True
        if not progressed:
            break
        pending = next_pending
    names_by_constant = {constant: value["name"] for constant, value in result.items()}
    return {value["name"]: {"berry": value["berry"], "ingredients": value["ingredients"],
                            "pokedex_number": value.get("pokedex_number"),
                            "specialty": value.get("specialty"),
                            "evolves_from": names_by_constant.get(
                                evolves_from.get(value["constant"], ""))}
            for value in result.values()}


@lru_cache(maxsize=1)
def species_metadata(engine: str = "engine/bin/pokesleep-engine") -> Dict[str, Any]:
    """Species rules (berry, ingredient options, specialty) from the pinned data."""
    try:
        from .engine import run_engine
        return run_engine({"mode": "metadata"}, engine).get("pokemon", {})
    except Exception:
        return load_source_metadata()


def berries_per_help(specialty: Optional[str], subskills: Sequence[Sequence[Any]],
                     level: Optional[int]) -> Optional[int]:
    """Berries per help: 1, or 2 for berry specialists, +1 with Berry Finding S unlocked."""
    if not specialty:
        return None
    count = 2 if specialty == "berry" else 1
    if any(entry[0] == "Berry Finding S" and level is not None and entry[1] <= level
           for entry in subskills):
        count += 1
    return count


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
