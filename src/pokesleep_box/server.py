from __future__ import annotations

import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Mapping, Optional
from urllib.parse import urlsplit

from .analytics import ISLANDS, analyze
from .core import (archive_individuals, build_team_plans, connect, decide, load_archived,
                   load_dashboard, plans_for_box, resolve_uids, training_for_box, unarchive_individuals)
from .engine import (SUBSKILL_UPGRADES, evaluate, evaluate_main_seed_upgrades, evaluate_training,
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
    rows = db.execute(f"SELECT * FROM individual WHERE archived=0 AND uid IN ({marks})",
                      uids).fetchall()
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


# Inputs read when the page is re-rendered. Relative paths resolve against the
# server's working directory, like the other commands' defaults.
DEFAULT_PATHS = {
    "settings": Path("config/settings.local.json"),
    "benchmarks": Path("data/private/species_benchmarks.json"),
    "encounters": Path("data/seed_encounters.json"),
    "teams": Path("data/private/team_plans.json"),
    "training": Path("data/private/training_plans.json"),
    "tier_compare": Path("data/species_tier_compare.json"),
    "seed_plans": Path("data/private/seed_plans.json"),
    "main_seed_plans": Path("data/private/main_seed_plans.json"),
}


def _read_json(path: Path, key: Optional[str] = None, default: Any = None) -> Any:
    if not path.exists():
        return default
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get(key, default) if key else data


def rerender_site(db_path: Path, site: Path, paths: Mapping[str, Path] = DEFAULT_PATHS) -> None:
    """Re-render the page from the database and stored plans without the engine.

    Stored team and seed plans that mention individuals no longer in the box
    are dropped until the next full recalculation rebuilds them.
    """
    db = connect(db_path)
    try:
        dashboard = load_dashboard(db)
        archived = load_archived(db)
    finally:
        db.close()
    active = [item["uid"] for item in dashboard]
    teams_path = Path(paths["teams"])
    teams = (plans_for_box(_read_json(teams_path, "plans", []), active)
             if teams_path.exists() else build_team_plans(dashboard))
    seed_plans = plans_for_box(_read_json(Path(paths["seed_plans"]), "plans", []), active)
    main_seed_plans = plans_for_box(_read_json(Path(paths["main_seed_plans"]), "plans", []), active)
    insights = analyze(dashboard, _read_json(Path(paths["settings"]), default={}),
                       _read_json(Path(paths["benchmarks"]), "benchmarks", []),
                       team_plans=teams, encounters=_read_json(Path(paths["encounters"]), default={}))
    render_site(dashboard, site, teams, insights,
                _read_json(Path(paths["tier_compare"]), "rows", []),
                seed_plans, main_seed_plans, archived=archived,
                training=training_for_box(_read_json(Path(paths["training"]), default={})
                                          if paths.get("training") else {}, active))


LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def request_problem(headers: Mapping[str, str], bound_host: str, require_json: bool) -> Optional[str]:
    """Return why a state-changing request looks cross-site, or None if it is fine.

    The browser always sends `Origin` on cross-site POSTs, so it must match the
    `Host` the page was served from. When bound to loopback, `Host` itself must
    be a loopback name to resist DNS rebinding. Requiring a JSON body forces a
    CORS preflight from other origins, which this server never approves.
    """
    host = headers.get("Host") or ""
    hostname = urlsplit(f"//{host}").hostname or ""
    if bound_host in LOOPBACK_HOSTS and hostname not in LOOPBACK_HOSTS:
        return "このサーバーへはlocalhostから接続してください"
    origin = headers.get("Origin")
    if origin and urlsplit(origin).netloc != host:
        return "別のサイトからの操作は受け付けません"
    if require_json and (headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
        return "Content-Type: application/json で送ってください"
    return None


def change_archive(db_path: Path, site: Path, request: Mapping[str, Any], undo: bool,
                   paths: Mapping[str, Path] = DEFAULT_PATHS) -> Dict[str, Any]:
    """Archive or un-archive individuals by uid, refresh decisions and the page."""
    raw = request.get("uids", [request.get("uid")] if request.get("uid") else [])
    if not isinstance(raw, list) or not raw or not all(isinstance(x, str) and x for x in raw):
        raise ValueError("個体IDを指定してください")
    db = connect(db_path)
    try:
        uids = resolve_uids(db, raw, archived=undo)
        count = (unarchive_individuals if undo else archive_individuals)(db, uids)
        # Removing or restoring a same-species rival can change other verdicts.
        decide(db)
    finally:
        db.close()
    result: Dict[str, Any] = {"uids": uids, ("unarchived" if undo else "archived"): count}
    try:
        rerender_site(db_path, site, paths)
        result["rendered"] = True
    except Exception as exc:  # the DB change already succeeded; report the page problem
        result["rendered"] = False
        result["warning"] = f"保存しましたが、ページの更新に失敗しました: {exc}"
    return result


def make_handler(db_path: Path, site: Path, host: str = "127.0.0.1",
                 engine: str = "engine/bin/pokesleep-engine",
                 paths: Mapping[str, Path] = DEFAULT_PATHS):
    """Build the request handler class serving `site` and the local JSON API."""
    class Handler(SimpleHTTPRequestHandler):
        def send_json(self, status: int, payload: Mapping[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            # Lets the page detect that it is served by this tool (a static copy,
            # e.g. GitHub Pages, has no API) before showing editing buttons.
            if urlsplit(self.path).path == "/api/status":
                self.send_json(200, {"app": "sleepbox-compass", "archive": True})
                return
            super().do_GET()

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            archive_paths = {"/api/archive": False, "/api/unarchive": True,
                             "/api/archive-individual": False}
            problem = request_problem(self.headers, host, require_json=self.path in archive_paths)
            if problem:
                self.send_json(403, {"error": problem})
                return
            if self.path in archive_paths:
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    request = json.loads(self.rfile.read(length) or b"{}")
                    if not isinstance(request, dict):
                        raise ValueError("JSONオブジェクトで送ってください")
                    self.send_json(200, change_archive(db_path, site, request,
                                                       archive_paths[self.path], paths))
                except (ValueError, json.JSONDecodeError) as exc:
                    self.send_json(400, {"error": str(exc)})
                return
            if self.path == "/api/confirm-review":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    request = json.loads(self.rfile.read(length))
                    uid = str(request.get("uid", ""))
                    request_db = connect(db_path)
                    try:
                        cursor = request_db.execute(
                            "UPDATE individual SET review_confirmed=1,verified=1,auto_verified=0,sp_flag=NULL "
                            "WHERE uid=?", (uid,))
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
                            "UPDATE individual SET review_confirmed=0,verified=0,auto_verified=0 WHERE uid=?", (uid,))
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
                        count = evaluate(request_db, engine, Path(paths["teams"]))
                        if paths.get("training"):
                            evaluate_training(request_db, engine, Path(paths["teams"]),
                                              Path(paths["training"]))
                        decisions = decide(request_db)
                        seed_plans = evaluate_seed_upgrades(request_db, engine)
                        main_seed_plans = evaluate_main_seed_upgrades(request_db, engine)
                    finally:
                        request_db.close()
                    for key, plans in (("seed_plans", seed_plans), ("main_seed_plans", main_seed_plans)):
                        plan_path = Path(paths[key])
                        plan_path.parent.mkdir(parents=True, exist_ok=True)
                        plan_path.write_text(json.dumps({"plans": plans}, ensure_ascii=False,
                                                        indent=2) + "\n", encoding="utf-8")
                    rerender_site(db_path, site, paths)
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
            if self.path == "/api/update-training":
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

    return partial(Handler, directory=str(site.resolve()))


def serve(db_path: Path, site: Path, host: str = "127.0.0.1", port: int = 8000,
          engine: str = "engine/bin/pokesleep-engine") -> None:
    handler = make_handler(db_path, site, host, engine)
    print(f"Sleepbox Compass: http://{host}:{port}/")
    ThreadingHTTPServer((host, port), handler).serve_forever()
