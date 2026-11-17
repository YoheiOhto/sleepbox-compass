import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pokesleep_box.core import connect, import_individuals, record_synced_sources, synced_sources
from pokesleep_box.ingest import file_sha256, ingest_path
from pokesleep_box.vlm import QwenReader, drop_near_duplicates, merge_reads, parse_response, scan

from pokesleep_box.icons import available as icons_available

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # Pillow ships with the optional `qwen` extra
    Image = None

ANSWER = {
    "screen": "detail", "species": "ポッチャマ", "level": 15, "sp": "503",
    "ingredient_amounts": [1, 1, 4],
    "main_skill": "おてつだいサポートS", "skill_level": 1,
    "subskills": [{"name": "食材確率アップS", "unlock": 25},
                  {"name": "おてつだいスピードS", "unlock": 25},
                  {"name": "リサーチEXPボーナス", "unlock": 50},
                  {"name": "最大所持数アップM", "unlock": 70},
                  {"name": "きのみの数S", "unlock": 80}],
    "nature": "しんちょう",
}
# Lv60 has two options with amount 4, so that slot stays provisional.
METADATA = {"PIPLUP": {"berry": "ORAN", "ingredients": [
    {"level": 1, "choices": [["Egg", 1]]},
    {"level": 30, "choices": [["Egg", 2], ["Leek", 1]]},
    {"level": 60, "choices": [["Honey", 4], ["Egg", 4], ["Leek", 2]]}]}}


def answer(**changes):
    return "```json\n" + json.dumps(dict(ANSWER, **changes), ensure_ascii=False) + "\n```"


class FakeReader(QwenReader):
    """QwenReader whose model is replaced by canned answers keyed by file content."""

    def __init__(self, answers, cache_dir=None):
        super().__init__("fake/model", cache_dir)
        self.answers = answers

    def generate_text(self, image):
        return self.answers[Path(image).read_bytes()]


class VLMTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_response_is_mapped_to_canonical_keys(self):
        row = parse_response(answer(nature="しんちよう"))  # small misread is snapped
        self.assertEqual((row["species"], row["sp"], row["nature"], row["main_skill"]),
                         ("PIPLUP", 503, "Careful", "Extra Helpful S"))
        self.assertEqual(row["subskills"][3], ["Inventory Up M", 70])
        self.assertEqual(parse_response('{"screen": "other"}'), {"screen": "other"})
        self.assertEqual(parse_response("no json")["screen"], "unreadable")

    def test_rough_video_frames_are_merged_per_individual(self):
        full = parse_response(answer())
        # Scrolled frame: header still visible, upper fields hidden.
        lower = parse_response(answer(level=None, ingredient_amounts=None, main_skill=None))
        # Mid-scroll frame without a readable header joins the previous individual.
        headless = parse_response(answer(species=None, sp=None, nature="なまいき"))
        other = parse_response('{"screen": "other"}')
        second = parse_response(answer(species="ゴース", sp=548, subskills=[None] * 5))
        rows = merge_reads([other, full, lower, headless, second, full])
        self.assertEqual([(r["species"], r["sp"]) for r in rows], [("PIPLUP", 503), ("GASTLY", 548)])
        piplup = rows[0]
        self.assertEqual(piplup["ocr_frames"], 4)  # revisiting adds evidence, not a duplicate
        self.assertEqual(piplup["nature"], "Careful")  # 3 votes beat 1 misread
        self.assertEqual([s[1] for s in piplup["subskills"]], [10, 25, 50, 70, 80])
        self.assertEqual(piplup["ingredient_amounts"], [1, 1, 4])
        self.assertNotIn("subskills", rows[1])  # never invent unseen slots
        self.assertEqual(piplup["subskills"][0][1], 10)  # misplaced badge on slot 1 is ignored

    @unittest.skipUnless(Image, "Pillow is not installed")
    def test_repeated_frames_are_skipped_but_similar_individuals_are_kept(self):
        def frame(name, sp_text, noise=0):
            image = Image.new("L", (300, 650), 240)
            draw = ImageDraw.Draw(image)
            draw.rectangle((20, 200, 280, 600), fill=200)  # same layout for every individual
            draw.text((100, 30), sp_text, fill=noise or 20, font=ImageFont.load_default(size=28))
            path = self.root / name
            image.save(path)
            return path

        frames = [frame("1.png", "SP 548  Gastly"), frame("2.png", "SP 548  Gastly", 25),
                  frame("3.png", "SP 617  Haunter")]
        self.assertEqual([f.name for f in drop_near_duplicates(frames)], ["1.png", "3.png"])

    def test_reads_are_cached_by_image_hash(self):
        image = self.root / "a.jpg"
        image.write_bytes(b"image-a")
        cache = self.root / "cache"
        reader = FakeReader({b"image-a": answer()}, cache)
        with patch("pokesleep_box.ocr.load_source_metadata", return_value=METADATA), \
                patch("pokesleep_box.engine.run_engine", side_effect=RuntimeError):
            first = scan(image, reader, self.root / "frames")
            second = scan(image, FakeReader({}, cache), self.root / "frames")
        self.assertEqual((reader.reads, reader.cache_hits), (1, 0))
        self.assertEqual(first, second)
        row = first[0]
        # Lv1 and Lv30 are fixed by their amounts; Lv60 (Honey 4 or Egg 4)
        # keeps an amount-consistent guess and is flagged for review.
        self.assertEqual(row["ingredients"][:2], [["Egg", 1], ["Leek", 1]])
        self.assertEqual(row["ingredients"][2][1], 4)
        self.assertIn("ingredients", row["ocr_missing"])
        self.assertLess(row["confidence"], .995)  # VLM reads always need review

    def test_unresolved_middle_slot_does_not_discard_later_slots(self):
        from pokesleep_box.ocr import enrich_with_species_data
        metadata = {"TINKATINK": {"berry": "MAGO", "ingredients": [
            {"level": 1, "choices": [["Tomato", 1]]},
            {"level": 30, "choices": [["Tomato", 2], ["Cacao", 2]]},
            {"level": 60, "choices": [["Tomato", 4], ["Cacao", 3], ["Potato", 2]]}]}}
        row = enrich_with_species_data([{"species": "TINKATINK", "ingredient_amounts": [1, 2, 3],
                                         "ocr_missing": ["ingredients"]}], pokemon_data=metadata)[0]
        # Lv30 is ambiguous (provisional guess with amount 2); Lv60 x3 is Cacao.
        self.assertEqual(row["ingredients"], [["Tomato", 1], ["Tomato", 2], ["Cacao", 3]])
        self.assertIn("ingredients", row["ocr_missing"])

    def test_amounts_shifted_by_one_slot_are_realigned(self):
        from pokesleep_box.ocr import align_amounts
        options = [{"choices": [["Herb", 2]]}, {"choices": [["Herb", 5], ["Mushroom", 4]]},
                   {"choices": [["Herb", 7], ["Mushroom", 6], ["Tomato", 7]]}]
        self.assertEqual(align_amounts([None, 2, 5], options), [2, 5])  # first slot skipped
        self.assertEqual(align_amounts([1, 2, 5], options), [2, 5])  # berry x1 read first
        self.assertEqual(align_amounts([2, 5, 6], options), [2, 5, 6])  # already aligned
        self.assertEqual(align_amounts([2, 4, 7], options), [2, 4, 7])

    def test_icon_names_resolve_slots_that_amounts_cannot(self):
        from pokesleep_box.ocr import resolve_ingredient_slots
        options = METADATA["PIPLUP"]["ingredients"]
        # Lv60 x4 fits Honey and Egg; the matched icon decides.
        confident, _ = resolve_ingredient_slots(options, [1, 1, 4], [None, None, "Egg"])
        self.assertEqual(confident, [["Egg", 1], ["Leek", 1], ["Egg", 4]])
        # An unread amount is filled from the icon; Lv1 has a single option.
        confident, _ = resolve_ingredient_slots(options, [None, 1, None], [None, None, "Honey"])
        self.assertEqual(confident, [["Egg", 1], ["Leek", 1], ["Honey", 4]])
        # Icon and amount disagree: keep it for review.
        confident, provisional = resolve_ingredient_slots(options, [1, 1, 4], [None, None, "Leek"])
        self.assertIsNone(confident[2])
        self.assertEqual(provisional[2][1], 4)

    @unittest.skipUnless(Image and icons_available(), "ingredient sprites are not installed")
    def test_icons_are_matched_on_a_rendered_screen(self):
        from pokesleep_box import icons
        width, height = 602, 1306
        screen = Image.new("RGB", (width, height), (255, 255, 255))
        row = .14
        for name, fx, alpha in (("egg", icons.SLOT_X[0], 255), ("leek", icons.SLOT_X[1], 110),
                                ("honey", icons.SLOT_X[2], 110)):  # locked slots look faded
            with Image.open(icons.ICON_DIR / f"{name}.png") as sprite:
                sprite = sprite.convert("RGBA").resize((70, 70))
            faded = sprite.copy()
            faded.putalpha(sprite.getchannel("A").point(lambda a: a * alpha // 255))
            screen.paste(faded, (int(fx * width) - 35, int(row * height) - 35), faded)
        path = self.root / "screen.png"
        screen.save(path)
        label_y = row + icons.ROW_OFFSET
        observations = [{"text": "おてつだい時間", "y": 1 - label_y - .01, "height": .02}]
        names = icons.match_slots(path, METADATA["PIPLUP"]["ingredients"], observations)
        self.assertEqual(names, [None, "Leek", "Honey"])  # Lv1 has one option: no match needed

    def test_current_game_rules(self):
        from pokesleep_box.localization import base_main_skill, normalize_individual
        from pokesleep_box.ocr import berries_per_help
        # Ver.3.6.0 moved the last two subskill unlocks from Lv75/100 to Lv70/80.
        row = normalize_individual({"species": "PIPLUP", "nature": "Careful",
                                    "main_skill": "Psystrike (Berry Zone)",
                                    "subskills": [["Helping Bonus", 75], ["Berry Finding S", 100]]})
        self.assertEqual([s[1] for s in row["subskills"]], [70, 80])
        self.assertEqual(row["main_skill"], "Berry Zone")
        self.assertEqual(base_main_skill("マジカルフレイム（料理アドバンスS）"), "料理アドバンスS")
        self.assertEqual(parse_response(answer(main_skill="マジカルフレイム（料理アドバンスS）"))["main_skill"],
                         "Cooking Advance S")
        berry_finding = [["Berry Finding S", 25]]
        self.assertEqual(berries_per_help("berry", berry_finding, 30), 3)
        self.assertEqual(berries_per_help("skill", berry_finding, 20), 1)  # not unlocked yet
        self.assertIsNone(berries_per_help(None, berry_finding, 30))

    def test_stored_legacy_unlocks_are_migrated(self):
        path = self.root / "old.sqlite"
        db = connect(path)
        import_individuals(db, [dict(json.loads((Path(__file__).parents[1] /
                                                  "data/example_individuals.json").read_text())["individuals"][0],
                                     uid="legacy")])
        db.execute("UPDATE individual SET subskills_json=? WHERE uid='legacy'",
                   (json.dumps([["Helping Bonus", 10], ["Berry Finding S", 75], ["Inventory Up S", 100]]),))
        db.commit()
        db.close()
        db = connect(path)
        stored = json.loads(db.execute("SELECT subskills_json FROM individual").fetchone()[0])
        self.assertEqual([s[1] for s in stored], [10, 70, 80])
        db.close()

    def test_only_new_captures_are_read_on_the_next_scan(self):
        inbox = self.root / "inbox"
        inbox.mkdir()
        (inbox / "one.jpg").write_bytes(b"one")
        db = connect(self.root / "box.sqlite")
        answers = {b"one": answer(), b"two": answer(species="ゴース", sp=548)}

        def sync():
            done = []
            reader = FakeReader(answers)
            with patch("pokesleep_box.ocr.load_source_metadata", return_value=METADATA), \
                    patch("pokesleep_box.engine.run_engine", side_effect=RuntimeError):
                rows = ingest_path(inbox, self.root / "frames", qwen=reader,
                                   skip_hashes=synced_sources(db),
                                   on_source_done=lambda s, h, n: done.append((h, s.name, "qwen", n)))
            import_individuals(db, rows)
            record_synced_sources(db, done)
            return reader.reads, rows

        self.assertEqual(sync()[0], 1)
        (inbox / "two.jpg").write_bytes(b"two")
        reads, rows = sync()
        self.assertEqual(reads, 1)  # one.jpg is skipped
        self.assertEqual([r["species"] for r in rows], ["GASTLY"])
        self.assertEqual(synced_sources(db), {file_sha256(inbox / "one.jpg"),
                                              file_sha256(inbox / "two.jpg")})
        self.assertEqual(db.execute("SELECT count(*) FROM individual").fetchone()[0], 2)
        db.close()


if __name__ == "__main__":
    unittest.main()
