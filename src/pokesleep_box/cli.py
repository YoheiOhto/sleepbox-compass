from __future__ import annotations

import argparse
import json
from pathlib import Path

from .core import build_team_plans, connect, decide, import_individuals, load_dashboard
from .analytics import analyze
from .engine import EngineUnavailable, evaluate, run_engine, verify
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
    render.add_argument("--teams", type=Path, default=Path("data/private/team_plans.json"))
    demo = commands.add_parser("demo")
    demo.add_argument("--input", type=Path, default=Path("data/example_individuals.json"))
    demo.add_argument("--out", type=Path, default=Path("site"))
    demo.add_argument("--benchmarks", type=Path, default=Path("data/example_species_benchmarks.json"))
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
    bench = commands.add_parser("benchmark")
    bench.add_argument("--engine", default="engine/bin/pokesleep-engine")
    bench.add_argument("--out", type=Path, default=Path("data/private/species_benchmarks.json"))
    bench.add_argument("--iterations", type=int, default=500)
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
        teams = json.loads(args.teams.read_text()).get("plans", []) if args.teams.exists() else build_team_plans(dashboard)
        render_site(dashboard, args.out, teams, analyze(dashboard, settings, benchmarks, team_plans=teams))
        print(args.out / "index.html")
    elif args.command == "demo":
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        import_individuals(db, payload["individuals"])
        decide(db)
        dashboard = load_dashboard(db)
        benchmarks = json.loads(args.benchmarks.read_text()).get("benchmarks", []) if args.benchmarks.exists() else []
        render_site(dashboard, args.out, build_team_plans(dashboard), analyze(dashboard, {}, benchmarks))
        print(args.out / "index.html")
    elif args.command in ("ingest", "scan"):
        items = ingest_path(args.path, args.frames, args.ocr_command,
                            vision=args.ocr == "vision", interval=args.interval)
        imported = import_individuals(db, items)
        report = audit(items, args.audit)
        render_review(items, args.review)
        print(json.dumps({"imported": imported, "audit": report,
                          "review": str(args.review)}, ensure_ascii=False))
    elif args.command == "verify":
        try:
            print(json.dumps(verify(db, args.engine, args.tolerance), ensure_ascii=False))
        except EngineUnavailable as exc:
            parser.error(str(exc))
    elif args.command == "evaluate":
        try:
            print(json.dumps({"evaluations": evaluate(db, args.engine, args.teams_out),
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
