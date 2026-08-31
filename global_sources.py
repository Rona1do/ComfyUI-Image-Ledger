from __future__ import annotations

import os
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

_COPY_SUFFIX_RE = re.compile(r"^(?P<stem>.+) \((?P<n>\d+)\)(?P<ext>\.[^.]+)$")


IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
    ".avif",
    ".gif",
}
DEFAULT_VIDEO_EXTENSIONS = {
    ".mp4",
    ".webm",
    ".mkv",
    ".mov",
    ".avi",
    ".m4v",
}
LOADER_CLASS_TYPES = {
    "LoadImage",
    "VHS_LoadImagePath",
    "LoadImageFromPath",
    "easy loadImage",
    "Load Images (Path)",
}
SKIP_LOADER_TYPES = {
    "LoadImageMask",
    "LoadImageOutput",
}
PRIMARY_TITLE_NEEDLES = (
    "first-frame-image",
    "first-frame",
    "first frame",
    "首帧",
    "选择源图",
)
SKIP_TITLE_NEEDLES = (
    "last-frame-image",
    "last-frame",
    "last frame",
    "末帧",
    "尾帧",
    "end-frame",
    "end frame",
    "mask",
    "遮罩",
)
IMAGE_INPUT_KEYS = (
    "image",
    "image_path",
    "file_path",
    "filepath",
    "filename",
    "file",
    "path",
)
SKIP_DIR_DEFAULT = (
    "_used",
    "_rejected",
    "效果不佳",  # Legacy default used by pre-release builds.
    "_trash",
    "_thumbs",
    "clipspace",
    "3d",
    ".git",
)
POOR_DIRNAME = "_rejected"


@dataclass(frozen=True)
class SourceRef:
    node_id: str
    class_type: str
    title: str
    annotated: str
    primary: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_rel(path: str) -> str:
    return (path or "").replace("\\", "/").strip().strip("/")


def unsuffixed_filename(name: str) -> str:
    """235 (1).png -> 235.png  (ComfyUI/Windows copy-collision suffix)."""
    text = Path(normalize_rel(name)).name
    match = _COPY_SUFFIX_RE.match(text)
    if not match:
        return text
    return f"{match.group('stem')}{match.group('ext')}"


def candidate_filenames(name: str) -> tuple[str, ...]:
    raw = Path(normalize_rel(name)).name
    orig = unsuffixed_filename(raw)
    if orig.casefold() == raw.casefold():
        return (raw,)
    return (raw, orig)


def title_of(node: dict[str, Any]) -> str:
    meta = node.get("_meta")
    if isinstance(meta, dict) and meta.get("title"):
        return str(meta.get("title") or "").strip()
    if node.get("title"):
        return str(node.get("title") or "").strip()
    return ""


def is_primary_title(title: str) -> bool:
    text = (title or "").casefold()
    return any(needle in text for needle in PRIMARY_TITLE_NEEDLES)


def is_skip_title(title: str) -> bool:
    text = (title or "").casefold()
    return any(needle in text for needle in SKIP_TITLE_NEEDLES)


def looks_like_image_name(value: str) -> bool:
    text = normalize_rel(value)
    if not text or ":" in text.split("/")[0]:
        # Drive-letter absolute paths are allowed if they have an image suffix.
        suffix = Path(text).suffix.lower()
        return suffix in IMAGE_EXTENSIONS
    suffix = Path(text).suffix.lower()
    return suffix in IMAGE_EXTENSIONS


def image_input_from_node(node: dict[str, Any]) -> str:
    inputs = node.get("inputs")
    if not isinstance(inputs, dict):
        return ""
    for key in IMAGE_INPUT_KEYS:
        value = inputs.get(key)
        if isinstance(value, str) and looks_like_image_name(value):
            return normalize_rel(value)
        if isinstance(value, (list, tuple)) and value:
            first = value[0]
            if isinstance(first, str) and looks_like_image_name(first):
                return normalize_rel(first)
    return ""


def find_source_images_from_prompt(prompt: dict[str, Any] | None) -> list[SourceRef]:
    """Pick the I2V source image(s) from a ComfyUI API prompt.

    Prefer explicitly titled first-frame / 源图 nodes. Never take Last-Frame,
    masks, or output-folder loaders. If no primary title exists, take every
    remaining LoadImage-like node.
    """
    if not isinstance(prompt, dict):
        return []
    found: list[SourceRef] = []
    for node_id, raw in prompt.items():
        if not isinstance(raw, dict):
            continue
        class_type = str(raw.get("class_type") or "").strip()
        if class_type in SKIP_LOADER_TYPES:
            continue
        if class_type not in LOADER_CLASS_TYPES and not class_type.endswith("LoadImage"):
            continue
        title = title_of(raw)
        if is_skip_title(title):
            continue
        annotated = image_input_from_node(raw)
        if not annotated:
            continue
        found.append(
            SourceRef(
                node_id=str(node_id),
                class_type=class_type,
                title=title,
                annotated=annotated,
                primary=is_primary_title(title),
            )
        )
    primaries = [item for item in found if item.primary]
    return primaries or found


def path_has_skipped_dir(
    relative: str,
    skip_dir_names: Iterable[str] = SKIP_DIR_DEFAULT,
) -> bool:
    parts = [part for part in normalize_rel(relative).split("/") if part]
    skipped = {str(name).casefold() for name in skip_dir_names if str(name).strip()}
    return any(part.casefold() in skipped for part in parts)


def is_watched_rel(
    relative: str,
    watch_subfolders: Iterable[str] | None = None,
    skip_dir_names: Iterable[str] = SKIP_DIR_DEFAULT,
) -> bool:
    rel = normalize_rel(relative)
    if not rel or path_has_skipped_dir(rel, skip_dir_names):
        return False
    watches = [normalize_rel(str(item)) for item in (watch_subfolders or []) if str(item).strip()]
    if not watches:
        return True
    return any(rel == watch or rel.startswith(f"{watch}/") for watch in watches)


def used_destination(
    relative: str,
    used_dirname: str = "_used",
) -> str:
    """Put _used inside the image's own folder, not a global dump.

    AI/portraits/1.png       -> AI/portraits/_used/1.png
    AI/illustrations/37.png  -> AI/illustrations/_used/37.png
    73.png                   -> _used/73.png
    """
    rel = normalize_rel(relative)
    used = (used_dirname or "_used").strip().replace("\\", "/").strip("/") or "_used"
    parts = [part for part in rel.split("/") if part]
    if not parts:
        raise ValueError("Empty source path")
    if any(part.casefold() == used.casefold() for part in parts):
        return "/".join(parts)
    if len(parts) == 1:
        return f"{used}/{parts[0]}"
    return "/".join([*parts[:-1], used, parts[-1]])


def source_folder_of(
    relative: str,
    used_dirname: str = "_used",
    poor_dirname: str = POOR_DIRNAME,
) -> str:
    """Folder to random-pick from: the image's category folder, ignoring _used."""
    parts = [part for part in normalize_rel(relative).split("/") if part]
    if not parts:
        return ""
    skip = {
        used_dirname.casefold(),
        poor_dirname.casefold(),
        "效果不佳".casefold(),
    }
    dirs = [part for part in parts[:-1] if part.casefold() not in skip]
    return "/".join(dirs)


def category_folder_of(
    relative: str,
    used_dirname: str = "_used",
    poor_dirname: str = POOR_DIRNAME,
    library_folder: str = "AI",
) -> str:
    """Top-level library category, e.g. AI/portraits.

    Never returns the whole AI root. Basename-only paths return "".
    """
    folder = source_folder_of(relative, used_dirname, poor_dirname)
    parts = [part for part in folder.split("/") if part]
    if len(parts) >= 2 and parts[0].casefold() == library_folder.casefold():
        return "/".join(parts[:2])
    return ""


def poor_destination(
    relative: str,
    used_dirname: str = "_used",
    poor_dirname: str = POOR_DIRNAME,
) -> str:
    """Move a source into that folder's ``_used/_rejected`` directory.

    AI/portraits/1.png        -> AI/portraits/_used/_rejected/1.png
    AI/portraits/_used/1.png  -> AI/portraits/_used/_rejected/1.png
    73.png                    -> _used/_rejected/73.png
    """
    rel = normalize_rel(relative)
    used = (used_dirname or "_used").strip().replace("\\", "/").strip("/") or "_used"
    poor = (poor_dirname or POOR_DIRNAME).strip().replace("\\", "/").strip("/") or POOR_DIRNAME
    parts = [part for part in rel.split("/") if part]
    if not parts:
        raise ValueError("Empty source path")
    name = parts[-1]
    skip = {used.casefold(), poor.casefold(), "效果不佳".casefold()}
    dirs = [part for part in parts[:-1] if part.casefold() not in skip]
    if not dirs:
        return f"{used}/{poor}/{name}"
    return "/".join([*dirs, used, poor, name])


def hard_move(src: Path, dest: Path) -> Path:
    """Move a file. Never leave the source behind (Windows junctions can copy)."""
    src = Path(src)
    dest = Path(dest)
    if not src.is_file():
        raise FileNotFoundError(src)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest = unique_path(dest)
    try:
        src_stat = src.stat()
        dest_stat = dest.stat() if dest.exists() else None
        if dest_stat is not None and dest.resolve() == src.resolve():
            return dest
    except OSError:
        pass
    try:
        os.replace(str(src), str(dest))
    except OSError:
        shutil.copy2(str(src), str(dest))
        try:
            src.unlink()
        except OSError as error:
            dest.unlink(missing_ok=True)
            raise OSError(f"Copied to {dest} but could not delete source {src}: {error}") from error
    if src.exists() and src.resolve() != dest.resolve():
        try:
            src.unlink()
        except OSError as error:
            raise OSError(f"Destination exists at {dest} but source still at {src}: {error}") from error
    return dest


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    index = 2
    while True:
        candidate = parent / f"{stem}__{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def extract_video_outputs(
    history_result: dict[str, Any] | None,
    video_extensions: Iterable[str] = DEFAULT_VIDEO_EXTENSIONS,
) -> list[dict[str, str]]:
    allowed = {
        ext.lower() if str(ext).startswith(".") else f".{str(ext).lower()}"
        for ext in video_extensions
    }
    found: list[dict[str, str]] = []
    outputs = (history_result or {}).get("outputs")
    if not isinstance(outputs, dict):
        return found
    seen: set[tuple[str, str, str]] = set()
    for node_out in outputs.values():
        if not isinstance(node_out, dict):
            continue
        for key in ("gifs", "videos", "images", "files"):
            items = node_out.get(key)
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                filename = str(item.get("filename") or "").strip()
                if not filename:
                    continue
                if Path(filename).suffix.lower() not in allowed:
                    continue
                kind = str(item.get("type") or "output").strip() or "output"
                if kind.casefold() == "temp":
                    continue
                subfolder = str(item.get("subfolder") or "").replace("\\", "/").strip("/")
                key_tuple = (kind, subfolder, filename)
                if key_tuple in seen:
                    continue
                seen.add(key_tuple)
                found.append(
                    {
                        "filename": filename,
                        "subfolder": subfolder,
                        "type": kind,
                    }
                )
    return found


def workflow_name_from_extra(extra_data: dict[str, Any] | None) -> str:
    extra = extra_data or {}
    pnginfo = extra.get("extra_pnginfo")
    workflow = pnginfo.get("workflow") if isinstance(pnginfo, dict) else None
    if isinstance(workflow, dict):
        for key in ("filename", "name", "title"):
            value = workflow.get(key)
            if value:
                return str(value)
        nested = workflow.get("extra")
        if isinstance(nested, dict):
            for key in ("workflow_name", "name", "title"):
                value = nested.get(key)
                if value:
                    return str(value)
    for key in ("workflow_name", "workflow"):
        value = extra.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def logical_join(base: Path, relative: str) -> Path:
    raw = normalize_rel(relative)
    base_abs = Path(os.path.abspath(str(base)))
    if not raw:
        return base_abs
    if Path(raw).is_absolute() or ":" in raw.split("/")[0]:
        raise ValueError("Path must be relative to the ComfyUI input directory")
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ValueError("Relative path cannot contain ..")
    return Path(os.path.abspath(str(base_abs.joinpath(*parts))))


def library_root(input_root: str | os.PathLike[str], library_folder: str = "AI") -> Path:
    return logical_join(Path(input_root), library_folder)


def iter_library_images(
    library_root_path: str | os.PathLike[str],
    skip_dir_names: Iterable[str] = SKIP_DIR_DEFAULT,
) -> list[Path]:
    root = Path(library_root_path)
    if not root.is_dir():
        return []
    skipped = {str(name).casefold() for name in skip_dir_names if str(name).strip()}
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        try:
            rel_parts = path.relative_to(root).parts
        except ValueError:
            continue
        if any(part.casefold() in skipped for part in rel_parts[:-1]):
            continue
        files.append(path)
    return files


def index_library_by_name(
    library_root_path: str | os.PathLike[str],
    skip_dir_names: Iterable[str] = SKIP_DIR_DEFAULT,
) -> dict[str, list[Path]]:
    index: dict[str, list[Path]] = {}
    for path in iter_library_images(library_root_path, skip_dir_names):
        index.setdefault(path.name.casefold(), []).append(path)
    return index


def pick_library_match(
    filename: str,
    index: dict[str, list[Path]],
    expected_sha256: str = "",
    hash_func=None,
) -> Path | None:
    """Resolve a LoadImage basename to a file in the configured image library.

    Unique filename wins. Duplicate names (1.png in many character folders)
    require a SHA-256 match so we do not move the wrong role's image.
    `235 (1).png` also matches library `235.png`.
    """
    hits: list[Path] = []
    seen: set[str] = set()
    for name in candidate_filenames(filename):
        for path in index.get(name.casefold(), []) or []:
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            hits.append(path)
    if not hits:
        return None
    digest = (expected_sha256 or "").strip().lower()
    if digest and hash_func is not None:
        matched = [path for path in hits if str(hash_func(path)).lower() == digest]
        if len(matched) == 1:
            return matched[0]
        if len(matched) > 1:
            return None
        return None
    if len(hits) == 1:
        return hits[0]
    return None


def input_relative(path: str | os.PathLike[str], input_root: str | os.PathLike[str]) -> str:
    """Best-effort input-relative posix path; does not follow junctions."""
    target = Path(os.path.abspath(str(path)))
    root = Path(os.path.abspath(str(input_root)))
    try:
        return target.relative_to(root).as_posix()
    except ValueError:
        try:
            resolved_target = Path(path).resolve()
            resolved_root = Path(input_root).resolve()
            return resolved_target.relative_to(resolved_root).as_posix()
        except Exception:
            return Path(path).name
