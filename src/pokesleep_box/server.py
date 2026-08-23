from __future__ import annotations

import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

from .analytics import ISLANDS, analyze
from .core import connect, decide, load_dashboard
from .engine import evaluate, individual_to_engine, run_engine
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
            if self.path == "/api/recalculate":
                try:
                    request_db = connect(db_path)
                    try:
                        count = evaluate(request_db, engine)
                        decisions = decide(request_db)
                        dashboard = load_dashboard(request_db)
                    finally:
                        request_db.close()
                    settings_path = Path("config/settings.local.json")
                    benchmark_path = Path("data/private/species_benchmarks.json")
                    team_path = Path("data/private/team_plans.json")
                    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
                    benchmarks = (json.loads(benchmark_path.read_text()).get("benchmarks", [])
                                  if benchmark_path.exists() else [])
                    teams = (json.loads(team_path.read_text()).get("plans", [])
                             if team_path.exists() else [])
                    render_site(dashboard, site, teams,
                                analyze(dashboard, settings, benchmarks, team_plans=teams))
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
