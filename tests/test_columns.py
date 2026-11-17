import json
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from pokesleep_box.columns import (DEFAULT_DATA_DIR, Article, Column, Section, discover,
                                   generate_columns, load_documents, render_columns)
from pokesleep_box.columns.article import render_article, render_index
from pokesleep_box.columns.page import CSS, document, table
from pokesleep_box.columns.svg import bar_chart, fmt_man, legend, line_chart, nice_ticks
from pokesleep_box.render import render_site

SVG_RE = re.compile(r"<svg\b.*?</svg>", re.S)


def svgs(page: str):
    return [ET.fromstring(block) for block in SVG_RE.findall(page)]


class SvgHelperTests(unittest.TestCase):
    def test_formatters_and_ticks(self):
        self.assertEqual(fmt_man(1141655), "114.2万")
        self.assertEqual(fmt_man(200000), "20万")
        self.assertEqual(fmt_man(9440), "9,440")
        ticks = nice_ticks(1141655)
        self.assertEqual(ticks[0], 0)
        self.assertGreaterEqual(ticks[-1], 1141655)
        self.assertEqual(nice_ticks(178, low=100)[0], 100)

    def test_line_chart_is_valid_accessible_svg(self):
        svg = line_chart([{"name": "A<&>", "points": [(50, 100), (60, 130), (70, 170)], "slot": 2}],
                         title="伸び", desc="説明", x_label="レベル", y_label="Lv50=100",
                         y_min=100, markers=[(60, "Lv60"), (70, "Lv70")])
        root = ET.fromstring(svg)
        ns = "{http://www.w3.org/2000/svg}"
        self.assertEqual(root.get("role"), "img")
        self.assertEqual(root.find(f"{ns}title").text, "伸び")
        self.assertEqual(root.find(f"{ns}desc").text, "説明")
        self.assertIn("A<&>", svg.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&"))
        self.assertEqual(len(root.findall(f"{ns}circle")), 3)
        # Every data point carries its value for hover.
        self.assertTrue(all(c.find(f"{ns}title") is not None for c in root.findall(f"{ns}circle")))
        # The Lv70 marker is at the right edge, so its label is anchored to the end.
        labels = [t for t in root.iter(f"{ns}text") if t.get("class") == "marker-label"]
        self.assertEqual([t.get("text-anchor") for t in labels], ["start", "end"])

    def test_bar_chart_highlight_ticks_and_values(self):
        svg = bar_chart(["a", "b", "c"], [{"name": "x", "values": [1, 5, 2], "slot": 1}],
                        title="t", desc="d", y_label="y", highlight=[1], tick_labels=["a", "", "c"])
        root = ET.fromstring(svg)
        ns = "{http://www.w3.org/2000/svg}"
        rects = root.findall(f"{ns}rect")
        self.assertEqual([r.get("class") for r in rects], ["bar s1", "bar s1 hi", "bar s1"])
        ticks = [t.text for t in root.iter(f"{ns}text") if t.get("class") == "tick"]
        self.assertIn("a", ticks)
        self.assertNotIn("b", ticks)
        values = [t.text for t in root.iter(f"{ns}text") if t.get("class") == "value"]
        self.assertEqual(values, ["1", "5", "2"])
        # Taller value -> smaller y (higher bar).
        self.assertLess(float(rects[1].get("y")), float(rects[0].get("y")))

    def test_legend_and_table_escape(self):
        self.assertIn("swatch s3", legend([{"name": "<b>", "slot": 3}]))
        self.assertNotIn("<b>", legend([{"name": "<b>", "slot": 3}]))
        html = table(["a", "b"], [["x", 1], ["y", 2]], numeric=[1], highlight=[1])
        self.assertIn('<tr class=hi><td>y</td><td class=num>2</td></tr>', html)

    def test_page_reuses_site_tokens_with_dark_mode(self):
        self.assertIn("--brand:#13774e", CSS)
        self.assertIn("@media (prefers-color-scheme:dark){:root{--bg:#0e1411", CSS)
        self.assertIn("--series-1:#3987e5", CSS)


FIXTURE = {
    "schema": 1, "slug": "fixture", "title": "テスト？",
    "provenance": {"generated_on": "2026-01-01", "generator": "tests", "game_version": "3.6.0",
                   "engine": None, "sources": ["テストの表"]},
    "assumptions": ["前提A"], "data": {"values": [3, 9]},
}


def fixture_build(doc):
    values = doc["data"]["values"]
    return Article(takeaway=f"最大は{max(values)}", conclusion=[f"答えは{sum(values)}"],
                   sections=[Section("図", bar_chart(["a", "b"], [{"name": "v", "values": values}],
                                                     title="図", desc="d", y_label="y"))],
                   how_to_read=["見方"], limits=["限界B"])


class RendererTests(unittest.TestCase):
    def test_article_layout_answer_first_then_limits_and_provenance(self):
        column = Column("fixture", 1, "テスト？", generate=lambda ctx: {}, build=fixture_build)
        body = render_article(column, FIXTURE, fixture_build(FIXTURE))
        order = [body.index(s) for s in ("テスト？", "結論", "答えは12", "<svg", "図と表の見方",
                                         "前提A", "限界B", "データの出どころ", "テストの表")]
        self.assertEqual(order, sorted(order))
        self.assertIn("使っていません", body)  # no engine recorded
        page = document("テスト？", body, [("戻る", "../index.html")])
        self.assertTrue(page.startswith("<!doctype html>"))
        self.assertNotIn("<script", page)
        self.assertEqual(len(svgs(page)), 1)
        index = render_index([(column, fixture_build(FIXTURE))])
        self.assertIn('href="fixture.html"', index)
        self.assertIn("最大は9", index)
        self.assertIn("まだコラムがありません", render_index([]))


class CommittedColumnTests(unittest.TestCase):
    def test_columns_are_discovered_with_unique_slugs(self):
        slugs = [c.slug for c in discover()]
        self.assertEqual(len(slugs), len(set(slugs)))
        self.assertTrue({"shard-jumps", "lv60-worth", "exp-types"} <= set(slugs))

    def test_every_column_has_committed_data_and_renders(self):
        docs = dict((c.slug, d) for c, d in load_documents(DEFAULT_DATA_DIR))
        self.assertEqual(set(docs), {c.slug for c in discover()})
        with tempfile.TemporaryDirectory() as tmp:
            written = render_columns(Path(tmp), DEFAULT_DATA_DIR)
            self.assertEqual(len(written), len(docs) + 1)
            for path in written:
                page = path.read_text(encoding="utf-8")
                self.assertNotIn("<script", page)
                for bad in ("nan", "None", "inf"):
                    self.assertIsNone(re.search(rf">[^<]*\b{bad}\b", page), (path.name, bad))
                for svg in svgs(page):
                    self.assertEqual(svg.get("role"), "img")
            for slug in docs:
                page = (Path(tmp) / f"{slug}.html").read_text(encoding="utf-8")
                self.assertIn("結論", page)
                self.assertIn("データの出どころ", page)
                self.assertGreaterEqual(len(svgs(page)), 1)
                self.assertIn(docs[slug]["provenance"]["generated_on"], page)

    def test_committed_data_matches_cost_tables(self):
        # Columns that need no engine are regenerated here and must equal the
        # committed JSON, so a cost-table change cannot leave stale pages.
        offline = [c.slug for c in discover() if not c.uses_engine]
        with tempfile.TemporaryDirectory() as tmp:
            for path in generate_columns(Path(tmp), only=offline, today="ignored"):
                fresh = json.loads(path.read_text(encoding="utf-8"))
                committed = json.loads((DEFAULT_DATA_DIR / path.name).read_text(encoding="utf-8"))
                for doc in (fresh, committed):
                    doc["provenance"].pop("generated_on")
                self.assertEqual(fresh, committed, path.name)

    def test_lv60_numbers_are_consistent(self):
        doc = json.loads((DEFAULT_DATA_DIR / "lv60-worth.json").read_text(encoding="utf-8"))
        self.assertTrue(doc["provenance"]["engine"].startswith("nerolis-lab@"))
        for row in doc["data"]["species"]:
            energy = row["energy"]
            for level in map(str, doc["data"]["levels"]):
                parts = energy[level]
                self.assertLessEqual(abs(parts["berry"] + parts["ingredient"] + parts["direct_skill"]
                                         - parts["expected"]), 2)
            self.assertGreater(energy["70"]["expected"], energy["60"]["expected"])
            self.assertGreater(energy["60"]["expected"], energy["50"]["expected"])
            if row["specialty"] == "ingredient":
                # Third ingredient slot opens at Lv60.
                self.assertGreater(energy["60"]["ingredient"], energy["59"]["ingredient"] * 1.15)

    def test_unknown_column_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            generate_columns(Path(tmp), only=["no-such-column"])


class SiteLinkTests(unittest.TestCase):
    def test_site_links_and_writes_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            render_site([], Path(tmp))
            index = (Path(tmp) / "index.html").read_text(encoding="utf-8")
            self.assertIn('<a class="tab-link" href="columns/index.html">コラム', index)
            self.assertTrue((Path(tmp) / "columns" / "index.html").exists())
            self.assertTrue((Path(tmp) / "columns" / "shard-jumps.html").exists())


if __name__ == "__main__":
    unittest.main()
