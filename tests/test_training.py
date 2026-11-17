import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pokesleep_box import engine
from pokesleep_box.core import connect, import_individuals, training_for_box
from pokesleep_box.engine import (TRAINING_TARGETS, evaluate_training, member_roles,
                                  summarize_training, team_gaps)
from pokesleep_box.render import render_site

ROOT = Path(__file__).resolve().parents[1]
ISLAND = "シアンの砂浜"  # favorite berries: ORAN, PAMTRE, PECHA


def profile(skill="Charge Strength S", berry="CHERI", specialty="berry", subskills=()):
    return {"main_skill": skill, "berry": berry, "specialty": specialty, "subskills": list(subskills)}


class RoleGapTests(unittest.TestCase):
    def test_member_roles(self):
        favorites = ("ORAN",)
        self.assertEqual(member_roles(profile("Energy For Everyone S", "ORAN", "skill"), favorites),
                         ["元気回復", "好物きのみ", "スキル枠"])
        self.assertEqual(member_roles(profile(subskills=["Helping Bonus"], specialty="ingredient"),
                                      favorites), ["おてつだい支援", "食材"])
        self.assertEqual(member_roles(profile("Extra Helpful S", specialty="all"), favorites),
                         ["おてつだい支援", "食材", "スキル枠"])

    def test_team_gaps_need_two_favorite_berries(self):
        team = [profile("Energizing Cheer S", "ORAN", "skill"), profile(specialty="ingredient",
                                                                       subskills=["Helping Bonus"])]
        self.assertEqual(team_gaps(team, ("ORAN",)), ["好物きのみ"])
        self.assertEqual(team_gaps(team + [profile(berry="ORAN")], ("ORAN",)), [])


class SummarizeTrainingTests(unittest.TestCase):
    def response(self):
        team = {"a": profile("Energy For Everyone S", "ORAN", "skill"),
                "b": profile(specialty="ingredient", subskills=["Helping Bonus"])}
        return {"islands": [{"island": ISLAND, "baseline": 1000, "team": ["a", "b"], "team_profiles": team,
                             "results": [
                                 {"uid": "c", "target": 50, "gain": 100, "total": 1100, "replaced": "b",
                                  "profile": profile(berry="ORAN", specialty="ingredient")},
                                 {"uid": "c", "target": 60, "gain": 300, "total": 1300, "replaced": "a",
                                  "profile": profile("Energizing Cheer S", "PECHA", "skill")},
                                 {"uid": "d", "target": 60, "gain": 200, "total": 1200, "replaced": "b",
                                  "profile": profile()},
                                 {"uid": "e", "target": 60, "gain": -5, "total": 995, "replaced": "b",
                                  "profile": profile()}]}]}

    def test_entries_ranked_by_best_gain_with_roles_and_cost_hook(self):
        result = summarize_training(self.response(), {"c": {"box_index": 1}, "d": {"box_index": 2}})
        self.assertEqual(result["islands"][0]["gaps"], ["好物きのみ"])
        self.assertEqual([e["uid"] for e in result["entries"]], ["c", "d"])  # e: no gain
        best = result["entries"][0]
        self.assertEqual((best["best"]["target"], best["best"]["gain_per_week"]), (60, 2100))
        self.assertEqual(best["best"]["replaced"], "a")
        # Without "a" the team lacks recovery, skill slot and favorite berries.
        self.assertEqual(best["roles"], ["元気回復", "好物きのみ", "スキル枠"])
        self.assertEqual(len(best["options"]), 2)
        self.assertIsNone(best["cost"])
        # Replacing "b" removes the only ingredient and help-support member.
        self.assertEqual(result["entries"][1]["roles"], [])

    def test_training_for_box_drops_stale_teams_and_individuals(self):
        result = summarize_training(self.response(), {})
        self.assertEqual([e["uid"] for e in training_for_box(result, "abcd")["entries"]], ["c", "d"])
        self.assertEqual(training_for_box(result, "abd")["entries"][0]["uid"], "d")
        # A team member left the box: its island's gains no longer apply.
        self.assertEqual(training_for_box(result, "acd"), {"targets": [], "islands": [], "entries": []})


class EvaluateTrainingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = connect(Path(self.tmp.name) / "box.sqlite")
        items = json.loads((ROOT / "data/example_individuals.json").read_text())["individuals"]
        import_individuals(self.db, items[:3])
        self.uids = [r["uid"] for r in self.db.execute("SELECT uid FROM individual ORDER BY box_index")]
        self.db.execute("UPDATE individual SET verified=1, level=20")
        self.db.execute("UPDATE individual SET level=55 WHERE uid=?", (self.uids[2],))
        self.db.commit()
        self.teams = Path(self.tmp.name) / "teams.json"
        self.teams.write_text(json.dumps({"engineVersion": "v", "plans": [
            {"island": ISLAND, "mode": "current", "members": [{"uid": self.uids[0]}]},
            {"island": ISLAND, "mode": "60", "members": [{"uid": self.uids[1]}]}]}))
        self.out = Path(self.tmp.name) / "training.json"
        self.payloads = []

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def run_training(self):
        def run(payload, *args, **kwargs):
            self.payloads.append(payload)
            team = payload["teams"][ISLAND]
            return {"engineVersion": "v", "islands": [{
                "island": ISLAND, "baseline": 10, "team": team,
                "team_profiles": {uid: profile() for uid in team},
                "results": [{"uid": uid, "target": int(target), "gain": 5, "total": 15,
                             "replaced": team[0], "profile": profile()}
                            for target, uids in payload["candidates"][ISLAND].items() for uid in uids]}]}
        with patch.object(engine, "run_engine", run):
            return evaluate_training(self.db, team_plans=self.teams, out=self.out)

    def test_payload_uses_current_team_and_candidates_below_target(self):
        document = self.run_training()
        payload = self.payloads[0]
        self.assertEqual(payload["mode"], "training-gain")
        self.assertEqual(payload["teams"], {ISLAND: [self.uids[0]]})
        self.assertEqual(list(payload["islands"]), [ISLAND])
        by_target = payload["candidates"][ISLAND]
        self.assertEqual(set(by_target), {str(t) for t in TRAINING_TARGETS})
        self.assertNotIn(self.uids[2], by_target["50"])  # already above Lv50
        self.assertIn(self.uids[2], by_target["60"])
        self.assertIn(self.uids[0], by_target["60"])  # a member can be raised too
        self.assertEqual(json.loads(self.out.read_text())["entries"], document["entries"])
        self.assertTrue(all(entry["cost"] is None for entry in document["entries"]))

    def test_unchanged_inputs_reuse_and_changes_recompute(self):
        self.run_training()
        self.run_training()
        self.assertEqual(len(self.payloads), 1)
        self.db.execute("UPDATE individual SET level=21 WHERE uid=?", (self.uids[1],))
        self.db.commit()
        self.run_training()
        self.assertEqual(len(self.payloads), 2)

    def test_unverified_or_archived_are_not_candidates(self):
        self.db.execute("UPDATE individual SET verified=0 WHERE uid=?", (self.uids[1],))
        self.db.execute("UPDATE individual SET archived=1 WHERE uid=?", (self.uids[2],))
        self.db.commit()
        self.run_training()
        pool = {uid for uids in self.payloads[0]["candidates"][ISLAND].values() for uid in uids}
        self.assertEqual(pool, {self.uids[0]})

    def test_no_team_plans_skips_engine(self):
        self.teams.unlink()
        self.assertIsNone(self.run_training())
        self.assertEqual(self.payloads, [])


class TrainingRenderTests(unittest.TestCase):
    def test_training_data_is_embedded(self):
        with tempfile.TemporaryDirectory() as tmp:
            training = {"islands": [{"island": ISLAND, "gaps": ["食材"], "team": []}],
                        "entries": [{"uid": "u1", "best": {"island": ISLAND, "target": 60,
                                                           "gain_per_week": 700}, "cost": None}]}
            render_site([], Path(tmp), training=training)
            page = (Path(tmp) / "index.html").read_text(encoding="utf-8")
        self.assertIn('const TRAINING={"islands": [{"island": "シアンの砂浜"', page)
        self.assertIn("trainingSortKey", page)


if __name__ == "__main__":
    unittest.main()


class TrainingCostTests(unittest.TestCase):
    def test_costs_rank_entries_by_gain_per_shard(self):
        from pokesleep_box.core import attach_training_costs
        training = {"entries": [
            {"uid": "low", "options": [{"island": "A", "target": 60, "gain_per_week": 70000}]},
            {"uid": "high", "options": [{"island": "A", "target": 60, "gain_per_week": 100000},
                                        {"island": "B", "target": 50, "gain_per_week": 90000}]},
            {"uid": "unknown", "options": [{"island": "A", "target": 60, "gain_per_week": 999999}]},
        ]}
        items = [{"uid": "low", "species": "PIKACHU", "nature": "Bashful", "level": 55},
                 {"uid": "high", "species": "PIKACHU", "nature": "Bashful", "level": 10},
                 {"uid": "unknown", "species": "NOT_A_SPECIES", "nature": "Bashful", "level": 10}]
        result = attach_training_costs(training, items)
        # Lv55->60 costs ~251k shards, Lv10->50 ~166k: the larger, cheaper gain wins.
        self.assertEqual([e["uid"] for e in result["entries"]], ["high", "low", "unknown"])
        high = result["entries"][0]
        # Lv10 -> Lv50 is cheaper per gain than Lv10 -> Lv60 here.
        self.assertEqual(high["best"]["target"], 50)
        self.assertGreater(high["cost"]["dream_shards"], 0)
        self.assertIsNone(result["entries"][2]["best"]["gain_per_10k_shards"])
