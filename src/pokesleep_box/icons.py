"""Identify ingredient icons on a detail screen by matching reference sprites.

The three ingredient icons sit at fixed fractions of the screen width, in the
row just above the "おてつだい時間" label. The label's position comes from the
macOS Vision OCR, so scrolled captures are handled. Each slot is compared only
against the species' legal options for that slot, using a masked, per-channel
normalized cross-correlation, which tolerates the faded look of locked slots.

Reference sprites come from the pinned Neroli's Lab checkout under
``engine/vendor`` (not redistributed). Matching is skipped, never guessed,
when Vision, NumPy/Pillow or the sprites are unavailable.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

ROOT = Path(__file__).parents[2]
ICON_DIR = ROOT / "engine/vendor/nerolis-lab/frontend/public/images/ingredient"
SLOT_X = (0.493, 0.660, 0.826)  # icon centres as fractions of the screen width
ROW_OFFSET = 0.072  # icon row centre above the label centre, fraction of height
ICON_SIZES = (0.10, 0.11, 0.12, 0.13)  # sprite size candidates, fraction of width
SEARCH_X, SEARCH_Y = 0.02, 0.014  # search window half-widths
MIN_SCORE, MIN_MARGIN = 0.4, 0.1  # accept only clear winners
ROW_LABEL = "おてつだい時間"

_templates: Dict[Tuple[str, int], Any] = {}


def available() -> bool:
    try:
        import numpy  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        return False
    return ICON_DIR.is_dir()


def _template(name: str, size: int) -> Any:
    import numpy as np
    from PIL import Image

    key = (name, size)
    if key not in _templates:
        path = ICON_DIR / f"{name.lower()}.png"
        with Image.open(path) as image:
            rgba = np.asarray(image.convert("RGBA").resize((size, size), Image.LANCZOS),
                              dtype=np.float32)
        _templates[key] = (rgba[..., :3], rgba[..., 3] > 128)
    return _templates[key]


def _ncc(crop: Any, template: Any, mask: Any) -> float:
    score = 0.0
    for channel in range(3):
        t = template[..., channel][mask]
        c = crop[..., channel][mask]
        t = t - t.mean()
        c = c - c.mean()
        norm = float((t * t).sum() * (c * c).sum()) ** .5
        score += float((t * c).sum()) / norm if norm else 0.0
    return score / 3


def _best_score(image: Any, cx: float, cy: float, name: str) -> float:
    height, width = image.shape[:2]
    best = -1.0
    for fraction in ICON_SIZES:
        size = int(round(fraction * width))
        template, mask = _template(name, size)
        for dy in range(-int(SEARCH_Y * height), int(SEARCH_Y * height) + 1, 2):
            for dx in range(-int(SEARCH_X * width), int(SEARCH_X * width) + 1, 2):
                x0, y0 = int(cx + dx - size / 2), int(cy + dy - size / 2)
                if x0 < 0 or y0 < 0 or x0 + size > width or y0 + size > height:
                    continue
                best = max(best, _ncc(image[y0:y0 + size, x0:x0 + size], template, mask))
    return best


def row_center(observations: Sequence[Mapping[str, Any]]) -> Optional[float]:
    """Icon row centre (fraction of height from the top) from Vision observations."""
    for observation in observations:
        if ROW_LABEL in str(observation.get("text", "")):
            # Vision uses a bottom-left origin.
            label = 1 - float(observation["y"]) - float(observation.get("height", 0)) / 2
            return label - ROW_OFFSET
    return None


def match_slots(image_path: Path, options: Sequence[Mapping[str, Any]],
                observations: Sequence[Mapping[str, Any]]) -> List[Optional[str]]:
    """Return the confidently matched ingredient name per slot (None if unsure)."""
    import numpy as np
    from PIL import Image

    centre = row_center(observations)
    if centre is None:
        return [None] * len(options)
    with Image.open(image_path) as source:
        image = np.asarray(source.convert("RGB"), dtype=np.float32)
    height, width = image.shape[:2]
    names: List[Optional[str]] = []
    for slot, fx in zip(options, SLOT_X):
        candidates = sorted({choice[0] for choice in slot.get("choices", [])})
        if len(candidates) < 2:
            # A single legal option needs no picture, and a margin is undefined.
            names.append(None)
            continue
        scores = sorted(((_best_score(image, fx * width, centre * height, name), name)
                         for name in candidates if (ICON_DIR / f"{name.lower()}.png").exists()),
                        reverse=True)
        if len(scores) >= 2 and scores[0][0] >= MIN_SCORE and \
                scores[0][0] - scores[1][0] >= MIN_MARGIN:
            names.append(scores[0][1])
        else:
            names.append(None)
    return names


def identify(frames: Sequence[Path], options: Sequence[Mapping[str, Any]],
             limit: int = 3) -> List[Optional[str]]:
    """Match icons on up to ``limit`` frames and keep slot names they agree on."""
    if not options or not available():
        return []
    from .ocr import recognize_path

    votes: List[Dict[str, int]] = [{} for _ in options]
    for frame in list(frames)[:limit]:
        try:
            observations = recognize_path(frame)[0].get("observations", [])
        except Exception:
            return []  # Vision unavailable: skip matching instead of guessing
        for index, name in enumerate(match_slots(frame, options, observations)):
            if name:
                votes[index][name] = votes[index].get(name, 0) + 1
    result: List[Optional[str]] = []
    for counts in votes:
        # Disagreement between frames means the match is not trustworthy.
        result.append(next(iter(counts)) if len(counts) == 1 else None)
    return result
