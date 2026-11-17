from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import (build_team_plans, compare_species_tiers, connect, decide, import_individuals,
                   load_dashboard)
from .analytics import analyze
from .engine import (EngineUnavailable, evaluate, evaluate_main_seed_upgrades,
                     evaluate_seed_upgrades, run_engine, species_scores, verify)
from .ingest import audit, render_review
from .localization import names
from .paths import DEFAULT_PATHS as P, load_settings, private_paths
from .render import render_site
from .sync import DEFAULT_VLM_MODEL, format_summary, render_private, run_sync, scan_new


def main() -> None:
    parser = argparse.ArgumentParser(prog="pokesleep-box")
    parser.add_argument("--db", type=Path, default=None,
                        help=f"SQLiteの場所（既定: {P.db}）")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db")
    imp = commands.add_parser("import-json")
    imp.add_argument("path", type=Path)
    dec = commands.add_parser("decide")
    dec.add_argument("--keep-top-n", type=int, default=2)
    render = commands.add_parser("render")
    render.add_argument("--out", type=Path, default=Path("site"))
    render.add_argument("--settings", type=Path, default=P.settings)
    render.add_argument("--benchmarks", type=Path, default=P.benchmarks)
    render.add_argument("--encounters", type=Path, default=Path("data/seed_encounters.json"))
    render.add_argument("--teams", type=Path, default=P.team_plans)
    render.add_argument("--tier-compare", type=Path, default=Path("data/species_tier_compare.json"))
    render.add_argument("--seed-plans", type=Path, default=P.seed_plans)
    render.add_argument("--main-seed-plans", type=Path, default=P.main_seed_plans)
    serve_cmd = commands.add_parser("serve")
    serve_cmd.add_argument("--site", type=Path, default=P.site)
    serve_cmd.add_argument("--host", default="127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=8000)
    serve_cmd.add_argument("--engine", default=P.engine)
    demo = commands.add_parser("demo")
    demo.add_argument("--input", type=Path, default=Path("data/example_individuals.json"))
    demo.add_argument("--out", type=Path, default=Path("site"))
    demo.add_argument("--benchmarks", type=Path, default=Path("data/example_species_benchmarks.json"))
    demo.add_argument("--encounters", type=Path, default=Path("data/seed_encounters.json"))
    demo.add_argument("--tier-compare", type=Path, default=Path("data/species_tier_compare.json"))
    for command_name in ("ingest", "scan"):
        scan = commands.add_parser(command_name)
        scan.add_argument("path", type=Path, nargs="?", default=P.inbox)
        scan.add_argument("--frames", type=Path, default=P.frames)
        scan.add_argument("--audit", type=Path, default=P.audit)
        scan.add_argument("--review", type=Path, default=P.review)
        scan.add_argument("--ocr", choices=("auto", "qwen", "vision", "sidecar"), default="auto",
                          help="auto: mlx-vlmが入っていればqwen、なければvision")
        scan.add_argument("--vlm-model", default=DEFAULT_VLM_MODEL,
                          help="Qwen OCRに使うローカルモデル（Hugging Face ID）")
        scan.add_argument("--vlm-cache", type=Path, default=P.vlm_cache,
                          help="Qwenの読み取り結果のキャッシュ（画像のSHA-256ごと）")
        scan.add_argument("--rescan", action="store_true",
                          help="取り込み済みの画像・動画も読み直す（通常は新しいものだけ）")
        scan.add_argument("--interval", type=float, default=.8, help="動画OCRのフレーム間隔（秒）")
        scan.add_argument("--ocr-command", help="画像パスを受け取り抽出JSONを標準出力するローカルコマンド")
    ver = commands.add_parser("verify")
    ver.add_argument("--engine", default=P.engine)
    ver.add_argument("--tolerance", type=int, default=0)
    ev = commands.add_parser("evaluate")
    ev.add_argument("--engine", default=P.engine)
    ev.add_argument("--teams-out", type=Path, default=P.team_plans)
    seed_ev = commands.add_parser("seed-evaluate")
    seed_ev.add_argument("--engine", default=P.engine)
    seed_ev.add_argument("--out", type=Path, default=P.seed_plans)
    main_seed_ev = commands.add_parser("main-seed-evaluate")
    main_seed_ev.add_argument("--engine", default=P.engine)
    main_seed_ev.add_argument("--out", type=Path, default=P.main_seed_plans)
    bench = commands.add_parser("benchmark")
    bench.add_argument("--engine", default=P.engine)
    bench.add_argument("--out", type=Path, default=P.benchmarks)
    bench.add_argument("--iterations", type=int, default=500)
    tier = commands.add_parser("tier-compare")
    tier.add_argument("--engine", default=P.engine)
    tier.add_argument("--out", type=Path, default=Path("data/species_tier_compare.json"))
    commands.add_parser("validate-names")
    ocr_demo = commands.add_parser("make-ocr-demo")
    ocr_demo.add_argument("--out", type=Path, default=P.inbox / "ocr-demo.png")
    sync = commands.add_parser(
        "sync", help="新しい画像・動画の取り込みからSP確認・評価・判定・非公開サイト生成まで")
    sync.add_argument("path", type=Path, nargs="?", default=None,
                      help=f"画像・動画のフォルダまたはファイル（既定: {P.inbox}）")
    sync.add_argument("--force", action="store_true",
                      help="新しい画像・動画がなくても評価・判定を実行する")
    sync.add_argument("--reviewed", type=Path,
                      help="レビュー画面で保存したJSONを先に取り込む（同じファイルは二度取り込まない）")
    sync.add_argument("--ocr", choices=("auto", "qwen", "vision"), default="auto",
                      help="auto: mlx-vlmが入っていればqwen、なければvision")
    sync.add_argument("--vlm-model", default=DEFAULT_VLM_MODEL)
    sync.add_argument("--interval", type=float, default=.8, help="動画OCRのフレーム間隔（秒）")
    sync.add_argument("--settings", type=Path, default=P.settings,
                      help="ローカル設定。\"paths\"で出力先を変更できます")
    args = parser.parse_args()
    if args.command == "sync":
        settings = load_settings(args.settings)
        try:
            paths = private_paths(settings)
        except ValueError as exc:
            parser.error(str(exc))
        if args.db is not None:
            from dataclasses import replace
            paths = replace(paths, db=args.db)
        if args.reviewed is not None and not args.reviewed.exists():
            parser.error(f"{args.reviewed} が見つかりません")
        summary = run_sync(args.path or paths.inbox, paths, settings, force=args.force,
                           ocr=args.ocr, vlm_model=args.vlm_model, interval=args.interval,
                           reviewed=args.reviewed)
        print(format_summary(summary, paths.review))
        return
    args.db = args.db or P.db
    db = connect(args.db)
    if args.command == "init-db":
        print(args.db)
    elif args.command == "import-json":
        payload = json.loads(args.path.read_text(encoding="utf-8"))
        print(json.dumps({"imported": import_individuals(db, payload["individuals"])}, ensure_ascii=False))
    elif args.command == "decide":
        print(json.dumps(decide(db, args.keep_top_n), ensure_ascii=False))
    elif args.command == "render":
        print(render_private(db, args.out, load_settings(args.settings), args.benchmarks,
                             args.teams, args.seed_plans, args.main_seed_plans,
                             args.encounters, args.tier_compare))
    elif args.command == "serve":
        from .server import serve
        serve(args.db, args.site, args.host, args.port, args.engine)
    elif args.command == "demo":
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        import_individuals(db, payload["individuals"])
        decide(db)
        dashboard = load_dashboard(db)
        benchmarks = json.loads(args.benchmarks.read_text()).get("benchmarks", []) if args.benchmarks.exists() else []
        encounters = json.loads(args.encounters.read_text()) if args.encounters.exists() else {}
        tier_rows = (json.loads(args.tier_compare.read_text()).get("rows", [])
                     if args.tier_compare.exists() else [])
        render_site(dashboard, args.out, build_team_plans(dashboard),
                    analyze(dashboard, {}, benchmarks, encounters=encounters), tier_rows)
        print(args.out / "index.html")
    elif args.command in ("ingest", "scan"):
        scanned = scan_new(db, args.path, args.frames, args.vlm_cache, args.ocr, args.vlm_model,
                           args.rescan, args.interval, args.ocr_command)
        items, done, qwen = scanned["items"], scanned["done"], scanned["qwen"]
        summary = {"imported": scanned["imported"], "new_sources": len(done)}
        if done:
            # Audit and review only the newly synced captures. With nothing new,
            # keep the previous report and review page instead of blanking them.
            args.audit.parent.mkdir(parents=True, exist_ok=True)
            summary["audit"] = audit(items, args.audit)
            render_review(items, args.review)
            summary["review"] = str(args.review)
        if qwen is not None:
            summary["vlm"] = {"reads": qwen.reads, "cache_hits": qwen.cache_hits}
        print(json.dumps(summary, ensure_ascii=False))
    elif args.command == "verify":
        def report_progress(message: str) -> None:
            print(message, file=sys.stderr, flush=True)
        try:
            print(json.dumps(verify(db, args.engine, args.tolerance,
                                    on_progress=report_progress), ensure_ascii=False))
        except EngineUnavailable as exc:
            parser.error(str(exc))
    elif args.command == "evaluate":
        def report_progress(message: str) -> None:
            print(message, file=sys.stderr, flush=True)
        try:
            print(json.dumps({"evaluations": evaluate(db, args.engine, args.teams_out,
                                                       on_progress=report_progress),
                              "teams": str(args.teams_out)}, ensure_ascii=False))
        except EngineUnavailable as exc:
            parser.error(str(exc))
    elif args.command == "benchmark":
        from .analytics import ISLANDS
        try:
            result = run_engine(
                {"mode": "benchmark", "iterations": args.iterations,
                 "islands": {name: list(berries) for name, berries in ISLANDS.items()},
                 "names": names()["species"]}, args.engine)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            print(args.out)
        except EngineUnavailable as exc:
            parser.error(str(exc))
    elif args.command == "seed-evaluate":
        try:
            plans = evaluate_seed_upgrades(db, args.engine)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps({"plans": plans}, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
            print(json.dumps({"plans": len(plans), "out": str(args.out)}, ensure_ascii=False))
        except EngineUnavailable as exc:
            parser.error(str(exc))
    elif args.command == "main-seed-evaluate":
        try:
            plans = evaluate_main_seed_upgrades(db, args.engine)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps({"plans": plans}, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
            print(json.dumps({"plans": len(plans), "out": str(args.out)}, ensure_ascii=False))
        except EngineUnavailable as exc:
            parser.error(str(exc))
    elif args.command == "tier-compare":
        def report_progress(message: str) -> None:
            print(message, file=sys.stderr, flush=True)
        try:
            scores = species_scores(args.engine, on_progress=report_progress)
            rows = compare_species_tiers(scores)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
            print(json.dumps({"compared": len(rows), "out": str(args.out)}, ensure_ascii=False))
        except EngineUnavailable as exc:
            parser.error(str(exc))
    elif args.command == "validate-names":
        tables = names()
        problems = {key: len(table) - len(set(table.values())) for key, table in tables.items()
                    if isinstance(table, dict) and key != "metadata"}
        if any(problems.values()):
            parser.error(f"日本語名の逆引き衝突があります: {problems}")
        print(json.dumps({key: len(table) for key, table in tables.items()
                          if isinstance(table, dict)}, ensure_ascii=False))
    elif args.command == "make-ocr-demo":
        from .ocr import make_demo
        print(make_demo(args.out))


if __name__ == "__main__":
    main()
