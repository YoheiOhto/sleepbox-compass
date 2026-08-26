from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import build_team_plans, compare_species_tiers, connect, decide, import_individuals, load_dashboard
from .analytics import analyze
from .engine import (EngineUnavailable, evaluate, evaluate_main_seed_upgrades,
                     evaluate_seed_upgrades, run_engine, species_scores, verify)
from .ingest import audit, ingest_path, render_review
from .localization import names
from .render import render_site


def main() -> None:
    parser = argparse.ArgumentParser(prog="pokesleep-box")
    parser.add_argument("--db", type=Path, default=Path("data/box.sqlite"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db")
    imp = commands.add_parser("import-json")
    imp.add_argument("path", type=Path)
    dec = commands.add_parser("decide")
    dec.add_argument("--keep-top-n", type=int, default=2)
    render = commands.add_parser("render")
    render.add_argument("--out", type=Path, default=Path("site"))
    render.add_argument("--settings", type=Path, default=Path("config/settings.local.json"))
    render.add_argument("--benchmarks", type=Path, default=Path("data/private/species_benchmarks.json"))
    render.add_argument("--encounters", type=Path, default=Path("data/seed_encounters.json"))
    render.add_argument("--teams", type=Path, default=Path("data/private/team_plans.json"))
    render.add_argument("--tier-compare", type=Path, default=Path("data/species_tier_compare.json"))
    render.add_argument("--seed-plans", type=Path, default=Path("data/private/seed_plans.json"))
    render.add_argument("--main-seed-plans", type=Path,
                        default=Path("data/private/main_seed_plans.json"))
    serve_cmd = commands.add_parser("serve")
    serve_cmd.add_argument("--site", type=Path, default=Path("site/private"))
    serve_cmd.add_argument("--host", default="127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=8000)
    serve_cmd.add_argument("--engine", default="engine/bin/pokesleep-engine")
    demo = commands.add_parser("demo")
    demo.add_argument("--input", type=Path, default=Path("data/example_individuals.json"))
    demo.add_argument("--out", type=Path, default=Path("site"))
    demo.add_argument("--benchmarks", type=Path, default=Path("data/example_species_benchmarks.json"))
    demo.add_argument("--encounters", type=Path, default=Path("data/seed_encounters.json"))
    for command_name in ("ingest", "scan"):
        scan = commands.add_parser(command_name)
        scan.add_argument("path", type=Path, nargs="?", default=Path("inbox"))
        scan.add_argument("--frames", type=Path, default=Path("frames"))
        scan.add_argument("--audit", type=Path, default=Path("audit_report.md"))
        scan.add_argument("--review", type=Path, default=Path("frames/review.html"))
        scan.add_argument("--ocr", choices=("vision", "sidecar"), default="vision")
        scan.add_argument("--interval", type=float, default=.8, help="動画OCRのフレーム間隔（秒）")
        scan.add_argument("--ocr-command", help="画像パスを受け取り抽出JSONを標準出力するローカルコマンド")
    ver = commands.add_parser("verify")
    ver.add_argument("--engine", default="engine/bin/pokesleep-engine")
    ver.add_argument("--tolerance", type=int, default=0)
    ev = commands.add_parser("evaluate")
    ev.add_argument("--engine", default="engine/bin/pokesleep-engine")
    ev.add_argument("--teams-out", type=Path, default=Path("data/private/team_plans.json"))
    seed_ev = commands.add_parser("seed-evaluate")
    seed_ev.add_argument("--engine", default="engine/bin/pokesleep-engine")
    seed_ev.add_argument("--out", type=Path, default=Path("data/private/seed_plans.json"))
    main_seed_ev = commands.add_parser("main-seed-evaluate")
    main_seed_ev.add_argument("--engine", default="engine/bin/pokesleep-engine")
    main_seed_ev.add_argument("--out", type=Path,
                              default=Path("data/private/main_seed_plans.json"))
    bench = commands.add_parser("benchmark")
    bench.add_argument("--engine", default="engine/bin/pokesleep-engine")
    bench.add_argument("--out", type=Path, default=Path("data/private/species_benchmarks.json"))
    bench.add_argument("--iterations", type=int, default=500)
    tier = commands.add_parser("tier-compare")
    tier.add_argument("--engine", default="engine/bin/pokesleep-engine")
    tier.add_argument("--out", type=Path, default=Path("data/species_tier_compare.json"))
    commands.add_parser("validate-names")
    ocr_demo = commands.add_parser("make-ocr-demo")
    ocr_demo.add_argument("--out", type=Path, default=Path("inbox/ocr-demo.png"))
    args = parser.parse_args()
    db = connect(args.db)
    if args.command == "init-db":
        print(args.db)
    elif args.command == "import-json":
        payload = json.loads(args.path.read_text(encoding="utf-8"))
        print(json.dumps({"imported": import_individuals(db, payload["individuals"])}, ensure_ascii=False))
    elif args.command == "decide":
        print(json.dumps(decide(db, args.keep_top_n), ensure_ascii=False))
    elif args.command == "render":
        dashboard = load_dashboard(db)
        settings = json.loads(args.settings.read_text()) if args.settings.exists() else {}
        benchmarks = json.loads(args.benchmarks.read_text()).get("benchmarks", []) if args.benchmarks.exists() else []
        encounters = json.loads(args.encounters.read_text()) if args.encounters.exists() else {}
        teams = json.loads(args.teams.read_text()).get("plans", []) if args.teams.exists() else build_team_plans(dashboard)
        tier_rows = (json.loads(args.tier_compare.read_text()).get("rows", [])
                    if args.tier_compare.exists() else [])
        seed_plans = (json.loads(args.seed_plans.read_text()).get("plans", [])
                      if args.seed_plans.exists() else [])
        main_seed_plans = (json.loads(args.main_seed_plans.read_text()).get("plans", [])
                           if args.main_seed_plans.exists() else [])
        render_site(dashboard, args.out, teams, analyze(dashboard, settings, benchmarks,
                   team_plans=teams, encounters=encounters),
                   tier_rows, seed_plans, main_seed_plans)
        print(args.out / "index.html")
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
        render_site(dashboard, args.out, build_team_plans(dashboard),
                    analyze(dashboard, {}, benchmarks, encounters=encounters))
        print(args.out / "index.html")
    elif args.command in ("ingest", "scan"):
        def report_progress(message: str) -> None:
            print(message, file=sys.stderr, flush=True)
        items = ingest_path(args.path, args.frames, args.ocr_command,
                            vision=args.ocr == "vision", interval=args.interval,
                            on_progress=report_progress)
        imported = import_individuals(db, items)
        report = audit(items, args.audit)
        render_review(items, args.review)
        print(json.dumps({"imported": imported, "audit": report,
                          "review": str(args.review)}, ensure_ascii=False))
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
