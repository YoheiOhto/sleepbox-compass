import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from pokesleep_box.cli import build_parser
from pokesleep_box.core import (absolute_role_scores, build_team_plans, canonical_uid, connect,
                                decide, export_backup, import_individuals, load_dashboard, record_observation,
                                restore_backup, save_cooking_plan, save_team, set_ingredient_inventory,
                                set_never_send, species_friendships, upsert_species_friendship)
from pokesleep_box.render import render_site
from pokesleep_box.localization import names, normalize_individual, to_english, to_japanese
from pokesleep_box.ingest import audit, ingest_path, render_review
from pokesleep_box.analytics import analyze, individual_label
from pokesleep_box.ocr import (_best_match, enrich_with_species_data, ingredient_amount_row,
                               merge_frames, parse_frame, parse_dex_friendship,
                               resolve_ingredients_by_sp, resolve_species_variant_by_sp,
                               write_vision_vocabulary)
from pokesleep_box.server import build_simulation_payload
from pokesleep_box.engine import individual_to_engine
from pokesleep_box.planning import catches_for_target, resource_plan


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

    def test_capture_probability_and_seed_budget_are_explicit(self):
        self.assertEqual(catches_for_target(.5, .875), 3)
        plan = resource_plan([{"gain": 5, "uid": "sub"}], [{"gain": 9, "uid": "main"}],
                             {"subSkillSeeds": 1, "mainSkillSeeds": 0})
        self.assertEqual([x["uid"] for x in plan], ["sub"])

    def test_portable_backup_excludes_captures_and_restores_box(self):
        import_individuals(self.db, [self.items[0]])
        path = Path(self.tmp.name) / "backup.json"
        export_backup(self.db, path)
        self.assertNotIn('"capture"', path.read_text())
        self.db.execute("DELETE FROM decision")
        self.db.execute("DELETE FROM evaluation")
        self.db.execute("DELETE FROM individual")
        restore_backup(self.db, path)
        self.assertEqual(self.db.execute("SELECT count(*) FROM individual").fetchone()[0], 1)

    def test_saved_team_and_production_observation_are_local_db_records(self):
        import_individuals(self.db, self.items)
        uids = [row[0] for row in self.db.execute("SELECT uid FROM individual")]
        # A small test box cannot contain five unique members, so verify the
        # safety check as well as observation persistence.
        with self.assertRaises(ValueError):
            save_team(self.db, "今週", uids)
        record_observation(self.db, "2026-08-30", "シアンの砂浜", 12345, 12000, "テスト")
        self.assertEqual(self.db.execute("SELECT energy FROM production_observation").fetchone()[0], 12345)

    def test_dex_friendship_and_never_send_protection_are_persisted(self):
        import_individuals(self.db, self.items)
        row = self.db.execute("SELECT uid FROM individual LIMIT 1").fetchone()
        set_never_send(self.db, row["uid"], True, ["色違い", "思い出"])
        self.assertEqual(decide(self.db)["protected"], 1)
        upsert_species_friendship(self.db, "BULBASAUR", 12, "bronze")
        self.assertEqual(species_friendships(self.db)[0]["friendship_level"], 12)
        parsed = parse_dex_friendship([{"text": "フシギダネ なかよしレベル Lv. 12 ブロンズ"}])
        self.assertEqual((parsed["species"], parsed["badge"]), ("BULBASAUR", "bronze"))

    def test_inventory_and_cooking_plan_are_local(self):
        set_ingredient_inventory(self.db, {"Honey": 30, "Apple": 12})
        save_cooking_plan(self.db, "今週", "はちみつカレー", {"Honey": 7, "Apple": 8}, 3, active=True)
        row = self.db.execute("SELECT requirements_json,active FROM cooking_plan").fetchone()
        self.assertEqual((json.loads(row["requirements_json"]), row["active"]), ({"Honey": 7, "Apple": 8}, 1))

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

    def test_evaluate_skips_unknown_and_empty_ingredient_rows(self):
        from unittest.mock import patch
        import_individuals(self.db, [dict(self.items[0], ingredients=[]), self.items[1]])
        captured = {}

        def fake_engine(payload, command, on_progress=None):
            captured["instances"] = payload["instances"]
            return {"results": []}

        with patch("pokesleep_box.engine.run_engine", side_effect=fake_engine):
            from pokesleep_box.engine import evaluate
            evaluate(self.db)
        self.assertTrue(captured["instances"])
        self.assertTrue(all(x["instance"]["ingredients"] for x in captured["instances"]))

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
        self.assertIn("博士に送る（一覧から消す）", page)
        self.assertIn("/api/archive-individual", page)
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
                 "Lv.70 おてつだいスピードS", "Lv.80 リサーチEXPボーナス", "おっとり"]
        frame = {"frame": 0, "seconds": 0,
                 "observations": [{"text": text, "confidence": .9} for text in lines]}
        row = merge_frames([frame])[0]
        self.assertEqual((row["species"], row["nature"], row["berry"]),
                         ("BULBASAUR", "Mild", "DURIN"))
        self.assertEqual(row["ingredients"], [["Honey", 2], ["Honey", 5], ["Potato", 6]])
        self.assertEqual([x[1] for x in row["subskills"]], [10, 25, 50, 70, 80])
        self.assertEqual((row["level"], row["sp"], row["main_skill"]),
                         (15, 513, "Ingredient Magnet S"))

    def test_known_near_miss_in_species_header_is_recovered(self):
        frame = {"frame": 0, "seconds": 0, "observations": [
            {"text": text, "confidence": .9}
            for text in ("SP 558", "Lv.13 ワーノコ", "まじめ", "エナジーチャージS Lv.1")
        ]}
        row = parse_frame(frame)
        self.assertEqual(row["species"], "TOTODILE")

    def test_sp_header_split_across_ocr_observations_is_read(self):
        frame = {"frame": 0, "seconds": 0, "observations": [
            {"text": text, "confidence": .9}
            for text in ("SP.", "473", "Lv.14 ムンナ", "てれや", "ゆめのかけらゲットS Lv.1")
        ]}
        self.assertEqual(parse_frame(frame)["sp"], 473)

    def test_sp_final_three_read_as_hiragana_is_recovered(self):
        frame = {"frame": 0, "observations": [
            {"text": "SP 63ろ", "confidence": .9},
            {"text": "Lv.17 ゴローン", "confidence": .9},
        ]}
        self.assertEqual(parse_frame(frame)["sp"], 633)

    def test_named_rescan_replaces_unknown_with_the_same_sp(self):
        unknown = dict(self.items[0], uid="unknown-513", species="UNKNOWN", sp=513)
        import_individuals(self.db, [unknown])
        recovered = dict(self.items[0], species="BULBASAUR", sp=513)
        import_individuals(self.db, [recovered])
        self.assertEqual(self.db.execute("SELECT count(*) FROM individual").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT species FROM individual").fetchone()[0], "BULBASAUR")

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
                {"text": "Lv.70 おてつだいスピードS", "confidence": .9},
                {"text": "Lv.80 リサーチEXPボーナス", "confidence": .9},
            ]},
        ]
        row = merge_frames(frames)[0]
        self.assertEqual([x[1] for x in row["subskills"]], [10, 25, 50, 70, 80])
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

    def detail_screen(self, name, level, sp, subskills, nature="まじめ"):
        """A detail screenshot the way Vision actually reports it.

        The header level and the species name arrive as separate observations,
        the ingredient Lv.30/Lv.60 badges repeat the same "Lv.n" text in the
        right half of the header band, each subskill's unlock badge sits just
        above its name, and the nature panel below 詳細ステータス carries stat
        labels that look like subskill names.
        """
        rows = [{"text": f"SP {sp}", "confidence": .9, "x": .245, "y": .905},
                {"text": f"LV.{level}", "confidence": .9, "x": .211, "y": .879},
                {"text": name, "confidence": .9, "x": .311, "y": .879},
                {"text": "LV.30", "confidence": .9, "x": .645, "y": .882},
                {"text": "LV. 60", "confidence": .9, "x": .805, "y": .882},
                {"text": "エナジーチャージS", "confidence": .9, "x": .255, "y": .570},
                {"text": "詳細ステータス", "confidence": .9, "x": .085, "y": .208},
                {"text": "せいかく", "confidence": .9, "x": .097, "y": .160},
                {"text": nature, "confidence": .9, "x": .195, "y": .134},
                {"text": "おてつだいスピード", "confidence": .9, "x": .519, "y": .150}]
        for index, (text, unlock) in enumerate(subskills):
            column, row = index % 2, index // 2
            x, y = (.150, .600)[column], (.440, .368, .293)[row]
            rows.append({"text": text, "confidence": .9, "x": x, "y": y})
            if unlock:
                rows.append({"text": f"h Lv.{unlock}", "confidence": .9,
                             "x": x - .06, "y": y + .026})
        return {"frame": 0, "seconds": 0, "observations": rows}

    def test_species_name_never_resolves_to_a_shorter_name_inside_it(self):
        # 26 species names end in another species' name, so matching by plain
        # containment read コラッタ as ラッタ and ブラッキー as ラッキー -- a
        # different Pokemon entirely, at full confidence.
        for japanese, expected in (("コラッタ", "RATTATA"), ("ラッタ", "RATICATE"),
                                   ("レアコイル", "MAGNETON"), ("コイル", "MAGNEMITE"),
                                   ("ブラッキー", "UMBREON"), ("ラッキー", "CHANSEY"),
                                   ("ゴースト", "HAUNTER"), ("ゴース", "GASTLY"),
                                   ("デカヌチャン", "TINKATON"), ("カヌチャン", "TINKATINK")):
            with self.subTest(japanese=japanese):
                self.assertEqual(to_english("species", japanese), expected)
                self.assertEqual(parse_frame({"observations": [
                    {"text": japanese, "confidence": .9}]})["species"], expected)

    def test_header_level_is_read_from_layout_when_the_name_is_a_near_miss(self):
        # Vision splits the header, and the alias table recovers the species
        # from text that never contains its canonical spelling, so the level
        # cannot be anchored on the species name.
        split = self.detail_screen("ムンナ", 14, 473, [("ゆめのかけらボーナス", 10)])
        near_miss = self.detail_screen("ワーノコ", 13, 558, [("睡眠EXPボーナス", 10)])

        self.assertEqual(parse_frame(split)["level"], 14)
        self.assertEqual(parse_frame(near_miss)["species"], "TOTODILE")
        self.assertEqual(parse_frame(near_miss)["level"], 13)

    def test_ingredient_slot_badges_are_never_read_as_the_current_level(self):
        frame = self.detail_screen("ムンナ", 14, 473, [("ゆめのかけらボーナス", 10)])

        self.assertEqual(parse_frame(frame)["level"], 14)

    def test_subskill_unlocks_come_from_the_badge_above_each_row(self):
        # Only the last three badges are readable; the first two rows keep their
        # slots from the unclaimed unlock levels rather than shifting upward.
        frame = self.detail_screen("ムンナ", 30, 473, [
            ("最大所持数アップS", 0), ("げんき回復ボーナス", 0), ("睡眠EXPボーナス", 50),
            ("リサーチEXPボーナス", 70), ("食材確率アップS", 80)])

        row = merge_frames([frame])[0]

        self.assertEqual([x[1] for x in row["subskills"]], [10, 25, 50, 70, 80])
        self.assertEqual(row["subskills"][2][0], "Sleep EXP Bonus")
        self.assertEqual(row["subskills"][4][0], "Ingredient Finder S")

    def test_nature_stat_label_is_never_read_as_a_subskill(self):
        # 「おてつだいスピード」 in the nature panel is one character away from
        # the subskill 「おてつだいスピードS」 and would take one of five slots.
        frame = self.detail_screen("ムンナ", 14, 473, [("ゆめのかけらボーナス", 10)])

        row = merge_frames([frame])[0]

        self.assertEqual([x[0] for x in row["subskills"]], ["Dream Shard Bonus"])

    def test_a_dropped_size_letter_makes_the_subskill_row_reviewable(self):
        frame = self.detail_screen("ムンナ", 14, 473, [("最大所持数アップ", 10)])

        row = merge_frames([frame])[0]
        self.assertIn("subskills", row["ocr_missing"])
        self.assertEqual(row["subskills_ambiguous_rows"][0]["unlock"], 10)

    def test_size_letter_read_as_katakana_still_resolves(self):
        frame = self.detail_screen("ムンナ", 14, 473, [("最大所持数アップレ", 10)])

        self.assertEqual(merge_frames([frame])[0]["subskills"][0][0], "Inventory Up L")

    def test_a_later_ingredient_slot_resolves_even_when_an_earlier_one_is_ambiguous(self):
        row = {"species": "TOTODILE", "ingredients": [], "ingredient_amounts": [1, 2, 3],
               "ocr_missing": ["ingredients"]}
        metadata = {"TOTODILE": {"berry": "ORAN", "ingredients": [
            {"level": 1, "choices": [["Sausage", 1]]},
            {"level": 30, "choices": [["Sausage", 2], ["Oil", 2]]},
            {"level": 60, "choices": [["Sausage", 4], ["Oil", 3]]},
        ]}}

        result = enrich_with_species_data([row], pokemon_data=metadata)[0]

        # Amount 3 identifies Lv60 as Oil even though the Lv30 slot stays
        # ambiguous; the Lv30 slot keeps a provisional first choice.
        self.assertEqual(result["ingredients"], [["Sausage", 1], ["Sausage", 2], ["Oil", 3]])
        self.assertIn("ingredients", result["ocr_missing"])

    def test_ambiguous_ingredients_are_resolved_by_one_batched_sp_check(self):
        row = {"species": "TOTODILE", "level": 20, "nature": "Hardy", "sp": 558,
               "main_skill": "Charge Strength S", "skill_level": 1, "subskills": [],
               "ingredients": [["Sausage", 1], ["Sausage", 2]],
               "ingredient_options": [
                   {"level": 1, "choices": [["Sausage", 1, "マメミート"]]},
                   {"level": 30, "choices": [["Sausage", 2, "マメミート"],
                                               ["Oil", 2, "ピュアなオイル"]]},
               ], "ocr_missing": ["ingredients"]}
        calls = []

        def fake_engine(payload, command):
            calls.append((payload, command))
            return {"results": [
                {"uid": candidate["uid"], "match": candidate["instance"]["ingredients"][1][0] == "Oil",
                 "diff": 0 if candidate["instance"]["ingredients"][1][0] == "Oil" else 7}
                for candidate in payload["instances"]]}

        result = resolve_ingredients_by_sp([row], "test-engine", fake_engine)[0]

        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0][0]["instances"]), 2)
        self.assertEqual(result["ingredients"], [["Sausage", 1], ["Oil", 2]])
        self.assertNotIn("ingredients", result["ocr_missing"])
        self.assertEqual(result["ingredients_resolved_by"], "sp_exact")

    def test_sp_resolution_keeps_review_when_more_than_one_candidate_matches(self):
        row = {"species": "TOTODILE", "level": 20, "nature": "Hardy", "sp": 558,
               "main_skill": "Charge Strength S", "skill_level": 1, "subskills": [],
               "ingredient_options": [{"level": 1, "choices": [["Sausage", 1], ["Oil", 1]]}],
               "ocr_missing": ["ingredients"]}

        def fake_engine(payload, command):
            return {"results": [{"uid": x["uid"], "match": True, "diff": 0}
                                for x in payload["instances"]]}

        resolve_ingredients_by_sp([row], runner=fake_engine)
        self.assertIn("ingredients", row["ocr_missing"])
        self.assertNotIn("ingredients_resolved_by", row)

    def test_sp_resolution_keeps_the_shared_active_prefix(self):
        row = {"species": "TOTODILE", "level": 30, "nature": "Hardy", "sp": 558,
               "main_skill": "Charge Strength S", "skill_level": 1, "subskills": [],
               "ingredients": [["Sausage", 1], ["Sausage", 2], ["Sausage", 4]],
               "ingredient_options": [
                   {"level": 1, "choices": [["Sausage", 1]]},
                   {"level": 30, "choices": [["Sausage", 2], ["Oil", 2]]},
                   {"level": 60, "choices": [["Sausage", 4], ["Oil", 3]]}],
               "ocr_missing": ["ingredients"]}

        def fake_engine(payload, command):
            return {"results": [{"uid": x["uid"],
                                  "match": x["instance"]["ingredients"][1][0] == "Oil",
                                  "diff": 0 if x["instance"]["ingredients"][1][0] == "Oil" else 1}
                                 for x in payload["instances"]]}

        resolve_ingredients_by_sp([row], runner=fake_engine)

        self.assertEqual(row["ingredients"], [["Sausage", 1], ["Oil", 2], ["Sausage", 4]])
        self.assertEqual(row["ingredient_slots_resolved_by_sp"], [0, 1])
        self.assertIn("ingredients", row["ocr_missing"])
        self.assertNotIn("ingredients_resolved_by", row)

    def test_vision_ingest_skips_one_failed_image_and_batches_sp_resolution(self):
        inbox = Path(self.tmp.name) / "inbox"
        inbox.mkdir()
        for name in ("bad.png", "good.png"):
            (inbox / name).write_bytes(b"sample")
        good = {"species": "BULBASAUR", "sp": 513, "ocr_missing": ["ingredients"]}
        progress = []
        with (patch("pokesleep_box.ocr.scan",
                    side_effect=[RuntimeError("nilError"), [good]]) as scan_mock,
              patch("pokesleep_box.ocr.resolve_ingredients_by_sp",
                    side_effect=lambda rows: rows) as resolve_mock,
              patch("pokesleep_box.ocr.resolve_species_variant_by_sp",
                    side_effect=lambda rows: rows) as variant_mock):
            rows = ingest_path(inbox, Path(self.tmp.name) / "frames", vision=True,
                               on_progress=progress.append)

        self.assertEqual(rows, [good])
        self.assertEqual(scan_mock.call_count, 2)
        self.assertEqual(resolve_mock.call_count, 1)
        self.assertEqual(variant_mock.call_count, 1)
        self.assertTrue(any("bad.png: 失敗" in message for message in progress))

    def test_text_identical_species_variant_is_resolved_by_sp(self):
        row = {"species": "WOOPER", "level": 14, "nature": "Docile", "sp": 416,
               "main_skill": "Charge Energy S", "skill_level": 1, "subskills": [],
               "ingredients": [["Mushroom", 2]], "ocr_missing": ["ingredients"]}
        metadata = {
            "WOOPER": {"berry": "ORAN", "ingredients": [
                {"level": 1, "choices": [["Mushroom", 2]]}]},
            "WOOPER_PALDEAN": {"berry": "CHESTO", "ingredients": [
                {"level": 1, "choices": [["Cacao", 2]]}]}}

        def fake_engine(payload, command):
            return {"results": [{"uid": x["uid"],
                                  "match": x["instance"]["species"] == "WOOPER_PALDEAN",
                                  "diff": 0 if x["instance"]["species"] == "WOOPER_PALDEAN" else 20}
                                 for x in payload["instances"]]}

        resolve_species_variant_by_sp([row], pokemon_data=metadata, runner=fake_engine)

        self.assertEqual(row["species"], "WOOPER_PALDEAN")
        self.assertEqual(row["species_resolved_by"], "sp_exact")
        self.assertEqual(row["ingredients"], [["Cacao", 2]])

    def test_contained_vocabulary_names_always_resolve_to_the_longest_name(self):
        table = names()
        for category in ("species", "natures", "mainskills", "berries", "ingredients", "subskills"):
            values = table[category]
            for expected, long_name in values.items():
                compact_long = long_name.replace(" ", "")
                contained = [short for key, short in values.items()
                             if key != expected and short.replace(" ", "") in compact_long]
                if contained:
                    with self.subTest(category=category, name=long_name):
                        match, ambiguous = _best_match([long_name], category, .72)
                        self.assertEqual(match[0], expected)
                        self.assertFalse(ambiguous)

    def test_area_bonus_is_applied_once_to_each_kind_of_forecast(self):
        settings = {"areaBonusByIsland": {"シアンの砂浜": 50}}
        items = [dict(self.items[0], uid="solo", verified=True, energy_scores={
            "シアンの砂浜": {"current": {"expected": 1000, "berry": 1000}}})]
        # The engine already applies the island's area bonus to a team plan, so
        # only the additive single-instance score may be scaled here.
        plans = [{"island": "シアンの砂浜", "mode": "current", "total_energy": 1800,
                  "cooking": 0, "synergy_gain": 500, "provisional": False,
                  "members": [{"uid": "solo", "energy": 1800, "berry": 1800,
                               "ingredient": 0, "direct_skill": 0, "marginal": 1800}]}]

        additive = analyze(items, settings)
        team_aware = analyze(items, settings, team_plans=plans)

        cyan = next(x for x in additive["forecasts"] if x["island"] == "シアンの砂浜")
        self.assertEqual(cyan["modes"]["current"]["daily"]["expected"], 1500)
        cyan = next(x for x in team_aware["forecasts"] if x["island"] == "シアンの砂浜")
        self.assertEqual(cyan["modes"]["current"]["daily"]["expected"], 1800)
        self.assertEqual(cyan["modes"]["current"]["synergy_gain"], 500)

    def test_capture_board_survives_a_benchmark_without_usable_energy(self):
        benchmarks = [{"species": "PIKACHU", "species_ja": "ピカチュウ", "specialty": "berry",
                       "berry": "ORAN", "base_species": "PICHU", "base_species_ja": "ピチュー",
                       "base_species_en": "Pichu",
                       "island_scores": {"シアンの砂浜": {"60": {"expected": 0}}}}]
        owned = [{"uid": "u1", "species": "RAICHU", "final_evolution": "RAICHU", "verified": True,
                  "absolute_score": 40.0,
                  "energy_scores": {"シアンの砂浜": {"60": {"expected": 10}}}}]
        encounters = {"fields": {"シアンの砂浜": {"うとうと": ["Pichu"]}}}

        result = analyze(owned, {}, benchmarks, encounters=encounters)

        self.assertIn("by_island", result["capture"])

    def test_the_most_recent_evaluation_wins_over_an_older_engine_run(self):
        import_individuals(self.db, [dict(self.items[0], scores={
            "50": {"berry": 10.0}, "60": {"berry": 10.0},
            "70": {"berry": 10.0}, "80": {"berry": 10.0}})])
        uid = self.db.execute("SELECT uid FROM individual").fetchone()["uid"]
        for anchor in (50, 60, 70, 80):
            # A second engine version stores its own row for the same cell,
            # because engine_version is part of evaluation's primary key.
            self.db.execute("INSERT OR REPLACE INTO evaluation VALUES (?,?,?,?,?,?,?,?,?)",
                            (uid, anchor, "berry", 99999.0, None, None, "engine@new", "v2",
                             "2099-01-01T00:00:00+00:00"))
        self.db.commit()

        row = next(x for x in load_dashboard(self.db) if x["uid"] == uid)

        self.assertEqual(row["evaluations"][50]["berry"], 99999.0)

    def test_audit_lists_unverified_individuals_next_to_low_confidence_ones(self):
        path = Path(self.tmp.name) / "audit.md"

        audit([{"confidence": .5, "verified": True, "species": "A", "box_index": 1},
               {"confidence": 1.0, "verified": False, "species": "B", "box_index": 2}], path)

        self.assertIn("BOX 2 / B", path.read_text(encoding="utf-8"))

    def test_restore_accepts_a_backup_written_before_a_table_existed(self):
        path = Path(self.tmp.name) / "old-backup.json"
        path.write_text(json.dumps({"format": "sleepbox-compass-backup-v1",
                                    "tables": {"individual": []}}), encoding="utf-8")

        restore_backup(self.db, path)

        self.assertEqual(self.db.execute("SELECT count(*) FROM individual").fetchone()[0], 0)

    def test_rendered_page_carries_the_saved_review_confirmation(self):
        import_individuals(self.db, [dict(self.items[0], review_confirmed=True)])
        out = Path(self.tmp.name) / "site"

        render_site(load_dashboard(self.db), out)

        self.assertIn('"review_confirmed": true', (out / "index.html").read_text(encoding="utf-8"))

    def test_frame_interval_must_advance_the_video_clock(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            build_parser().parse_args(["scan", "--interval", "0"])


if __name__ == "__main__":
    unittest.main()
