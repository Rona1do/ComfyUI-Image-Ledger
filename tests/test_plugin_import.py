from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "image_ledger_plugin_smoke_test"


class _Routes:
    def __init__(self):
        self.paths: list[tuple[str, str]] = []

    def _decorator(self, method: str, path: str):
        def register(handler):
            self.paths.append((method, path))
            return handler

        return register

    def get(self, path: str):
        return self._decorator("GET", path)

    def post(self, path: str):
        return self._decorator("POST", path)


class PluginImportTests(unittest.TestCase):
    def setUp(self):
        self.previous = {name: sys.modules.get(name) for name in ("folder_paths", "nodes", "execution", "server")}

        folder_paths = types.ModuleType("folder_paths")
        folder_paths.get_input_directory = lambda: str(PACKAGE_ROOT / "_test_input")
        folder_paths.get_output_directory = lambda: str(PACKAGE_ROOT / "_test_output")
        folder_paths.get_temp_directory = lambda: str(PACKAGE_ROOT / "_test_temp")
        folder_paths.get_user_directory = lambda: str(PACKAGE_ROOT / "_test_user")
        folder_paths.exists_annotated_filepath = lambda _name: False
        folder_paths.get_annotated_filepath = lambda name, _default=None: name
        folder_paths.recursive_search = lambda _directory, excluded_dir_names=None: ([], {})

        comfy_nodes = types.ModuleType("nodes")

        class LoadImage:
            @classmethod
            def INPUT_TYPES(cls):
                return {"required": {"image": ([], {"image_upload": True})}}

            def load_image(self, _path):  # pragma: no cover - execution only
                raise RuntimeError("not used by import smoke test")

        comfy_nodes.LoadImage = LoadImage
        comfy_nodes.NODE_CLASS_MAPPINGS = {"LoadImage": LoadImage}

        execution = types.ModuleType("execution")

        class PromptExecutor:
            async def execute_async(self, *_args, **_kwargs):
                return None

        execution.PromptExecutor = PromptExecutor

        server = types.ModuleType("server")
        routes = _Routes()

        class _PromptServer:
            instance = types.SimpleNamespace(
                routes=routes,
                add_on_prompt_handler=lambda _handler: None,
                send_sync=lambda *_args, **_kwargs: None,
            )

        server.PromptServer = _PromptServer
        self.routes = routes

        sys.modules.update(
            {
                "folder_paths": folder_paths,
                "nodes": comfy_nodes,
                "execution": execution,
                "server": server,
            }
        )

    def tearDown(self):
        for name, module in self.previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        for name in list(sys.modules):
            if name == PACKAGE_NAME or name.startswith(f"{PACKAGE_NAME}."):
                sys.modules.pop(name, None)

    def test_package_registers_nodes_routes_and_web_assets(self):
        spec = importlib.util.spec_from_file_location(
            PACKAGE_NAME,
            PACKAGE_ROOT / "__init__.py",
            submodule_search_locations=[str(PACKAGE_ROOT)],
        )
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        sys.modules[PACKAGE_NAME] = module
        assert spec.loader is not None
        spec.loader.exec_module(module)

        self.assertEqual(len(module.NODE_CLASS_MAPPINGS), 6)
        self.assertEqual(module.WEB_DIRECTORY, "./web")
        self.assertIn("ImageLedger_SimpleTrackedImageLoader", module.NODE_CLASS_MAPPINGS)
        self.assertIn(("GET", "/image_ledger/global/status"), self.routes.paths)
        self.assertIn(("POST", "/image_ledger/global/stage"), self.routes.paths)
        self.assertEqual(len(self.routes.paths), 13)


if __name__ == "__main__":
    unittest.main()
