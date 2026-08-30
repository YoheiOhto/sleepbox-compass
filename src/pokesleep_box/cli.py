from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import (build_team_plans, compare_species_tiers, connect, cooking_plans, decide, export_backup,
                   import_individuals, ingredient_inventory, load_dashboard, observations, restore_backup,
                   saved_teams, save_cooking_plan, set_ingredient_inventory, species_friendships,
                   upsert_species_friendship)
from .analytics import analyze
from .engine import (EngineUnavailable, evaluate, evaluate_main_seed_upgrades,
                     evaluate_seed_upgrades, run_engine, species_scores, verify)
from .ingest import audit, ingest_path, render_review
from .localization import names
from .planning import catches_for_target, resource_plan
from .render import render_site


def positive_seconds(value: str) -> float:
    """Reject a frame interval that would never advance the video clock."""
    seconds = float(value)
    if not seconds > 0:
        raise argparse.ArgumentTypeError("フレーム間隔は0より大きい秒数で指定してください")
    return seconds


def build_parser() -> argparse.ArgumentParser:
    """Build the command line parser.

    Kept separate from `main` so tests can assert on defaults that carry
    privacy consequences, such as where `render` writes personal data.
    """
    parser = argparse.ArgumentParser(prog="pokesleep-box")
    parser.add_argument("--db", type=Path, default=Path("data/box.sqlite"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db")
    backup = commands.add_parser("backup")
    backup.add_argument("--out", type=Path, default=Path("data/private/sleepbox-backup.json"))
    restore = commands.add_parser("restore")
    restore.add_argument("path", type=Path)
    restore.add_argument("--replace", action="store_true", help="現在のローカルDBの計画データを置換する")
    dex = commands.add_parser("import-dex")
    dex.add_argument("path", type=Path, help="friendships配列を持つ、図鑑から確認したJSON")
    dex_scan = commands.add_parser("scan-dex")
    dex_scan.add_argument("path", type=Path, help="寝顔図鑑のスクリーンショット")
    inventory = commands.add_parser("set-inventory")
    inventory.add_argument("path", type=Path, help="食材名: 個数 のJSON")
    cooking = commands.add_parser("save-cooking-plan")
    cooking.add_argument("path", type=Path, help="料理計画JSON")
    odds = commands.add_parser("capture-odds")
    odds.add_argument("--per-catch", type=float, required=True, help="より良い個体を得る1捕獲あたりの確率（0〜1）")
    odds.add_argument("--target", type=float, default=.9, help="到達したい累積確率（0〜1）")
    resources = commands.add_parser("resource-plan")
    resources.add_argument("--settings", type=Path, default=Path("config/settings.local.json"))
    resources.add_argument("--seed-plans", type=Path, default=Path("data/private/seed_plans.json"))
    resources.add_argument("--main-seed-plans", type=Path, default=Path("data/private/main_seed_plans.json"))
    imp = commands.add_parser("import-json")
    imp.add_argument("path", type=Path)
    review = commands.add_parser("review")
    review.add_argument("--out", type=Path, default=Path("frames/review.html"),
                        help="現在DBに登録されている個体の確認ページ")
    dec = commands.add_parser("decide")
    dec.add_argument("--keep-top-n", type=int, default=2)
    render = commands.add_parser("render")
    # `--db` defaults to the private box, so the rendered page contains personal
    # data. It must never default into `site/`, which is tracked by Git and
    # published to GitHub Pages; only `demo` writes the public sample page.
    render.add_argument("--out", type=Path, default=Path("site/private"))
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
        scan.add_argument("--interval", type=positive_seconds, default=.8,
                          help="動画OCRのフレーム間隔（秒）")
        scan.add_argument("--ocr-command", help="画像パスを受け取り抽出JSONを標準出力するローカルコマンド")
    ver = commands.add_parser("verify")
    ver.add_argument("--engine", default="engine/bin/pokesleep-engine")
    ver.add_argument("--tolerance", type=int, default=0)
    ev = commands.add_parser("evaluate")
    ev.add_argument("--engine", default="engine/bin/pokesleep-engine")
    ev.add_argument("--teams-out", type=Path, default=Path("data/private/team_plans.json"))
    ev.add_argument("--settings", type=Path, default=Path("config/settings.local.json"))
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
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    db = connect(args.db)
    if args.command == "init-db":
        print(args.db)
    elif args.command == "backup":
        print(export_backup(db, args.out))
    elif args.command == "restore":
        if not args.replace:
            parser.error("復元は既存の計画データを置換します。--replace を明示してください")
        restore_backup(db, args.path)
        print(args.db)
    elif args.command == "review":
        rows = []
        for item in load_dashboard(db):
            row = dict(item)
            row["ingredients"] = json.loads(row.get("ingredients_json") or "[]")
            row["subskills"] = json.loads(row.get("subskills_json") or "[]")
            # OCRの元フレームをDBに持たないため、未確定の項目は既存の
            # 検証状態から表示する。JSONを保存して再取込すると確定できる。
            row["ocr_missing"] = [] if row.get("review_confirmed") else ["内容の照合"]
            rows.append(row)
        render_review(rows, args.out)
        print(args.out)
    elif args.command == "import-dex":
        payload = json.loads(args.path.read_text(encoding="utf-8"))
        rows = payload if isinstance(payload, list) else payload.get("friendships", [])
        for row in rows:
            upsert_species_friendship(db, row["species"], int(row["friendship_level"]),
                                      row.get("badge", "none"), row.get("gold_slots", (True, True, True)),
                                      row.get("source", "manual"))
        print(json.dumps({"imported": len(rows)}, ensure_ascii=False))
    elif args.command == "scan-dex":
        from .ocr import parse_dex_friendship, recognize_path
        frames = recognize_path(args.path)
        rows = [value for frame in frames
                if (value := parse_dex_friendship(frame.get("observations", [])))]
        print(json.dumps({"friendships": rows}, ensure_ascii=False, indent=2))
    elif args.command == "set-inventory":
        set_ingredient_inventory(db, json.loads(args.path.read_text(encoding="utf-8")))
        print(json.dumps(ingredient_inventory(db), ensure_ascii=False))
    elif args.command == "save-cooking-plan":
        row = json.loads(args.path.read_text(encoding="utf-8"))
        save_cooking_plan(db, row["name"], row["recipe_name"], row["requirements"],
                         int(row.get("meals_per_day", 3)), row.get("team_id"), bool(row.get("active")))
        print(json.dumps({"plans": cooking_plans(db)}, ensure_ascii=False))
    elif args.command == "capture-odds":
        print(json.dumps({"catches": catches_for_target(args.per_catch, args.target),
                          "per_catch": args.per_catch, "target": args.target}, ensure_ascii=False))
    elif args.command == "resource-plan":
        settings = json.loads(args.settings.read_text()) if args.settings.exists() else {}
        sub = json.loads(args.seed_plans.read_text()).get("plans", []) if args.seed_plans.exists() else []
        main = (json.loads(args.main_seed_plans.read_text()).get("plans", [])
                if args.main_seed_plans.exists() else [])
        print(json.dumps({"plans": resource_plan(sub, main, settings.get("resources", {})),
                          "unmodeled": ["candy", "dreamShards"]}, ensure_ascii=False, indent=2))
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
                   tier_rows, seed_plans, main_seed_plans, saved_teams(db), observations(db), settings,
                   species_friendships(db), ingredient_inventory(db), cooking_plans(db))
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
            settings = json.loads(args.settings.read_text()) if args.settings.exists() else {}
            print(json.dumps({"evaluations": evaluate(db, args.engine, args.teams_out, settings,
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
