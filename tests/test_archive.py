import io
import json
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from pokesleep_box import cli
from pokesleep_box.analytics import analyze
from pokesleep_box.core import (ANCHORS, ROLES, archive_individuals, build_team_plans, connect,
                                decide, import_individuals, load_archived, load_dashboard,
                                plans_for_box, resolve_uids, unarchive_individuals)
from pokesleep_box.server import (build_simulation_payload, make_handler, request_problem,
                                  rerender_site)


ROOT = Path(__file__).parents[1]


def sample_items():
    """Five fictional same-species individuals with full role scores."""
    base = json.loads((ROOT / "data/example_individuals.json").read_text())["individuals"][0]
    items = []
    for i in range(5):
        score = 1000.0 + 100 * i
        items.append(dict(base, uid=f"sample-{i}-" + "x" * 6 + f"{i:06d}",
                          display_name=f"sample-{i}", box_index=i + 1, sp=500 + i,
                          verified=True,
                          island_scores={"シアンの砂浜": {"current": score}},
                          scores={str(a): {r: score for r in ROLES} for a in ANCHORS}))
    return items


class ArchiveCoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "box.sqlite"
        self.db = connect(self.path)
        self.items = sample_items()
        import_individuals(self.db, self.items)
        self.uids = [item["uid"] for item in self.items]

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_archive_records_time_and_unarchive_restores(self):
        self.assertEqual(archive_individuals(self.db, [self.uids[0]]), 1)
        # Archiving twice is a no-op rather than a new timestamp.
        self.assertEqual(archive_individuals(self.db, [self.uids[0]]), 0)
        row = self.db.execute("SELECT archived,archived_at FROM individual WHERE uid=?",
                              (self.uids[0],)).fetchone()
        self.assertEqual(row["archived"], 1)
        self.assertTrue(row["archived_at"])
        listed = load_archived(self.db)
        self.assertEqual([x["uid"] for x in listed], [self.uids[0]])
        self.assertEqual(listed[0]["species_ja"], "フシギダネ")
        self.assertEqual(unarchive_individuals(self.db, [self.uids[0]]), 1)
        row = self.db.execute("SELECT archived,archived_at FROM individual WHERE uid=?",
                              (self.uids[0],)).fetchone()
        self.assertEqual((row["archived"], row["archived_at"]), (0, None))
        self.assertEqual(load_archived(self.db), [])

    def test_archived_at_column_is_added_to_existing_databases(self):
        self.db.close()
        legacy = Path(self.tmp.name) / "legacy.sqlite"
        import sqlite3
        raw = sqlite3.connect(str(legacy))
        raw.execute("CREATE TABLE individual (uid TEXT PRIMARY KEY, species TEXT NOT NULL, "
                    "display_name TEXT, level INTEGER, nature TEXT NOT NULL, "
                    "ingredients_json TEXT NOT NULL, subskills_json TEXT NOT NULL, "
                    "main_skill TEXT NOT NULL, skill_level INTEGER NOT NULL, sp INTEGER, "
                    "box_index INTEGER, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, "
                    "confidence REAL NOT NULL, verified INTEGER NOT NULL DEFAULT 0)")
        raw.commit()
        raw.close()
        self.db = connect(legacy)
        columns = {row["name"] for row in self.db.execute("PRAGMA table_info(individual)")}
        self.assertTrue({"archived", "archived_at"} <= columns)

    def test_archived_rows_are_excluded_from_dashboard_decisions_and_teams(self):
        decide(self.db, keep_top_n=2, compare="all", low_score_threshold=0)
        verdicts = {r["uid"]: r["verdict"] for r in self.db.execute("SELECT * FROM decision")}
        # Each of the three weakest is dominated by at least two stronger rivals.
        self.assertEqual(verdicts[self.uids[2]], "send")
        self.assertEqual(verdicts[self.uids[3]], "keep")
        archive_individuals(self.db, [self.uids[4], self.uids[3]])
        decide(self.db, keep_top_n=2, compare="all", low_score_threshold=0)
        dashboard = load_dashboard(self.db)
        self.assertEqual({x["uid"] for x in dashboard}, set(self.uids[:3]))
        verdicts = {x["uid"]: x["verdict"] for x in dashboard}
        # With the two strongest gone, only the weakest is still dominated twice.
        self.assertEqual(verdicts[self.uids[2]], "keep")
        self.assertEqual(verdicts[self.uids[1]], "keep")
        self.assertEqual(verdicts[self.uids[0]], "send")
        members = {m["uid"] for plan in build_team_plans(dashboard) for m in plan["members"]}
        self.assertFalse(members & {self.uids[3], self.uids[4]})
        coverage = {x["uid"] for x in analyze(dashboard)["coverage"]}
        self.assertFalse(coverage & {self.uids[3], self.uids[4]})

    def test_custom_team_payload_rejects_archived_member(self):
        archive_individuals(self.db, [self.uids[0]])
        with self.assertRaises(ValueError):
            build_simulation_payload(self.db, {"uids": self.uids, "island": "シアンの砂浜"})

    def test_stored_plans_with_archived_members_are_dropped(self):
        plans = [{"island": "A", "members": [{"uid": self.uids[0]}, {"uid": self.uids[1]}]},
                 {"island": "B", "members": [{"uid": self.uids[1]}]},
                 {"uid": self.uids[0], "score": 1}, {"uid": self.uids[1], "score": 2}]
        kept = plans_for_box(plans, self.uids[1:])
        self.assertEqual([p.get("island", p.get("uid")) for p in kept], ["B", self.uids[1]])

    def test_uid_fragments_resolve_by_prefix_or_suffix(self):
        self.assertEqual(resolve_uids(self.db, [self.uids[1]]), [self.uids[1]])
        self.assertEqual(resolve_uids(self.db, ["sample-1"]), [self.uids[1]])
        # The page shows the last 6 characters as the individual ID.
        self.assertEqual(resolve_uids(self.db, [self.uids[2][-6:]]), [self.uids[2]])
        with self.assertRaisesRegex(ValueError, "複数"):
            resolve_uids(self.db, ["sample-"])
        with self.assertRaisesRegex(ValueError, "短すぎ"):
            resolve_uids(self.db, ["b1"])
        with self.assertRaisesRegex(ValueError, "一致する個体がありません"):
            resolve_uids(self.db, ["zzzzzz"])
        archive_individuals(self.db, [self.uids[1]])
        with self.assertRaisesRegex(ValueError, "すでにボックスから外した"):
            resolve_uids(self.db, ["sample-1"], archived=False)
        self.assertEqual(resolve_uids(self.db, ["sample-1"], archived=True), [self.uids[1]])
        with self.assertRaisesRegex(ValueError, "ボックスにあります"):
            resolve_uids(self.db, ["sample-2"], archived=True)


class ArchiveCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "box.sqlite"
        db = connect(self.path)
        self.items = sample_items()
        import_individuals(db, self.items)
        db.close()
        self.uids = [item["uid"] for item in self.items]

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["pokesleep-box", "--db", str(self.path), *args]), \
                redirect_stdout(out), redirect_stderr(err):
            try:
                cli.main()
                code = 0
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def archived_uids(self):
        db = connect(self.path)
        try:
            return [x["uid"] for x in load_archived(db)]
        finally:
            db.close()

    def test_archive_list_and_undo(self):
        code, out, _ = self.run_cli("archive", self.uids[0][-6:], self.uids[1][:8])
        self.assertEqual(code, 0)
        self.assertIn("2匹をボックスから外しました", out)
        self.assertEqual(set(self.archived_uids()), set(self.uids[:2]))
        code, out, _ = self.run_cli("archived")
        self.assertEqual(code, 0)
        lines = out.strip().splitlines()
        self.assertEqual(len(lines), 2)
        fields = lines[0].split("\t")
        self.assertTrue(fields[0].startswith("フシギダネ"))
        self.assertTrue(fields[1].startswith("Lv"))
        self.assertTrue(fields[2].startswith("SP "))
        self.assertIn(fields[4], self.uids[:2])
        code, out, _ = self.run_cli("archive", "--undo", self.uids[0][-6:])
        self.assertEqual(code, 0)
        self.assertIn("1匹をボックスに戻しました", out)
        self.assertEqual(self.archived_uids(), [self.uids[1]])

    def test_ambiguous_or_unknown_uid_fails_without_changes(self):
        code, _, err = self.run_cli("archive", "sample-")
        self.assertEqual(code, 2)
        self.assertIn("複数の個体に一致", err)
        code, _, err = self.run_cli("archive", self.uids[0], "nosuchuid")
        self.assertEqual(code, 2)
        self.assertEqual(self.archived_uids(), [])

    def test_empty_archive_list(self):
        code, out, _ = self.run_cli("archived")
        self.assertEqual(code, 0)
        self.assertIn("博士に送った個体はありません", out)


class ArchiveServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.path = root / "box.sqlite"
        self.site = root / "site"
        db = connect(self.path)
        self.items = sample_items()
        import_individuals(db, self.items)
        decide(db)
        db.close()
        self.uids = [item["uid"] for item in self.items]
        # Point every page input at the temp dir so tests never read real data.
        self.paths = {key: root / f"{key}.json" for key in
                      ("settings", "benchmarks", "encounters", "teams", "tier_compare",
                       "seed_plans", "main_seed_plans")}
        stale = {"plans": [{"island": "シアンの砂浜", "mode": "current", "total_energy": 1,
                            "members": [{"uid": self.uids[0]}]}]}
        self.paths["teams"].write_text(json.dumps(stale), encoding="utf-8")
        rerender_site(self.path, self.site, self.paths)
        handler = make_handler(self.path, self.site, "127.0.0.1", "missing-engine", self.paths)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def request(self, method, path, payload=None, headers=None):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=10)
        body = None if payload is None else json.dumps(payload).encode()
        default = {"Content-Type": "application/json"} if payload is not None else {}
        conn.request(method, path, body=body, headers={**default, **(headers or {})})
        response = conn.getresponse()
        data = response.read()
        conn.close()
        try:
            return response.status, json.loads(data)
        except ValueError:
            return response.status, data

    def page_data(self, name):
        html = (self.site / "index.html").read_text(encoding="utf-8")
        start = html.index(f"const {name}=") + len(f"const {name}=")
        return json.JSONDecoder().raw_decode(html[start:])[0]

    def test_status_endpoint_announces_api(self):
        status, body = self.request("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"app": "sleepbox-compass", "archive": True})

    def test_archive_and_unarchive_rerender_page(self):
        self.assertEqual(len(self.page_data("TEAMS")), 1)
        status, body = self.request("POST", "/api/archive", {"uids": [self.uids[0][-6:]]},
                                    {"Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["archived"], 1)
        self.assertTrue(body["rendered"])
        self.assertNotIn(self.uids[0], [x["uid"] for x in self.page_data("DATA")])
        self.assertEqual([x["uid"] for x in self.page_data("ARCHIVED")], [self.uids[0]])
        # The stored team plan contained the archived member, so it is dropped.
        self.assertEqual(self.page_data("TEAMS"), [])
        status, body = self.request("POST", "/api/unarchive", {"uid": self.uids[0]})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["unarchived"], 1)
        self.assertIn(self.uids[0], [x["uid"] for x in self.page_data("DATA")])
        self.assertEqual(self.page_data("ARCHIVED"), [])

    def test_archive_rejects_bad_requests(self):
        status, body = self.request("POST", "/api/archive", {"uids": ["nosuchuid"]})
        self.assertEqual(status, 400)
        self.assertIn("一致する個体がありません", body["error"])
        status, _ = self.request("POST", "/api/archive", {"uids": []})
        self.assertEqual(status, 400)
        status, _ = self.request("POST", "/api/unarchive", {"uids": [self.uids[0]]})
        self.assertEqual(status, 400)

    def test_cross_site_and_form_posts_are_refused(self):
        status, body = self.request("POST", "/api/archive", {"uids": [self.uids[0]]},
                                    {"Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        conn = HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", "/api/archive", body=f"uids={self.uids[0]}",
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(conn.getresponse().status, 403)
        conn.close()
        status, _ = self.request("POST", "/api/archive", {"uids": [self.uids[0]]},
                                 {"Host": "attacker.example"})
        self.assertEqual(status, 403)
        db = connect(self.path)
        try:
            self.assertEqual(load_archived(db), [])
        finally:
            db.close()

    def test_request_problem_allows_lan_host_when_bound_to_all_interfaces(self):
        headers = {"Host": "192.168.0.5:8000", "Origin": "http://192.168.0.5:8000",
                   "Content-Type": "application/json; charset=utf-8"}
        self.assertIsNone(request_problem(headers, "0.0.0.0", require_json=True))
        self.assertIsNotNone(request_problem(headers, "127.0.0.1", require_json=True))


if __name__ == "__main__":
    unittest.main()
