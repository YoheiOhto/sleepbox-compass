"""Low-resolution warnings for still images (synthetic images only)."""
import importlib.util
import json
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from pokesleep_box import cli
from pokesleep_box.ingest import (LOW_RESOLUTION_SHORT_SIDE, _header_size, image_size,
                                  low_resolution_size, render_review)
from pokesleep_box.sync import low_resolution_message

sys.path.insert(0, str(Path(__file__).parent))
import test_sync  # noqa: E402
from test_sync import scanned  # noqa: E402

HAS_PIL = importlib.util.find_spec("PIL") is not None


def write_png(path: Path, width: int, height: int) -> Path:
    """A valid grayscale PNG built with the standard library only."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    rows = b"".join(b"\x00" + b"\x80" * width for _ in range(height))
    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
    return path


class ImageSizeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_threshold_is_on_the_shorter_side(self):
        small = write_png(self.root / "small.png", 359, 780)
        edge = write_png(self.root / "edge.png", LOW_RESOLUTION_SHORT_SIDE, 1600)
        wide = write_png(self.root / "wide.png", 1600, 700)
        self.assertEqual(image_size(small), (359, 780))
        self.assertEqual(low_resolution_size(small), (359, 780))
        self.assertIsNone(low_resolution_size(edge))
        self.assertEqual(low_resolution_size(wide), (1600, 700))

    def test_videos_and_unreadable_files_are_never_reported(self):
        video = self.root / "clip.mov"
        video.write_bytes(b"fictional")
        broken = self.root / "broken.png"
        broken.write_bytes(b"not an image")
        self.assertIsNone(low_resolution_size(video))
        self.assertIsNone(image_size(broken))
        self.assertIsNone(low_resolution_size(broken))
        self.assertIsNone(low_resolution_size(self.root / "missing.png"))

    def test_header_parser_without_pillow(self):
        png = write_png(self.root / "a.png", 300, 650).read_bytes()
        self.assertEqual(_header_size(png), (300, 650))
        # SOI, an APP0 segment, then SOF0 with height 780 and width 359.
        jpeg = (b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
                + b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, 780, 359, 1) + b"\x01\x11\x00")
        self.assertEqual(_header_size(jpeg), (359, 780))
        self.assertIsNone(_header_size(b"GIF89a"))
        with patch.dict(sys.modules, {"PIL": None}):
            self.assertEqual(image_size(self.root / "a.png"), (300, 650))

    @unittest.skipUnless(HAS_PIL, "Pillow is not installed")
    def test_pillow_jpeg_sizes(self):
        from PIL import Image

        Image.new("RGB", (359, 780)).save(self.root / "shrunk_c.jpeg")
        Image.new("RGB", (1179, 2556)).save(self.root / "original.jpeg")
        self.assertEqual(low_resolution_size(self.root / "shrunk_c.jpeg"), (359, 780))
        self.assertIsNone(low_resolution_size(self.root / "original.jpeg"))
        data = (self.root / "shrunk_c.jpeg").read_bytes()
        self.assertEqual(_header_size(data), (359, 780))

    def test_message_lists_at_most_ten_names(self):
        names = [f"img{i}.jpeg" for i in range(12)]
        text = low_resolution_message(names)
        self.assertIn("低解像度の画像が12枚あります（短い辺が720px未満）", text)
        self.assertIn("img9.jpeg ほか2枚", text)
        self.assertNotIn("img10.jpeg", text)
        self.assertTrue(low_resolution_message(names[:2]).endswith(": img0.jpeg、img1.jpeg"))

    def test_review_page_marks_low_resolution_rows(self):
        page = self.root / "review.html"
        render_review([scanned("a", low_resolution=True)], page)
        text = page.read_text(encoding="utf-8")
        self.assertIn("低解像度の画像", text)
        self.assertIn('"low_resolution": true', text)


class SyncLowResolutionTests(test_sync.SyncTests):
    """Runs run_sync with the fakes of ``SyncTests`` and real synthetic images."""

    def fake_ingest(self, path, frames, ocr_command=None, vision=False, interval=.8,
                    on_progress=None, qwen=None, skip_hashes=(), on_source_done=None):
        self.calls.append("scan")
        if "hash-1" in skip_hashes:
            return []
        on_source_done(path / "small_c.png", "hash-1", 1)
        on_source_done(path / "large.png", "hash-2", 1)
        on_source_done(path / "clip.mov", "hash-3", 0)
        return [scanned("a", ocr_sources=["small_c.png"]),
                scanned("b", ocr_sources=["large.png"])]

    def test_low_resolution_is_warned_but_still_read(self):
        write_png(self.paths.inbox / "small_c.png", 359, 780)
        write_png(self.paths.inbox / "large.png", 1179, 1200)
        (self.paths.inbox / "clip.mov").write_bytes(b"fictional")
        messages = []
        from pokesleep_box.sync import run_sync
        summary = run_sync(self.paths.inbox, self.paths, {}, ocr="vision",
                           on_progress=messages.append)
        self.assertEqual(summary["new_sources"], 3)
        self.assertEqual(summary["new_individuals"], 2)
        self.assertEqual(summary["low_resolution"], 1)
        self.assertEqual([m for m in messages if "低解像度" in m],
                         ["small_c.png: 低解像度の画像です（359x780、短い辺が720px未満）。"
                          "読み間違いが増えます"])
        self.assertIn(low_resolution_message(["small_c.png"]), summary["messages"])
        self.assertIn("低解像度の画像が1枚あります", cli.format_summary(summary, self.paths.review))
        page = self.paths.review.read_text(encoding="utf-8")
        rows = json.loads(page.split("const rows=", 1)[1].split(",ORIG=", 1)[0])
        flags = {row["uid"]: row.get("low_resolution", False) for row in rows}
        self.assertEqual(flags, {"capture-a": True, "capture-b": False})

    def test_no_low_resolution_adds_no_message(self):
        write_png(self.paths.inbox / "small_c.png", 1179, 2556)
        write_png(self.paths.inbox / "large.png", 1179, 2556)
        summary = self.sync()
        self.assertEqual(summary["low_resolution"], 0)
        self.assertFalse(any("低解像度" in m for m in summary["messages"]))


# Keep only this module's tests; the inherited ones already run in test_sync.
for _name in [n for n in vars(test_sync.SyncTests) if n.startswith("test_")]:
    setattr(SyncLowResolutionTests, _name, None)


class ScanCommandLowResolutionTests(unittest.TestCase):
    def test_scan_json_counts_low_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inbox = root / "inbox"
            inbox.mkdir()
            write_png(inbox / "a.png", 359, 780)
            sidecar = {k: v for k, v in scanned("a").items() if k != "uid"}
            (inbox / "a.png.json").write_text(json.dumps(sidecar), encoding="utf-8")
            argv = ["pokesleep-box", "--db", str(root / "box.sqlite"), "scan", str(inbox),
                    "--ocr", "sidecar", "--frames", str(root / "frames"),
                    "--audit", str(root / "frames/audit_report.md"),
                    "--review", str(root / "frames/review.html")]
            with patch.object(sys, "argv", argv), patch("builtins.print") as printed:
                cli.main()
            summary = json.loads(printed.call_args[0][0])
            self.assertEqual((summary["imported"], summary["low_resolution"]), (1, 1))
            warned = [c for c in printed.call_args_list if "低解像度の画像が1枚" in str(c[0][0])]
            self.assertEqual(len(warned), 1)


if __name__ == "__main__":
    unittest.main()
