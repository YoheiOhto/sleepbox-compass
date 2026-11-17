import json
import sys
import tempfile
import unittest
from dataclasses import fields, replace
from pathlib import Path
from unittest.mock import patch

from pokesleep_box import cli
from pokesleep_box.core import connect, import_individuals, record_synced_sources
from pokesleep_box.ingest import file_sha256
from pokesleep_box.paths import DEFAULT_PATHS, PrivatePaths
from pokesleep_box.repair import flagged_items, repair_mismatches
from pokesleep_box.spcheck import check_items
from pokesleep_box.sync import FALLBACK_VLM_MODEL, run_sync

# Fictional sample individual (see tests/test_sync.py).
SUBSKILLS = [["Helping Speed S", 10], ["Berry Finding S", 25], ["Skill Trigger S", 50],
             ["Inventory Up S", 70], ["Dream Shard Bonus", 80]]
OPTIONS = [{"level": 1, "choices": [["Apple", 1, "a"]]},
           {"level": 30, "choices": [["Ginger", 2, "g"], ["Egg", 3, "e"]]},
           {"level": 60, "choices": [["Egg", 3, "e"], ["Apple", 4, "a"]]}]


def scanned(name, **changes):
    item = {"uid": f"capture-{name}", "species": "PICHU", "level": 16, "sp": 400,
            "nature": "Brave", "ingredients": [["Apple", 1], ["Ginger", 2], ["Egg", 3]],
            "subskills": [list(x) for x in SUBSKILLS], "main_skill": "Charge Strength S",
            "skill_level": 1, "confidence": .99, "verified": False, "ocr_missing": [],
            "ingredient_options": OPTIONS, "ingredient_amounts": [1, 2, 3]}
    item.update(changes)
    return item


def level_engine(truth, calls=None):
    """run_engine stand-in: the shown SP is reproduced only at the levels in
    ``truth[uid]``; uids not in ``truth`` match at any level."""
    def run(payload, command=None, on_progress=None):
        assert payload["mode"] == "verify"
        if calls is not None:
            calls.append([entry["uid"] for entry in payload["instances"]])
        results = []
        for entry in payload["instances"]:
            uid = entry["uid"].split("|")[0]
            level = entry["instance"]["level"]
            levels = truth.get(uid)
            sp = entry["displayedSp"] if levels is None or level in levels else \
                entry["displayedSp"] + 7 * (level - min(levels))
            diff = sp - entry["displayedSp"]
            strict = level < payload["strictBelowLevel"]
            match = diff == 0 if strict else abs(diff) <= payload["tolerance"]
            results.append({"uid": entry["uid"], "computedSp": sp, "diff": diff, "match": match,
                            "mode": "strict" if strict else ("tolerant" if match else "failed")})
        return {"results": results}
    return run


class FakeFallback:
    model_name = FALLBACK_VLM_MODEL


class LevelRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = connect(self.root / "box.sqlite")
        self.calls = []

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def run_repair(self, items, truth, images=None, fallback=None):
        import_individuals(self.db, items)
        with patch("pokesleep_box.engine.run_engine", side_effect=level_engine(truth, self.calls)):
            check_items(self.db, items, "engine")
            return repair_mismatches(self.db, items, "engine", images or {}, fallback,
                                     self.root / "frames")

    def row(self, uid):
        return self.db.execute("SELECT * FROM individual WHERE uid=?", (uid,)).fetchone()

    def test_unique_level_shift_is_repaired_and_verified(self):
        item = scanned("a")
        stats = self.run_repair([item], {"capture-a": {14}})
        row = self.row("capture-a")
        self.assertEqual((row["level"], row["repaired"], row["verified"], row["auto_verified"]),
                         (14, 1, 1, 1))
        self.assertIsNone(row["sp_flag"])
        self.assertEqual(row["repair_note"], "Lv 16→14（SPで補正）")
        self.assertEqual((item["level"], item["sp_check"], item["repair_note"]),
                         (14, "match", "Lv 16→14（SPで補正）"))
        self.assertEqual((stats["level_repaired"], stats["still_mismatch"]), (1, 0))
        # One batched engine call for the shifts, excluding the read level.
        self.assertEqual(sorted(self.calls[1]), sorted(
            ["capture-a|-2", "capture-a|-1", "capture-a|1", "capture-a|2"]))

    def test_ambiguous_or_no_shift_is_not_repaired(self):
        ambiguous, none = scanned("a"), scanned("b", nature="Mild")
        stats = self.run_repair([ambiguous, none], {"capture-a": {14, 18}, "capture-b": {30}})
        for uid in ("capture-a", "capture-b"):
            row = self.row(uid)
            self.assertEqual((row["level"], row["repaired"], row["verified"]), (16, 0, 0))
            self.assertTrue(row["sp_flag"].startswith("SP不一致"))
        self.assertEqual((stats["level_repaired"], stats["still_mismatch"]), (0, 2))

    def test_human_confirmed_rows_are_untouched(self):
        import_individuals(self.db, [scanned("a", review_confirmed=True)])
        item = scanned("a")
        stats = self.run_repair([item], {"capture-a": {14}})
        row = self.row("capture-a")
        self.assertEqual((row["level"], row["repaired"], row["review_confirmed"]), (16, 0, 1))
        self.assertEqual(len(self.calls), 0)  # nothing computable, no level-repair call
        self.assertEqual(stats["level_repaired"], 0)

    def test_level_56_and_above_is_repaired_and_auto_verified(self):
        item = scanned("a", level=60)
        self.run_repair([item], {"capture-a": {58}})
        row = self.row("capture-a")
        self.assertEqual((row["level"], row["repaired"], row["verified"]), (58, 1, 1))
        self.assertEqual(item["sp_check"], "match")

    def test_escalation_rereads_remaining_still_images_with_fallback(self):
        still, video = scanned("a"), scanned("b", nature="Mild")
        image = self.root / "a.png"
        image.write_bytes(b"fictional")
        made, read = [], []

        def fallback():
            made.append(1)
            return FakeFallback()

        def fake_scan(path, reader, frames, *args, **kwargs):
            read.append((path, reader.model_name))
            return [scanned("x", uid=None, level=14, ocr_missing=["nature"], nature="Hardy")]

        with patch("pokesleep_box.vlm.scan", side_effect=fake_scan):
            stats = self.run_repair([still, video], {"capture-a": {14, 18}, "capture-b": {30}},
                                    images={"capture-a": image}, fallback=fallback)
        self.assertEqual(made, [1])
        self.assertEqual(read, [(image, FALLBACK_VLM_MODEL)])
        row = self.row("capture-a")
        # Same row; the unread nature keeps the stored value.
        self.assertEqual((row["level"], row["nature"], row["verified"]), (14, "Brave", 1))
        self.assertEqual(row["repair_note"], "Qwen3.5-9B-8bitで読み直し（Lv 16→14）")
        self.assertEqual(self.db.execute("SELECT count(*) FROM individual").fetchone()[0], 2)
        self.assertEqual((stats["escalated"], stats["escalated_fixed"], stats["still_mismatch"]),
                         (1, 1, 1))

    def test_no_fallback_means_no_escalation(self):
        image = self.root / "a.png"
        image.write_bytes(b"fictional")
        with patch("pokesleep_box.vlm.scan", side_effect=AssertionError):
            stats = self.run_repair([scanned("a")], {"capture-a": {30}},
                                    images={"capture-a": image}, fallback=None)
        self.assertEqual((stats["escalated"], stats["still_mismatch"]), (0, 1))


class RecheckTests(unittest.TestCase):
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

    def store(self, name, content, reader="qwen:mlx-community/Qwen3.5-4B-4bit", **changes):
        path = self.paths.inbox / name
        path.write_bytes(content)
        digest = file_sha256(path)
        item = scanned(name, uid=f"capture-{digest[:20]}", **changes)
        import_individuals(self.db, [item])
        self.db.execute("UPDATE individual SET sp_flag='SP不一致（画面 400 / 計算 414）' WHERE uid=?",
                        (item["uid"],))
        record_synced_sources(self.db, [(digest, name, reader, 1)])
        return item["uid"], path

    def test_flagged_rows_are_mapped_to_images_via_scan_source(self):
        qwen_uid, qwen_path = self.store("one.png", b"one")
        vision_uid, _ = self.store("two.png", b"two", reader="vision", nature="Mild")
        moved_uid, moved = self.store("three.png", b"three", nature="Lonely")
        moved.rename(self.paths.inbox / "renamed.png")
        lost_uid, lost = self.store("four.png", b"four", nature="Bold")
        lost.unlink()
        found = flagged_items(self.db, self.paths.inbox, self.paths.vlm_cache,
                              FALLBACK_VLM_MODEL, self.paths.frames)
        self.assertEqual(len(found["items"]), 4)
        self.assertEqual(found["images"], {qwen_uid: qwen_path,
                                           moved_uid: self.paths.inbox / "renamed.png"})
        # Without the scan-time read, unread fields are assumed unknown.
        self.assertIn("main_skill", found["items"][0]["ocr_missing"])
        # Already read by the fallback model: no re-read.
        self.assertEqual(flagged_items(self.db, self.paths.inbox, None,
                                       "mlx-community/Qwen3.5-4B-4bit",
                                       self.paths.frames)["images"], {})

    def test_every_unverified_row_is_rechecked_but_verified_rows_are_not(self):
        flagged_uid, _ = self.store("one.png", b"one")
        unchecked_uid, _ = self.store("two.png", b"two", nature="Mild")
        checked_uid, _ = self.store("three.png", b"three", nature="Lonely")
        verified_uid, _ = self.store("four.png", b"four", nature="Bold")
        self.db.execute("UPDATE individual SET sp_flag=NULL, sp_computed=NULL WHERE uid=?",
                        (unchecked_uid,))
        self.db.execute("UPDATE individual SET sp_flag=NULL, sp_computed=500 WHERE uid=?",
                        (checked_uid,))
        self.db.execute("UPDATE individual SET sp_flag=NULL, sp_computed=NULL, verified=1 WHERE uid=?",
                        (verified_uid,))
        found = flagged_items(self.db, self.paths.inbox, self.paths.vlm_cache,
                              FALLBACK_VLM_MODEL, self.paths.frames)
        self.assertEqual(sorted(item["stored_uid"] for item in found["items"]),
                         sorted([flagged_uid, unchecked_uid, checked_uid]))

    def test_sync_recheck_repairs_stored_rows_then_evaluates(self):
        uid, path = self.store("one.png", b"one")
        calls, rereads = [], []
        self.db.close()
        with patch("pokesleep_box.ingest.ingest_path", return_value=[]), \
                patch("pokesleep_box.engine.run_engine", side_effect=level_engine({uid: {14}})), \
                patch("pokesleep_box.engine.evaluate", side_effect=lambda *a, **k: calls.append("evaluate")), \
                patch("pokesleep_box.sync.decide", side_effect=lambda *a, **kw: calls.append("decide")), \
                patch("pokesleep_box.sync.render_private",
                      side_effect=lambda db, out, *a, **k: out / "index.html"), \
                patch("pokesleep_box.repair.reread", side_effect=lambda *a: rereads.append(a)):
            summary = run_sync(self.paths.inbox, self.paths, {}, ocr="vision", recheck=True,
                               on_progress=lambda message: None)
        self.db = connect(self.paths.db)
        row = self.db.execute("SELECT * FROM individual WHERE uid=?", (uid,)).fetchone()
        self.assertEqual((row["level"], row["repaired"]), (14, 1))
        self.assertEqual(calls, ["evaluate", "decide"])
        self.assertEqual(rereads, [])  # fixed by level repair, no re-read needed
        self.assertEqual(summary["rechecked"], 1)
        self.assertEqual(summary["repair"]["level_repaired"], 1)
        text = cli.format_summary(summary, self.paths.review)
        self.assertIn("SP不一致の補正: レベル補正 1匹 / 読み直しで解消 0匹（読み直し 0匹） / 不一致のまま 0匹",
                      text)
        page = self.paths.review.read_text(encoding="utf-8")
        self.assertIn("Lv 16→14（SPで補正）", page)

    def test_sync_recheck_escalates_with_fallback_model_once(self):
        first, _ = self.store("one.png", b"one")
        second, _ = self.store("two.png", b"two", nature="Mild")
        readers, rereads = [], []

        class FakeReader:
            def __init__(self, model, cache_dir=None):
                self.model_name = model
                readers.append(model)

            def _cache_path(self, digest):
                return None

        def fake_reread(db, item, image, reader, frames):
            rereads.append((item["stored_uid"], reader.model_name))
            return None
        self.db.close()
        for fallback in (FALLBACK_VLM_MODEL, None):
            readers.clear()
            rereads.clear()
            with patch("pokesleep_box.ingest.ingest_path", return_value=[]), \
                    patch("pokesleep_box.engine.run_engine",
                          side_effect=level_engine({first: {40}, second: {40}})), \
                    patch("pokesleep_box.engine.evaluate"), patch("pokesleep_box.sync.decide"), \
                    patch("pokesleep_box.sync.render_private",
                          side_effect=lambda db, out, *a, **k: out / "index.html"), \
                    patch("pokesleep_box.vlm.QwenReader", FakeReader), \
                    patch("pokesleep_box.repair.reread", side_effect=fake_reread):
                run_sync(self.paths.inbox, self.paths, {}, ocr="vision", recheck=True,
                         fallback_vlm_model=fallback, on_progress=lambda message: None)
            if fallback:
                self.assertEqual(readers.count(FALLBACK_VLM_MODEL), 1)  # loaded once
                self.assertEqual(sorted(rereads), sorted([(first, FALLBACK_VLM_MODEL),
                                                          (second, FALLBACK_VLM_MODEL)]))
            else:
                self.assertNotIn(FALLBACK_VLM_MODEL, readers)
                self.assertEqual(rereads, [])
        self.db = connect(self.paths.db)

    def test_cli_fallback_none_disables_escalation(self):
        seen = {}

        def fake_run_sync(*args, **kwargs):
            seen.update(kwargs)
            return {"messages": [], "new_sources": 0, "new_individuals": 0,
                    "updated_individuals": 0, "site": "x"}
        settings = Path(self.tmp.name) / "settings.json"
        settings.write_text(json.dumps({"paths": {"site": str(self.paths.site)}}), encoding="utf-8")
        for value, expected in (("none", None), ("other/model", "other/model")):
            argv = ["pokesleep-box", "sync", "--recheck", "--settings", str(settings),
                    "--fallback-vlm-model", value]
            with patch.object(sys, "argv", argv), \
                    patch("pokesleep_box.cli.run_sync", side_effect=fake_run_sync), \
                    patch("builtins.print"):
                cli.main()
            self.assertEqual((seen["fallback_vlm_model"], seen["recheck"]), (expected, True))


if __name__ == "__main__":
    unittest.main()
