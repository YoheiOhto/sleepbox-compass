import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pokesleep_box import engine
from pokesleep_box.analytics import analyze, capture_species_rows, capture_tonight
from pokesleep_box.core import capture_for_box, connect, import_individuals
from pokesleep_box.engine import CAPTURE_BUILD, evaluate_capture, summarize_capture
from pokesleep_box.render import render_site

ROOT = Path(__file__).resolve().parents[1]
ISLAND = "シアンの砂浜"  # favorite berries: ORAN, PAMTRE, PECHA
OTHER = "トープ洞窟"


def profile(skill="Charge Strength S", berry="CHERI", specialty="berry"):
    return {"main_skill": skill, "berry": berry, "specialty": specialty, "subskills": []}


def result(species, build, baseline, gain, replaced="a", target=50, prof=None):
    return {"species": species, "build": build, "baseline": baseline, "target": target,
            "gain": gain, "total": 1000 + gain, "replaced": replaced, "profile": prof or profile()}


def response():
    """A fake `capture-gain` response: Pichu line (RAICHU) and Eevee line (two branches)."""
    team = {"a": profile("Energy For Everyone S", "ORAN", "skill"), "b": profile(berry="PECHA")}
    trained = {"a": profile("Energy For Everyone S", "ORAN", "skill"), "b": profile(berry="ORAN")}
    return {
        "engineVersion": "v",
        "names": {"Pichu": ["PICHU"], "Raichu": ["RAICHU"], "Eevee": ["EEVEE"], "Ghost": []},
        "lines": [
            {"base": "PICHU", "encountered": ["PICHU", "RAICHU"], "members": ["PICHU", "PIKACHU", "RAICHU"],
             "finals": [{"species": "RAICHU", "specialty": "berry", "target": 50}]},
            {"base": "EEVEE", "encountered": ["EEVEE"], "members": ["EEVEE", "SYLVEON", "UMBREON"],
             "finals": [{"species": "SYLVEON", "specialty": "skill", "target": 50},
                        {"species": "UMBREON", "specialty": "skill", "target": 50}]}],
        "islands": [
            {"island": ISLAND, "teams": {"current": ["a", "b"], "50": ["a", "b"]},
             "baselines": {"current": 900, "50": 1000},
             "team_profiles": {"current": team, "50": trained},
             "results": [
                 result("RAICHU", "typical", "current", 300),
                 result("RAICHU", "typical", "50", 100, prof=profile(berry="PAMTRE")),
                 result("RAICHU", "ideal", "50", 400, replaced="b"),
                 result("SYLVEON", "typical", "current", 50),
                 result("SYLVEON", "typical", "50", -20, prof=profile("Energizing Cheer S", "PECHA", "skill")),
                 result("SYLVEON", "ideal", "50", 80),
                 result("UMBREON", "typical", "current", -10),
                 result("UMBREON", "typical", "50", -50),
                 result("UMBREON", "ideal", "50", -5)]},
            {"island": OTHER, "teams": {"current": ["c"], "50": ["c"]},
             "baselines": {"current": 500, "50": 600},
             "team_profiles": {"current": {"c": profile()}, "50": {"c": profile()}},
             "results": [result("RAICHU", "typical", "current", 10, replaced="c"),
                         result("RAICHU", "typical", "50", 5, replaced="c"),
                         result("RAICHU", "ideal", "50", 50, replaced="c")]}]}


ENCOUNTERS = {"updated": "2026-11-17", "fields": {
    "ゴールド旧発電所": {"すやすや": ["Raichu", "Eevee", "Ghost"], "ぐっすり": ["Pichu"]}}}


class SummarizeCaptureTests(unittest.TestCase):
    def test_options_split_by_baseline_and_build(self):
        doc = summarize_capture(response())
        by_species = {e["species"]: e for e in doc["species"]}
        self.assertEqual([e["species"] for e in doc["species"]], ["RAICHU", "SYLVEON", "UMBREON"])
        raichu = by_species["RAICHU"]
        self.assertEqual(raichu["line"], ["PICHU", "PIKACHU", "RAICHU"])
        self.assertEqual(raichu["encountered"], ["PICHU", "RAICHU"])
        best = raichu["options"][0]
        self.assertEqual((best["island"], best["gain_now"], best["gain"], best["gain_ideal"]),
                         (ISLAND, 300, 100, 400))
        self.assertEqual((best["replaced"], best["replaced_ideal"]), ("a", "b"))
        # Without "a" only one favorite-berry member is left (two are needed).
        self.assertEqual(best["roles"], ["好物きのみ"])
        self.assertEqual(doc["islands"][0]["gaps"]["current"], ["おてつだい支援", "食材"])
        self.assertEqual(doc["names"]["Ghost"], [])

    def test_capture_for_box_drops_islands_with_missing_team_members(self):
        doc = summarize_capture(response())
        kept = capture_for_box(doc, ["a", "b"])  # "c" left the box
        self.assertEqual([i["island"] for i in kept["islands"]], [ISLAND])
        raichu = next(e for e in kept["species"] if e["species"] == "RAICHU")
        self.assertEqual([o["island"] for o in raichu["options"]], [ISLAND])
        self.assertEqual(capture_for_box(doc, ["c"])["species"][0]["options"][0]["island"], OTHER)


class CaptureViewTests(unittest.TestCase):
    items = [{"uid": "a", "species": "SYLVEON", "final_evolution": "SYLVEON", "absolute_score": 70.0},
             {"uid": "b", "species": "PIKACHU", "final_evolution": "RAICHU", "absolute_score": 82.5}]

    def test_rows_status_cost_and_owned_line(self):
        rows = capture_species_rows(summarize_capture(response()), self.items)
        raichu, sylveon, umbreon = rows["RAICHU"], rows["SYLVEON"], rows["UMBREON"]
        self.assertEqual((raichu["status"], raichu["gain_per_week"]), ("good", 700))
        self.assertEqual(raichu["species"], "ピチュー")
        self.assertEqual(raichu["evolves_to"], "ライチュウ")
        self.assertEqual(raichu["replaced"], "ニンフィア")  # named by final evolution
        self.assertEqual((raichu["owned_count"], raichu["owned_score"]), (1, 82.5))
        self.assertEqual(raichu["helps"], [ISLAND, OTHER])
        self.assertGreater(raichu["cost"]["dream_shards"], 0)
        self.assertEqual(raichu["gain_per_10k_shards"],
                         round(700 / raichu["cost"]["dream_shards"] * 10000))
        # Only the ideal build beats the trained team.
        self.assertEqual((sylveon["status"], sylveon["gain_per_week"]), ("lucky", 560))
        self.assertEqual(sylveon["typical_per_week"], -140)
        self.assertIn("理想の性格・サブスキルなら", sylveon["reason"])
        self.assertEqual(sylveon["roles"], ["元気回復", "好物きのみ", "スキル枠"])
        self.assertEqual(umbreon["status"], "skip")
        self.assertIsNone(umbreon["gain_per_10k_shards"])

    def test_catch_level_changes_cost(self):
        doc = summarize_capture(response())
        low = capture_species_rows(doc, [], catch_level=5)["RAICHU"]["cost"]["dream_shards"]
        high = capture_species_rows(doc, [], catch_level=30)["RAICHU"]["cost"]["dream_shards"]
        self.assertGreater(low, high)

    def test_views_follow_encounters_and_merge_lines(self):
        views = capture_tonight(summarize_capture(response()), self.items, ENCOUNTERS)
        self.assertEqual([v["field"] for v in views], ["ゴールド旧発電所"])
        snooze, slumber = views[0]["sleep_types"]
        self.assertEqual(snooze["sleep_type"], "すやすや")
        self.assertEqual([r["species_key"] for r in snooze["good"]], ["RAICHU"])
        self.assertEqual(snooze["good"][0]["seen"], ["ライチュウ"])
        # One row for the Eevee line: its best branch, the other one listed.
        self.assertEqual([r["species_key"] for r in snooze["lucky"]], ["SYLVEON"])
        self.assertEqual(snooze["lucky"][0]["others"], ["ブラッキー"])
        self.assertEqual(snooze["skip"], [])
        self.assertEqual(slumber["good"][0]["seen"], ["ピチュー"])

    def test_analyze_uses_capture_level_setting(self):
        result = analyze(self.items, {"captureLevel": 20}, encounters=ENCOUNTERS,
                         capture_plans=summarize_capture(response()))
        capture = result["capture"]
        self.assertEqual(capture["catch_level"], 20)
        default = analyze(self.items, {}, encounters=ENCOUNTERS,
                          capture_plans=summarize_capture(response()))["capture"]
        cost = lambda c: c["tonight"][0]["sleep_types"][0]["good"][0]["cost"]["dream_shards"]
        self.assertLess(cost(capture), cost(default))
        self.assertEqual(analyze([], {}, encounters=ENCOUNTERS)["capture"]["tonight"], [])


class EvaluateCaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        self.db = connect(tmp / "box.sqlite")
        items = json.loads((ROOT / "data/example_individuals.json").read_text())["individuals"]
        import_individuals(self.db, items[:3])
        self.uids = [r["uid"] for r in self.db.execute("SELECT uid FROM individual ORDER BY box_index")]
        self.teams = tmp / "teams.json"
        self.teams.write_text(json.dumps({"engineVersion": "v", "plans": [
            {"island": ISLAND, "mode": "current", "members": [{"uid": self.uids[0]}]},
            {"island": ISLAND, "mode": "50", "members": [{"uid": self.uids[1]}]},
            {"island": ISLAND, "mode": "80", "members": [{"uid": self.uids[2]}]},
            {"island": OTHER, "mode": "50", "members": [{"uid": self.uids[2]}]}]}))
        self.encounters = tmp / "encounters.json"
        self.encounters.write_text(json.dumps(ENCOUNTERS))
        self.out = tmp / "capture.json"
        self.payloads = []

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def run_capture(self):
        def run(payload, *args, **kwargs):
            self.payloads.append(payload)
            return response()
        with patch.object(engine, "run_engine", run):
            return evaluate_capture(self.db, team_plans=self.teams, encounters=self.encounters,
                                    out=self.out)

    def test_payload_teams_encounters_and_build(self):
        document = self.run_capture()
        payload = self.payloads[0]
        self.assertEqual(payload["mode"], "capture-gain")
        # Lv80 plans are not used; an island without a current team is skipped.
        self.assertEqual(payload["teams"], {ISLAND: {"current": [self.uids[0]], "50": [self.uids[1]]}})
        self.assertEqual(payload["encounters"], ["Eevee", "Ghost", "Pichu", "Raichu"])
        self.assertEqual(payload["typicalNature"], CAPTURE_BUILD["nature"])
        self.assertEqual({x["uid"] for x in payload["instances"]}, set(self.uids[:2]))
        stored = json.loads(self.out.read_text())
        self.assertEqual(stored["species"], document["species"])
        self.assertEqual(stored["build"], CAPTURE_BUILD)

    def test_unchanged_inputs_reuse_and_team_changes_recompute(self):
        self.run_capture()
        # A non-member's change does not touch the request.
        self.db.execute("UPDATE individual SET level=33 WHERE uid=?", (self.uids[2],))
        self.db.commit()
        self.run_capture()
        self.assertEqual(len(self.payloads), 1)
        self.db.execute("UPDATE individual SET level=21 WHERE uid=?", (self.uids[1],))
        self.db.commit()
        self.run_capture()
        self.assertEqual(len(self.payloads), 2)

    def test_missing_inputs_skip_engine(self):
        self.encounters.write_text(json.dumps({"fields": {}}))
        self.assertIsNone(self.run_capture())
        self.encounters.unlink()
        self.assertIsNone(self.run_capture())
        self.assertEqual(self.payloads, [])


class CaptureRenderTests(unittest.TestCase):
    def test_capture_views_are_embedded(self):
        insights = analyze([], {}, encounters=ENCOUNTERS, capture_plans=summarize_capture(response()))
        with tempfile.TemporaryDirectory() as tmp:
            render_site([], Path(tmp), insights=insights)
            page = (Path(tmp) / "index.html").read_text(encoding="utf-8")
        self.assertIn('"tonight": [{"field": "ゴールド旧発電所"', page)
        self.assertIn("今夜ねらうポケモン", page)

    def test_encounter_data_resolves_to_known_species_format(self):
        data = json.loads((ROOT / "data/seed_encounters.json").read_text(encoding="utf-8"))
        self.assertTrue(data["updated"])
        self.assertIn("シアンの砂浜EX", data["fields"])
        for groups in data["fields"].values():
            self.assertEqual(set(groups), {"うとうと", "すやすや", "ぐっすり"})
            self.assertTrue(all(isinstance(name, str) and name for names in groups.values()
                                for name in names))


if __name__ == "__main__":
    unittest.main()
