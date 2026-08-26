from __future__ import annotations

import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

from .analytics import ISLANDS, analyze
from .core import connect, decide, load_dashboard
from .engine import (SUBSKILL_UPGRADES, evaluate, evaluate_main_seed_upgrades,
                     evaluate_seed_upgrades, individual_to_engine, run_engine)
from .render import render_site


def build_simulation_payload(db, request: Mapping[str, Any]) -> dict[str, Any]:
    uids = request.get("uids")
    island = request.get("island")
    mode = str(request.get("mode", "current"))
    if not isinstance(uids, list) or len(uids) != 5 or len(set(uids)) != 5:
        raise ValueError("異なる5匹を選んでください")
    if island not in ISLANDS:
        raise ValueError("固定島を選んでください")
    if mode not in {"current", "50", "60", "70", "80"}:
        raise ValueError("育成段階が不正です")
    marks = ",".join("?" for _ in uids)
    rows = db.execute(f"SELECT * FROM individual WHERE uid IN ({marks})", uids).fetchall()
    by_uid = {row["uid"]: row for row in rows}
    if len(by_uid) != 5:
        raise ValueError("選択した個体がボックスにありません")
    return {
        "mode": "custom-team", "teamMode": mode,
        "island": {"name": island, "berries": list(ISLANDS[island])},
        "iterations": 500, "teamSearchIterations": 80, "teamIterations": 500,
        "instances": [{"uid": uid, "instance": individual_to_engine(dict(by_uid[uid]))}
                      for uid in uids],
    }


def serve(db_path: Path, site: Path, host: str = "127.0.0.1", port: int = 8000,
          engine: str = "engine/bin/pokesleep-engine") -> None:
    class Handler(SimpleHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            if self.path == "/api/confirm-review":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    request = json.loads(self.rfile.read(length))
                    uid = str(request.get("uid", ""))
                    request_db = connect(db_path)
                    try:
                        cursor = request_db.execute(
                            "UPDATE individual SET review_confirmed=1,verified=1 WHERE uid=?", (uid,))
                        request_db.commit()
                    finally:
                        request_db.close()
                    if cursor.rowcount != 1:
                        raise ValueError("個体が見つかりません")
                    body = b'{"confirmed":true}'
                    self.send_response(200)
                except (ValueError, json.JSONDecodeError) as exc:
                    body = json.dumps({"error": str(exc)}, ensure_ascii=False).encode()
                    self.send_response(400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/api/flag-review":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    request = json.loads(self.rfile.read(length))
                    uid = str(request.get("uid", ""))
                    request_db = connect(db_path)
                    try:
                        # Sends the individual back to `protected` regardless of
                        # any prior automatic SP-match verification, since a
                        # user flag means the captured data itself is suspect.
                        cursor = request_db.execute(
                            "UPDATE individual SET review_confirmed=0,verified=0 WHERE uid=?", (uid,))
                        request_db.commit()
                    finally:
                        request_db.close()
                    if cursor.rowcount != 1:
                        raise ValueError("個体が見つかりません")
                    body = b'{"flagged":true}'
                    self.send_response(200)
                except (ValueError, json.JSONDecodeError) as exc:
                    body = json.dumps({"error": str(exc)}, ensure_ascii=False).encode()
                    self.send_response(400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/api/recalculate":
                try:
                    request_db = connect(db_path)
                    try:
                        count = evaluate(request_db, engine)
                        decisions = decide(request_db)
                        seed_plans = evaluate_seed_upgrades(request_db, engine)
                        main_seed_plans = evaluate_main_seed_upgrades(request_db, engine)
                        dashboard = load_dashboard(request_db)
                    finally:
                        request_db.close()
                    settings_path = Path("config/settings.local.json")
                    benchmark_path = Path("data/private/species_benchmarks.json")
                    encounter_path = Path("data/seed_encounters.json")
                    team_path = Path("data/private/team_plans.json")
                    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
                    benchmarks = (json.loads(benchmark_path.read_text()).get("benchmarks", [])
                                  if benchmark_path.exists() else [])
                    encounters = (json.loads(encounter_path.read_text())
                                  if encounter_path.exists() else {})
                    teams = (json.loads(team_path.read_text()).get("plans", [])
                             if team_path.exists() else [])
                    seed_path = Path("data/private/seed_plans.json")
                    seed_path.parent.mkdir(parents=True, exist_ok=True)
                    seed_path.write_text(json.dumps({"plans": seed_plans}, ensure_ascii=False,
                                                    indent=2) + "\n", encoding="utf-8")
                    main_seed_path = Path("data/private/main_seed_plans.json")
                    main_seed_path.write_text(json.dumps({"plans": main_seed_plans}, ensure_ascii=False,
                                                         indent=2) + "\n", encoding="utf-8")
                    render_site(dashboard, site, teams,
                                analyze(dashboard, settings, benchmarks, team_plans=teams,
                                        encounters=encounters),
                                seed_plans=seed_plans, main_seed_plans=main_seed_plans)
                    body = json.dumps({"evaluations": count, "decisions": decisions},
                                      ensure_ascii=False).encode()
                    self.send_response(200)
                except Exception as exc:
                    body = json.dumps({"error": f"再計算に失敗しました: {exc}"},
                                      ensure_ascii=False).encode()
                    self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path in ("/api/update-training", "/api/archive-individual"):
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    payload = json.loads(self.rfile.read(length) or b"{}")
                    uid = str(payload.get("uid", ""))
                    request_db = connect(db_path)
                    try:
                        row = request_db.execute(
                            "SELECT * FROM individual WHERE uid=? AND archived=0", (uid,)).fetchone()
                        if not row:
                            raise ValueError("個体が見つかりません")
                        if self.path == "/api/archive-individual":
                            request_db.execute("UPDATE individual SET archived=1 WHERE uid=?", (uid,))
                        else:
                            level, skill_level = int(payload.get("level", 0)), int(payload.get("skill_level", 0))
                            if not 1 <= level <= 100 or not 1 <= skill_level <= 7:
                                raise ValueError("レベルは1〜100、メインスキルLvは1〜7で指定してください")
                            subskills = json.loads(row["subskills_json"] or "[]")
                            source = payload.get("subskill_from")
                            if source:
                                owned = {name for name, _ in subskills}
                                target = SUBSKILL_UPGRADES.get(source)
                                matches = [(name, unlock) for name, unlock in subskills if name == source]
                                if not target or not matches or target in owned or matches[0][1] > level:
                                    raise ValueError("そのサブスキルは現在強化できません")
                                subskills = [[target if name == source else name, unlock]
                                             for name, unlock in subskills]
                            request_db.execute(
                                """UPDATE individual SET level=?,skill_level=?,subskills_json=?,sp=NULL,
                                   sp_computed=NULL,sp_diff=NULL,verify_mode='skipped',
                                   verified=1,review_confirmed=1 WHERE uid=?""",
                                (level, skill_level, json.dumps(subskills, ensure_ascii=False), uid))
                        request_db.commit()
                    finally:
                        request_db.close()
                    body = json.dumps({"saved": True}, ensure_ascii=False).encode()
                    self.send_response(200)
                except (ValueError, json.JSONDecodeError) as exc:
                    body = json.dumps({"error": str(exc)}, ensure_ascii=False).encode()
                    self.send_response(400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path != "/api/simulate-team":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                request = json.loads(self.rfile.read(length))
                request_db = connect(db_path)
                try:
                    payload = build_simulation_payload(request_db, request)
                finally:
                    request_db.close()
                result = run_engine(payload, engine)
                body = json.dumps(result, ensure_ascii=False).encode()
                self.send_response(200)
            except (ValueError, json.JSONDecodeError) as exc:
                body = json.dumps({"error": str(exc)}, ensure_ascii=False).encode()
                self.send_response(400)
            except Exception as exc:  # keep local UI informative without exposing internals
                body = json.dumps({"error": f"シミュレーションに失敗しました: {exc}"},
                                  ensure_ascii=False).encode()
                self.send_response(500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    handler = partial(Handler, directory=str(site.resolve()))
    print(f"Sleepbox Compass: http://{host}:{port}/")
    ThreadingHTTPServer((host, port), handler).serve_forever()
