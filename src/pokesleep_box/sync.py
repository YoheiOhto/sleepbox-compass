"""One-command flow: scan new captures, SP check, evaluate, decide, render.

All outputs go to the private locations in ``paths.PrivatePaths``.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .core import (build_team_plans, connect, decide, import_individuals, load_archived, load_dashboard,
                   plans_for_box,
                   record_synced_sources, synced_sources)
from .paths import PrivatePaths

DEFAULT_VLM_MODEL = "mlx-community/Qwen3.5-9B-8bit"
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
    (synced sources), ``imported`` and ``qwen`` (the reader, if any).
    """
    from .ingest import ingest_path

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
    items = ingest_path(path, frames, ocr_command,
                        vision=reader_name == "vision", interval=interval,
                        on_progress=on_progress, qwen=qwen,
                        skip_hashes=set() if rescan else synced_sources(db),
                        on_source_done=lambda source, digest, count: done.append(
                            (digest, source.name, reader_name, count)))
    imported = import_individuals(db, items)
    record_synced_sources(db, done)
    return {"items": items, "done": done, "imported": imported, "qwen": qwen}


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
             on_progress: Progress = stderr_progress) -> Dict[str, Any]:
    """Run scan -> SP check -> evaluate -> decide -> render and return a summary."""
    from .engine import EngineUnavailable, evaluate
    from .ingest import audit, render_review
    from .spcheck import MATCH, MISMATCH, check_items, review_order

    settings = settings or {}
    db = connect(paths.db)
    summary: Dict[str, Any] = {"messages": [], "steps": []}
    try:
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
        changed = bool(done) or bool(summary.get("reviewed_imported"))
        engine_ok = True
        if not changed and not force:
            summary["messages"].append(
                "新しい画像・動画がないため、SP確認・評価・判定を省略しました（--forceで実行）")
        else:
            if items:
                summary["steps"].append("sp-check")
                try:
                    summary["sp"] = check_items(db, items, paths.engine, on_progress=on_progress)
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
            decide(db, int(settings.get("keepTopNPerSpecies", 2)))
        if done:
            # Audit and review only this sync's captures; with nothing new the
            # previous report and review page are kept.
            ordered = review_order(items)
            paths.audit.parent.mkdir(parents=True, exist_ok=True)
            audit(ordered, paths.audit)
            render_review(ordered, paths.review)
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
