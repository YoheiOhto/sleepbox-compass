from __future__ import annotations

import argparse
import json
from pathlib import Path

from .core import build_team_plans, connect, decide, import_individuals, load_dashboard
from .engine import EngineUnavailable, evaluate, verify
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
    demo = commands.add_parser("demo")
    demo.add_argument("--input", type=Path, default=Path("data/example_individuals.json"))
    demo.add_argument("--out", type=Path, default=Path("site"))
    scan = commands.add_parser("ingest")
    scan.add_argument("path", type=Path, nargs="?", default=Path("inbox"))
    scan.add_argument("--frames", type=Path, default=Path("frames"))
    scan.add_argument("--audit", type=Path, default=Path("audit_report.md"))
    scan.add_argument("--review", type=Path, default=Path("frames/review.html"))
    scan.add_argument("--ocr-command", help="画像パスを受け取り抽出JSONを標準出力するローカルコマンド")
    ver = commands.add_parser("verify")
    ver.add_argument("--engine", default="engine/bin/pokesleep-engine")
    ver.add_argument("--tolerance", type=int, default=0)
    ev = commands.add_parser("evaluate")
    ev.add_argument("--engine", default="engine/bin/pokesleep-engine")
    commands.add_parser("validate-names")
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
        render_site(dashboard, args.out, build_team_plans(dashboard))
        print(args.out / "index.html")
    elif args.command == "demo":
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        import_individuals(db, payload["individuals"])
        decide(db)
        dashboard = load_dashboard(db)
        render_site(dashboard, args.out, build_team_plans(dashboard))
        print(args.out / "index.html")
    elif args.command == "ingest":
        items = ingest_path(args.path, args.frames, args.ocr_command)
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
            print(json.dumps({"evaluations": evaluate(db, args.engine)}, ensure_ascii=False))
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


if __name__ == "__main__":
    main()
