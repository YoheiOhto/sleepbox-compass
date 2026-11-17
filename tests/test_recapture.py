import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pokesleep_box.core import connect, import_individuals
from pokesleep_box.ingest import audit, ingest_path

# Fictional sample individuals; evolution links follow the pinned species data.
METADATA = {
    "PICHU": {"evolves_from": None},
    "PIKACHU": {"evolves_from": "PICHU"},
    "RAICHU": {"evolves_from": "PIKACHU"},
    "BULBASAUR": {"evolves_from": None},
}
SUBSKILLS = [["Helping Speed S", 10], ["Berry Finding S", 25], ["Skill Trigger S", 50],
             ["Inventory Up S", 70], ["Dream Shard Bonus", 80]]


def pichu(**changes):
    item = {"species": "PICHU", "level": 10, "sp": 400, "nature": "Brave",
            "ingredients": [["Apple", 1], ["Ginger", 2], ["Egg", 3]],
            "subskills": [list(x) for x in SUBSKILLS],
            "main_skill": "Charge Strength S", "skill_level": 1,
            "confidence": 1.0, "verified": False}
    item.update(changes)
    return item


def still(name, **changes):
    return dict(pichu(**changes), uid=f"capture-{name}")


class RecaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = connect(self.root / "box.sqlite")
        patcher = patch("pokesleep_box.ocr.species_metadata", return_value=METADATA)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def rows(self):
        return self.db.execute("SELECT * FROM individual ORDER BY uid").fetchall()

    def test_higher_level_and_sp_update_the_same_uid(self):
        import_individuals(self.db, [still("a")])
        first_seen = self.rows()[0]["first_seen"]
        self.db.execute("INSERT INTO decision VALUES ('capture-a','keep','test','now')")
        later = still("b", level=18, sp=620, skill_level=2)
        import_individuals(self.db, [later])
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["uid"], "capture-a")
        self.assertEqual((rows[0]["level"], rows[0]["sp"], rows[0]["skill_level"]), (18, 620, 2))
        self.assertEqual(rows[0]["first_seen"], first_seen)
        self.assertEqual(later["uid"], "capture-a")
        self.assertTrue(later["recaptured"])

    def test_evolution_updates_species_and_drops_verification(self):
        import_individuals(self.db, [still("a", verified=True)])
        import_individuals(self.db, [still("b", species="RAICHU", level=30, sp=900)])
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["uid"], rows[0]["species"]), ("capture-a", "RAICHU"))
        self.assertEqual(rows[0]["verified"], 0)

    def test_devolution_or_other_species_is_a_new_individual(self):
        import_individuals(self.db, [still("a", species="PIKACHU")])
        import_individuals(self.db, [still("b", species="PICHU", level=20)])
        import_individuals(self.db, [still("c", species="BULBASAUR", level=20)])
        self.assertEqual(len(self.rows()), 3)

    def test_without_evolution_data_only_the_same_species_matches(self):
        import_individuals(self.db, [still("a")])
        with patch("pokesleep_box.ocr.species_metadata", return_value={}):
            import_individuals(self.db, [still("b", species="PIKACHU", level=30)])
        self.assertEqual(len(self.rows()), 2)

    def test_subskill_seed_upgrade_updates(self):
        import_individuals(self.db, [still("a")])
        upgraded = [list(x) for x in SUBSKILLS]
        upgraded[0][0] = "Helping Speed M"
        upgraded[3][0] = "Inventory Up L"
        import_individuals(self.db, [still("b", level=12, subskills=upgraded)])
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0]["subskills_json"]), upgraded)

    def test_other_subskill_change_is_a_new_individual(self):
        import_individuals(self.db, [still("a")])
        other = [list(x) for x in SUBSKILLS]
        other[0], other[1] = other[1], other[0]
        import_individuals(self.db, [still("b", subskills=other)])
        downgraded = [list(x) for x in SUBSKILLS]
        downgraded[2][0] = "Helping Speed S"
        import_individuals(self.db, [still("c", subskills=downgraded)])
        self.assertEqual(len(self.rows()), 3)

    def test_different_nature_or_ingredient_is_a_new_individual(self):
        import_individuals(self.db, [still("a")])
        import_individuals(self.db, [still("b", nature="Mild", level=20)])
        import_individuals(self.db, [still("c", level=20,
                                           ingredients=[["Apple", 1], ["Apple", 2], ["Egg", 3]])])
        self.assertEqual(len(self.rows()), 3)

    def test_identical_stills_in_one_batch_are_one_individual(self):
        import_individuals(self.db, [still("a"), still("b")])
        self.assertEqual([row["uid"] for row in self.rows()], ["capture-a"])

    def test_same_build_at_another_level_in_one_batch_stays_distinct(self):
        import_individuals(self.db, [still("a"), still("b", level=20, sp=700)])
        self.assertEqual(len(self.rows()), 2)

    def test_merge_exact_duplicates_cleans_stored_rows(self):
        from pokesleep_box.core import merge_exact_duplicates
        import_individuals(self.db, [still("a")])
        columns = ("species,nature,ingredients_json,subskills_json,main_skill,skill_level,"
                   "level,sp,first_seen,last_seen,confidence,review_confirmed")
        for uid, verified in (("capture-b", 1), ("capture-c", 0)):
            self.db.execute(f"INSERT INTO individual (uid,verified,{columns}) "
                            f"SELECT ?,?,{columns} FROM individual WHERE uid='capture-a'",
                            (uid, verified))
        self.db.execute("UPDATE individual SET verified=0 WHERE uid='capture-a'")
        merged = merge_exact_duplicates(self.db)
        self.assertEqual(merged, [("capture-b", ["capture-a", "capture-c"])])
        self.assertEqual([row["uid"] for row in self.rows()], ["capture-b"])

    def test_one_stored_row_is_not_claimed_twice_by_one_batch(self):
        import_individuals(self.db, [still("a")])
        import_individuals(self.db, [still("b", level=15), still("c", level=15, sp=600)])
        uids = [row["uid"] for row in self.rows()]
        self.assertEqual(uids, ["capture-a", "capture-c"])

    def test_identical_builds_merge_only_into_the_unique_lower_row(self):
        import_individuals(self.db, [still("low", level=10, sp=400),
                                     still("high", level=40, sp=1200)])
        import_individuals(self.db, [still("new", level=20, sp=700)])
        rows = {row["uid"]: row["level"] for row in self.rows()}
        self.assertEqual(rows, {"capture-low": 20, "capture-high": 40})

    def test_ambiguous_identical_builds_create_a_reported_row(self):
        import_individuals(self.db, [still("one", level=10, sp=400),
                                     still("two", level=12, sp=450)])
        item = still("new", level=20, sp=700)
        import_individuals(self.db, [item])
        self.assertEqual(len(self.rows()), 3)
        self.assertEqual(item["possible_duplicates"], 2)
        report = self.root / "audit.md"
        self.assertEqual(audit([item], report)["possible_duplicates"], 1)
        self.assertIn("重複の可能性", report.read_text(encoding="utf-8"))

    def test_unchanged_recapture_prefers_the_exact_identical_build(self):
        import_individuals(self.db, [still("one", level=10, sp=400),
                                     still("two", level=12, sp=450)])
        item = still("new", level=12, sp=450)
        import_individuals(self.db, [item])
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(item["uid"], "capture-two")

    def test_lower_level_is_not_a_recapture_and_level_never_decreases(self):
        import_individuals(self.db, [still("a", level=30, sp=900)])
        import_individuals(self.db, [still("b", level=20, sp=700)])
        self.assertEqual(len(self.rows()), 2)

    def test_sp_never_decreases(self):
        import_individuals(self.db, [still("a", level=30, sp=900)])
        # Same level with an SP misread lower: merged, but SP stays.
        import_individuals(self.db, [still("c", level=30, sp=850)])
        self.assertEqual(len(self.rows()), 1)
        row = self.db.execute("SELECT * FROM individual WHERE uid='capture-a'").fetchone()
        self.assertEqual((row["level"], row["sp"]), (30, 900))

    def test_ocr_missing_fields_are_not_overwritten(self):
        import_individuals(self.db, [still("a", display_name="サンプル")])
        partial = still("b", level=25, sp=None, main_skill="", skill_level=1,
                        ingredients=[["Apple", 1]],
                        ocr_missing=["ingredients", "main_skill", "sp"])
        import_individuals(self.db, [partial])
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["level"], 25)
        self.assertEqual(row["sp"], 400)
        self.assertEqual(row["main_skill"], "Charge Strength S")
        self.assertEqual(row["display_name"], "サンプル")
        self.assertEqual(json.loads(row["ingredients_json"]),
                         [["Apple", 1], ["Ginger", 2], ["Egg", 3]])

    def test_review_confirmation_is_kept(self):
        import_individuals(self.db, [still("a", review_confirmed=True)])
        import_individuals(self.db, [still("b", level=20, sp=600)])
        row = self.rows()[0]
        self.assertEqual((row["review_confirmed"], row["verified"]), (1, 1))

    def test_archived_individuals_are_not_matched(self):
        import_individuals(self.db, [still("a")])
        self.db.execute("UPDATE individual SET archived=1")
        import_individuals(self.db, [still("b", level=20)])
        self.assertEqual(len(self.rows()), 2)

    def test_scanned_still_updates_the_stored_individual(self):
        import_individuals(self.db, [still("a")])
        inbox = self.root / "inbox"
        inbox.mkdir()
        (inbox / "later.jpg").write_bytes(b"later capture")
        with patch("pokesleep_box.ocr.scan",
                   side_effect=lambda *args, **kwargs: [pichu(level=22, sp=760)]):
            rows = ingest_path(inbox, self.root / "frames", vision=True)
        self.assertTrue(rows[0]["uid"].startswith("capture-"))
        import_individuals(self.db, rows)
        stored = self.rows()
        self.assertEqual([(r["uid"], r["level"]) for r in stored], [("capture-a", 22)])
        # Importing the same capture again stays idempotent.
        import_individuals(self.db, rows)
        self.assertEqual(len(self.rows()), 1)


if __name__ == "__main__":
    unittest.main()
