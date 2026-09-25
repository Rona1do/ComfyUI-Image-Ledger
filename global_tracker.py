from __future__ import annotations

import hashlib
import os
import threading
import traceback
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any

from .global_settings import (
    GLOBAL_CAMPAIGN,
    POOR_DIRNAME,
    ledger_db_path,
    library_folder,
    load_settings,
    profile_data_root,
)
from .global_sources import (
    IMAGE_EXTENSIONS,
    candidate_filenames,
    category_folder_of,
    extract_video_outputs,
    find_source_images_from_prompt,
    hard_move,
    index_library_by_name,
    input_relative,
    is_library_rel,
    is_watched_rel,
    iter_library_images,
    library_root,
    logical_join,
    pick_library_match,
    poor_destination,
    unique_path,
    unsuffixed_filename,
    used_destination,
    workflow_name_from_extra,
)
from .ledger import Ledger


SESSION_ID = f"global-{uuid.uuid4().hex}"
_HOOKS_INSTALLED = False
_LOCK = threading.Lock()
_LAST_RESULT: dict[str, Any] | None = None
_LIBRARY_INDEX: dict[str, list[Path]] | None = None
_LIBRARY_INDEX_KEY = ""
_RANDOM_SKIPPED: dict[str, set[str]] = {}


def _ledger() -> Ledger:
    return Ledger(ledger_db_path(), session_id=SESSION_ID)


def _input_root() -> Path:
    import folder_paths

    return Path(os.path.abspath(str(folder_paths.get_input_directory())))


def _output_root() -> Path:
    import folder_paths

    return Path(os.path.abspath(str(folder_paths.get_output_directory())))


def _category_of(relative: str, cfg: dict[str, Any] | None = None) -> str:
    cfg = cfg if cfg is not None else load_settings()
    return category_folder_of(
        relative,
        str(cfg.get("used_dirname") or "_used"),
        str(cfg.get("poor_dirname") or POOR_DIRNAME),
        library_folder(cfg),
    )


def _done_paths() -> set[str]:
    """Completed sources that still sit in their category (move-on-success off)."""
    try:
        return _ledger().done_rel_paths(GLOBAL_CAMPAIGN)
    except Exception:
        return set()


def _library_index(input_root: Path, skip_dir_names: list[str]) -> dict[str, list[Path]]:
    global _LIBRARY_INDEX, _LIBRARY_INDEX_KEY
    root = library_root(input_root, library_folder())
    key = f"{root}|{','.join(skip_dir_names)}"
    if _LIBRARY_INDEX is not None and _LIBRARY_INDEX_KEY == key:
        return _LIBRARY_INDEX
    _LIBRARY_INDEX = index_library_by_name(root, skip_dir_names)
    _LIBRARY_INDEX_KEY = key
    return _LIBRARY_INDEX


def invalidate_library_index() -> None:
    global _LIBRARY_INDEX, _LIBRARY_INDEX_KEY
    _LIBRARY_INDEX = None
    _LIBRARY_INDEX_KEY = ""


def _resolve_source(
    annotated: str,
    input_root: Path,
    *,
    skip_dir_names: list[str] | None = None,
    expected_sha256: str = "",
    hash_func=None,
    preferred_folder: str = "",
) -> tuple[str, Path] | None:
    """Resolve a LoadImage path, preferring the copy inside the source library."""
    raw = (annotated or "").replace("\\", "/").strip()
    if not raw:
        return None
    skip = list(skip_dir_names or [])
    index = _library_index(input_root, skip)
    digest = (expected_sha256 or "").strip().lower()
    names = candidate_filenames(Path(raw).name)

    if preferred_folder:
        for name in names:
            try:
                cand = logical_join(input_root, f"{preferred_folder}/{name}")
            except ValueError:
                continue
            if not cand.is_file():
                continue
            if digest and hash_func is not None:
                try:
                    if str(hash_func(cand)).lower() != digest:
                        continue
                except OSError:
                    continue
            return input_relative(cand, input_root), cand

    # Already a library-relative path that exists.
    try:
        direct = logical_join(input_root, raw)
    except ValueError:
        direct = None
    if direct is not None and direct.is_file():
        rel = input_relative(direct, input_root)
        if is_library_rel(rel, library_folder()):
            return rel, direct

    library = pick_library_match(
        raw,
        index,
        expected_sha256=digest,
        hash_func=hash_func,
    )
    if library is not None and library.is_file():
        return input_relative(library, input_root), library

    # Last resort: input-root file. Avoid treating 235 (1).png as the original.
    if direct is not None and direct.is_file() and Path(raw).name == unsuffixed_filename(Path(raw).name):
        rel = input_relative(direct, input_root)
        if ":" in raw.split("/")[0] or Path(raw).is_absolute():
            return rel, direct
        return raw.strip("/"), direct
    return None


def _resolve_video(item: dict[str, str]) -> Path | None:
    import folder_paths

    kind = (item.get("type") or "output").strip() or "output"
    if kind == "temp":
        root = Path(os.path.abspath(str(folder_paths.get_temp_directory())))
    elif kind == "input":
        root = Path(os.path.abspath(str(folder_paths.get_input_directory())))
    else:
        root = Path(os.path.abspath(str(folder_paths.get_output_directory())))
    sub = (item.get("subfolder") or "").replace("\\", "/").strip("/")
    name = item.get("filename") or ""
    path = Path(os.path.abspath(str(root.joinpath(*sub.split("/"), name) if sub else root / name)))
    return path if path.is_file() else None


def _notify(payload: dict[str, Any]) -> None:
    global _LAST_RESULT
    _LAST_RESULT = payload
    try:
        from server import PromptServer

        instance = getattr(PromptServer, "instance", None)
        if instance is not None:
            instance.send_sync("image_ledger_global_used", payload)
    except Exception:
        pass


def last_result() -> dict[str, Any] | None:
    return _LAST_RESULT


_CURRENT_PICK = ""
_LAST_PICK_FOLDER = ""


def safe_input_file(name: str) -> Path | None:
    """Resolve an input-relative image without following junctions out of input."""
    raw = str(name or "").replace("\\", "/").strip()
    if not raw or raw.startswith("/") or ":" in raw.split("/")[0]:
        return None
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return None
    try:
        path = logical_join(_input_root(), "/".join(parts))
    except ValueError:
        return None
    return path if path.is_file() else None


def stage_for_loadimage(path_hint: str) -> dict[str, Any]:
    """Return the input-relative path LoadImage should use. No copy, no extra file."""
    global _CURRENT_PICK, _LAST_PICK_FOLDER
    cfg = load_settings()
    input_root = _input_root()
    used_dirname = str(cfg.get("used_dirname") or "_used")
    poor_dirname = str(cfg.get("poor_dirname") or POOR_DIRNAME)
    raw = (path_hint or "").strip().replace("\\", "/")
    if not raw:
        return {"ok": False, "error": "No source image path was provided."}

    if Path(path_hint).is_absolute():
        return {
            "ok": False,
            "error": "Absolute paths are not allowed. Choose an image under ComfyUI/input.",
        }

    located = _locate_existing(raw, input_root, used_dirname, poor_dirname)
    if located is None:
        located = _resolve_source(
            raw,
            input_root,
            skip_dir_names=list(cfg.get("skip_dir_names") or []),
        )
    if located is None:
        return {"ok": False, "error": f"Source image not found: {path_hint}"}
    library_rel, src = located
    if not src.is_file():
        return {"ok": False, "error": f"Source image not found: {path_hint}"}
    load_name = library_rel.replace("\\", "/")
    _CURRENT_PICK = load_name
    category = _category_of(load_name, cfg)
    if category:
        _LAST_PICK_FOLDER = category
    return {
        "ok": True,
        "load_name": load_name,
        "source": load_name,
        "folder": category,
        "image": _preview_payload(load_name),
        "message": f"Using library path {load_name} without copying the file.",
    }


def move_source_to_used(
    abs_path: Path,
    rel_path: str,
    input_root: Path,
    used_dirname: str,
) -> tuple[Path, str]:
    dest_rel = used_destination(rel_path, used_dirname)
    dest = logical_join(input_root, dest_rel)
    dest = hard_move(abs_path, dest)
    return dest, input_relative(dest, input_root)


def mark_sources(
    *,
    refs: list[dict[str, Any]] | None = None,
    annotated_paths: list[str] | None = None,
    output_path: str | None = None,
    workflow_name: str = "",
    settings: dict[str, Any] | None = None,
    force_move: bool | None = None,
) -> dict[str, Any]:
    cfg = settings or load_settings()
    if not cfg.get("enabled", True) and force_move is None:
        return {"ok": True, "skipped": "disabled", "marked": []}

    input_root = _input_root()
    ledger = _ledger()
    watch = cfg.get("watch_subfolders") or []
    skip = cfg.get("skip_dir_names") or []
    used_dirname = str(cfg.get("used_dirname") or "_used")
    should_move = cfg.get("auto_move", True) if force_move is None else bool(force_move)

    candidates: list[str] = []
    for ref in refs or []:
        annotated = str(ref.get("annotated") or "").strip()
        if annotated:
            candidates.append(annotated)
    for annotated in annotated_paths or []:
        if annotated:
            candidates.append(str(annotated))

    marked: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[str] = set()
    hash_func = ledger.hash_file_cached
    for annotated in candidates:
        # Hash a same-name input-root copy first so duplicate names (1.png)
        # can be matched to the correct category folder in the library.
        expected_sha = ""
        annotated_name = Path(annotated.replace("\\", "/")).name
        hash_candidates = [
            logical_join(input_root, annotated_name),
            logical_join(input_root, f"_used/{annotated_name}"),
        ]
        for candidate in hash_candidates:
            try:
                if candidate.is_file():
                    expected_sha = hash_func(candidate)
                    break
            except Exception:
                continue
        preferred = _LAST_PICK_FOLDER or _category_of(_CURRENT_PICK, cfg)
        resolved = _resolve_source(
            annotated,
            input_root,
            skip_dir_names=list(skip),
            expected_sha256=expected_sha,
            hash_func=hash_func,
            preferred_folder=preferred,
        )
        if resolved is None:
            errors.append(f"Source image not found: {annotated}")
            continue
        rel_path, abs_path = resolved
        if not is_watched_rel(rel_path, watch, skip):
            continue
        try:
            digest = expected_sha or hash_func(abs_path)
        except OSError as error:
            errors.append(f"Could not read {rel_path}: {error}")
            continue
        if digest in seen:
            continue
        seen.add(digest)
        moved_to = ""
        final_abs = str(abs_path)
        try:
            stat = abs_path.stat()
            size_bytes = stat.st_size
            mtime_ns = stat.st_mtime_ns
        except OSError:
            size_bytes = 0
            mtime_ns = 0
        dest_rel_hint = rel_path
        if preferred and not is_library_rel(rel_path, library_folder(cfg)):
            dest_rel_hint = f"{preferred}/{unsuffixed_filename(Path(rel_path).name)}"
        if should_move:
            try:
                dest, dest_rel = move_source_to_used(
                    abs_path, dest_rel_hint, input_root, used_dirname
                )
                moved_to = dest_rel
                final_abs = str(dest)
                invalidate_library_index()
                # ComfyUI copy-collisions like 235 (1).png in input/_used: delete if same content.
                for name in candidate_filenames(annotated_name):
                    for folder in (input_root, input_root / "_used"):
                        extra = folder / name
                        try:
                            if (
                                extra.is_file()
                                and extra.resolve() != Path(dest).resolve()
                                and hash_func(extra).lower() == digest
                            ):
                                extra.unlink()
                        except OSError:
                            pass
            except OSError as error:
                errors.append(f"Could not move {rel_path}: {error}")
        record = ledger.upsert_done_source(
            campaign=GLOBAL_CAMPAIGN,
            sha256=digest,
            rel_path=rel_path,
            abs_path=final_abs,
            source_root=str(input_root),
            output_path=output_path,
            workflow_name=workflow_name,
            moved_to=moved_to,
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
        )
        marked.append(
            {
                **record,
                "annotated": annotated,
                "moved": bool(moved_to),
            }
        )

    payload = {
        "ok": True,
        "campaign": GLOBAL_CAMPAIGN,
        "workflow_name": workflow_name,
        "output_path": output_path or "",
        "marked": marked,
        "errors": errors,
        "status": ledger.status(GLOBAL_CAMPAIGN),
    }
    if marked:
        _notify(payload)
        names = ", ".join(str(item.get("rel_path") or "") for item in marked)
        moved_note = "and moved to _used" if any(item.get("moved") for item in marked) else "tracked only"
        print(f"[ComfyUI Image Ledger] Tracked source(s) {names} ({moved_note})")
    return payload


def mark_committed_job(abs_path: str, output_path: str, workflow_name: str = "") -> dict[str, Any]:
    if not abs_path:
        return {"ok": True, "skipped": "no-path", "marked": []}
    try:
        input_root = _input_root()
        rel = input_relative(abs_path, input_root)
        return mark_sources(
            annotated_paths=[rel],
            output_path=output_path,
            workflow_name=workflow_name or "Image Ledger workflow",
        )
    except Exception as error:
        print(f"[ComfyUI Image Ledger] Commit hook failed: {error}")
        traceback.print_exc()
        return {"ok": False, "error": str(error), "marked": []}


def on_execution_success(
    prompt: dict[str, Any],
    prompt_id: str,
    extra_data: dict[str, Any] | None,
    history_result: dict[str, Any] | None,
) -> dict[str, Any] | None:
    cfg = load_settings()
    if not cfg.get("enabled", True):
        return None
    videos = extract_video_outputs(history_result, cfg.get("video_extensions") or [])
    if not videos:
        return None
    min_bytes = int(cfg.get("min_video_bytes") or 1)
    chosen: Path | None = None
    for item in videos:
        path = _resolve_video(item)
        if path is None:
            continue
        try:
            if path.stat().st_size < min_bytes:
                continue
        except OSError:
            continue
        chosen = path
    if chosen is None:
        return None
    refs = [item.to_dict() for item in find_source_images_from_prompt(prompt)]
    if not refs:
        return None
    workflow_name = workflow_name_from_extra(extra_data or {})
    return mark_sources(
        refs=refs,
        output_path=str(chosen),
        workflow_name=workflow_name,
        settings=cfg,
    )


def _preview_payload(rel_path: str) -> dict[str, str]:
    rel = (rel_path or "").replace("\\", "/").strip("/")
    parts = [part for part in rel.split("/") if part]
    filename = parts[-1] if parts else ""
    subfolder = "/".join(parts[:-1])
    return {
        "filename": filename,
        "subfolder": subfolder,
        "type": "input",
        "selected": rel,
    }


def _locate_existing(annotated: str, input_root: Path, used_dirname: str, poor_dirname: str) -> tuple[str, Path] | None:
    raw = (annotated or "").replace("\\", "/").strip()
    if not raw:
        return None
    guesses = [raw]
    try:
        guesses.append(used_destination(raw, used_dirname))
        guesses.append(poor_destination(raw, used_dirname, poor_dirname))
    except ValueError:
        pass
    name = Path(raw).name
    preferred = (_LAST_PICK_FOLDER or "").replace("\\", "/").strip("/")
    if preferred and name:
        guesses.append(f"{preferred}/{used_dirname}/{name}")
        guesses.append(f"{preferred}/{used_dirname}/{poor_dirname}/{name}")
        # Pre-release builds archived rejects in _used/效果不佳.
        if poor_dirname.casefold() != "效果不佳":
            guesses.append(f"{preferred}/{used_dirname}/效果不佳/{name}")
    seen: set[str] = set()
    for guess in guesses:
        key = guess.replace("\\", "/").strip("/")
        if not key or key in seen:
            continue
        seen.add(key)
        try:
            path = logical_join(input_root, key)
        except ValueError:
            continue
        if path.is_file():
            return input_relative(path, input_root), path
    return _resolve_source(raw, input_root)


def _as_input_relative(hint: str, input_root: Path) -> str:
    """Return an input-relative path. Absolute paths outside input are dropped."""
    text = str(hint or "").strip().strip('"').strip("'")
    if not text:
        return ""
    candidate = Path(text)
    drive_absolute = len(text) >= 2 and text[1] == ":"
    if candidate.is_absolute() or drive_absolute:
        try:
            root = Path(os.path.abspath(str(input_root)))
            resolved = Path(os.path.abspath(str(candidate)))
            return resolved.relative_to(root).as_posix()
        except (OSError, ValueError):
            return ""
    return text.replace("\\", "/").strip("/")


def _path_hints(*groups: Any) -> list[str]:
    hints: list[str] = []
    seen: set[str] = set()
    for group in groups:
        if group is None or group == "":
            continue
        if isinstance(group, dict):
            values = [
                group.get("moved_to"),
                group.get("rel_path"),
                group.get("abs_path"),
                group.get("annotated"),
                group.get("source"),
                group.get("load_name"),
                group.get("selected"),
            ]
        elif isinstance(group, (list, tuple)):
            values = list(group)
        else:
            values = [group]
        for value in values:
            text = str(value or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            hints.append(text)
    return hints


def stage_last_run(path_hint: str = "") -> dict[str, Any]:
    """Point LoadImage at the last recorded source, including files already in _used."""
    global _LAST_PICK_FOLDER
    cfg = load_settings()
    input_root = _input_root()
    used_dirname = str(cfg.get("used_dirname") or "_used")
    poor_dirname = str(cfg.get("poor_dirname") or POOR_DIRNAME)

    last = last_result() or {}
    marked = list(last.get("marked") or [])
    try:
        event = _ledger().last_global_event(GLOBAL_CAMPAIGN)
    except Exception:
        event = None

    if not _LAST_PICK_FOLDER and isinstance(event, dict):
        category = _category_of(str(event.get("moved_to") or event.get("rel_path") or ""), cfg)
        if category:
            _LAST_PICK_FOLDER = category

    for hint in _path_hints(path_hint, *marked, event, _CURRENT_PICK):
        relative = _as_input_relative(hint, input_root)
        if not relative:
            continue
        located = _locate_existing(relative, input_root, used_dirname, poor_dirname)
        if located is None:
            continue
        staged = stage_for_loadimage(located[0])
        if not staged.get("ok"):
            continue
        load_name = str(staged.get("load_name") or located[0]).replace("\\", "/")
        staged["rerun"] = True
        staged["from_used"] = any(
            part.casefold() == used_dirname.casefold() for part in load_name.split("/")
        )
        staged["message"] = f"Rerun last image: {load_name}"
        return staged
    return {
        "ok": False,
        "error": (
            "No previous source image was found. Run one first, or check that it "
            "is still in that category's _used folder."
        ),
    }


def mark_poor_quality(annotated: str = "") -> dict[str, Any]:
    cfg = load_settings()
    input_root = _input_root()
    ledger = _ledger()
    used_dirname = str(cfg.get("used_dirname") or "_used")
    poor_dirname = str(cfg.get("poor_dirname") or POOR_DIRNAME)
    path_hint = (annotated or "").strip()
    if not path_hint:
        event = ledger.last_global_event(GLOBAL_CAMPAIGN) or {}
        path_hint = str(event.get("moved_to") or event.get("rel_path") or event.get("abs_path") or "")
    relative = _as_input_relative(path_hint, input_root)
    located = (
        _locate_existing(relative, input_root, used_dirname, poor_dirname) if relative else None
    )
    if located is None:
        return {"ok": False, "error": "No source image is available to reject."}
    rel_path, abs_path = located
    dest_rel = poor_destination(rel_path, used_dirname, poor_dirname)
    dest = logical_join(input_root, dest_rel)
    try:
        if dest.exists() and dest.resolve() == abs_path.resolve():
            already = True
        else:
            dest = hard_move(abs_path, dest)
            dest_rel = input_relative(dest, input_root)
            already = False
    except OSError as error:
        return {"ok": False, "error": f"Move failed: {error}"}
    digest = ""
    try:
        digest = ledger.hash_file_cached(dest)
    except OSError:
        pass
    if digest:
        ledger.upsert_done_source(
            campaign=GLOBAL_CAMPAIGN,
            sha256=digest,
            rel_path=rel_path,
            abs_path=str(dest),
            source_root=str(input_root),
            workflow_name="Rejected",
            moved_to=dest_rel,
        )
    invalidate_library_index()
    payload = {
        "ok": True,
        "already": already,
        "rel_path": rel_path,
        "moved_to": dest_rel,
        "status": ledger.status(GLOBAL_CAMPAIGN),
        "message": f"Moved to rejected: {dest_rel}",
    }
    _notify({**payload, "marked": [{"rel_path": rel_path, "moved_to": dest_rel, "moved": True}]})
    print(f"[ComfyUI Image Ledger] Rejected {rel_path} -> {dest_rel}")
    return payload


def list_pick_folders() -> list[dict[str, Any]]:
    """First-level folders under the source library, excluding _used."""
    cfg = load_settings()
    input_root = _input_root()
    used_dirname = str(cfg.get("used_dirname") or "_used")
    poor_dirname = str(cfg.get("poor_dirname") or POOR_DIRNAME)
    skip = {name.casefold() for name in (cfg.get("skip_dir_names") or [])}
    skip.update({used_dirname.casefold(), poor_dirname.casefold()})
    library = library_folder(cfg)
    root = library_root(input_root, library)
    if not root.is_dir():
        return []
    names = [
        path.name
        for path in sorted(root.iterdir(), key=lambda item: item.name.casefold())
        if path.is_dir() and path.name.casefold() not in skip
    ]
    counts = {name: 0 for name in names}
    done = _done_paths()
    for path in iter_library_images(root, skip):
        try:
            top = path.relative_to(root).parts[0]
        except ValueError:
            continue
        if top in counts and input_relative(path, input_root).casefold() not in done:
            counts[top] += 1
    return [
        {"path": f"{library}/{name}", "name": name, "pending": counts.get(name, 0)}
        for name in names
    ]


def pending_files_in_category(category: str) -> list[Path]:
    cfg = load_settings()
    input_root = _input_root()
    used_dirname = str(cfg.get("used_dirname") or "_used")
    poor_dirname = str(cfg.get("poor_dirname") or POOR_DIRNAME)
    skip_dirs = {name.casefold() for name in (cfg.get("skip_dir_names") or [])}
    skip_dirs.update({used_dirname.casefold(), poor_dirname.casefold()})
    folder_abs = logical_join(input_root, category)
    if not folder_abs.is_dir():
        return []
    done = _done_paths()
    files: list[Path] = []
    for path in folder_abs.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        if path.name.startswith("."):
            continue
        try:
            rel_parts = path.relative_to(folder_abs).parts
        except ValueError:
            continue
        if any(part.casefold() in skip_dirs for part in rel_parts[:-1]):
            continue
        if input_relative(path, input_root).casefold() in done:
            continue
        files.append(path)
    files.sort(key=lambda item: item.relative_to(folder_abs).as_posix().casefold())
    return files


def resolve_preview_file(path_hint: str) -> Path | None:
    raw = (path_hint or "").strip().strip('"')
    if not raw:
        return None
    found = safe_input_file(raw)
    if found is not None:
        return found
    cfg = load_settings()
    located = _locate_existing(
        raw,
        _input_root(),
        str(cfg.get("used_dirname") or "_used"),
        str(cfg.get("poor_dirname") or POOR_DIRNAME),
    )
    if located is not None:
        return located[1]
    return None


def thumbnail_jpeg(rel_path: str, size: int = 360) -> bytes:
    from PIL import Image

    src = resolve_preview_file(rel_path)
    if src is None:
        raise FileNotFoundError(rel_path)
    cache = profile_data_root() / "thumbs"
    cache.mkdir(parents=True, exist_ok=True)
    stat = src.stat()
    side = max(64, min(2048, int(size)))
    key = hashlib.sha1(
        f"{rel_path}|{stat.st_mtime_ns}|{stat.st_size}|pad{side}".encode("utf-8")
    ).hexdigest()[:20]
    dest = cache / f"{key}.jpg"
    if dest.is_file() and dest.stat().st_size > 0:
        return dest.read_bytes()
    with Image.open(src) as img:
        rgb = img.convert("RGB")
        resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.LANCZOS)
        rgb.thumbnail((side, side), resample)
        canvas = Image.new("RGB", (side, side), (16, 16, 16))
        canvas.paste(rgb, ((side - rgb.width) // 2, (side - rgb.height) // 2))
        buffer = BytesIO()
        canvas.save(buffer, format="JPEG", quality=80, optimize=True)
        data = buffer.getvalue()
    dest.write_bytes(data)
    return data


def list_folder_images(folder: str, query: str = "", limit: int = 400) -> dict[str, Any]:
    global _LAST_PICK_FOLDER
    category = resolve_pick_folder("", folder)
    if not category:
        return {
            "ok": False,
            "error": "Select a category folder first.",
            "items": [],
            "folders": list_pick_folders(),
        }
    _LAST_PICK_FOLDER = category
    input_root = _input_root()
    needle = (query or "").strip().casefold()
    items: list[dict[str, str]] = []
    files = pending_files_in_category(category)
    for path in files:
        rel = input_relative(path, input_root)
        name = path.name
        if needle and needle not in rel.casefold() and needle not in name.casefold():
            continue
        rel_posix = rel.replace("\\", "/")
        prefix = category.replace("\\", "/") + "/"
        subpath = rel_posix[len(prefix) :] if rel_posix.startswith(prefix) else name
        items.append({"path": rel, "name": name, "subpath": subpath})
        if len(items) >= max(1, int(limit)):
            break
    return {
        "ok": True,
        "folder": category,
        "total": len(files),
        "shown": len(items),
        "items": items,
        "folders": list_pick_folders(),
    }


def resolve_pick_folder(
    current_path: str = "",
    folder: str = "",
) -> str:
    """Resolve a locked category such as AI/portraits. Never the whole library root."""
    cfg = load_settings()
    library = library_folder(cfg)
    explicit = (folder or "").replace("\\", "/").strip().strip("/")
    if explicit:
        if explicit.casefold() == library.casefold():
            return ""
        if not is_library_rel(explicit, library):
            explicit = f"{library}/{explicit}"
        return _category_of(explicit + "/x.png", cfg)

    inferred = _category_of(current_path, cfg)
    if inferred:
        return inferred

    # LoadImage often stores only the basename. A unique name in the library
    # can still recover the category.
    name = Path((current_path or "").replace("\\", "/")).name
    if name:
        input_root = _input_root()
        match = pick_library_match(
            name,
            _library_index(input_root, list(cfg.get("skip_dir_names") or [])),
        )
        if match is not None:
            return _category_of(input_relative(match, input_root), cfg)
    return ""


def random_pick_from_folder(
    current_path: str,
    *,
    folder: str = "",
    skip_current: bool = True,
) -> dict[str, Any]:
    global _LAST_PICK_FOLDER
    input_root = _input_root()
    category = resolve_pick_folder(current_path, folder)
    if not category:
        return {
            "ok": False,
            "error": "Select a category folder first. The library root is never sampled directly.",
            "folders": list_pick_folders(),
        }

    if not logical_join(input_root, category).is_dir():
        return {"ok": False, "error": f"Folder not found: {category}"}

    current_name = Path((current_path or "").replace("\\", "/")).name.casefold()
    files = pending_files_in_category(category)
    if not files:
        return {"ok": False, "error": f"No pending images in {category} (_used is excluded)."}

    skipped = _RANDOM_SKIPPED.setdefault(category, set())
    candidates = [path for path in files if path.name.casefold() not in skipped]
    if skip_current and current_name:
        narrowed = [path for path in candidates if path.name.casefold() != current_name]
        if narrowed:
            candidates = narrowed
    if not candidates:
        skipped.clear()
        candidates = [
            path for path in files
            if not skip_current or path.name.casefold() != current_name
        ] or files

    import random

    chosen = random.choice(candidates)
    skipped.add(chosen.name.casefold())
    _LAST_PICK_FOLDER = category
    rel = input_relative(chosen, input_root)
    remaining = max(0, len(files) - len(skipped))
    return {
        "ok": True,
        "folder": category,
        "selected": rel,
        "remaining": remaining,
        "total": len(files),
        "skipped_session": len(skipped),
        "image": _preview_payload(rel),
        "folders": list_pick_folders(),
        "message": f"Selected {rel} from {category}; {len(files)} pending image(s), excluding _used.",
    }


def undo_last() -> dict[str, Any]:
    ledger = _ledger()
    event = ledger.undo_last_global_event(GLOBAL_CAMPAIGN)
    if event is None:
        return {"ok": False, "error": "There is no record to undo."}
    restored = ""
    moved_to = str(event.get("moved_to") or "").strip()
    original = str(event.get("abs_path") or "").strip()
    rel_path = str(event.get("rel_path") or "").strip()
    input_root = _input_root()
    if moved_to:
        src = logical_join(input_root, moved_to)
        dest = logical_join(input_root, rel_path) if rel_path else Path(original)
        if src.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest = unique_path(dest)
            dest = hard_move(src, dest)
            restored = input_relative(dest, input_root)
        elif Path(original).is_file():
            restored = input_relative(original, input_root)
    return {
        "ok": True,
        "restored": restored or rel_path,
        "event": event,
        "status": ledger.status(GLOBAL_CAMPAIGN),
    }


def _remove_empty_dirs(root: Path) -> None:
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_dir():
            try:
                next(path.iterdir())
            except StopIteration:
                try:
                    path.rmdir()
                except OSError:
                    pass
    try:
        next(root.iterdir())
    except StopIteration:
        try:
            root.rmdir()
        except OSError:
            pass
    except OSError:
        pass


def restructure_used_folders(used_dirname: str = "_used") -> dict[str, Any]:
    """Legacy layout fix: AI/_used/category/1.png -> AI/category/_used/1.png"""
    input_root = _input_root()
    library = library_folder()
    depth = len(library.split("/"))
    dump = logical_join(input_root, f"{library}/{used_dirname}")
    moved = 0
    samples: list[dict[str, str]] = []
    if dump.is_dir():
        files = [path for path in dump.rglob("*") if path.is_file()]
        for path in files:
            rel = input_relative(path, input_root).replace("\\", "/")
            parts = [part for part in rel.split("/") if part]
            if len(parts) < depth + 2 or parts[depth].casefold() != used_dirname.casefold():
                continue
            rest = parts[depth + 1 :]
            new_rel = "/".join([library, *rest[:-1], used_dirname, rest[-1]])
            dest = logical_join(input_root, new_rel)
            dest = hard_move(path, dest)
            moved += 1
            if len(samples) < 12:
                samples.append({"from": rel, "to": input_relative(dest, input_root)})
        _remove_empty_dirs(dump)
    invalidate_library_index()
    return {"moved": moved, "samples": samples}


def relocate_recorded(*, force_move: bool = True) -> dict[str, Any]:
    """Move booked originals into each category's own _used folder."""
    cfg = load_settings()
    input_root = _input_root()
    ledger = _ledger()
    skip = list(cfg.get("skip_dir_names") or [])
    used_dirname = str(cfg.get("used_dirname") or "_used")
    restructured = restructure_used_folders(used_dirname)

    hashes = set(ledger.done_hashes(GLOBAL_CAMPAIGN))
    for campaign in ("image-ledger-default", "ImageLedger"):
        try:
            hashes |= ledger.done_hashes(campaign)
        except Exception:
            continue

    moved = 0
    skipped = 0
    missing = 0
    samples: list[dict[str, Any]] = list(restructured.get("samples") or [])
    library = library_root(input_root, library_folder(cfg))
    for path in iter_library_images(library, skip):
        rel = input_relative(path, input_root)
        dest_rel = used_destination(rel, used_dirname)
        dest = logical_join(input_root, dest_rel)
        try:
            if dest.exists() and dest.resolve() == path.resolve():
                skipped += 1
                continue
        except OSError:
            pass
        try:
            digest = ledger.hash_file_cached(path)
        except OSError:
            missing += 1
            continue
        if digest not in hashes:
            continue
        try:
            dest = hard_move(path, dest)
            dest_rel = input_relative(dest, input_root)
        except OSError:
            missing += 1
            continue
        ledger.upsert_done_source(
            campaign=GLOBAL_CAMPAIGN,
            sha256=digest,
            rel_path=rel,
            abs_path=str(dest),
            source_root=str(input_root),
            workflow_name="relocate-per-folder",
            moved_to=dest_rel,
        )
        moved += 1
        if len(samples) < 20:
            samples.append({"from": rel, "to": dest_rel})

    invalidate_library_index()
    return {
        "ok": True,
        "moved": moved,
        "restructured": restructured.get("moved", 0),
        "skipped": skipped,
        "missing": missing,
        "samples": samples,
        "status": ledger.status(GLOBAL_CAMPAIGN),
    }


def scan_existing_videos(
    *,
    move: bool = False,
    limit: int = 0,
) -> dict[str, Any]:
    from .history import discover_videos, find_ffmpeg, read_video_metadata

    cfg = load_settings()
    relocate = relocate_recorded(force_move=move) if move else {
        "moved": 0,
        "skipped": 0,
        "missing": 0,
        "samples": [],
    }
    output_root = _output_root()
    skip = {str(name).casefold() for name in (cfg.get("skip_dir_names") or [])}
    patterns = [f"*{ext}" for ext in (cfg.get("video_extensions") or [".mp4"])]
    videos = discover_videos(output_root, patterns, recursive=True)
    filtered: list[Path] = []
    for video in videos:
        try:
            rel_parts = {part.casefold() for part in video.relative_to(output_root).parts}
        except ValueError:
            rel_parts = {part.casefold() for part in video.parts}
        if rel_parts & skip:
            continue
        filtered.append(video)
    if limit and limit > 0:
        filtered = filtered[: int(limit)]

    ffmpeg = find_ffmpeg()
    ledger = _ledger()
    scanned = 0
    marked_total = 0
    unresolved = 0
    errors = 0
    samples: list[dict[str, Any]] = []
    for video in filtered:
        scanned += 1
        try:
            metadata = read_video_metadata(video, ffmpeg)
            prompt = metadata.get("prompt")
            if isinstance(prompt, str):
                import json

                prompt = json.loads(prompt)
            if not isinstance(prompt, dict):
                raise ValueError("prompt metadata missing")
            refs = find_source_images_from_prompt(prompt)
            if not refs:
                unresolved += 1
                continue
            result = mark_sources(
                refs=[item.to_dict() for item in refs],
                output_path=str(video),
                workflow_name=str(video.name),
                settings=cfg,
                force_move=move,
            )
            count = len(result.get("marked") or [])
            marked_total += count
            if count == 0:
                unresolved += 1
            elif len(samples) < 12:
                samples.append(
                    {
                        "video": str(video),
                        "sources": [item.get("rel_path") for item in result.get("marked") or []],
                    }
                )
        except Exception:
            errors += 1

    return {
        "ok": True,
        "scanned": scanned,
        "marked": marked_total,
        "unresolved": unresolved,
        "errors": errors,
        "moved": bool(move),
        "relocated": relocate,
        "samples": samples,
        "status": ledger.status(GLOBAL_CAMPAIGN),
    }


def _after_execute(executor: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
    """Record sources once ComfyUI reports a successful run.

    Arguments are read positionally or by name so the hook survives
    signature changes in PromptExecutor.
    """
    try:
        if not getattr(executor, "success", False):
            return
        prompt = args[0] if len(args) > 0 else kwargs.get("prompt")
        prompt_id = args[1] if len(args) > 1 else kwargs.get("prompt_id", "")
        extra_data = args[2] if len(args) > 2 else kwargs.get("extra_data")
        on_execution_success(
            prompt,
            str(prompt_id),
            extra_data if isinstance(extra_data, dict) else {},
            getattr(executor, "history_result", None),
        )
    except Exception as error:
        print(f"[ComfyUI Image Ledger] Execution hook failed: {error}")
        traceback.print_exc()


def _wrap_execute() -> None:
    import execution

    executor = execution.PromptExecutor
    # Current ComfyUI runs prompts through execute_async; older builds use execute.
    name = "execute_async" if hasattr(executor, "execute_async") else "execute"
    original = getattr(executor, name)
    if getattr(original, "_image_ledger_global", False):
        return

    if name == "execute_async":

        async def wrapped(self, *args, **kwargs):
            try:
                return await original(self, *args, **kwargs)
            finally:
                _after_execute(self, args, kwargs)

    else:

        def wrapped(self, *args, **kwargs):
            try:
                return original(self, *args, **kwargs)
            finally:
                _after_execute(self, args, kwargs)

    wrapped._image_ledger_global = True  # type: ignore[attr-defined]
    setattr(executor, name, wrapped)


def _remember_pick_from_prompt(json_data):
    global _CURRENT_PICK
    prompt = json_data.get("prompt") if isinstance(json_data, dict) else None
    if not isinstance(prompt, dict):
        return json_data
    # Same source-node rules as tracking, so a last-frame or mask loader
    # listed first in the prompt is never remembered as the pick.
    for ref in find_source_images_from_prompt(prompt):
        if ref.class_type == "LoadImage":
            _CURRENT_PICK = ref.annotated
            break
    return json_data


def _wrap_loadimage_paths() -> None:
    """Let stock LoadImage open AI/category/1.png without copying into input root."""
    import folder_paths
    import nodes

    if not getattr(folder_paths.exists_annotated_filepath, "_image_ledger_global", False):
        orig_exists = folder_paths.exists_annotated_filepath
        orig_get = folder_paths.get_annotated_filepath

        def exists_annotated_filepath(name):
            if safe_input_file(name) is not None:
                return True
            return orig_exists(name)

        def get_annotated_filepath(name, default_dir=None):
            safe = safe_input_file(name)
            if safe is not None:
                return str(safe)
            return orig_get(name, default_dir)

        exists_annotated_filepath._image_ledger_global = True  # type: ignore[attr-defined]
        folder_paths.exists_annotated_filepath = exists_annotated_filepath
        folder_paths.get_annotated_filepath = get_annotated_filepath

    cls = getattr(nodes, "LoadImage", None) or nodes.NODE_CLASS_MAPPINGS.get("LoadImage")
    if cls is None or getattr(cls, "_image_ledger_global", False):
        return
    orig_types = cls.INPUT_TYPES

    @classmethod
    def INPUT_TYPES(s):
        result = orig_types()
        extra = (_CURRENT_PICK or "").replace("\\", "/").strip()
        if extra:
            spec = result["required"]["image"]
            files = list(spec[0])
            if extra not in files:
                files.insert(0, extra)
            result["required"]["image"] = (files, spec[1]) if len(spec) > 1 else (files,)
        return result

    cls.INPUT_TYPES = INPUT_TYPES
    cls._image_ledger_global = True
    try:
        from server import PromptServer

        instance = getattr(PromptServer, "instance", None)
        if instance is not None:
            instance.add_on_prompt_handler(_remember_pick_from_prompt)
    except Exception:
        pass
    print("[ComfyUI Image Ledger] LoadImage can use nested input paths without copying files.")


def _wrap_recursive_search() -> None:
    import folder_paths

    original = folder_paths.recursive_search
    if getattr(original, "_image_ledger_global", False):
        return

    def wrapped(directory, excluded_dir_names=None):
        # Only hide archive folders from input listings, never from model folders.
        try:
            inside_input = Path(os.path.abspath(str(directory))).is_relative_to(_input_root())
        except Exception:
            inside_input = False
        if not inside_input:
            return original(directory, excluded_dir_names=excluded_dir_names)
        excluded = list(excluded_dir_names or [])
        try:
            used = str(load_settings().get("used_dirname") or "_used")
        except Exception:
            used = "_used"
        for name in (used, "_used", "_trash", "_thumbs"):
            if name not in excluded:
                excluded.append(name)
        return original(directory, excluded_dir_names=excluded)

    wrapped._image_ledger_global = True  # type: ignore[attr-defined]
    folder_paths.recursive_search = wrapped


def register_hooks() -> None:
    global _HOOKS_INSTALLED
    with _LOCK:
        if _HOOKS_INSTALLED:
            return
        try:
            _wrap_execute()
            _wrap_recursive_search()
            _wrap_loadimage_paths()
            _HOOKS_INSTALLED = True
            print(
                "[ComfyUI Image Ledger] Global tracking enabled. "
                "Successful video workflows are recorded; moving sources is opt-in."
            )
        except Exception as error:
            print(f"[ComfyUI Image Ledger] Failed to install hooks: {error}")
            traceback.print_exc()
