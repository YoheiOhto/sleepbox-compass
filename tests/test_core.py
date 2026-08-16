import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from pokesleep_box.core import absolute_role_scores, canonical_uid, connect, decide, import_individuals
from pokesleep_box.render import render_site
from pokesleep_box.core import load_dashboard


ROOT = Path(__file__).parents[1]


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = connect(Path(self.tmp.name) / "box.sqlite")
        self.items = json.loads((ROOT / "data/example_individuals.json").read_text())["individuals"]

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_uid_ignores_level_and_box_position(self):
        a = dict(self.items[0])
        b = dict(a, level=80, box_index=99)
        self.assertEqual(canonical_uid(a), canonical_uid(b))

    def test_dominated_sample_is_send(self):
        self.assertEqual(import_individuals(self.db, self.items), 3)
        result = decide(self.db, keep_top_n=2)
        self.assertEqual(result, {"keep": 2, "send": 1, "protected": 0})
        sent = self.db.execute("SELECT reason FROM decision WHERE verdict='send'").fetchone()
        self.assertIn("全3ロール", sent["reason"])

    def test_unverified_is_never_sent(self):
        item = dict(self.items[0], verified=False)
        import_individuals(self.db, [item])
        self.assertEqual(decide(self.db)["protected"], 1)

    def test_render_escapes_display_name(self):
        item = dict(self.items[0], display_name="<script>alert(1)</script>")
        import_individuals(self.db, [item])
        decide(self.db)
        out = Path(self.tmp.name) / "site"
        render_site(load_dashboard(self.db), out)
        page = (out / "index.html").read_text()
        self.assertNotIn("</script>\"", page)

    def test_absolute_score_uses_fixed_reference(self):
        evaluations = {level: {role: 125 for role in ("berry", "ingredient", "skill")}
                       for level in (50, 60, 70, 80)}
        self.assertEqual(absolute_role_scores(evaluations),
                         {"berry": 50.0, "ingredient": 50.0, "skill": 50.0})

    def test_missing_anchor_has_no_absolute_score(self):
        evaluations = {level: {"berry": 250} for level in (50, 60, 80)}
        self.assertEqual(absolute_role_scores(evaluations)["berry"], 0.0)


if __name__ == "__main__":
    unittest.main()
