from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any


GLOBAL_CAMPAIGN = "__global__"
USED_DIRNAME = "_used"
POOR_DIRNAME = "_rejected"
SETTINGS_NAME = "settings.json"

DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": True,
    # Tracking is safe by default. Moving source files requires explicit opt-in.
    "auto_move": False,
    "hide_used_in_picker": True,
    "watch_subfolders": [],
    "skip_dir_names": [
        "_used",
        "_rejected",
        "效果不佳",
        "_trash",
        "_thumbs",
        "clipspace",
        "3d",
        ".git",
    ],
    "poor_dirname": POOR_DIRNAME,
    "used_dirname": USED_DIRNAME,
    "min_video_bytes": 50_000,
    "video_extensions": [".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v"],
}


def profile_data_root() -> Path:
    forced = os.environ.get("COMFYUI_IMAGE_LEDGER_DIR", "").strip()
    if forced:
        return Path(forced).expanduser().resolve()
    try:
        import folder_paths

        user_root = Path(folder_paths.get_user_directory()).resolve()
        default_profile = user_root / "default"
        profile_root = default_profile if default_profile.is_dir() else user_root
        return profile_root / "image_ledger"
    except Exception:
        return Path(__file__).resolve().parent / "_local_ledger"


def settings_path() -> Path:
    return profile_data_root() / SETTINGS_NAME


def ledger_db_path() -> Path:
    return profile_data_root() / "ledger.sqlite3"


def normalize_settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    data = deepcopy(DEFAULT_SETTINGS)
    if not isinstance(raw, dict):
        return data
    if "enabled" in raw:
        data["enabled"] = bool(raw["enabled"])
    if "auto_move" in raw:
        data["auto_move"] = bool(raw["auto_move"])
    if "hide_used_in_picker" in raw:
        data["hide_used_in_picker"] = bool(raw["hide_used_in_picker"])
    if "watch_subfolders" in raw:
        watch = raw["watch_subfolders"]
        if isinstance(watch, str):
            watch = [part.strip() for part in watch.replace(";", ",").split(",")]
        if isinstance(watch, list):
            data["watch_subfolders"] = [
                str(part).replace("\\", "/").strip("/")
                for part in watch
                if str(part).strip()
            ]
    if "skip_dir_names" in raw:
        skip = raw["skip_dir_names"]
        if isinstance(skip, str):
            skip = [part.strip() for part in skip.replace(";", ",").split(",")]
        if isinstance(skip, list):
            names = [str(part).strip() for part in skip if str(part).strip()]
            if names:
                data["skip_dir_names"] = names
    for required in (USED_DIRNAME, POOR_DIRNAME):
        if required not in data["skip_dir_names"]:
            data["skip_dir_names"].append(required)
    if raw.get("poor_dirname"):
        name = str(raw["poor_dirname"]).strip().replace("\\", "/").strip("/")
        if name and "/" not in name and name not in {".", ".."}:
            data["poor_dirname"] = name
    if raw.get("used_dirname"):
        name = str(raw["used_dirname"]).strip().replace("\\", "/").strip("/")
        if name and "/" not in name and name not in {".", ".."}:
            data["used_dirname"] = name
    if "min_video_bytes" in raw:
        try:
            data["min_video_bytes"] = max(1, int(raw["min_video_bytes"]))
        except (TypeError, ValueError):
            pass
    if "video_extensions" in raw:
        exts = raw["video_extensions"]
        if isinstance(exts, str):
            exts = [part.strip() for part in exts.replace(";", ",").split(",")]
        if isinstance(exts, list):
            normalized: list[str] = []
            for ext in exts:
                text = str(ext).strip().lower()
                if not text:
                    continue
                if not text.startswith("."):
                    text = f".{text}"
                if text not in normalized:
                    normalized.append(text)
            if normalized:
                data["video_extensions"] = normalized
    return data


def load_settings() -> dict[str, Any]:
    path = settings_path()
    if not path.is_file():
        return deepcopy(DEFAULT_SETTINGS)
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return deepcopy(DEFAULT_SETTINGS)
    return normalize_settings(parsed if isinstance(parsed, dict) else None)


def save_settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    data = normalize_settings(raw)
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return data
