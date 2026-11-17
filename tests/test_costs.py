import unittest

from pokesleep_box.costs import (candy_exp, cumulative_exp, exp_type, nature_exp_effect,
                                 shards_per_candy, tables, training_cost)


def simulate_candy_by_candy(type_, effect, level, target, boost=False):
    """Independent reference: feed one candy at a time like the game does."""
    candies = shards = 0
    total = cumulative_exp(level, type_)
    goal = cumulative_exp(target, type_)
    current = level
    while current < target:
        total += candy_exp(current, effect, boost)
        shards += shards_per_candy(current, boost)
        candies += 1
        while current < target and total >= cumulative_exp(current + 1, type_):
            current += 1
    return candies, shards, goal - cumulative_exp(level, type_)


class TrainingCostTests(unittest.TestCase):
    def test_tables_have_one_entry_per_level(self):
        data = tables()
        self.assertEqual(len(data["cumulative_exp"]), data["max_level"])
        self.assertEqual(len(data["shards_per_candy"]), data["max_level"] - 1)
        values = data["cumulative_exp"]
        self.assertTrue(all(a < b for a, b in zip(values, values[1:])))

    def test_cumulative_exp_matches_pokebip_table(self):
        # Pokebip "niveau-experience" cumulative EXP columns (normal,
        # particulier=900, special=1080, fabuleux=1320). Rounding the scaled
        # cumulative value (nitoyon/pokesleep-tool) matches every row exactly
        # except 1080 at Lv60 (92687 vs 92688) and Lv70 (147892 vs 147891);
        # the sources disagree there by 1 EXP, so 1080 allows a delta of 1.
        expected = {
            25: (8668, 13002, 15602, 19070),
            30: (11992, 17988, 21586, 26382),
            50: (29993, 44990, 53987, 65985),
            60: (51493, 77240, 92688, 113285),
            66: (69458, 104187, 125024, 152808),
            70: (82162, 123243, 147891, 180756),
        }
        for level, row in expected.items():
            for type_, value in zip((600, 900, 1080, 1320), row):
                self.assertAlmostEqual(cumulative_exp(level, type_), value,
                                       delta=1 if type_ == 1080 else 0, msg=(level, type_))

    def test_single_level_costs_match_game8(self):
        # Game8 JP level table (EXP, candies, shards) for Lv49->50, Lv50->51,
        # Lv59->60 of a normal species; Lv30+ candies give 25 EXP in every version.
        for level, expected in ((49, (1066, 43, 12685)), (50, (1362, 55, 16995)),
                                (59, (2806, 113, 67009))):
            result = training_cost("PIKACHU", "Bashful", level, level + 1)
            self.assertEqual((result["exp"], result["candies"], result["dream_shards"]), expected)

    def test_hand_worked_carry_over(self):
        # Lv1->2 needs 54 EXP: 2 candies x 40 EXP (14 shards each), 26 EXP carries.
        # Lv2->3 needs 71-26=45 EXP: 2 candies (18 shards each).
        result = training_cost("PIKACHU", "Bashful", 1, 3)
        self.assertEqual((result["candies"], result["dream_shards"], result["exp"]), (4, 64, 125))

    def test_lv1_to_30_worked_examples(self):
        # Regression pins computed from the cross-checked tables; they also
        # equal the candy-by-candy reference simulation below.
        cases = {
            ("PIKACHU", "Bashful"): (312, 24072, 11992, 600),
            ("PIKACHU", "Timid"): (266, 20524, 11992, 600),
            ("PIKACHU", "Brave"): (378, 29168, 11992, 600),
            ("DRAGONITE", "Bashful"): (468, 36138, 17988, 900),
        }
        for (species, nature), expected in cases.items():
            result = training_cost(species, nature, 1, 30)
            self.assertEqual((result["candies"], result["dream_shards"], result["exp"],
                              result["exp_type"]), expected, (species, nature))

    def test_matches_candy_by_candy_simulation(self):
        for species, type_ in (("PIKACHU", 600), ("TYRANITAR", 900), ("RAIKOU", 1080), ("MEW", 1320)):
            for nature, effect in (("Bashful", "neutral"), ("Jolly", "up"), ("Sassy", "down")):
                for level, target in ((1, 30), (10, 50), (24, 31), (30, 60), (55, 70)):
                    for boost in (False, True):
                        result = training_cost(species, nature, level, target, mini_boost=boost)
                        expected = simulate_candy_by_candy(type_, effect, level, target, boost)
                        self.assertEqual((result["candies"], result["dream_shards"], result["exp"]),
                                         expected, (species, nature, level, target, boost))

    def test_mini_boost_doubles_exp_and_quadruples_shards(self):
        normal = training_cost("PIKACHU", "Bashful", 30, 31)
        boosted = training_cost("PIKACHU", "Bashful", 30, 31, mini_boost=True)
        # 729 EXP: 30 candies x 25 EXP x 122 shards vs 15 candies x 50 EXP x 488 shards.
        self.assertEqual((normal["candies"], normal["dream_shards"]), (30, 3660))
        self.assertEqual((boosted["candies"], boosted["dream_shards"]), (15, 7320))

    def test_exp_types_and_natures(self):
        self.assertEqual(exp_type("DRAGONITE"), 900)
        self.assertEqual(exp_type("darkrai"), 1320)
        self.assertEqual(exp_type("ENTEI"), 1080)
        self.assertEqual(exp_type("PIKACHU"), 600)
        self.assertIsNone(exp_type("NOT_A_POKEMON"))
        self.assertEqual(nature_exp_effect("Naive"), "up")
        self.assertEqual(nature_exp_effect("Relaxed"), "down")
        self.assertEqual(nature_exp_effect("ようき"), "up")
        self.assertEqual(nature_exp_effect("Rash"), "neutral")
        self.assertIsNone(nature_exp_effect("???"))
        self.assertEqual([candy_exp(level, "up") for level in (24, 25, 30)], [47, 41, 30])
        self.assertEqual([candy_exp(level, "down") for level in (24, 25, 30)], [33, 29, 21])

    def test_edge_cases(self):
        self.assertIsNone(training_cost("NOT_A_POKEMON", "Rash", 1, 30))
        self.assertIsNone(training_cost("PIKACHU", "Rash", 60, 80))
        self.assertIsNone(training_cost("PIKACHU", "Rash", 0, 10))
        same = training_cost("PIKACHU", "Rash", 40, 40)
        self.assertEqual((same["candies"], same["dream_shards"], same["exp"]), (0, 0, 0))
        unknown = training_cost("PIKACHU", None, 1, 30)
        self.assertEqual(unknown["nature_effect"], "neutral")
        self.assertEqual(unknown["candies"], 312)
        self.assertTrue(any("性格" in note for note in unknown["notes"]))


if __name__ == "__main__":
    unittest.main()
