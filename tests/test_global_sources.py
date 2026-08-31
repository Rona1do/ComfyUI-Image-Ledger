from __future__ import annotations

import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "image_ledger_global_tests"
if PACKAGE_NAME not in sys.modules:
    package = types.ModuleType(PACKAGE_NAME)
    package.__path__ = [str(PACKAGE_ROOT)]
    sys.modules[PACKAGE_NAME] = package

sources = importlib.import_module(f"{PACKAGE_NAME}.global_sources")
ledger_module = importlib.import_module(f"{PACKAGE_NAME}.ledger")
settings_module = importlib.import_module(f"{PACKAGE_NAME}.global_settings")

Ledger = ledger_module.Ledger


class SourcePickerTests(unittest.TestCase):
    def test_prefers_first_frame_and_skips_last_frame(self):
        prompt = {
            "23": {
                "class_type": "LoadImage",
                "inputs": {"image": "AI/set-a/1.png"},
                "_meta": {"title": "First-Frame-Image"},
            },
            "24": {
                "class_type": "LoadImage",
                "inputs": {"image": "35.png"},
                "_meta": {"title": "Last-Frame-Image"},
            },
        }
        found = sources.find_source_images_from_prompt(prompt)
        self.assertEqual([item.annotated for item in found], ["AI/set-a/1.png"])
        self.assertTrue(found[0].primary)

    def test_smooth_source_title(self):
        prompt = {
            "287": {
                "class_type": "LoadImage",
                "inputs": {"image": "724 (1).png"},
                "_meta": {"title": "① 选择源图（必填）"},
            },
            "338": {
                "class_type": "LoadImage",
                "inputs": {"image": "694.png"},
            },
        }
        found = sources.find_source_images_from_prompt(prompt)
        self.assertEqual([item.annotated for item in found], ["724 (1).png"])

    def test_untitled_single_loadimage(self):
        prompt = {
            "62": {
                "class_type": "LoadImage",
                "inputs": {"image": "bffa6450.png"},
            }
        }
        found = sources.find_source_images_from_prompt(prompt)
        self.assertEqual([item.annotated for item in found], ["bffa6450.png"])
        self.assertFalse(found[0].primary)

    def test_used_destination_keeps_character_folder(self):
        self.assertEqual(
            sources.used_destination("AI/set-a/1.png"),
            "AI/set-a/_used/1.png",
        )
        self.assertEqual(
            sources.used_destination("AI/illustrations/37.png"),
            "AI/illustrations/_used/37.png",
        )
        self.assertEqual(sources.used_destination("73.png"), "_used/73.png")
        self.assertEqual(
            sources.used_destination("AI/portraits/_used/1.png"),
            "AI/portraits/_used/1.png",
        )

    def test_poor_destination_nests_inside_used(self):
        self.assertEqual(
            sources.poor_destination("AI/portraits/1.png"),
            "AI/portraits/_used/_rejected/1.png",
        )
        self.assertEqual(
            sources.poor_destination("AI/portraits/_used/1.png"),
            "AI/portraits/_used/_rejected/1.png",
        )
        self.assertEqual(
            sources.poor_destination("73.png"),
            "_used/_rejected/73.png",
        )

    def test_source_folder_ignores_used_and_poor(self):
        self.assertEqual(
            sources.source_folder_of("AI/portraits/1.png"),
            "AI/portraits",
        )
        self.assertEqual(
            sources.source_folder_of("AI/portraits/_used/1.png"),
            "AI/portraits",
        )
        self.assertEqual(
            sources.source_folder_of("AI/portraits/_used/效果不佳/1.png"),
            "AI/portraits",
        )
        self.assertEqual(
            sources.category_folder_of("AI/portraits/closeups/1.png"),
            "AI/portraits",
        )
        self.assertEqual(sources.category_folder_of("73.png"), "")
        self.assertEqual(sources.category_folder_of("AI/foo.png"), "")

    def test_skips_clipspace_and_used(self):
        self.assertFalse(sources.is_watched_rel("clipspace/foo.png"))
        self.assertFalse(sources.is_watched_rel("AI/_used/a.png"))
        self.assertTrue(sources.is_watched_rel("AI/a.png"))
        self.assertTrue(sources.is_watched_rel("73.png"))
        self.assertTrue(sources.is_watched_rel("AI/a.png", ["AI"]))
        self.assertFalse(sources.is_watched_rel("73.png", ["AI"]))

    def test_extracts_output_videos_not_temp(self):
        history = {
            "outputs": {
                "1": {
                    "gifs": [
                        {
                            "filename": "preview.mp4",
                            "subfolder": "",
                            "type": "temp",
                        },
                        {
                            "filename": "final.mp4",
                            "subfolder": "video",
                            "type": "output",
                        },
                    ]
                }
            }
        }
        videos = sources.extract_video_outputs(history)
        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0]["filename"], "final.mp4")

    def test_logical_join_rejects_traversal_and_absolute_paths(self):
        root = Path(tempfile.gettempdir()) / "image-ledger-root"
        with self.assertRaises(ValueError):
            sources.logical_join(root, "../outside.png")
        with self.assertRaises(ValueError):
            sources.logical_join(root, "C:/outside.png")

    def test_public_defaults_do_not_move_files(self):
        defaults = settings_module.normalize_settings(None)
        self.assertTrue(defaults["enabled"])
        self.assertFalse(defaults["auto_move"])
        self.assertEqual(defaults["poor_dirname"], "_rejected")


class LibraryMatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ai = Path(self.temp.name) / "AI"
        (self.ai / "portraits").mkdir(parents=True)
        (self.ai / "set-a").mkdir()
        (self.ai / "set-b").mkdir()
        # extra dirs used by copy-suffix test
        (self.ai / "portraits").mkdir(parents=True, exist_ok=True)
        (self.ai / "portraits" / "ComfyUI_temp_sapof_00287_.png").write_bytes(b"real-one")
        (self.ai / "set-a" / "160.png").write_bytes(b"xiao-160")
        (self.ai / "set-b" / "160.png").write_bytes(b"hana-160")

    def tearDown(self):
        self.temp.cleanup()

    def test_hard_move_deletes_source(self):
        src = self.ai / "portraits" / "keep-name.png"
        src.write_bytes(b"payload")
        dest = self.ai / "portraits" / "_used" / "keep-name.png"
        result = sources.hard_move(src, dest)
        self.assertTrue(result.is_file())
        self.assertFalse(src.exists())
        self.assertEqual(result.read_bytes(), b"payload")

    def test_copy_suffix_matches_original_name_by_hash(self):
        index = sources.index_library_by_name(self.ai)
        digest = ledger_module.sha256_file(
            self.ai / "portraits" / "ComfyUI_temp_sapof_00287_.png"
        )
        # Pretend LoadImage copied it to input as "….png (1)" — not needed;
        # numbered 160.png already covers duplicates. Check unsuffix helper.
        self.assertEqual(sources.unsuffixed_filename("235 (1).png"), "235.png")
        self.assertEqual(sources.candidate_filenames("235 (1).png"), ("235 (1).png", "235.png"))
        (self.ai / "portraits" / "235.png").write_bytes(b"real-235")
        (self.ai / "set-a" / "235.png").write_bytes(b"other-235")
        index = sources.index_library_by_name(self.ai)
        digest = ledger_module.sha256_file(self.ai / "portraits" / "235.png")
        hit = sources.pick_library_match(
            "235 (1).png",
            index,
            expected_sha256=digest,
            hash_func=ledger_module.sha256_file,
        )
        self.assertEqual(hit, self.ai / "portraits" / "235.png")

    def test_unique_comfyui_temp_name_maps_to_character_folder(self):
        index = sources.index_library_by_name(self.ai)
        hit = sources.pick_library_match("ComfyUI_temp_sapof_00287_.png", index)
        self.assertEqual(hit, self.ai / "portraits" / "ComfyUI_temp_sapof_00287_.png")

    def test_duplicate_number_name_requires_hash(self):
        index = sources.index_library_by_name(self.ai)
        self.assertIsNone(sources.pick_library_match("160.png", index))
        digest = ledger_module.sha256_file(self.ai / "set-b" / "160.png")
        hit = sources.pick_library_match(
            "160.png",
            index,
            expected_sha256=digest,
            hash_func=ledger_module.sha256_file,
        )
        self.assertEqual(hit, self.ai / "set-b" / "160.png")


class GlobalLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "ledger.sqlite3"
        self.ledger = Ledger(self.db, session_id="global-test")
        self.source = self.root / "input"
        self.source.mkdir()
        (self.source / "a.png").write_bytes(b"image-a")

    def tearDown(self):
        self.temp.cleanup()

    def test_upsert_done_is_idempotent(self):
        first = self.ledger.upsert_done_source(
            campaign=settings_module.GLOBAL_CAMPAIGN,
            sha256="a" * 64,
            rel_path="AI/a.png",
            abs_path=str(self.source / "a.png"),
            source_root=str(self.source),
            output_path=str(self.root / "out.mp4"),
            workflow_name="Smooth",
        )
        (self.root / "out.mp4").write_bytes(b"video")
        second = self.ledger.upsert_done_source(
            campaign=settings_module.GLOBAL_CAMPAIGN,
            sha256="a" * 64,
            rel_path="AI/a.png",
            abs_path=str(self.source / "a.png"),
            source_root=str(self.source),
            output_path=str(self.root / "out.mp4"),
            workflow_name="Smooth",
        )
        self.assertTrue(first["newly_done"])
        self.assertFalse(second["newly_done"])
        status = self.ledger.status(settings_module.GLOBAL_CAMPAIGN)
        self.assertEqual(status["done"], 1)
        self.assertIn("AI/a.png", self.ledger.list_used_rel_paths(settings_module.GLOBAL_CAMPAIGN))

    def test_undo_last_event_returns_to_pending(self):
        self.ledger.upsert_done_source(
            campaign=settings_module.GLOBAL_CAMPAIGN,
            sha256="b" * 64,
            rel_path="b.png",
            abs_path=str(self.source / "a.png"),
            source_root=str(self.source),
        )
        undone = self.ledger.undo_last_global_event(settings_module.GLOBAL_CAMPAIGN)
        self.assertIsNotNone(undone)
        status = self.ledger.status(settings_module.GLOBAL_CAMPAIGN)
        self.assertEqual(status["done"], 0)
        self.assertEqual(status["pending"], 1)

    def test_discover_files_skips_used_folder(self):
        used = self.source / "_used"
        used.mkdir()
        (used / "old.png").write_bytes(b"old")
        nested = self.source / "AI" / "_used"
        nested.mkdir(parents=True)
        (nested / "old2.png").write_bytes(b"old2")
        files = Ledger.discover_files(self.source, recursive=True, extensions=".png")
        names = {path.name for path in files}
        self.assertIn("a.png", names)
        self.assertNotIn("old.png", names)
        self.assertNotIn("old2.png", names)


if __name__ == "__main__":
    unittest.main()
