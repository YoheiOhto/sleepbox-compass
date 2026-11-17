"""Local Qwen VLM reader for Pokemon Sleep detail screens.

The reader runs entirely on this machine through ``mlx-vlm`` (Apple Silicon).
Every read is cached by the SHA-256 of the image bytes, so re-running a scan,
or resuming an interrupted video, never pays for the same frame twice.

Rough captures are expected: the model is asked to return ``null`` for any
field that is not visible, and video frames are merged per individual by a
per-field majority vote. A frame that only shows the lower half of the screen
therefore still contributes its nature and subskills.
"""
from __future__ import annotations

import difflib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from .localization import SUBSKILL_UNLOCKS, base_main_skill, names

DEFAULT_MODEL = "mlx-community/Qwen3.5-4B-4bit"
DEFAULT_CACHE = Path("data/private/vlm_cache")
# Bump when the prompt or parser changes so stale cached reads are not reused.
PROMPT_VERSION = "3"
# Default unlock levels for subskill slots whose badge is not visible (already
# unlocked slots show no badge). Matches ocr.parse_frame.
SLOT_UNLOCKS = SUBSKILL_UNLOCKS
# Mean absolute grey-level difference below which a frame repeats the last
# kept one. Identical screens measure <0.3; neighbouring individuals with the
# same layout (e.g. Gastly then Haunter) measure >1.1 overall and >2.5 in the
# header band, so skipping errs on the side of reading more frames.
DUPLICATE_FRAME_THRESHOLD = 0.8

PROMPT = """これはスマホゲーム「ポケモンスリープ」の画面です。
ポケモンの詳細画面なら、見えている項目だけを読み取ってJSONだけを出力してください。説明は不要です。
画面に見えない・読めない項目は推測せず null にしてください。

{
  "screen": 詳細画面なら "detail"、それ以外の画面（ボックス一覧、画面遷移中など）なら "other",
  "species": 左上の枠にあるポケモンの名前（カタカナ）,
  "level": 名前の左の「Lv.」の数字,
  "sp": 「SP」の右の数字（カンマなしの整数）,
  "ingredient_amounts": 「食材」の3つのアイコンの右下にある「x数字」を左から順に3つ（整数）。1つ目のアイコンは名前の枠に一部隠れることがあるが、「x数字」が見えれば読む。枠そのものが見えなければ null,
  "main_skill": 黄色い枠のメインスキル名,
  "skill_level": メインスキル名の右の「Lv.」の数字,
  "subskills": サブスキル5枠を左上→右上→左中→右中→左下の順に [{"name": 名前, "unlock": 枠の上の鍵バッジ「Lv.」の数字（バッジが無ければ null）}]。見えない枠は null,
  "nature": 「せいかく」の枠の中の文字
}
"""


class VLMUnavailable(RuntimeError):
    pass


def _inverse(category: str) -> Dict[str, str]:
    return {japanese: english for english, japanese in names()[category].items()}


def snap(category: str, value: Any, cutoff: float = .6) -> Optional[str]:
    """Map a Japanese label to its canonical key, tolerating small misreads."""
    if not isinstance(value, str):
        return None
    table = _inverse(category)
    text = re.sub(r"\s", "", value)
    if text in table:
        return table[text]
    best = difflib.get_close_matches(text, list(table), n=1, cutoff=cutoff)
    return table[best[0]] if best else None


def _int(value: Any) -> Optional[int]:
    try:
        return int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def parse_response(text: str) -> Dict[str, Any]:
    """Turn the model's JSON answer into a partial read with canonical keys."""
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return {"screen": "unreadable"}
    try:
        raw = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"screen": "unreadable"}
    if raw.get("screen") == "other":
        return {"screen": "other"}
    subskills: List[Optional[List[Any]]] = []
    for slot in (raw.get("subskills") or [])[:5]:
        name = snap("subskills", slot.get("name") if isinstance(slot, dict) else slot)
        unlock = _int(slot.get("unlock")) if isinstance(slot, dict) else None
        subskills.append([name, unlock] if name else None)
    # Ingredient icons are not named: the model misidentified them in testing,
    # so ingredients are resolved from the amounts and the species' options.
    amounts = raw.get("ingredient_amounts")
    amounts = [_int(x) for x in amounts[:3]] if isinstance(amounts, list) else []
    return {
        "screen": "detail",
        "species": snap("species", raw.get("species")),
        "level": _int(raw.get("level")),
        "sp": _int(raw.get("sp")),
        "nature": snap("natures", raw.get("nature")),
        "main_skill": snap("mainskills", base_main_skill(raw["main_skill"])
                           if isinstance(raw.get("main_skill"), str) else None),
        "skill_level": _int(raw.get("skill_level")),
        "subskills": subskills,
        "ingredient_amounts": amounts,
    }


class QwenReader:
    """Read screenshots with a local Qwen VLM, caching every answer on disk."""

    def __init__(self, model: str = DEFAULT_MODEL, cache_dir: Optional[Path] = DEFAULT_CACHE,
                 max_tokens: int = 500):
        self.model_name = model
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.max_tokens = max_tokens
        self._loaded: Optional[tuple] = None
        self.reads = 0
        self.cache_hits = 0

    def _load(self) -> tuple:
        if self._loaded is None:
            try:
                from mlx_vlm import generate, load
                from mlx_vlm.prompt_utils import apply_chat_template
            except ImportError as exc:
                raise VLMUnavailable(
                    "Qwen OCRにはmlx-vlmが必要です（Apple Silicon・Python 3.10以上）。"
                    "`pip install -e '.[qwen]'` を実行するか、`--ocr vision` を使ってください。"
                ) from exc
            model, processor = load(self.model_name)
            prompt = apply_chat_template(processor, model.config, PROMPT, num_images=1,
                                         enable_thinking=False)
            self._loaded = (generate, model, processor, prompt)
        return self._loaded

    def _cache_path(self, digest: str) -> Optional[Path]:
        if not self.cache_dir:
            return None
        slug = re.sub(r"[^A-Za-z0-9._-]", "_", self.model_name)
        return self.cache_dir / f"{slug}-p{PROMPT_VERSION}" / f"{digest}.json"

    def generate_text(self, image: Path) -> str:
        generate, model, processor, prompt = self._load()
        result = generate(model, processor, prompt, image=[str(image)],
                          max_tokens=self.max_tokens, temperature=0.0, verbose=False)
        return result.text if hasattr(result, "text") else str(result)

    def read(self, image: Path) -> Dict[str, Any]:
        from .ingest import file_sha256

        cache = self._cache_path(file_sha256(image))
        if cache and cache.exists():
            self.cache_hits += 1
            text = json.loads(cache.read_text(encoding="utf-8"))["text"]
        else:
            text = self.generate_text(image)
            self.reads += 1
            if cache:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps({"text": text}, ensure_ascii=False), encoding="utf-8")
        return parse_response(text)


def _signature(path: Path) -> tuple:
    from PIL import Image

    with Image.open(path) as image:
        grey = image.convert("L")
        header = grey.crop((0, 0, grey.width, int(grey.height * .16)))
        return (grey.resize((64, 128)).tobytes(), header.resize((128, 32)).tobytes())


def drop_near_duplicates(frames: Sequence[Path],
                         threshold: float = DUPLICATE_FRAME_THRESHOLD) -> List[Path]:
    """Skip frames that repeat the last kept frame (a paused or slow video).

    Both the whole screen and the header band (name, level, SP) must match,
    so a different individual with an identical layout is never skipped.
    """
    try:
        import PIL  # noqa: F401
    except ImportError:
        return list(frames)
    kept: List[Path] = []
    last = None
    for frame in frames:
        signature = _signature(frame)
        if last is not None and all(
                sum(abs(a - b) for a, b in zip(now, before)) / len(now) < threshold
                for now, before in zip(signature, last)):
            continue
        kept.append(frame)
        last = signature
    return kept


def _vote(values: Sequence[Any]) -> tuple:
    """Return (winner, agreement) over non-empty values."""
    present = [json.dumps(v, ensure_ascii=False) for v in values if v not in (None, "", [])]
    if not present:
        return None, 0.0
    winner, count = Counter(present).most_common(1)[0]
    return json.loads(winner), count / len(present)


def merge_reads(reads: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Group frame reads by individual (species + SP) and vote field by field.

    Frames without a readable header (e.g. mid-scroll) join the previous
    individual. Groups are keyed globally, so scrolling back to an earlier
    individual in the same video adds evidence instead of duplicating it.
    """
    groups: Dict[tuple, List[Mapping[str, Any]]] = {}
    order: List[tuple] = []
    current: Optional[tuple] = None
    for read in reads:
        if read.get("screen") != "detail":
            continue
        species, sp = read.get("species"), read.get("sp")
        if species and sp is not None:
            key: Optional[tuple] = (species, sp)
        elif species and current and current[0] == species:
            key = current
        elif not species and current:
            key = current
        elif species:
            key = (species, None)
        else:
            key = None
        if key is None:
            continue
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(read)
        current = key

    merged = []
    for key in order:
        frames = groups[key]
        item: Dict[str, Any] = {"field_confidence": {}, "ocr_frames": len(frames)}
        # Frames that show the ingredient row, for icon matching.
        icon_frames = [f["frame"] for f in frames
                       if f.get("frame") and any(a is not None for a in f.get("ingredient_amounts") or [])]
        if icon_frames:
            item["_icon_frames"] = icon_frames
        for field in ("species", "level", "sp", "nature", "main_skill", "skill_level"):
            value, agreement = _vote([f.get(field) for f in frames])
            if value is not None:
                item[field] = value
                item["field_confidence"][field] = agreement
        slots, slot_agreement = [], []
        for index in range(5):
            names_seen = [f["subskills"][index][0] for f in frames
                          if len(f.get("subskills") or []) > index and f["subskills"][index]]
            name, agreement = _vote(names_seen)
            # The unlock level is fixed by the slot position (game rule since
            # Ver.3.6.0); read badges are ignored because small screenshots
            # misread them (e.g. Lv.80 read as 60).
            slots.append([name, SLOT_UNLOCKS[index]] if name else None)
            slot_agreement.append(agreement)
        complete_subskills = all(slots)
        if complete_subskills:
            item["subskills"] = slots
            item["field_confidence"]["subskills"] = min(slot_agreement)
        amounts = []
        for index in range(3):
            value, _ = _vote([f["ingredient_amounts"][index] for f in frames
                              if len(f.get("ingredient_amounts") or []) > index])
            amounts.append(value)
        if any(a is not None for a in amounts):
            item["ingredient_amounts"] = amounts
        merged.append(item)
    return merged


def finalize(item: Dict[str, Any], box_index: int) -> Dict[str, Any]:
    from .ocr import finalize_candidate

    # A VLM gives no calibrated probability. Agreement across frames is the
    # best available signal; cap it below the audit's "trusted" threshold so
    # every VLM read still goes through review.
    agreements = list(item.get("field_confidence", {}).values())
    item["ocr_confidence"] = min(.99, min(agreements)) if agreements else 0
    return finalize_candidate(item, box_index)


def scan(path: Path, reader: QwenReader, frames_dir: Path, interval: float = .8,
         on_progress: Optional[Callable[[str], None]] = None,
         match_icons_enabled: bool = True) -> List[Dict[str, Any]]:
    """Read one still image or video and return candidate individuals."""
    from . import icons
    from .ingest import IMAGE_SUFFIXES, extract_frames
    from .ocr import enrich_with_species_data

    if path.suffix.lower() in IMAGE_SUFFIXES:
        frames = [path]
    else:
        frames = drop_near_duplicates(extract_frames(path, frames_dir / path.stem, interval))
    reads = []
    for index, frame in enumerate(frames, start=1):
        reads.append(dict(reader.read(frame), frame=str(frame)))
        if on_progress and len(frames) > 1:
            on_progress(f"フレーム{index}/{len(frames)}")
    items = [finalize(item, index + 1) for index, item in enumerate(merge_reads(reads))]

    def match_icons(item: Dict[str, Any], options: list) -> List[Optional[str]]:
        return icons.identify([Path(p) for p in item.get("_icon_frames", [])], options)

    items = enrich_with_species_data(items, icon_matcher=match_icons if match_icons_enabled else None)
    for item in items:
        item.pop("_icon_frames", None)
    return items
