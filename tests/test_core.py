import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from pokesleep_box.core import absolute_role_scores, build_team_plans, canonical_uid, connect, decide, import_individuals, load_dashboard
from pokesleep_box.render import render_site
from pokesleep_box.localization import names, normalize_individual, to_english, to_japanese
from pokesleep_box.ingest import audit, ingest_path, render_review
from pokesleep_box.analytics import analyze
from pokesleep_box.ocr import enrich_with_species_data, merge_frames


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

    def test_team_optimizer_selects_highest_unique_members(self):
        items = [
            {"uid": str(n), "species": "P", "display_name": str(n), "verified": 1,
             "pokemon_type": "grass", "island_scores": {"lapis": {"current": n}}}
            for n in range(1, 8)
        ]
        plan = build_team_plans(items)[0]
        self.assertEqual(plan["total_score"], 25)
        self.assertEqual([m["name"] for m in plan["members"]], ["7", "6", "5", "4", "3"])

    def test_all_localized_tables_are_reversible(self):
        for category, table in names().items():
            if category == "metadata":
                continue
            self.assertEqual(len(table), len(set(table.values())), category)
            for english, japanese in table.items():
                self.assertEqual(to_english(category, japanese), english)
                self.assertEqual(to_japanese(category, english), japanese)
        packaged = ROOT / "src/pokesleep_box/names_ja.json"
        published = ROOT / "data/names_ja.yaml"
        self.assertEqual(packaged.read_bytes(), published.read_bytes())

    def test_japanese_individual_is_normalized_on_import(self):
        item = {
            "species": "フシギダネ", "nature": "おっとり", "berry": "ドリ",
            "ingredients": [["あまいミツ", 2], ["あまいミツ", 5], ["ほっこりポテト", 6]],
            "subskills": [["きのみの数S", 10]], "main_skill": "食材ゲットS",
            "skill_level": 1, "confidence": 1, "verified": False,
        }
        import_individuals(self.db, [item])
        row = self.db.execute("SELECT * FROM individual").fetchone()
        self.assertEqual((row["species"], row["nature"], row["berry"]),
                         ("BULBASAUR", "Mild", "DURIN"))

    def test_sidecar_ingest_and_private_review(self):
        source = Path(self.tmp.name) / "capture.png"
        source.write_bytes(b"not a real image; extraction does not decode still images")
        item = dict(self.items[0], species="フシギダネ", nature="おっとり")
        Path(str(source) + ".json").write_text(json.dumps(item, ensure_ascii=False))
        rows = ingest_path(source, Path(self.tmp.name) / "frames")
        self.assertEqual(rows[0]["species"], "BULBASAUR")
        report = Path(self.tmp.name) / "audit.md"
        review = Path(self.tmp.name) / "review.html"
        self.assertEqual(audit(rows, report)["total"], 1)
        render_review(rows, review)
        self.assertIn("取り込みレビュー", review.read_text())

    def test_japanese_vision_observations_are_parsed(self):
        lines = ["フシギダネ", "Lv.15 SP 513", "きのみ ドリ", "あまいミツ x2",
                 "あまいミツ x5 Lv.30", "ほっこりポテト x6 Lv.60",
                 "食材ゲットS Lv.1", "Lv.10 げんき回復ボーナス",
                 "Lv.25 きのみの数S", "Lv.50 食材確率アップM",
                 "Lv.75 おてつだいスピードS", "Lv.100 リサーチEXPボーナス", "おっとり"]
        frame = {"frame": 0, "seconds": 0,
                 "observations": [{"text": text, "confidence": .9} for text in lines]}
        row = merge_frames([frame])[0]
        self.assertEqual((row["species"], row["nature"], row["berry"]),
                         ("BULBASAUR", "Mild", "DURIN"))
        self.assertEqual(row["ingredients"], [["Honey", 2], ["Honey", 5], ["Potato", 6]])
        self.assertEqual([x[1] for x in row["subskills"]], [10, 25, 50, 75, 100])
        self.assertEqual((row["level"], row["sp"], row["main_skill"]),
                         (15, 513, "Ingredient Magnet S"))

    def test_species_metadata_fills_berry_and_constrains_ingredients(self):
        row = {"species": "BULBASAUR", "ingredients": [], "ocr_missing": ["ingredients"]}
        metadata = {"BULBASAUR": {"berry": "DURIN", "ingredients": [
            {"level": 1, "choices": [["Honey", 2]]},
            {"level": 30, "choices": [["Honey", 5], ["Tomato", 4]]},
            {"level": 60, "choices": [["Honey", 7], ["Potato", 6]]},
        ]}}
        result = enrich_with_species_data([row], pokemon_data=metadata)[0]
        self.assertEqual(result["berry"], "DURIN")
        self.assertEqual(result["ingredients"], [["Honey", 2]])
        self.assertEqual(result["ingredient_options"][1]["choices"][1][2], "あんみんトマト")
        self.assertIn("ingredients", result["ocr_missing"])

    def test_cooking_free_energy_forecast_and_growth(self):
        item = dict(self.items[0], uid="energy", verified=True, energy_scores={
            "ラピスラズリ湖畔": {
                "current": {"berry": 1000, "direct_skill": 200, "cooking": 999999,
                            "expected": 1200, "low": 1100, "high": 1300},
                "60": {"berry": 2000, "direct_skill": 400, "expected": 2400},
            }
        })
        result = analyze([item], {"areaBonus": 50})
        lapis = next(x for x in result["forecasts"] if x["island"] == "ラピスラズリ湖畔")
        self.assertEqual(lapis["modes"]["current"]["daily"]["expected"], 1800)
        self.assertEqual(lapis["modes"]["current"]["daily"]["berry"], 1500)
        self.assertEqual(lapis["growth_to_60"], 12600)

    def test_unverified_energy_is_visible_but_marked_provisional(self):
        item = dict(self.items[0], uid="provisional", verified=False, energy_scores={
            "シアンの砂浜": {"current": {"berry": 1000, "expected": 1000}}
        })
        result = analyze([item])
        cyan = next(x for x in result["forecasts"] if x["island"] == "シアンの砂浜")
        self.assertEqual(cyan["modes"]["current"]["daily"]["expected"], 1000)
        self.assertTrue(cyan["modes"]["current"]["provisional"])

    def test_capture_recommendation_prefers_unowned_species_for_weak_island(self):
        benchmark = {"species": "RAICHU", "island_scores": {
            "ゴールド旧発電所": {"60": {"expected": 80000}}
        }}
        result = analyze([], {}, [benchmark])
        self.assertEqual(result["capture"]["general"][0]["species"], "ライチュウ")
        self.assertTrue(any(x["species_key"] == "RAICHU" for x in result["capture"]["tailored"]))


if __name__ == "__main__":
    unittest.main()
