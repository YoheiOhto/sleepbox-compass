import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from pokesleep_box.cli import build_parser
from pokesleep_box.core import absolute_role_scores, build_team_plans, canonical_uid, connect, decide, import_individuals, load_dashboard
from pokesleep_box.render import render_site
from pokesleep_box.localization import names, normalize_individual, to_english, to_japanese
from pokesleep_box.ingest import audit, ingest_path, render_review
from pokesleep_box.analytics import analyze, individual_label
from pokesleep_box.ocr import (enrich_with_species_data, ingredient_amount_row, merge_frames,
                               write_vision_vocabulary)
from pokesleep_box.server import build_simulation_payload
from pokesleep_box.engine import individual_to_engine


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

    def test_custom_team_payload_requires_five_unique_box_members(self):
        items = [dict(self.items[i % len(self.items)], display_name=f"member-{i}",
                      box_index=i + 1, sp=1000 + i)
                 for i in range(5)]
        import_individuals(self.db, items)
        uids = [row["uid"] for row in self.db.execute("SELECT uid FROM individual ORDER BY box_index")]
        payload = build_simulation_payload(
            self.db, {"uids": uids, "island": "シアンの砂浜", "mode": "60"})
        self.assertEqual(payload["mode"], "custom-team")
        self.assertEqual(payload["teamMode"], "60")
        self.assertEqual(len(payload["instances"]), 5)
        with self.assertRaisesRegex(ValueError, "異なる5匹"):
            build_simulation_payload(
                self.db, {"uids": [uids[0]] * 5, "island": "シアンの砂浜"})

    def test_engine_normalizes_legacy_soft_potato_name(self):
        row = {"species": "BULBASAUR", "level": 50, "nature": "Mild",
               "ingredients_json": '[["Soft Potato",6]]', "subskills_json": "[]",
               "main_skill": "Ingredient Magnet S", "skill_level": 1}
        self.assertEqual(individual_to_engine(row)["ingredients"], [["Potato", 6]])

    def test_dominated_sample_is_send(self):
        self.assertEqual(import_individuals(self.db, self.items), 3)
        result = decide(self.db, keep_top_n=2)
        self.assertEqual(result, {"keep": 2, "send": 1, "protected": 0})
        sent = self.db.execute("SELECT reason FROM decision WHERE verdict='send'").fetchone()
        self.assertIn("最も尖った役割", sent["reason"])

    def test_decision_ranks_each_individual_by_its_peak_future_role(self):
        items = []
        for index, (nature, berry, ingredient, skill) in enumerate(
                (("Mild", 1200, 100, 100),
                 ("Calm", 100, 1000, 100),
                 ("Sassy", 100, 100, 600)), 1):
            item = dict(self.items[0], display_name=f"peak-{index}", box_index=index,
                        nature=nature, sp=500 + index)
            item["scores"] = {
                str(anchor): {"berry": berry, "ingredient": ingredient, "skill": skill}
                for anchor in (50, 60, 70, 80)
            }
            items.append(item)
        import_individuals(self.db, items)

        result = decide(self.db, keep_top_n=2)

        self.assertEqual(result, {"keep": 2, "send": 1, "protected": 0})
        sent = self.db.execute(
            "SELECT i.display_name,d.reason FROM decision d JOIN individual i ON i.uid=d.uid "
            "WHERE d.verdict='send'"
        ).fetchone()
        self.assertEqual(sent["display_name"], "peak-3")
        self.assertIn("skill", sent["reason"])

    def test_unverified_is_never_sent(self):
        item = dict(self.items[0], verified=False)
        import_individuals(self.db, [item])
        self.assertEqual(decide(self.db)["protected"], 1)

    def test_archived_individual_is_hidden_and_not_decided(self):
        import_individuals(self.db, [self.items[0]])
        self.db.execute("UPDATE individual SET archived=1")
        self.db.commit()

        self.assertEqual(decide(self.db), {"keep": 0, "send": 0, "protected": 0})
        self.assertEqual(load_dashboard(self.db), [])

    def test_lone_species_is_kept_even_when_absolute_score_is_low(self):
        import_individuals(self.db, [self.items[0]])

        result = decide(self.db)

        self.assertEqual(result, {"keep": 1, "send": 0, "protected": 0})
        decision = self.db.execute("SELECT verdict,reason FROM decision").fetchone()
        self.assertEqual(decision["verdict"], "keep")
        self.assertIn("最も尖った役割", decision["reason"])

    def test_rescan_preserves_verification_only_when_core_is_unchanged(self):
        original = dict(self.items[0], verified=True, sp=513)
        import_individuals(self.db, [original])
        import_individuals(self.db, [dict(original, verified=False)])
        row = self.db.execute("SELECT uid,verified FROM individual").fetchone()
        self.assertEqual(row["verified"], 1)
        uid = row["uid"]

        corrected = dict(original, nature="Calm", verified=False)
        import_individuals(self.db, [corrected])
        rows = self.db.execute("SELECT uid,verified,nature FROM individual").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["uid"], rows[0]["verified"], rows[0]["nature"]),
                         (uid, 0, "Calm"))

    def test_manual_review_confirmation_never_returns_to_review(self):
        original = dict(self.items[0], verified=False, review_confirmed=True)
        import_individuals(self.db, [original])
        import_individuals(self.db, [dict(original, verified=False, review_confirmed=False,
                                           nature="Calm")])
        row = self.db.execute("SELECT verified,review_confirmed,nature FROM individual").fetchone()
        self.assertEqual((row["verified"], row["review_confirmed"], row["nature"]), (1, 1, "Calm"))

    def test_partial_rescan_never_erases_reviewed_fields(self):
        original = dict(self.items[0], sp=513, verified=True)
        import_individuals(self.db, [original])
        partial = dict(original, ingredients=original["ingredients"][:1], verified=False,
                       ocr_missing=["ingredients"])
        import_individuals(self.db, [partial])
        row = self.db.execute("SELECT ingredients_json,verified FROM individual").fetchone()
        self.assertEqual(json.loads(row["ingredients_json"]), original["ingredients"])
        self.assertEqual(row["verified"], 1)

    def test_render_escapes_display_name(self):
        item = dict(self.items[0], display_name="<script>alert(1)</script>")
        import_individuals(self.db, [item])
        decide(self.db)
        out = Path(self.tmp.name) / "site"
        render_site(load_dashboard(self.db), out)
        page = (out / "index.html").read_text()
        self.assertNotIn("</script>\"", page)

    def test_box_page_links_grid_to_individual_details(self):
        import_individuals(self.db, self.items)
        out = Path(self.tmp.name) / "box-site"
        render_site(load_dashboard(self.db), out)
        page = (out / "index.html").read_text()
        self.assertIn("現在のボックス", page)
        self.assertIn("openPokemonDetail", page)
        self.assertIn("サブスキル", page)
        self.assertIn("取込#", page)
        self.assertIn("食材ゲットS", page)
        self.assertIn("きのみ・食材・スキルのうち最も高い個体評価", page)
        self.assertIn("box-avatar score", page)
        self.assertIn("基礎エナジー×個数×生産回数", page)
        self.assertIn("つやつやアボカド", page)
        self.assertIn("性格補正", page)
        self.assertIn("食材おてつだい確率", page)
        self.assertIn("nature-up", page)
        self.assertIn("nature-down", page)
        self.assertIn("detail-row.locked", page)
        self.assertIn("未解放", page)
        self.assertEqual(page.count('id="pokemon-dialog"'), 1)
        self.assertIn("育成優先 上位", page)
        self.assertIn("育成停止：現状Lv", page)
        self.assertIn("data-pokemon-uid", page)
        self.assertIn("任意の5匹をシミュレーション", page)
        self.assertIn("targetIslands", page)
        self.assertNotIn('data-tab="pokemon"', page)
        self.assertIn("box-search", page)
        self.assertIn("データ・計算状態", page)
        self.assertIn("team-goal", page)
        self.assertIn("Game8の最新Tier表", page)
        self.assertIn("このアプリの「理想個体」", page)
        self.assertIn("たねの使い道", page)
        self.assertIn("サブスキルのたねは解放済み候補が複数あるとランダム", page)
        self.assertIn("SUBSKILL_UPGRADES", page)
        self.assertIn("review-mark", page)
        self.assertIn("評価と編成を再計算", page)
        self.assertIn("/api/recalculate", page)
        self.assertIn("確認済みにする", page)
        self.assertIn("/api/confirm-review", page)

    def test_individual_label_is_traceable_across_views(self):
        item = dict(self.items[0], display_name="相棒", box_index=20, level=61, sp=4234)
        self.assertEqual(individual_label(item), "相棒 · 取込#20 · Lv61 · SP 4234")

    def test_absolute_score_uses_fixed_reference(self):
        references = {level: {role: 1000 for role in ("berry", "ingredient", "skill")}
                      for level in (50, 60, 70, 80)}
        evaluations = {level: {role: 500 for role in ("berry", "ingredient", "skill")}
                       for level in (50, 60, 70, 80)}
        self.assertEqual(absolute_role_scores(evaluations, references),
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
        row = {"species": "BULBASAUR", "ingredients": [], "ingredient_amounts": [2, 4, 6],
               "ocr_missing": ["ingredients"]}
        metadata = {"BULBASAUR": {"berry": "DURIN", "main_skill": "Ingredient Magnet S", "ingredients": [
            {"level": 1, "choices": [["Honey", 2]]},
            {"level": 30, "choices": [["Honey", 5], ["Tomato", 4]]},
            {"level": 60, "choices": [["Honey", 7], ["Potato", 6]]},
        ]}}
        result = enrich_with_species_data([row], pokemon_data=metadata)[0]
        self.assertEqual(result["berry"], "DURIN")
        self.assertEqual(result["main_skill"], "Ingredient Magnet S")
        self.assertEqual(result["ingredients"], [["Honey", 2], ["Tomato", 4], ["Potato", 6]])
        self.assertEqual(result["ingredient_options"][1]["choices"][1][2], "あんみんトマト")
        self.assertNotIn("ingredients", result["ocr_missing"])

    def test_video_frames_split_same_species_when_sp_changes(self):
        def frame(sp):
            return {"frame": sp, "observations": [
                {"text": "ゼニガメ", "confidence": .9},
                {"text": f"SP {sp}", "confidence": .9},
            ]}
        rows = merge_frames([frame(540), frame(540), frame(511), frame(511)])
        self.assertEqual([x["sp"] for x in rows], [540, 511])

    def test_rescan_keeps_engine_scores_when_the_core_is_unchanged(self):
        item = dict(self.items[0], sp=513, energy_scores={"シアンの砂浜": {"current": {"expected": 1}}})
        import_individuals(self.db, [item])
        capture = {k: v for k, v in item.items() if k not in ("energy_scores", "uid")}
        import_individuals(self.db, [capture])
        row = self.db.execute("SELECT energy_scores_json FROM individual").fetchone()
        self.assertEqual(json.loads(row["energy_scores_json"])["シアンの砂浜"]["current"]["expected"], 1)

    def test_rescan_drops_engine_scores_when_the_core_changed(self):
        item = dict(self.items[0], sp=513, energy_scores={"シアンの砂浜": {"current": {"expected": 1}}})
        import_individuals(self.db, [item])
        capture = {k: v for k, v in item.items() if k not in ("energy_scores", "uid")}
        capture["skill_level"] = int(item["skill_level"]) + 1
        import_individuals(self.db, [capture])
        rows = self.db.execute(
            "SELECT energy_scores_json FROM individual WHERE sp=513 ORDER BY last_seen").fetchall()
        self.assertEqual([json.loads(x["energy_scores_json"]) for x in rows][-1], {})

    def test_render_never_defaults_into_the_published_site_directory(self):
        parser = build_parser()
        private = parser.parse_args(["render"])
        public = parser.parse_args(["demo"])
        # `render` reads the private box, so its page must stay out of `site/`,
        # which is tracked by Git and published. Only `demo` writes there.
        self.assertEqual(private.out, Path("site/private"))
        self.assertEqual(private.db, Path("data/box.sqlite"))
        self.assertEqual(public.out, Path("site"))

    def test_single_misread_frame_never_splits_one_individual(self):
        def frame(index, species, sp):
            return {"frame": index, "observations": [
                {"text": species, "confidence": .9},
                {"text": f"SP {sp}", "confidence": .9},
            ]}
        rows = merge_frames([frame(1, "ゼニガメ", 540), frame(2, "ゼニガメ", 540),
                             frame(3, "ゼニガメ", 511), frame(4, "ゼニガメ", 540),
                             frame(5, "ゼニガメ", 540)])
        self.assertEqual([x["sp"] for x in rows], [540])

    def test_higher_confidence_frame_wins_over_a_later_blurry_frame(self):
        frames = [
            {"frame": 1, "observations": [
                {"text": "ヒトカゲ", "confidence": .97},
                {"text": "SP 508", "confidence": .97},
                {"text": "おっとり", "confidence": .95},
            ]},
            {"frame": 2, "observations": [
                {"text": "ヒトカゲ", "confidence": .4},
                {"text": "SP 508", "confidence": .4},
                {"text": "さみしがり", "confidence": .4},
            ]},
        ]
        self.assertEqual(merge_frames(frames)[0]["nature"], "Mild")

    def test_subskills_from_different_scroll_positions_are_combined(self):
        frames = [
            {"frame": 1, "observations": [
                {"text": "ゼニガメ", "confidence": .9},
                {"text": "SP 511", "confidence": .9},
                {"text": "Lv.10 げんき回復ボーナス", "confidence": .9},
                {"text": "Lv.25 きのみの数S", "confidence": .9},
            ]},
            {"frame": 2, "observations": [
                {"text": "ゼニガメ", "confidence": .9},
                {"text": "Lv.50 食材確率アップM", "confidence": .9},
                {"text": "Lv.75 おてつだいスピードS", "confidence": .9},
                {"text": "Lv.100 リサーチEXPボーナス", "confidence": .9},
            ]},
        ]
        row = merge_frames(frames)[0]
        self.assertEqual([x[1] for x in row["subskills"]], [10, 25, 50, 75, 100])
        self.assertNotIn("subskills", row["ocr_missing"])

    def test_ingredient_amounts_survive_a_shifted_capture(self):
        observations = [
            {"text": "あまいミツ x2", "confidence": .9, "x": .20, "y": .55},
            {"text": "あまいミツ x5 Lv.30", "confidence": .9, "x": .50, "y": .55},
            {"text": "ほっこりポテト x6 Lv.60", "confidence": .9, "x": .80, "y": .54},
        ]
        self.assertEqual(ingredient_amount_row(observations), [2, 5, 6])

    def test_unrelated_amount_is_never_read_as_an_ingredient_row(self):
        self.assertEqual(ingredient_amount_row(
            [{"text": "ゆめのかけす x20", "confidence": .9, "x": .5, "y": .12}]), [])

    def test_vision_vocabulary_carries_the_known_game_names(self):
        words = json.loads(write_vision_vocabulary().read_text(encoding="utf-8"))
        self.assertIn("フシギダネ", words)
        self.assertIn("げんき回復ボーナス", words)

    def test_scrolled_ingredient_level_never_overwrites_current_level(self):
        frames = [
            {"frame": 1, "observations": [
                {"text": "SP 511", "confidence": .9},
                {"text": "Lv.11 ゼニガメ", "confidence": .9},
            ]},
            {"frame": 2, "observations": [
                {"text": "ゼニガメ", "confidence": .9},
                {"text": "Lv.30", "confidence": .9},
                {"text": "Lv.60", "confidence": .9},
            ]},
        ]
        self.assertEqual(merge_frames(frames)[0]["level"], 11)

    def test_hidden_main_skill_level_never_resets_detected_level(self):
        frames = [
            {"frame": 1, "observations": [
                {"text": "SP 508", "confidence": .9},
                {"text": "Lv.12 ヒトカゲ", "confidence": .9},
                {"text": "食材ゲットS Lv.3", "confidence": .9},
            ]},
            {"frame": 2, "observations": [
                {"text": "SP 508", "confidence": .9},
                {"text": "Lv.12 ヒトカゲ", "confidence": .9},
                {"text": "詳細ステータス", "confidence": .9},
            ]},
        ]
        self.assertEqual(merge_frames(frames)[0]["skill_level"], 3)

    def test_percentage_never_becomes_sp_during_transition(self):
        frame = {"frame": 1, "observations": [
            {"text": "イワーク", "confidence": .9},
            {"text": "SP 100%", "confidence": .9},
            {"text": "Lv.30", "confidence": .9},
        ]}
        row = merge_frames([frame])[0]
        self.assertIsNone(row.get("sp"))
        self.assertIsNone(row.get("level"))

    def test_ingredient_base_energy_is_included_but_explicit_cooking_is_not(self):
        item = dict(self.items[0], uid="energy", verified=True, energy_scores={
            "ラピスラズリ湖畔": {
                "current": {"berry": 1000, "ingredient": 300, "direct_skill": 200,
                            "cooking": 999999, "expected": 1500, "low": 1400, "high": 1600},
                "60": {"berry": 2000, "ingredient": 600, "direct_skill": 400,
                       "expected": 3000},
            }
        })
        result = analyze([item], {"areaBonus": 50})
        lapis = next(x for x in result["forecasts"] if x["island"] == "ラピスラズリ湖畔")
        self.assertEqual(lapis["modes"]["current"]["daily"]["expected"], 2250)
        self.assertEqual(lapis["modes"]["current"]["daily"]["berry"], 1500)
        self.assertEqual(lapis["modes"]["current"]["daily"]["ingredient"], 450)
        self.assertEqual(lapis["growth_to_60"], 15750)

    def test_unverified_energy_is_visible_but_marked_provisional(self):
        item = dict(self.items[0], uid="provisional", verified=False, energy_scores={
            "シアンの砂浜": {"current": {"berry": 1000, "expected": 1000}}
        })
        result = analyze([item])
        cyan = next(x for x in result["forecasts"] if x["island"] == "シアンの砂浜")
        self.assertEqual(cyan["modes"]["current"]["daily"]["expected"], 1000)
        self.assertTrue(cyan["modes"]["current"]["provisional"])

    def test_team_plan_overrides_additive_forecast_and_keeps_synergy(self):
        items = [dict(self.items[0], uid="healer", verified=True, energy_scores={
            "シアンの砂浜": {"current": {"expected": 1000, "berry": 1000}}
        })]
        plans = [{"island": "シアンの砂浜", "mode": "current", "total_energy": 1800,
                  "cooking": 400, "recipe_level": 1,
                  "synergy_gain": 500, "provisional": False,
                  "members": [{"uid": "healer", "energy": 1300, "berry": 1000,
                               "ingredient": 200,
                               "direct_skill": 100, "marginal": 1800,
                               "team_help_support": 2, "subskills": ["Helping Bonus"]}]}]
        result = analyze(items, team_plans=plans)
        cyan = next(x for x in result["forecasts"] if x["island"] == "シアンの砂浜")
        current = cyan["modes"]["current"]
        self.assertEqual(current["daily"]["expected"], 1800)
        self.assertEqual(current["daily"]["ingredient"], 200)
        self.assertEqual(current["daily"]["cooking"], 400)
        self.assertEqual(current["synergy_gain"], 500)
        self.assertTrue(current["team_aware"])

    def test_capture_recommendation_prefers_unowned_species_for_weak_island(self):
        benchmark = {"species": "RAICHU", "island_scores": {
            "ゴールド旧発電所": {"60": {"expected": 80000}}
        }}
        result = analyze([], {}, [benchmark])
        self.assertEqual(result["capture"]["general"][0]["species"], "ライチュウ")
        self.assertTrue(any(x["species_key"] == "RAICHU" for x in result["capture"]["tailored"]))

    def test_capture_recommendation_counts_owned_pre_evolution(self):
        benchmark = {"species": "RAICHU", "island_scores": {
            "ゴールド旧発電所": {"60": {"expected": 80000}}
        }}
        pikachu = dict(self.items[0], uid="owned-pikachu", species="PIKACHU", final_evolution="RAICHU",
                       verified=True, absolute_score=90, energy_scores={
                           "ゴールド旧発電所": {"60": {"expected": 50000}}
                       })

        result = analyze([pikachu], {}, [benchmark])

        self.assertFalse(any(x["species_key"] == "RAICHU"
                             for x in result["capture"]["tailored"]))

    def test_capture_board_excludes_weaker_species_in_same_role_type_slot(self):
        benchmarks = [
            {"species": "ALTARIA", "species_ja": "チルタリス", "specialty": "berry",
             "berry": "YACHE", "island_scores": {"アンバー渓谷": {"60": {"expected": 70000}}}},
            {"species": "OTHER", "species_ja": "同枠上位", "specialty": "berry",
             "berry": "YACHE", "island_scores": {"アンバー渓谷": {"60": {"expected": 80000}}}},
        ]

        result = analyze([], {}, benchmarks)
        amber = next(x for x in result["capture"]["by_island"] if x["island"] == "アンバー渓谷")

        self.assertEqual([x["species_key"] for x in amber["priority"]], ["OTHER"])
        self.assertIn("同枠上位", next(x for x in amber["skip"]
                                      if x["species_key"] == "ALTARIA")["reason"])

    def test_capture_board_keeps_different_roles_separate(self):
        benchmarks = [
            {"species": "ALTARIA", "specialty": "berry", "berry": "YACHE",
             "island_scores": {"アンバー渓谷": {"60": {"expected": 70000}}}},
            {"species": "DRAGONITE", "specialty": "ingredient", "berry": "YACHE",
             "island_scores": {"アンバー渓谷": {"60": {"expected": 90000}}}},
        ]

        result = analyze([], {}, benchmarks)
        amber = next(x for x in result["capture"]["by_island"] if x["island"] == "アンバー渓谷")

        self.assertEqual({x["species_key"] for x in amber["priority"]},
                         {"ALTARIA", "DRAGONITE"})

    def test_capture_encounter_view_uses_seed_species_and_selected_sleep_pool(self):
        benchmarks = [
            {"species": "RAICHU", "species_ja": "ライチュウ", "base_species": "PICHU",
             "base_species_ja": "ピチュー", "base_species_en": "Pichu", "specialty": "berry",
             "berry": "GREPA", "island_scores": {
                 "ゴールド旧発電所": {"60": {"expected": 80000}}}},
            {"species": "TYPHLOSION", "species_ja": "バクフーン", "base_species": "CYNDAQUIL",
             "base_species_ja": "ヒノアラシ", "base_species_en": "Cyndaquil", "specialty": "berry",
             "berry": "LEPPA", "island_scores": {
                 "トープ洞窟": {"60": {"expected": 90000}}}},
        ]
        encounters = {"updated": "2026-08-26", "fields": {"ゴールド旧発電所": {
            "ぐっすり": ["Pichu"], "すやすや": ["Cyndaquil"]}}}

        result = analyze([], {}, benchmarks, encounters=encounters)
        view = result["capture"]["encounter_views"][0]

        self.assertEqual(view["sleep_types"][0]["priority"][0]["species"], "ピチュー")
        self.assertEqual(view["sleep_types"][0]["priority"][0]["evolves_to"], "ライチュウ")
        self.assertEqual(view["sleep_types"][1]["priority"][0]["species"], "ヒノアラシ")


if __name__ == "__main__":
    unittest.main()
