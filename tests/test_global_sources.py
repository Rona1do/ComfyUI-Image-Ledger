from __future__ import annotations

import importlib
import os
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
    def test_source_title_matches_panel_language(self):
        self.assertTrue(sources.is_primary_title("源图"))
        self.assertTrue(sources.is_primary_title("原图"))
        self.assertTrue(sources.is_primary_title("First Frame"))
        self.assertTrue(sources.is_skip_title("Last Frame"))
        self.assertTrue(sources.is_skip_title("遮罩"))

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


class StageLastRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.input_root = Path(self.temp.name) / "input"
        self.category = "AI/portraits"
        used = self.input_root / "AI" / "portraits" / "_used"
        used.mkdir(parents=True)
        self.filename = "ComfyUI_temp_kkavg_00132_.png"
        self.used_file = used / self.filename
        self.used_file.write_bytes(b"payload-132")
        self.tracker = importlib.import_module(f"{PACKAGE_NAME}.global_tracker")
        self._orig_input_root = self.tracker._input_root
        self._orig_ledger = self.tracker._ledger
        self._orig_last_result = self.tracker._LAST_RESULT
        self._orig_pick_folder = self.tracker._LAST_PICK_FOLDER
        self._orig_current_pick = self.tracker._CURRENT_PICK
        self._orig_data_dir = os.environ.get("COMFYUI_IMAGE_LEDGER_DIR")
        os.environ["COMFYUI_IMAGE_LEDGER_DIR"] = str(Path(self.temp.name) / "ledger-data")
        self.tracker._input_root = lambda: self.input_root
        self.db = Path(self.temp.name) / "ledger.sqlite3"
        self.ledger = Ledger(self.db, session_id="rerun-test")
        self.tracker._ledger = lambda: self.ledger
        self.tracker._LAST_RESULT = None
        self.tracker._LAST_PICK_FOLDER = self.category
        self.tracker._CURRENT_PICK = ""

    def tearDown(self):
        self.tracker._input_root = self._orig_input_root
        self.tracker._ledger = self._orig_ledger
        self.tracker._LAST_RESULT = self._orig_last_result
        self.tracker._LAST_PICK_FOLDER = self._orig_pick_folder
        self.tracker._CURRENT_PICK = self._orig_current_pick
        if self._orig_data_dir is None:
            os.environ.pop("COMFYUI_IMAGE_LEDGER_DIR", None)
        else:
            os.environ["COMFYUI_IMAGE_LEDGER_DIR"] = self._orig_data_dir
        self.temp.cleanup()

    def _record_used(self):
        self.ledger.upsert_done_source(
            campaign=settings_module.GLOBAL_CAMPAIGN,
            sha256="c" * 64,
            rel_path=f"{self.category}/{self.filename}",
            abs_path=str(self.used_file),
            source_root=str(self.input_root),
            moved_to=f"{self.category}/_used/{self.filename}",
        )

    def test_locate_existing_finds_used_copy(self):
        located = self.tracker._locate_existing(
            f"{self.category}/{self.filename}",
            self.input_root,
            "_used",
            "_rejected",
        )
        self.assertIsNotNone(located)
        self.assertEqual(
            located[0].replace("\\", "/"),
            f"{self.category}/_used/{self.filename}",
        )

    def test_locate_existing_finds_used_copy_from_basename(self):
        located = self.tracker._locate_existing(
            self.filename,
            self.input_root,
            "_used",
            "_rejected",
        )
        self.assertIsNotNone(located)
        self.assertEqual(
            located[0].replace("\\", "/"),
            f"{self.category}/_used/{self.filename}",
        )

    def test_locate_existing_finds_legacy_rejected_folder(self):
        legacy = self.input_root / "AI" / "portraits" / "_used" / "效果不佳"
        legacy.mkdir()
        (legacy / "old.png").write_bytes(b"old")
        located = self.tracker._locate_existing(
            "old.png",
            self.input_root,
            "_used",
            "_rejected",
        )
        self.assertIsNotNone(located)
        self.assertEqual(
            located[0].replace("\\", "/"),
            "AI/portraits/_used/效果不佳/old.png",
        )

    def test_stage_last_run_from_ledger_moved_to(self):
        self.tracker._LAST_PICK_FOLDER = ""
        self._record_used()
        result = self.tracker.stage_last_run()
        self.assertTrue(result.get("ok"), result)
        self.assertTrue(result.get("rerun"))
        self.assertTrue(result.get("from_used"))
        self.assertEqual(
            str(result.get("load_name") or "").replace("\\", "/"),
            f"{self.category}/_used/{self.filename}",
        )
        self.assertTrue(self.used_file.is_file())

    def test_stage_last_run_ignores_missing_hint(self):
        self._record_used()
        result = self.tracker.stage_last_run("missing.png")
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(
            str(result.get("load_name") or "").replace("\\", "/"),
            f"{self.category}/_used/{self.filename}",
        )

    def test_stage_last_run_accepts_absolute_path_inside_input(self):
        result = self.tracker.stage_last_run(str(self.used_file))
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(
            str(result.get("load_name") or "").replace("\\", "/"),
            f"{self.category}/_used/{self.filename}",
        )

    def test_stage_last_run_rejects_path_outside_input(self):
        outside = Path(self.temp.name) / "secret.png"
        outside.write_bytes(b"secret")
        result = self.tracker.stage_last_run(str(outside))
        self.assertFalse(result.get("ok"))
        self.assertNotIn("secret.png", str(result.get("load_name") or ""))
        result = self.tracker.stage_last_run("../secret.png")
        self.assertFalse(result.get("ok"))
        self.assertNotIn("secret.png", str(result.get("load_name") or ""))

    def test_stage_last_run_without_history(self):
        self.tracker._LAST_PICK_FOLDER = ""
        result = self.tracker.stage_last_run()
        self.assertFalse(result.get("ok"))
        self.assertIn("error", result)

    def test_stage_last_run_returns_category(self):
        self._record_used()
        result = self.tracker.stage_last_run()
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("folder"), self.category)


class LibraryFolderTests(unittest.TestCase):
    def test_normalize_library_folder(self):
        normalize = settings_module.normalize_library_folder
        self.assertEqual(normalize("AI"), "AI")
        self.assertEqual(normalize("\\Pictures\\AI\\"), "Pictures/AI")
        self.assertEqual(normalize("./Sources/./set"), "Sources/set")
        self.assertEqual(normalize("../outside"), "")
        self.assertEqual(normalize("C:/Users"), "")
        self.assertEqual(normalize(""), "")

    def test_unsafe_library_setting_keeps_default(self):
        cfg = settings_module.normalize_settings({"library_folder": "../../etc"})
        self.assertEqual(cfg["library_folder"], "AI")
        cfg = settings_module.normalize_settings({"library_folder": "Sources"})
        self.assertEqual(settings_module.library_folder(cfg), "Sources")

    def test_category_for_custom_and_nested_library(self):
        category = sources.category_folder_of
        self.assertEqual(category("Sources/cats/1.png", library_folder="Sources"), "Sources/cats")
        self.assertEqual(category("Sources/cats/_used/1.png", library_folder="Sources"), "Sources/cats")
        self.assertEqual(category("AI/cats/1.png", library_folder="Sources"), "")
        self.assertEqual(category("Pics/AI/cats/deep/1.png", library_folder="Pics/AI"), "Pics/AI/cats")
        self.assertEqual(category("Pics/AI/1.png", library_folder="Pics/AI"), "")

    def test_is_library_rel(self):
        self.assertTrue(sources.is_library_rel("Sources/cats/1.png", "Sources"))
        self.assertTrue(sources.is_library_rel("sources/cats/1.png", "Sources"))
        self.assertFalse(sources.is_library_rel("SourcesExtra/1.png", "Sources"))
        self.assertFalse(sources.is_library_rel("1.png", "Sources"))


class GlobalPickTests(unittest.TestCase):
    """Gallery and random pick with a custom library and move-on-success off."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.input_root = Path(self.temp.name) / "input"
        self.cats = self.input_root / "Sources" / "cats"
        self.cats.mkdir(parents=True)
        for name in ("1.png", "2.png", "3.png"):
            (self.cats / name).write_bytes(name.encode())
        (self.input_root / "Sources" / "dogs").mkdir()
        self._orig_data_dir = os.environ.get("COMFYUI_IMAGE_LEDGER_DIR")
        os.environ["COMFYUI_IMAGE_LEDGER_DIR"] = str(Path(self.temp.name) / "ledger-data")
        settings_module.save_settings({"library_folder": "Sources", "auto_move": False})
        self.tracker = importlib.import_module(f"{PACKAGE_NAME}.global_tracker")
        self._orig_input_root = self.tracker._input_root
        self._orig_ledger = self.tracker._ledger
        self._orig_pick_folder = self.tracker._LAST_PICK_FOLDER
        self.tracker._input_root = lambda: self.input_root
        self.ledger = Ledger(Path(self.temp.name) / "ledger.sqlite3", session_id="pick-test")
        self.tracker._ledger = lambda: self.ledger
        self.tracker._RANDOM_SKIPPED.clear()
        self.tracker.invalidate_library_index()

    def tearDown(self):
        self.tracker._input_root = self._orig_input_root
        self.tracker._ledger = self._orig_ledger
        self.tracker._LAST_PICK_FOLDER = self._orig_pick_folder
        self.tracker._RANDOM_SKIPPED.clear()
        self.tracker.invalidate_library_index()
        if self._orig_data_dir is None:
            os.environ.pop("COMFYUI_IMAGE_LEDGER_DIR", None)
        else:
            os.environ["COMFYUI_IMAGE_LEDGER_DIR"] = self._orig_data_dir
        self.temp.cleanup()

    def test_folders_come_from_configured_library(self):
        folders = self.tracker.list_pick_folders()
        self.assertEqual([item["path"] for item in folders], ["Sources/cats", "Sources/dogs"])
        self.assertEqual(folders[0]["pending"], 3)

    def test_tracked_image_is_excluded_without_moving(self):
        result = self.tracker.mark_sources(annotated_paths=["Sources/cats/2.png"])
        self.assertEqual(len(result["marked"]), 1)
        self.assertTrue((self.cats / "2.png").is_file(), "move is off, file must stay")

        listing = self.tracker.list_folder_images("cats")
        self.assertTrue(listing["ok"], listing)
        self.assertEqual(listing["folder"], "Sources/cats")
        self.assertEqual(sorted(item["name"] for item in listing["items"]), ["1.png", "3.png"])
        self.assertEqual(self.tracker.list_pick_folders()[0]["pending"], 2)
        for _ in range(6):
            picked = self.tracker.random_pick_from_folder("", folder="Sources/cats")
            self.assertNotEqual(Path(picked["selected"]).name, "2.png")

    def test_undo_returns_image_to_pending(self):
        self.tracker.mark_sources(annotated_paths=["Sources/cats/2.png"])
        self.tracker.undo_last()
        names = sorted(item["name"] for item in self.tracker.list_folder_images("cats")["items"])
        self.assertEqual(names, ["1.png", "2.png", "3.png"])


class HookTests(unittest.TestCase):
    def setUp(self):
        self.tracker = importlib.import_module(f"{PACKAGE_NAME}.global_tracker")
        self.previous = {name: sys.modules.get(name) for name in ("execution", "folder_paths")}
        self.calls = []
        self._orig_success = self.tracker.on_execution_success
        self.tracker.on_execution_success = lambda *args: self.calls.append(args)

    def tearDown(self):
        self.tracker.on_execution_success = self._orig_success
        for name, module in self.previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def _install_execution(self, executor_cls):
        execution = types.ModuleType("execution")
        execution.PromptExecutor = executor_cls
        sys.modules["execution"] = execution
        self.tracker._wrap_execute()

    def test_async_wrapper_accepts_changed_signature(self):
        import asyncio

        class PromptExecutor:
            success = True
            history_result = {"outputs": {}}

            async def execute_async(self, prompt, prompt_id, extra_data=None, execute_outputs=None, new_flag=False):
                return "done"

        self._install_execution(PromptExecutor)
        result = asyncio.run(
            PromptExecutor().execute_async({"1": {}}, "p1", extra_data={"x": 1}, new_flag=True)
        )
        self.assertEqual(result, "done")
        self.assertEqual(len(self.calls), 1)
        prompt, prompt_id, extra, history = self.calls[0]
        self.assertEqual((prompt, prompt_id, extra), ({"1": {}}, "p1", {"x": 1}))
        self.assertEqual(history, {"outputs": {}})

    def test_sync_execute_is_wrapped_on_older_comfyui(self):
        class PromptExecutor:
            success = False

            def execute(self, prompt, prompt_id, extra_data=None):
                return "ran"

        self._install_execution(PromptExecutor)
        self.assertEqual(PromptExecutor().execute({}, "p2"), "ran")
        self.assertEqual(self.calls, [], "failed runs must not be recorded")

    def test_recursive_search_only_hides_archives_under_input(self):
        seen = []
        folder_paths = types.ModuleType("folder_paths")
        folder_paths.recursive_search = lambda directory, excluded_dir_names=None: seen.append(
            (directory, list(excluded_dir_names or []))
        )
        sys.modules["folder_paths"] = folder_paths
        with tempfile.TemporaryDirectory() as temp:
            input_root = Path(temp) / "input"
            orig_input_root = self.tracker._input_root
            self.tracker._input_root = lambda: input_root
            try:
                self.tracker._wrap_recursive_search()
                folder_paths.recursive_search(str(Path(temp) / "models" / "loras"))
                folder_paths.recursive_search(str(input_root / "AI"))
            finally:
                self.tracker._input_root = orig_input_root
        self.assertEqual(seen[0][1], [])
        self.assertIn("_used", seen[1][1])


if __name__ == "__main__":
    unittest.main()
