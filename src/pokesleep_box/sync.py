"""One-command flow: scan new captures, SP check, evaluate, decide, render.

All outputs go to the private locations in ``paths.PrivatePaths``.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .core import (build_team_plans, connect, decide, decide_options, import_individuals, load_archived, load_dashboard,
                   merge_exact_duplicates,
                   plans_for_box,
                   record_synced_sources, synced_sources)
from .paths import PrivatePaths

DEFAULT_VLM_MODEL = "mlx-community/Qwen3.5-4B-4bit"
# Larger model for re-reading still images whose SP does not match.
FALLBACK_VLM_MODEL = "mlx-community/Qwen3.5-9B-8bit"
REVIEWED_READER = "reviewed-json"

Progress = Callable[[str], None]


def stderr_progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def scan_new(db: sqlite3.Connection, path: Path, frames: Path, vlm_cache: Path,
             ocr: str = "auto", vlm_model: str = DEFAULT_VLM_MODEL, rescan: bool = False,
             interval: float = .8, ocr_command: Optional[str] = None,
             on_progress: Progress = stderr_progress) -> Dict[str, Any]:
    """Read captures not synced before, import them and record their hashes.

    Returns ``items`` (the imported dicts, with ``stored_uid``), ``done``
    (synced sources), ``imported``, ``qwen`` (the reader, if any) and
    ``low_resolution`` (names of new still images whose shorter side is below
    ``ingest.LOW_RESOLUTION_SHORT_SIDE``). Those images are still read; each
    gets a progress line and their items get ``low_resolution``.
    """
    from .ingest import LOW_RESOLUTION_SHORT_SIDE, ingest_path, low_resolution_size

    reader_name = ocr
    if reader_name == "auto":
        import importlib.util
        reader_name = "qwen" if importlib.util.find_spec("mlx_vlm") else "vision"
        on_progress(f"OCR: {reader_name}")
    qwen = None
    if reader_name == "qwen":
        from .vlm import QwenReader
        qwen = QwenReader(vlm_model, vlm_cache)
        reader_name = f"qwen:{vlm_model}"
    done: List[tuple] = []
    low_resolution: List[str] = []

    def source_done(source: Path, digest: str, count: int) -> None:
        done.append((digest, source.name, reader_name, count))
        size = low_resolution_size(source)
        if size is not None:
            low_resolution.append(source.name)
            on_progress(f"{source.name}: 低解像度の画像です（{size[0]}x{size[1]}、"
                        f"短い辺が{LOW_RESOLUTION_SHORT_SIDE}px未満）。読み間違いが増えます")

    items = ingest_path(path, frames, ocr_command,
                        vision=reader_name == "vision", interval=interval,
                        on_progress=on_progress, qwen=qwen,
                        skip_hashes=set() if rescan else synced_sources(db),
                        on_source_done=source_done)
    if low_resolution:
        names = set(low_resolution)
        for item in items:
            if names & set(item.get("ocr_sources") or []):
                item["low_resolution"] = True
    imported = import_individuals(db, items)
    record_synced_sources(db, done)
    return {"items": items, "done": done, "imported": imported, "qwen": qwen,
            "low_resolution": low_resolution}


def low_resolution_message(names: List[str], limit: int = 10) -> str:
    """Japanese advice to re-export low-resolution images, naming at most ``limit``."""
    from .ingest import LOW_RESOLUTION_SHORT_SIDE

    listed = "、".join(names[:limit])
    if len(names) > limit:
        listed += f" ほか{len(names) - limit}枚"
    return (f"低解像度の画像が{len(names)}枚あります（短い辺が{LOW_RESOLUTION_SHORT_SIDE}px未満）。"
            f"読み間違いが増えるので、写真アプリから元のサイズで書き出し直してください: {listed}")


def import_checked(db: sqlite3.Connection, items: List[Dict[str, Any]], engine: str,
                   on_progress: Optional[Progress] = None) -> Dict[str, Any]:
    """Import reviewed rows, then SP-check the ones no human ticked 「照合済み」.

    Shared by `import-json` and `sync --reviewed`. The rules are those of
    ``spcheck.check_items``; ``review_confirmed`` rows are never changed.
    Returns ``imported``, ``sp`` (the check's counts) or, when the engine is
    unavailable, ``note`` (the rows are then imported as before, unchecked).
    """
    from .engine import EngineUnavailable
    from .spcheck import check_items

    result: Dict[str, Any] = {"imported": import_individuals(db, items)}
    try:
        result["sp"] = check_items(db, items, engine, on_progress=on_progress)
    except EngineUnavailable as exc:
        result["note"] = f"計算エンジンを使えないため、SP確認を省略しました: {exc}"
    return result


def import_reviewed(db: sqlite3.Connection, path: Path, engine: str,
                    on_progress: Optional[Progress] = None) -> Optional[Dict[str, Any]]:
    """Import and SP-check a reviewed JSON once; ``None`` when this exact file was imported before.

    The file's SHA-256 is recorded with the synced captures, so running
    `sync --reviewed` again with the same file never imports it twice.
    Returns the result of ``import_checked``.
    """
    from .ingest import file_sha256

    digest = file_sha256(path)
    if digest in synced_sources(db):
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    result = import_checked(db, payload["individuals"], engine, on_progress)
    record_synced_sources(db, [(digest, path.name, REVIEWED_READER, result["imported"])])
    return result


def render_private(db: sqlite3.Connection, out: Path, settings: Dict[str, Any],
                   benchmarks: Path, teams: Path, seed_plans: Path, main_seed_plans: Path,
                   encounters: Path = Path("data/seed_encounters.json"),
                   tier_compare: Path = Path("data/species_tier_compare.json")) -> Path:
    """Render the site from the DB and the optional private plan files."""
    from .analytics import analyze
    from .render import render_site

    def read(path: Path, key: Optional[str], default: Any) -> Any:
        if not path.exists():
            return default
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get(key, default) if key else data

    dashboard = load_dashboard(db)
    # Stored plans may name individuals that were sent to the Professor since;
    # drop those plans until the next recalculation rebuilds them.
    active = [item["uid"] for item in dashboard]
    team_plans = (plans_for_box(read(teams, "plans", []), active) if teams.exists()
                  else build_team_plans(dashboard))
    render_site(dashboard, out, team_plans,
                analyze(dashboard, settings, read(benchmarks, "benchmarks", []),
                        team_plans=team_plans, encounters=read(encounters, None, {})),
                read(tier_compare, "rows", []), plans_for_box(read(seed_plans, "plans", []), active),
                plans_for_box(read(main_seed_plans, "plans", []), active),
                archived=load_archived(db))
    return out / "index.html"


def run_sync(source: Path, paths: PrivatePaths, settings: Optional[Dict[str, Any]] = None,
             force: bool = False, ocr: str = "auto", vlm_model: str = DEFAULT_VLM_MODEL,
             interval: float = .8, reviewed: Optional[Path] = None,
             fallback_vlm_model: Optional[str] = FALLBACK_VLM_MODEL, recheck: bool = False,
             on_progress: Progress = stderr_progress) -> Dict[str, Any]:
    """Run scan -> SP check -> repair -> evaluate -> decide -> render and return a summary.

    SP mismatches go through ``repair.repair_mismatches``: SP repair (a
    candidate search), then a re-read of this run's Qwen-read still images
    with ``fallback_vlm_model`` (``None`` disables the re-read). Partial SP
    matches of those images then go through ``repair.confirm_partials``
    (two-model agreement with the same fallback model, loaded at most once).
    With ``recheck``, every unverified row already stored goes through the
    same steps too (see ``repair.flagged_items``), and evaluate -> decide ->
    render always run.
    """
    from .engine import EngineUnavailable, evaluate
    from .ingest import audit, render_review
    from .repair import (confirm_partials, flagged_items, icon_downgrades, repair_mismatches,
                         scanned_images)
    from .spcheck import MATCH, MISMATCH, check_items, review_order

    settings = settings or {}
    db = connect(paths.db)
    summary: Dict[str, Any] = {"messages": [], "steps": []}
    try:
        # Boxes built before repeated screenshots were merged on import.
        merged = merge_exact_duplicates(db)
        if merged:
            removed = sum(len(uids) for _, uids in merged)
            summary["duplicates_merged"] = removed
            summary["messages"].append(
                f"同じ個体を撮った画像の重複 {removed}件 を1匹にまとめました（{len(merged)}匹分）")
        before = {row["uid"] for row in db.execute("SELECT uid FROM individual")}
        if reviewed is not None:
            result = import_reviewed(db, reviewed, paths.engine, on_progress)
            summary["reviewed_imported"] = result and result["imported"]
            if result is None:
                summary["messages"].append(f"{reviewed} は取り込み済みのため読み込みませんでした")
            else:
                message = f"{reviewed} から{result['imported']}件を取り込みました"
                if result.get("sp"):
                    summary["reviewed_sp"] = result["sp"]
                    message += (f"（SP一致で確定: {result['sp'][MATCH]} / "
                                f"SP不一致: {result['sp'][MISMATCH]}）")
                summary["messages"].append(message)
                if result.get("note"):
                    summary["messages"].append(result["note"])
        summary["steps"].append("scan")
        scanned = scan_new(db, source, paths.frames, paths.vlm_cache, ocr, vlm_model,
                           interval=interval, on_progress=on_progress)
        items, done = scanned["items"], scanned["done"]
        if scanned["qwen"] is not None:
            summary["vlm"] = {"reads": scanned["qwen"].reads,
                              "cache_hits": scanned["qwen"].cache_hits}
        touched = {item["stored_uid"] for item in items if item.get("stored_uid")}
        summary.update(new_sources=len(done),
                       new_individuals=len(touched - before),
                       updated_individuals=len(touched & before))
        summary["low_resolution"] = len(scanned["low_resolution"])
        if scanned["low_resolution"]:
            summary["messages"].append(low_resolution_message(scanned["low_resolution"]))
        changed = bool(done) or bool(summary.get("reviewed_imported")) or recheck
        engine_ok = True
        images: Dict[str, Path] = {}
        # Every still image found, shown next to its row on the review page.
        stills = scanned_images(items, source) if items else {}
        if scanned["qwen"] is not None:
            for item in items:
                item.setdefault("read_model", vlm_model)
            if fallback_vlm_model and fallback_vlm_model != vlm_model:
                images = dict(stills)
        if recheck:
            # Auto-verified rows whose locked ingredient contradicts its icon
            # are un-verified first, so the recheck below lists them.
            downgrades = icon_downgrades(db, source, paths.engine, on_progress=on_progress)
            summary["icon_checked"] = downgrades["checked"]
            summary["icon_downgraded"] = downgrades["downgraded"]
            # Stored unverified rows not already handled with this run's captures.
            flagged = flagged_items(db, source, paths.vlm_cache, fallback_vlm_model, paths.frames)
            for item in flagged["items"]:
                uid = item["stored_uid"]
                if uid in downgrades["names"]:
                    item["ingredient_names"] = downgrades["names"][uid]
                    item["ingredient_conflicts"] = downgrades["notes"][uid]
                    item["ocr_missing"] = sorted(set(item.get("ocr_missing") or []) | {"ingredients"})
            seen = {item.get("stored_uid") for item in items}
            items = items + [item for item in flagged["items"] if item["stored_uid"] not in seen]
            images.update({uid: path for uid, path in flagged["images"].items() if uid not in seen})
            stills.update({uid: path for uid, path in flagged["stills"].items() if uid not in seen})
            summary["rechecked"] = len(flagged["items"])
        if not changed and not force:
            summary["messages"].append(
                "新しい画像・動画がないため、SP確認・評価・判定を省略しました（--forceで実行）")
        else:
            if items:
                summary["steps"].append("sp-check")
                try:
                    summary["sp"] = check_items(db, items, paths.engine, on_progress=on_progress)
                    summary["steps"].append("repair")
                    fallback = None
                    if images and fallback_vlm_model:
                        loaded: List[Any] = []

                        def fallback() -> Any:
                            # One reader for re-reads and agreement checks; it
                            # loads the model lazily, on its first uncached read.
                            if not loaded:
                                from .vlm import QwenReader
                                on_progress(f"読み直し用のモデル: {fallback_vlm_model}")
                                loaded.append(QwenReader(fallback_vlm_model, paths.vlm_cache))
                            return loaded[0]
                    repair = repair_mismatches(db, items, paths.engine, images, fallback,
                                               paths.frames, on_progress)
                    summary["repair"] = repair
                    if repair.get("note"):
                        summary["messages"].append(repair["note"])
                    agreement = confirm_partials(db, items, paths.engine, images,
                                                 None if repair.get("note") else fallback,
                                                 paths.frames, fallback_vlm_model, on_progress)
                    if agreement["checked"]:
                        summary["agreement"] = agreement
                    if agreement.get("note"):
                        summary["messages"].append(agreement["note"])
                except EngineUnavailable as exc:
                    engine_ok = False
                    summary["messages"].append(
                        f"計算エンジンを使えないため、SP確認と評価を省略しました: {exc}")
            if engine_ok:
                summary["steps"].append("evaluate")
                try:
                    evaluate(db, paths.engine, paths.team_plans, on_progress=on_progress)
                except EngineUnavailable as exc:
                    engine_ok = False
                    summary["messages"].append(f"計算エンジンを使えないため、評価を省略しました: {exc}")
            summary["steps"].append("decide")
            decide(db, **decide_options(settings))
        if done or recheck:
            # Audit and review only this sync's captures (and, with --recheck,
            # the rechecked rows); with nothing new the previous report and
            # review page are kept.
            ordered = review_order(items)
            paths.audit.parent.mkdir(parents=True, exist_ok=True)
            audit(ordered, paths.audit)
            render_review(ordered, paths.review, images=stills)
            summary["audit"] = str(paths.audit)
            summary["review"] = str(paths.review)
        statuses = [item.get("sp_check") for item in items]
        summary["sp_match"] = statuses.count(MATCH)
        summary["sp_mismatch"] = statuses.count(MISMATCH)
        summary["needs_review"] = sum(1 for item in items
                                      if not item.get("verified") and item.get("sp_check") != "human")
        summary["steps"].append("render")
        summary["site"] = str(render_private(db, paths.site, settings, paths.benchmarks,
                                             paths.team_plans, paths.seed_plans,
                                             paths.main_seed_plans))
        summary["engine"] = engine_ok
    finally:
        db.close()
    return summary


def format_summary(summary: Dict[str, Any], review: Path) -> str:
    """Short Japanese summary for the terminal."""
    lines = ["同期が終わりました"]
    lines += [f"- {message}" for message in summary["messages"]]
    lines.append(f"- 新しい画像・動画: {summary['new_sources']}件")
    lines.append(f"- 新しい個体: {summary['new_individuals']}匹 / "
                 f"更新した個体（撮り直し）: {summary['updated_individuals']}匹")
    mismatch = summary.get("sp_mismatch", 0)
    other = summary.get("needs_review", 0) - mismatch
    if summary.get("rechecked") is not None:
        lines.append(f"- 再確認した登録済みの未検証の個体: {summary['rechecked']}匹")
    if summary.get("icon_downgraded"):
        lines.append(f"- 食材アイコンと食い違うため自動確定を取り消した個体: "
                     f"{summary['icon_downgraded']}匹（確認 {summary.get('icon_checked', 0)}匹）")
    if summary.get("repair"):
        from .repair import count_summary
        lines.append(f"- {count_summary(summary['repair'])}")
    if summary.get("agreement"):
        from .repair import agreement_summary
        lines.append(f"- {agreement_summary(summary['agreement'])}")
    lines.append(f"- SP一致: {summary.get('sp_match', 0)} / SP不一致: {mismatch} / 要確認: {other}")
    if summary.get("review"):
        lines.append(f"- レビュー: {summary['review']}")
    lines.append(f"- サイト: {summary['site']}")
    needs = summary.get("needs_review", 0)
    if needs:
        lines.append(f"次にすること: レビューが必要な個体: {needs} → {review} を開く")
    else:
        lines.append(f"次にすること: {summary['site']} を開く")
    return "\n".join(lines)
