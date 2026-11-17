"""SP candidate repair, two-model agreement, ingredient icons and the review page.

All data is fictional; the engine, VLM and icon matcher are fakes.
"""
import json
import tempfile
import unittest
from dataclasses import fields, replace
from pathlib import Path
from unittest.mock import patch

from pokesleep_box.core import connect, import_individuals, record_synced_sources
from pokesleep_box.ingest import file_sha256, render_review
from pokesleep_box.localization import to_japanese
from pokesleep_box.ocr import enrich_with_species_data
from pokesleep_box.paths import DEFAULT_PATHS, PrivatePaths
from pokesleep_box.repair import (_sp_blind, build_candidates, confirm_partials, icon_downgrades,
                                  repair_mismatches)
from pokesleep_box.spcheck import NEUTRAL_NATURES, SP_BLIND_SUBSKILLS, check_items, review_order
from pokesleep_box.sync import FALLBACK_VLM_MODEL, run_sync

FAST = "mlx-community/Qwen3.5-4B-4bit"
SUBSKILLS = [["Helping Speed S", 10], ["Berry Finding S", 25], ["Skill Trigger S", 50],
             ["Inventory Up S", 70], ["Dream Shard Bonus", 80]]
OPTIONS = [{"level": 1, "choices": [["Apple", 1, "a"]]},
           {"level": 30, "choices": [["Ginger", 2, "g"], ["Egg", 3, "e"]]},
           {"level": 60, "choices": [["Egg", 3, "e"], ["Apple", 4, "a"]]}]


def scanned(name, **changes):
    item = {"uid": f"capture-{name}", "species": "PICHU", "level": 26, "sp": 400,
            "nature": "Brave", "ingredients": [["Apple", 1], ["Ginger", 2], ["Egg", 3]],
            "subskills": [list(x) for x in SUBSKILLS], "main_skill": "Charge Strength S",
            "skill_level": 2, "confidence": .99, "verified": False, "ocr_missing": [],
            "ingredient_options": OPTIONS, "ingredient_amounts": [1, 2, 3]}
    item.update(changes)
    return item


def _sp_key(instance):
    """What the (fake) SP depends on, with SP-equal values merged."""
    level = instance["level"]
    nature = "neutral" if instance["nature"] in NEUTRAL_NATURES else instance["nature"]
    blind = {"Skill Level Up S": "slu", "Skill Level Up M": "slu"}
    subskills = tuple(sorted(
        blind.get(name, "bonus" if name in SP_BLIND_SUBSKILLS else name)
        for name, unlock in instance["subskills"] if unlock <= level))
    return (level, nature, subskills, min(instance["skillLevel"], 6))


def build_engine(truth, calls=None):
    """run_engine stand-in: the shown SP is reproduced only by the builds in
    ``truth[uid]`` (lists of changes from the stored item) and by builds the
    SP cannot tell apart from them; uids not in ``truth`` always match."""
    def run(payload, command=None, on_progress=None):
        assert payload["mode"] == "verify"
        if calls is not None:
            calls.append(len(payload["instances"]))
        results = []
        for entry in payload["instances"]:
            uid = entry["uid"].split("|")[0]
            builds = truth.get(uid)
            ok = builds is None or _sp_key(entry["instance"]) in builds
            diff = 0 if ok else 9
            results.append({"uid": entry["uid"], "computedSp": entry["displayedSp"] + diff,
                            "diff": diff, "match": ok, "mode": "strict", "skillMaxLevel": 6})
        return {"results": results}
    return run


def key_of(item, **changes):
    build = dict(item, **changes)
    return _sp_key({"level": build["level"], "nature": build["nature"],
                    "subskills": build["subskills"], "skillLevel": build["skill_level"]})


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = connect(self.root / "box.sqlite")
        self.calls = []

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def row(self, uid):
        return self.db.execute("SELECT * FROM individual WHERE uid=?", (uid,)).fetchone()


class CandidateRepairTests(Base):
    def repair(self, item, *truths):
        import_individuals(self.db, [item])
        truth = {"capture-a": set(truths)}
        with patch("pokesleep_box.engine.run_engine", side_effect=build_engine(truth, self.calls)):
            check_items(self.db, [item], "engine")
            return repair_mismatches(self.db, [item], "engine", {}, None, self.root / "frames")

    def test_unique_nature_is_repaired(self):
        item = scanned("a")
        stats = self.repair(item, key_of(item, nature="Adamant"))
        row = self.row("capture-a")
        self.assertEqual((row["nature"], row["repaired"], row["verified"]), ("Adamant", 1, 1))
        self.assertEqual(row["repair_note"],
                         f"性格 {to_japanese('natures', 'Brave')}→"
                         f"{to_japanese('natures', 'Adamant')}（SPで補正）")
        self.assertEqual((stats["sp_repaired"], item["sp_check"]), (1, "match"))
        self.assertEqual(len(self.calls), 3)  # check, round 1, re-check: no round 2

    def test_unique_subskill_grade_is_repaired(self):
        item = scanned("a")
        subskills = [list(x) for x in SUBSKILLS]
        subskills[0][0] = "Helping Speed M"
        self.repair(item, key_of(item, subskills=subskills))
        row = self.row("capture-a")
        self.assertEqual(json.loads(row["subskills_json"])[0][0], "Helping Speed M")
        self.assertIn("サブスキル1", row["repair_note"])
        self.assertEqual(row["verified"], 1)

    def test_unique_skill_level_is_repaired(self):
        item = scanned("a")
        self.repair(item, key_of(item, skill_level=3))
        row = self.row("capture-a")
        self.assertEqual((row["skill_level"], row["repair_note"]), (3, "スキルLv 2→3（SPで補正）"))

    def test_level_plus_nature_in_round_two(self):
        item = scanned("a")
        self.repair(item, key_of(item, level=24, nature="Adamant"))
        row = self.row("capture-a")
        self.assertEqual((row["level"], row["nature"], row["verified"]), (24, "Adamant", 1))
        self.assertTrue(row["repair_note"].startswith("Lv 26→24、性格 "))
        self.assertEqual(len(self.calls), 4)  # check, round 1, round 2, re-check
        self.assertLessEqual(max(self.calls), 500)

    def test_two_candidates_are_ambiguous(self):
        item = scanned("a")
        stats = self.repair(item, key_of(item, nature="Adamant"), key_of(item, level=25))
        row = self.row("capture-a")
        self.assertEqual((row["nature"], row["level"], row["repaired"], row["verified"]),
                         ("Brave", 26, 0, 0))
        self.assertEqual(stats["still_mismatch"], 1)

    def test_sp_invisible_alternatives_are_never_chosen(self):
        # Neutral natures all give the same SP: five fits, no repair.
        item = scanned("a")
        self.repair(item, key_of(item, nature="Hardy"))
        self.assertEqual((self.row("capture-a")["nature"], self.row("capture-a")["repaired"]),
                         ("Brave", 0))
        # A lone fit into or out of an SP-blind value is rejected as well.
        row = {"subskills_json": json.dumps(SUBSKILLS)}
        self.assertTrue(_sp_blind(row, {"nature": "Docile"}))
        self.assertTrue(_sp_blind(row, {"subskill": [0, "Helping Bonus"]}))
        self.assertTrue(_sp_blind(row, {"subskill": [4, "Helping Speed M"]}))
        self.assertFalse(_sp_blind(row, {"subskill": [0, "Helping Speed M"]}))

    def test_sp_blind_subskills_tie_and_are_not_repaired(self):
        item = scanned("a")
        subskills = [list(x) for x in SUBSKILLS]
        subskills[1][0] = "Energy Recovery Bonus"
        self.repair(item, key_of(item, subskills=subskills))
        row = self.row("capture-a")
        self.assertEqual((json.loads(row["subskills_json"])[1][0], row["repaired"]),
                         ("Berry Finding S", 0))

    def test_candidate_counts_stay_under_the_cap(self):
        row = {"level": 85, "nature": "Brave", "skill_level": 3,
               "subskills_json": json.dumps(SUBSKILLS)}
        first, second = build_candidates(row, 1), build_candidates(row, 2)
        self.assertEqual(sum(1 for c in first if len(c) != 1), 0)
        self.assertTrue(all("level" in c and len(c) == 2 for c in second))
        self.assertLessEqual(len(first), 500)
        self.assertLessEqual(len(second), 500)

    def test_human_rows_are_untouched(self):
        import_individuals(self.db, [scanned("a", review_confirmed=True)])
        item = scanned("a")
        self.repair(item, key_of(item, nature="Adamant"))
        row = self.row("capture-a")
        self.assertEqual((row["nature"], row["repaired"], row["review_confirmed"]),
                         ("Brave", 0, 1))


class FakeReader:
    model_name = FALLBACK_VLM_MODEL


class AgreementTests(Base):
    def run_agreement(self, item, second_read, images=True):
        import_individuals(self.db, [item])
        image = self.root / "a.png"
        image.write_bytes(b"fictional")
        reads = []

        def fake_scan(path, reader, frames, *args, **kwargs):
            reads.append(reader.model_name)
            return [dict(scanned("x"), **second_read)]
        with patch("pokesleep_box.engine.run_engine", side_effect=build_engine({})), \
                patch("pokesleep_box.vlm.scan", side_effect=fake_scan):
            check_items(self.db, [item], "engine")
            stats = confirm_partials(self.db, [item], "engine",
                                     {"capture-a": image} if images else {},
                                     lambda: FakeReader(), self.root / "frames",
                                     FALLBACK_VLM_MODEL)
        return stats, reads

    def test_agreeing_fallback_read_auto_verifies(self):
        item = scanned("a", nature="Hardy", ocr_missing=["nature"], read_model=FAST)
        stats, reads = self.run_agreement(item, {"nature": "Hardy"})
        self.assertEqual(reads, [FALLBACK_VLM_MODEL])
        row = self.row("capture-a")
        self.assertEqual((row["verified"], row["auto_verified"]), (1, 1))
        self.assertEqual((item["sp_check"], item["model_agreed"]), ("match", ["nature"]))
        self.assertEqual(stats["confirmed"], 1)

    def test_disagreeing_fallback_read_stays_partial_with_note(self):
        item = scanned("a", nature="Hardy", ocr_missing=["nature"], read_model=FAST)
        stats, _ = self.run_agreement(item, {"nature": "Docile"})
        row = self.row("capture-a")
        self.assertEqual((row["verified"], item["sp_check"]), (0, "partial"))
        self.assertEqual(row["repair_note"],
                         f"性格: 4B {to_japanese('natures', 'Hardy')} / "
                         f"9B {to_japanese('natures', 'Docile')}")
        self.assertEqual(stats["disagreed"], 1)

    def test_ingredient_disagreement_between_models_stays_partial(self):
        # Lv26: the Lv30/60 slots are locked. The fast read guessed Egg x3 at
        # Lv60 (amount unread); the fallback reads 4, which is Apple.
        item = scanned("a", ingredient_amounts=[1, 2, None], read_model=FAST)
        self.run_agreement(item, {"ingredient_amounts": [1, 2, 4],
                                  "ingredients": [["Apple", 1], ["Ginger", 2], ["Apple", 4]]})
        row = self.row("capture-a")
        self.assertEqual((row["verified"], item["review_fields"]), (0, ["ingredients"]))
        self.assertIn("食材: 4B", row["repair_note"])

    def test_no_self_agreement_with_the_fallback_model(self):
        item = scanned("a", nature="Hardy", ocr_missing=["nature"], read_model=FALLBACK_VLM_MODEL)
        stats, reads = self.run_agreement(item, {"nature": "Hardy"})
        self.assertEqual((reads, stats["checked"], self.row("capture-a")["verified"]), ([], 0, 0))

    def test_human_rows_are_not_reread(self):
        import_individuals(self.db, [scanned("a", review_confirmed=True)])
        item = scanned("a", nature="Hardy", ocr_missing=["nature"], read_model=FAST)
        _, reads = self.run_agreement(item, {"nature": "Docile"})
        self.assertEqual((reads, self.row("capture-a")["repair_note"]), ([], None))


ICON_DATA = {"PICHU": {"berry": "GREPA", "main_skill": "Charge Strength S", "ingredients": [
    {"level": 1, "choices": [["Apple", 1]]},
    {"level": 30, "choices": [["Ginger", 2], ["Egg", 3]]},
    {"level": 60, "choices": [["Cacao", 4], ["Milk", 6], ["Egg", 5]]}]}}


def icon_read(amounts, matcher):
    item = {"species": "PICHU", "level": 26, "nature": "Brave", "sp": 400,
            "subskills": [list(x) for x in SUBSKILLS], "main_skill": "Charge Strength S",
            "skill_level": 2, "ocr_missing": ["ingredients"], "ingredient_amounts": amounts}
    return enrich_with_species_data([item], pokemon_data=ICON_DATA, icon_matcher=matcher)[0]


class IngredientIconTests(Base):
    def test_unique_amount_contradicting_icon_is_not_clean(self):
        calls = []

        def matcher(item, options):
            calls.append(1)
            return [None, None, "Cacao"]
        item = icon_read([1, 2, 6], matcher)
        self.assertEqual(calls, [1])  # consulted although the amounts were unique
        self.assertIn("ingredients", item["ocr_missing"])
        self.assertEqual(item["ingredient_conflicts"],
                         ["Lv60食材: 個数→モーモーミルク×6 / アイコン→リラックスカカオ"])
        item.update(uid="capture-a", confidence=.99, verified=False)
        import_individuals(self.db, [item])
        with patch("pokesleep_box.engine.run_engine", side_effect=build_engine({})):
            check_items(self.db, [item], "engine")
        row = self.row("capture-a")
        self.assertEqual((item["sp_check"], item["review_fields"], row["verified"]),
                         ("partial", ["ingredients"], 0))
        self.assertIn("リラックスカカオ", row["sp_flag"])

    def test_agreeing_amount_and_icon_is_clean(self):
        item = icon_read([1, 2, 6], lambda item, options: [None, "Ginger", "Milk"])
        self.assertNotIn("ingredients", item["ocr_missing"])
        self.assertEqual(item["ingredients"][2], ["Milk", 6])
        self.assertNotIn("ingredient_conflicts", item)

    def test_only_one_source_decides_and_is_used(self):
        # Icon only: the amount is unread or fits no legal option.
        for amounts in ([1, 2, None], [1, 2, 7]):
            item = icon_read(amounts, lambda item, options: [None, "Ginger", "Cacao"])
            self.assertNotIn("ingredients", item["ocr_missing"])
            self.assertEqual(item["ingredients"][2], ["Cacao", 4])
            self.assertNotIn("ingredient_conflicts", item)
        # Amount only: the icon match was not confident.
        item = icon_read([1, 2, 5], lambda item, options: [None, None, None])
        self.assertEqual(item["ingredients"][2], ["Egg", 5])
        self.assertNotIn("ingredients", item["ocr_missing"])

    def test_icon_matching_unavailable_behaves_as_before(self):
        for matcher in (None, lambda item, options: []):
            item = icon_read([1, 2, 6], matcher)
            self.assertNotIn("ingredients", item["ocr_missing"])
            self.assertEqual(item["ingredients"][2], ["Milk", 6])


class IconDowngradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.paths = replace(
            DEFAULT_PATHS, **{f.name: root / getattr(DEFAULT_PATHS, f.name)
                              for f in fields(PrivatePaths) if f.name != "engine"})
        self.paths.inbox.mkdir(parents=True)
        self.db = connect(self.paths.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def store(self, name, content, **changes):
        path = self.paths.inbox / name
        path.write_bytes(content)
        digest = file_sha256(path)
        item = scanned(name, uid=f"capture-{digest[:20]}",
                       ingredients=[["Apple", 1], ["Ginger", 2], ["Milk", 6]], **changes)
        import_individuals(self.db, [item])
        self.db.execute("UPDATE individual SET verified=1,auto_verified=1 WHERE uid=?", (item["uid"],))
        record_synced_sources(self.db, [(digest, name, f"qwen:{FAST}", 1)])
        self.db.commit()
        return item["uid"]

    def test_auto_verified_row_contradicting_its_icon_is_unverified(self):
        wrong = self.store("one.png", b"one")
        human = self.store("two.png", b"two", nature="Mild", review_confirmed=True)
        right = self.store("three.png", b"three", nature="Bold")
        old = self.store("four.png", b"four", nature="Lonely", level=70)

        def identify(frames, options):
            return [None, None, "Milk" if frames[0].name == "three.png" else "Cacao"]
        with patch("pokesleep_box.ocr.species_metadata", return_value=ICON_DATA):
            stats = icon_downgrades(self.db, self.paths.inbox, "engine", identify=identify)
        self.assertEqual((stats["checked"], stats["downgraded"]), (2, 1))
        rows = {r["uid"]: r for r in self.db.execute("SELECT * FROM individual")}
        self.assertEqual((rows[wrong]["verified"], rows[wrong]["auto_verified"]), (0, 0))
        self.assertEqual(rows[wrong]["repair_note"],
                         "Lv60食材: 登録値→モーモーミルク×6 / アイコン→リラックスカカオ")
        self.assertEqual((rows[human]["verified"], rows[human]["review_confirmed"]), (1, 1))
        self.assertEqual(rows[right]["verified"], 1)
        self.assertEqual(rows[old]["verified"], 1)  # Lv70: no locked slot

    def test_sync_recheck_lists_the_downgraded_row_for_review(self):
        uid = self.store("one.png", b"one")
        self.db.close()
        with patch("pokesleep_box.ingest.ingest_path", return_value=[]), \
                patch("pokesleep_box.engine.run_engine", side_effect=build_engine({})), \
                patch("pokesleep_box.engine.evaluate"), patch("pokesleep_box.sync.decide"), \
                patch("pokesleep_box.sync.render_private",
                      side_effect=lambda db, out, *a, **k: out / "index.html"), \
                patch("pokesleep_box.icons.available", return_value=True), \
                patch("pokesleep_box.icons.identify", return_value=[None, None, "Cacao"]), \
                patch("pokesleep_box.ocr.species_metadata", return_value=ICON_DATA):
            summary = run_sync(self.paths.inbox, self.paths, {}, ocr="vision", recheck=True,
                               fallback_vlm_model=None, on_progress=lambda message: None)
        self.db = connect(self.paths.db)
        row = self.db.execute("SELECT * FROM individual WHERE uid=?", (uid,)).fetchone()
        self.assertEqual((row["verified"], row["auto_verified"]), (0, 0))
        self.assertEqual((summary["icon_downgraded"], summary["rechecked"]), (1, 1))
        self.assertIn("リラックスカカオ", row["sp_flag"])
        page = self.paths.review.read_text(encoding="utf-8")
        self.assertIn("../inbox/one.png", page)


class ReviewPageTests(unittest.TestCase):
    def test_order_and_image_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inbox").mkdir()
            (root / "inbox" / "a b.png").write_bytes(b"fictional")
            good = dict(scanned("good"), stored_uid="good", sp_check="match", verified=True,
                        auto_verified=True, review_fields=[])
            two = dict(scanned("two"), stored_uid="two", sp_check="partial",
                       review_fields=["nature", "ingredients"])
            one = dict(scanned("one"), stored_uid="one", sp_check="partial",
                       review_fields=["nature"])
            bad = dict(scanned("bad"), stored_uid="bad", sp_check="mismatch", sp_flag="SP不一致")
            ordered = review_order([good, two, one, bad])
            self.assertEqual([x["stored_uid"] for x in ordered], ["bad", "one", "two", "good"])
            page = root / "frames" / "review.html"
            render_review([good, two, one, bad], page,
                          images={"one": root / "inbox" / "a b.png",
                                  "two": root / "inbox" / "missing.png"})
            text = page.read_text(encoding="utf-8")
            self.assertIn('"capture_image": "../inbox/a%20b.png"', text)
            self.assertEqual(text.count('"capture_image"'), 1)  # missing file skipped
            self.assertLess(text.index('"uid": "capture-bad"'), text.index('"uid": "capture-one"'))
            self.assertLess(text.index('"uid": "capture-one"'), text.index('"uid": "capture-two"'))
            self.assertIn("まとめて照合済み", text)
            self.assertNotIn("base64", text)


if __name__ == "__main__":
    unittest.main()
