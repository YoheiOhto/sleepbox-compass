from __future__ import annotations

import difflib
import json
import os
import platform
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .localization import names, normalize_individual, to_japanese

ROOT = Path(__file__).parents[2]
VISION_SOURCE = ROOT / "engine/macos/vision_ocr.swift"
VISION_BINARY = ROOT / ".cache/vision-ocr"
DEMO_SOURCE = ROOT / "engine/macos/make_demo.swift"


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


def recognize_path(path: Path, interval: float = .8) -> List[Dict[str, Any]]:
    binary = ensure_vision_binary()
    proc = subprocess.run([str(binary), str(path), str(interval)], capture_output=True,
                          text=True, check=False)
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or f"OCRに失敗しました: {path}")
    return json.loads(proc.stdout)


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
    levels = [int(x) for x in re.findall(r"(?:Lv\.?|レベル)\s*(\d{1,3})", joined, re.I)]
    if levels:
        result["level"] = levels[0]
    sp = re.search(r"(?:SP|RP)\s*[:：]?\s*([0-9,]{2,6})", joined, re.I)
    if sp:
        result["sp"] = int(sp.group(1).replace(",", ""))
    skill_level = re.search(r"(?:メインスキル|スキル)[\s\S]{0,50}?Lv\.?\s*(\d+)", joined, re.I)
    result["skill_level"] = int(skill_level.group(1)) if skill_level else 1

    subskills = []
    for text in texts:
        found = _match([text], "subskills", .78)
        if found:
            badge = re.search(r"Lv\.?\s*(\d{1,3})", text, re.I)
            subskills.append([found[0], int(badge.group(1)) if badge else 0])
    if subskills:
        defaults = [10, 25, 50, 75, 100]
        result["subskills"] = [[name, unlock or defaults[i]] for i, (name, unlock) in enumerate(subskills)]

    ingredients = []
    for text in texts:
        found = _match([text], "ingredients", .78)
        if found:
            amount = re.search(r"[x×]\s*(\d{1,2})", text, re.I)
            ingredients.append([found[0], int(amount.group(1)) if amount else 0])
    if ingredients:
        result["ingredients"] = ingredients
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
        if (species_changed or core_conflict) and current:
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


def scan(path: Path, interval: float = .8) -> List[Dict[str, Any]]:
    return merge_frames(recognize_path(path, interval))


def make_demo(path: Path) -> Path:
    cache = ROOT / ".cache/clang"
    cache.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, CLANG_MODULE_CACHE_PATH=str(cache))
    proc = subprocess.run(["swift", str(DEMO_SOURCE), str(path)], capture_output=True,
                          text=True, check=False, env=env)
    if proc.returncode:
        raise RuntimeError("OCRデモ画像の生成に失敗しました: " + proc.stderr.strip())
    return path
