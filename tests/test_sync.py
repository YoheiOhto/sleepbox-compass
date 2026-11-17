import fnmatch
import json
import sys
import tempfile
import unittest
from dataclasses import fields, replace
from pathlib import Path
from unittest.mock import patch

from pokesleep_box import cli
from pokesleep_box.core import connect, import_individuals
from pokesleep_box.engine import EngineUnavailable
from pokesleep_box.ingest import audit
from pokesleep_box.paths import DEFAULT_PATHS, PrivatePaths, private_paths
from pokesleep_box.spcheck import check_items, review_order
from pokesleep_box.sync import import_checked, run_sync

ROOT = Path(__file__).parents[1]
# Fictional sample individual (Lv10: subskills from Lv25 and the Lv30/60
# ingredient slots are still locked).
SUBSKILLS = [["Helping Speed S", 10], ["Berry Finding S", 25], ["Skill Trigger S", 50],
             ["Inventory Up S", 70], ["Dream Shard Bonus", 80]]
OPTIONS = [{"level": 1, "choices": [["Apple", 1, "a"]]},
           {"level": 30, "choices": [["Ginger", 2, "g"], ["Egg", 3, "e"]]},
           {"level": 60, "choices": [["Egg", 3, "e"], ["Apple", 4, "a"]]}]


def scanned(name, **changes):
    item = {"uid": f"capture-{name}", "species": "PICHU", "level": 10, "sp": 400,
            "nature": "Brave", "ingredients": [["Apple", 1], ["Ginger", 2], ["Egg", 3]],
            "subskills": [list(x) for x in SUBSKILLS], "main_skill": "Charge Strength S",
            "skill_level": 1, "confidence": .99, "verified": False, "ocr_missing": [],
            "ingredient_options": OPTIONS, "ingredient_amounts": [1, 2, 3]}
    item.update(changes)
    return item


def fake_engine(computed):
    """run_engine stand-in: computed SP per uid (default: the shown SP)."""
    def run(payload, command=None, on_progress=None):
        assert payload["mode"] == "verify"
        results = []
        for entry in payload["instances"]:
            sp = computed.get(entry["uid"], entry["displayedSp"])
            diff = sp - entry["displayedSp"]
            strict = entry["instance"]["level"] < payload["strictBelowLevel"]
            match = diff == 0 if strict else abs(diff) <= payload["tolerance"]
            results.append({"uid": entry["uid"], "computedSp": sp, "diff": diff, "match": match,
                            "mode": "strict" if strict else ("tolerant" if match else "failed")})
        return {"results": results}
    return run


class SpCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = connect(Path(self.tmp.name) / "box.sqlite")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def check(self, items, computed=None):
        import_individuals(self.db, items)
        with patch("pokesleep_box.engine.run_engine", side_effect=fake_engine(computed or {})):
            return check_items(self.db, items, "engine")

    def row(self, uid):
        return self.db.execute("SELECT * FROM individual WHERE uid=?", (uid,)).fetchone()

    def test_strict_match_with_clean_locked_parts_is_auto_verified(self):
        item = scanned("a")
        counts = self.check([item])
        self.assertEqual(counts["match"], 1)
        row = self.row("capture-a")
        self.assertEqual((row["verified"], row["auto_verified"], row["review_confirmed"]), (1, 1, 0))
        self.assertEqual((row["verify_mode"], row["sp_computed"], row["sp_diff"]), ("strict", 400, 0))
        self.assertTrue(item["auto_verified"])
        self.assertEqual(item["review_fields"], [])

    def test_locked_subskill_not_read_keeps_only_that_field(self):
        # "ingredients" is in ocr_missing, but the locked Lv30/60 slots were
        # resolved from their amounts, so only the subskills remain.
        item = scanned("a", ocr_missing=["subskills", "ingredients"], ingredient_amounts=[None, 2, 3])
        counts = self.check([item])
        self.assertEqual(counts["partial"], 1)
        self.assertEqual(item["review_fields"], ["subskills"])
        self.assertEqual(self.row("capture-a")["verified"], 0)
        self.assertFalse(item.get("verified"))

    def test_unlocked_misread_subskills_are_covered_by_the_sp(self):
        item = scanned("a", level=85, ocr_missing=["subskills"],
                       subskills=[["Helping Speed S", 10], ["Berry Finding S", 25],
                                  ["Skill Trigger S", 50], ["Inventory Up S", 70],
                                  ["Ingredient Finder S", 80]])
        with patch("pokesleep_box.engine.run_engine",
                   side_effect=lambda payload, *a, **k: fake_engine({})(
                       dict(payload, strictBelowLevel=100))):
            import_individuals(self.db, [item])
            check_items(self.db, [item], "engine")
        self.assertTrue(item.get("auto_verified"))

    def test_provisional_locked_ingredient_needs_review(self):
        # Lv30 slot amount 2 fits Ginger only; Lv60 amount unread -> provisional.
        item = scanned("a", ingredient_amounts=[1, 2, None])
        self.check([item])
        self.assertEqual(item["review_fields"], ["ingredients"])
        self.assertEqual(self.row("capture-a")["verified"], 0)

    def test_mismatch_is_flagged_and_sorted_first(self):
        good, bad, unclear = (scanned("a", box_index=1), scanned("b", box_index=2, nature="Mild"),
                              scanned("c", box_index=3, nature="Lonely", ocr_missing=["subskills"]))
        counts = self.check([good, bad, unclear], {"capture-b": 412})
        self.assertEqual((counts["match"], counts["mismatch"], counts["partial"]), (1, 1, 1))
        self.assertEqual(bad["sp_flag"], "SP不一致（画面 400 / 計算 412）")
        row = self.row("capture-b")
        self.assertEqual((row["verified"], row["sp_flag"], row["sp_diff"]), (0, bad["sp_flag"], 12))
        ordered = review_order([good, unclear, bad])
        self.assertEqual([x["uid"] for x in ordered], ["capture-b", "capture-c", "capture-a"])
        report = Path(self.tmp.name) / "audit.md"
        result = audit(ordered, report)
        self.assertEqual((result["sp_mismatch"], result["auto_verified"]), (1, 1))
        listed = [line for line in report.read_text(encoding="utf-8").splitlines()
                  if line.startswith("- BOX")]
        self.assertEqual(len(listed), 2)
        self.assertIn("SP不一致（画面 400 / 計算 412）", listed[0])
        self.assertIn("不足: subskills", listed[1])

    def test_level_56_and_above_is_not_auto_verified(self):
        item = scanned("a", level=60)
        counts = self.check([item])
        self.assertEqual(counts["unchecked"], 1)
        self.assertEqual(self.row("capture-a")["verified"], 0)

    def test_human_confirmed_rows_are_untouched(self):
        import_individuals(self.db, [scanned("a", review_confirmed=True)])
        item = scanned("a", review_confirmed=False)
        counts = self.check([item], {"capture-a": 999})
        self.assertEqual(counts["human"], 1)
        row = self.row("capture-a")
        self.assertEqual((row["verified"], row["review_confirmed"], row["auto_verified"]), (1, 1, 0))
        self.assertIsNone(row["sp_flag"])

    def test_mismatch_never_makes_a_send_candidate(self):
        from pokesleep_box.core import decide
        self.check([scanned("a")], {"capture-a": 401})
        decide(self.db)
        verdict = self.db.execute("SELECT verdict FROM decision WHERE uid='capture-a'").fetchone()
        self.assertEqual(verdict["verdict"], "protected")


class ReviewedSpCheckTests(unittest.TestCase):
    """Rows edited on the review page but not ticked 「照合済み」."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = connect(Path(self.tmp.name) / "box.sqlite")
        # The scan: a neutral nature that the SP cannot confirm stays partial.
        self.scan = scanned("a", nature="Hardy", ocr_missing=["nature"])
        import_individuals(self.db, [self.scan])

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def row(self, uid="capture-a"):
        return self.db.execute("SELECT * FROM individual WHERE uid=?", (uid,)).fetchone()

    def reviewed(self, **changes):
        # As saved by the review page: scan-time ocr_missing kept, unticked.
        item = json.loads(json.dumps(self.scan))
        item.update(dict({"review_confirmed": False}, **changes))
        return item

    def import_checked(self, items, computed=None):
        with patch("pokesleep_box.engine.run_engine",
                   side_effect=fake_engine(computed or {})) as engine:
            result = import_checked(self.db, items, "engine")
        self.engine = engine
        return result

    def test_edited_row_with_sp_match_is_auto_verified(self):
        item = self.reviewed(nature="Docile", human_edited=["nature"])
        result = self.import_checked([item])
        self.assertEqual((result["imported"], result["sp"]["match"]), (1, 1))
        row = self.row()
        self.assertEqual((row["verified"], row["auto_verified"], row["review_confirmed"]), (1, 1, 0))
        self.assertEqual(row["nature"], "Docile")
        self.assertIsNone(row["sp_flag"])

    def test_untouched_stale_field_still_needs_review(self):
        # Saving the page without changing the nature does not confirm it.
        item = self.reviewed(human_edited=[])
        result = self.import_checked([item])
        self.assertEqual(result["sp"]["partial"], 1)
        self.assertEqual(item["review_fields"], ["nature"])
        self.assertEqual(self.row()["verified"], 0)

    def test_edited_locked_ingredients_count_as_resolved(self):
        self.scan = scanned("b", ocr_missing=["ingredients"], ingredient_amounts=[1, None, None])
        import_individuals(self.db, [self.scan])
        item = self.reviewed(ingredients=[["Apple", 1], ["Egg", 3], ["Apple", 4]],
                             human_edited=["ingredients"])
        self.import_checked([item])
        self.assertEqual(self.row("capture-b")["auto_verified"], 1)

    def test_edited_row_with_sp_mismatch_is_flagged(self):
        item = self.reviewed(nature="Docile", sp=420, human_edited=["nature", "sp"])
        result = self.import_checked([item], {"capture-a": 400})
        self.assertEqual(result["sp"]["mismatch"], 1)
        row = self.row()
        self.assertEqual((row["verified"], row["auto_verified"]), (0, 0))
        self.assertEqual(row["sp_flag"], "SP不一致（画面 420 / 計算 400）")

    def test_ticked_rows_are_untouched(self):
        item = self.reviewed(review_confirmed=True)
        result = self.import_checked([item], {"capture-a": 999})
        self.assertEqual(result["sp"]["human"], 1)
        self.engine.assert_not_called()
        row = self.row()
        self.assertEqual((row["verified"], row["review_confirmed"], row["auto_verified"]), (1, 1, 0))
        self.assertIsNone(row["sp_flag"])
        # A row confirmed in the DB stays untouched by a later unticked JSON.
        self.import_checked([self.reviewed(nature="Docile", human_edited=["nature"])],
                            {"capture-a": 999})
        row = self.row()
        self.assertEqual((row["verified"], row["review_confirmed"], row["auto_verified"]), (1, 1, 0))
        self.assertIsNone(row["sp_flag"])

    def test_import_json_without_engine_imports_unchanged_with_note(self):
        root = Path(self.tmp.name)
        path = root / "reviewed_individuals.json"
        path.write_text(json.dumps({"individuals": [
            self.reviewed(uid="capture-z", nature="Docile", human_edited=["nature"])]}),
            encoding="utf-8")
        argv = ["pokesleep-box", "--db", str(root / "cli.sqlite"), "import-json", str(path),
                "--engine", str(root / "missing-engine")]
        with patch.object(sys, "argv", argv), patch("builtins.print") as printed:
            cli.main()
        outputs = [call.args[0] for call in printed.call_args_list]
        self.assertEqual(json.loads(outputs[-1]), {"imported": 1})
        self.assertTrue(any("SP確認を省略" in str(text) for text in outputs[:-1]))
        db = connect(root / "cli.sqlite")
        try:
            row = db.execute("SELECT * FROM individual WHERE uid='capture-z'").fetchone()
            self.assertEqual((row["verified"], row["auto_verified"], row["review_confirmed"]),
                             (0, 0, 0))
            self.assertIsNone(row["verify_mode"])
            self.assertIsNone(row["sp_flag"])
        finally:
            db.close()

    def test_import_json_with_engine_auto_verifies(self):
        root = Path(self.tmp.name)
        path = root / "reviewed_individuals.json"
        path.write_text(json.dumps({"individuals": [
            self.reviewed(nature="Docile", human_edited=["nature"])]}), encoding="utf-8")
        argv = ["pokesleep-box", "--db", str(root / "box.sqlite"), "import-json", str(path)]
        with patch.object(sys, "argv", argv), patch("builtins.print") as printed, \
                patch("pokesleep_box.engine.run_engine", side_effect=fake_engine({})):
            cli.main()
        self.assertEqual(json.loads(printed.call_args[0][0])["sp"]["match"], 1)
        self.assertEqual(self.row()["auto_verified"], 1)


def ignored(path: Path, patterns) -> bool:
    """Whether ``path`` (a file, or a directory's contents) matches .gitignore."""
    candidates = [path.as_posix(), path.as_posix() + "/x"] + \
        [parent.as_posix() + "/x" for parent in path.parents]
    return any(fnmatch.fnmatch(text, pattern) for text in candidates for pattern in patterns)


class PathTests(unittest.TestCase):
    def test_every_private_default_is_gitignored(self):
        patterns = [line.strip() for line in (ROOT / ".gitignore").read_text().splitlines()
                    if line.strip() and not line.startswith(("#", "!"))]
        for field in fields(PrivatePaths):
            if field.name in ("engine", "inbox"):
                continue
            path = getattr(DEFAULT_PATHS, field.name)
            self.assertTrue(ignored(Path(path), patterns), path)
        self.assertTrue(ignored(DEFAULT_PATHS.inbox / "capture.png", patterns))

    def test_settings_override_and_public_site_is_refused(self):
        paths = private_paths({"paths": {"db": "data/other.sqlite"}})
        self.assertEqual(paths.db, Path("data/other.sqlite"))
        self.assertEqual(paths.site, Path("site/private"))
        with self.assertRaises(ValueError):
            private_paths({"paths": {"site": "site"}})


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.paths = replace(
            DEFAULT_PATHS, **{f.name: root / getattr(DEFAULT_PATHS, f.name)
                              for f in fields(PrivatePaths) if f.name != "engine"})
        self.paths.inbox.mkdir(parents=True)
        self.calls = []
        self.items = [scanned("a"), scanned("b", nature="Mild", sp=500)]
        patches = {
            "pokesleep_box.ingest.ingest_path": self.fake_ingest,
            "pokesleep_box.engine.run_engine": self.fake_run_engine,
            "pokesleep_box.engine.evaluate": self.fake_evaluate,
            "pokesleep_box.sync.decide": lambda db, **kw: self.calls.append("decide"),
            "pokesleep_box.sync.render_private": self.fake_render,
        }
        for target, fake in patches.items():
            patcher = patch(target, side_effect=fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.engine_ok = True

    def tearDown(self):
        self.tmp.cleanup()

    def fake_ingest(self, path, frames, ocr_command=None, vision=False, interval=.8,
                    on_progress=None, qwen=None, skip_hashes=(), on_source_done=None):
        self.calls.append("scan")
        if "hash-1" in skip_hashes:
            return []
        on_source_done(path / "a.png", "hash-1", len(self.items))
        return [dict(x) for x in self.items]

    def fake_run_engine(self, payload, command=None, on_progress=None):
        repair = any("|" in entry["uid"] for entry in payload["instances"])
        self.calls.append("level-repair" if repair else "sp-check")
        if not self.engine_ok:
            raise EngineUnavailable("未ビルド")
        return fake_engine({"capture-b": 480})(payload)

    def fake_evaluate(self, db, command, team_out, on_progress=None):
        self.calls.append("evaluate")
        self.assertEqual(team_out, self.paths.team_plans)
        return 0

    def fake_render(self, db, out, *args, **kwargs):
        self.calls.append("render")
        self.assertEqual(out, self.paths.site)
        return out / "index.html"

    def sync(self, **kwargs):
        return run_sync(self.paths.inbox, self.paths, {}, ocr="vision",
                        on_progress=lambda message: None, **kwargs)

    def test_runs_every_step_in_order_into_private_paths(self):
        summary = self.sync()
        # The SP mismatch (capture-b) gets one batched level-repair call.
        self.assertEqual(self.calls, ["scan", "sp-check", "level-repair", "evaluate", "decide",
                                      "render"])
        self.assertEqual((summary["new_sources"], summary["new_individuals"],
                          summary["updated_individuals"]), (1, 2, 0))
        self.assertEqual((summary["sp_match"], summary["sp_mismatch"], summary["needs_review"]),
                         (1, 1, 1))
        self.assertTrue(self.paths.db.exists())
        self.assertTrue(self.paths.audit.exists())
        page = self.paths.review.read_text(encoding="utf-8")
        # The mismatch is listed first on the review page.
        self.assertLess(page.index("capture-b"), page.index("capture-a"))
        self.assertIn("SP不一致（画面 500 / 計算 480）", page)
        self.assertEqual(summary["site"], str(self.paths.site / "index.html"))
        text = cli.format_summary(summary, self.paths.review)
        self.assertIn("SP一致: 1 / SP不一致: 1 / 要確認: 0", text)
        self.assertIn(f"レビューが必要な個体: 1 → {self.paths.review} を開く", text)

    def test_nothing_new_skips_expensive_steps_unless_forced(self):
        self.sync()
        self.calls.clear()
        summary = self.sync()
        self.assertEqual(self.calls, ["scan", "render"])
        self.assertTrue(any("省略" in m for m in summary["messages"]))
        self.calls.clear()
        self.sync(force=True)
        self.assertEqual(self.calls, ["scan", "evaluate", "decide", "render"])

    def test_engine_unavailable_skips_sp_and_evaluate_but_renders(self):
        self.engine_ok = False
        summary = self.sync()
        self.assertEqual(self.calls, ["scan", "sp-check", "decide", "render"])
        self.assertFalse(summary["engine"])
        self.assertEqual(summary["needs_review"], 2)
        self.assertTrue(any("計算エンジンを使えない" in m for m in summary["messages"]))

    def test_recapture_counts_as_updated(self):
        self.sync()
        self.items = [scanned("c", level=12, sp=450)]
        # A new capture hash so the scan is not skipped.
        def ingest(path, frames, *args, skip_hashes=(), on_source_done=None, **kwargs):
            self.calls.append("scan")
            on_source_done(path / "c.png", "hash-2", 1)
            return [dict(x) for x in self.items]
        with patch("pokesleep_box.ingest.ingest_path", side_effect=ingest), \
                patch("pokesleep_box.ocr.species_metadata", return_value={"PICHU": {}}):
            summary = self.sync()
        self.assertEqual((summary["new_individuals"], summary["updated_individuals"]), (0, 1))

    def test_reviewed_json_is_imported_once(self):
        reviewed = Path(self.tmp.name) / "reviewed_individuals.json"
        reviewed.write_text(json.dumps({"individuals": [scanned("r", review_confirmed=True)]}),
                            encoding="utf-8")
        first = self.sync(reviewed=reviewed)
        self.assertEqual(first["reviewed_imported"], 1)
        second = self.sync(reviewed=reviewed)
        self.assertIsNone(second["reviewed_imported"])

    def test_reviewed_edited_unticked_rows_are_sp_checked(self):
        self.items = []
        reviewed = Path(self.tmp.name) / "reviewed_individuals.json"
        reviewed.write_text(json.dumps({"individuals": [
            scanned("r", nature="Docile", ocr_missing=["nature"], human_edited=["nature"]),
            scanned("s", sp=500, human_edited=["sp"])]}), encoding="utf-8")
        with patch("pokesleep_box.engine.run_engine",
                   side_effect=fake_engine({"capture-s": 480})):
            summary = self.sync(reviewed=reviewed)
        self.assertEqual(summary["reviewed_sp"]["match"], 1)
        self.assertEqual(summary["reviewed_sp"]["mismatch"], 1)
        self.assertTrue(any("SP一致で確定: 1 / SP不一致: 1" in m for m in summary["messages"]))
        db = connect(self.paths.db)
        try:
            rows = {row["uid"]: row for row in db.execute("SELECT * FROM individual")}
        finally:
            db.close()
        self.assertEqual(rows["capture-r"]["auto_verified"], 1)
        self.assertEqual(rows["capture-s"]["sp_flag"], "SP不一致（画面 500 / 計算 480）")


class ScanCommandTests(unittest.TestCase):
    def test_scan_with_sidecar_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inbox = root / "inbox"
            inbox.mkdir()
            (inbox / "a.png").write_bytes(b"fictional")
            sidecar = {k: v for k, v in scanned("a").items() if k != "uid"}
            (inbox / "a.png.json").write_text(json.dumps(sidecar), encoding="utf-8")
            argv = ["pokesleep-box", "--db", str(root / "box.sqlite"), "scan", str(inbox),
                    "--ocr", "sidecar", "--frames", str(root / "frames"),
                    "--audit", str(root / "frames/audit_report.md"),
                    "--review", str(root / "frames/review.html")]
            with patch.object(sys, "argv", argv), patch("builtins.print") as printed:
                cli.main()
            summary = json.loads(printed.call_args[0][0])
            self.assertEqual((summary["imported"], summary["new_sources"]), (1, 1))
            self.assertTrue((root / "frames/review.html").exists())
            with patch.object(sys, "argv", argv), patch("builtins.print") as printed:
                cli.main()
            self.assertEqual(json.loads(printed.call_args[0][0])["new_sources"], 0)


if __name__ == "__main__":
    unittest.main()
