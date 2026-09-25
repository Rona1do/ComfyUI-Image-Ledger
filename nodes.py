from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

import folder_paths
import nodes as comfy_nodes

from .global_settings import library_folder
from .history import import_history
from .ledger import (
    DEFAULT_EXTENSIONS,
    Ledger,
    LedgerError,
    NoPendingImages,
    normalize_extensions,
    validate_output_path,
)


SESSION_ID = uuid.uuid4().hex
LEGACY_HISTORY_SYNCED: set[str] = set()
# campaign|folder|recursive -> held preview reservation (picked before Queue)
PREVIEW_HOLD: dict[str, dict[str, Any]] = {}
# campaign -> sha256 values skipped this ComfyUI session (avoid immediate re-draw)
PREVIEW_SKIPPED: dict[str, set[str]] = {}
VIDEO_EXTENSIONS = {
    ".mp4",
    ".webm",
    ".mkv",
    ".mov",
    ".avi",
    ".m4v",
}
SELECTION_MODES = [
    "random_no_repeat",
    "name_asc",
    "name_desc",
    "oldest",
    "newest",
]
SIMPLE_CAMPAIGN = "image-ledger-default"
SIMPLE_LEGACY_GLOB = "*.mp4"
SIMPLE_SHUFFLE_SEED = 1337
SIMPLE_LEASE_MINUTES = 1440


def _profile_data_root() -> Path:
    forced = os.environ.get("COMFYUI_IMAGE_LEDGER_DIR", "").strip()
    if forced:
        return Path(forced).expanduser().resolve()
    user_root = Path(folder_paths.get_user_directory()).resolve()
    default_profile = user_root / "default"
    profile_root = default_profile if default_profile.is_dir() else user_root
    return profile_root / "image_ledger"


def _ledger() -> Ledger:
    return Ledger(_profile_data_root() / "ledger.sqlite3", session_id=SESSION_ID)


def _logical_join(base: Path, relative: str) -> Path:
    """Join without resolving Windows junctions/symlinks (resolve() would leave base)."""
    raw = (relative or "").strip().replace("\\", "/").strip("/")
    if not raw:
        return Path(os.path.abspath(str(base)))
    # Reject absolute / drive-letter paths in the relative segment.
    if Path(raw).is_absolute() or ":" in raw.split("/")[0]:
        raise LedgerError(
            "Folder must be relative to ComfyUI/input (for example, AI). "
            "Do not use an absolute path. Link external folders under input first."
        )
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise LedgerError("Folder cannot leave the ComfyUI data folder.")
    logical = Path(os.path.abspath(str(base.joinpath(*parts))))
    base_abs = Path(os.path.abspath(str(base)))
    try:
        logical.relative_to(base_abs)
    except ValueError as error:
        raise LedgerError("Folder cannot leave the ComfyUI data folder.") from error
    return logical


def _resolve_relative_folder(
    base_folder: str | os.PathLike[str],
    relative_folder: str,
    *,
    must_exist: bool = False,
) -> Path:
    """Return a folder under base.

    Important: Windows directory junctions (e.g. input/AI → Pictures/AI) must stay
    addressable as under input. Using Path.resolve() would follow the junction and
    fail the containment check — so we only abspath the logical path here.
    """
    base = Path(os.path.abspath(str(Path(base_folder).expanduser())))
    logical = _logical_join(base, relative_folder)
    if must_exist:
        if not logical.exists():
            raise LedgerError(f"Source folder does not exist: {logical}")
        if not logical.is_dir():
            raise LedgerError(f"Source path is not a folder: {logical}")
    return logical


def _source_root(source_subfolder: str) -> Path:
    folder = _normalize_subfolder(source_subfolder)
    # If callers still pass a corrupted "true"/"false", treat as empty then the library default.
    if not folder:
        library = library_folder()
        if Path(os.path.abspath(str(folder_paths.get_input_directory())), library).is_dir():
            folder = library
    try:
        return _resolve_relative_folder(
            folder_paths.get_input_directory(),
            folder,
            must_exist=True,
        )
    except LedgerError:
        # Last resort: input root if it has images; else re-raise with clearer text.
        root = _resolve_relative_folder(
            folder_paths.get_input_directory(),
            "",
            must_exist=True,
        )
        if folder and folder.lower() in {"true", "false"}:
            raise LedgerError(
                "The source folder was read as true/false (a stale widget layout). "
                "Set Source folder to AI or reload the workflow."
            ) from None
        return root


def _output_root(output_subfolder: str) -> Path:
    return _resolve_relative_folder(
        folder_paths.get_output_directory(),
        output_subfolder,
        must_exist=False,
    )


def _input_relative(source_subfolder: str, job_rel_path: str) -> str:
    """Build LoadImage /view path relative to ComfyUI/input (works through junctions)."""
    folder = _normalize_subfolder(source_subfolder)
    rel = (job_rel_path or "").replace("\\", "/").lstrip("/")
    if folder and rel:
        return f"{folder}/{rel}"
    return folder or rel


def _preview_from_input_relative(selected: str) -> dict[str, str]:
    selected = (selected or "").replace("\\", "/").strip("/")
    if not selected:
        raise LedgerError("Empty image path for preview.")
    parts = selected.split("/")
    filename = parts[-1]
    subfolder = "/".join(parts[:-1])
    return {
        "filename": filename,
        "subfolder": subfolder,
        "type": "input",
        "selected": selected,
    }


def _status_text(status: dict[str, object]) -> str:
    return (
        f"Pending {status['pending']}  |  Running {status['running']}  |  "
        f"Done {status['done']}  |  Unique {status['total_unique']}  |  "
        f"Duplicates {status['duplicate_paths']}"
    )


def _ticket_payload(raw_ticket: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_ticket)
    except json.JSONDecodeError as error:
        raise LedgerError("Invalid image-ledger job ticket.") from error
    if not isinstance(parsed, dict) or parsed.get("v") != 1:
        raise LedgerError("Unsupported image-ledger job ticket.")
    mode = str(parsed.get("mode") or "tracked").strip()
    if mode == "manual_untracked":
        selected = str(parsed.get("selected") or "").strip()
        if not selected:
            raise LedgerError("Manual ticket is missing selected path.")
        return parsed
    token = str(parsed.get("token", "")).strip()
    if len(token) != 32:
        raise LedgerError("Image-ledger job ticket is missing its token.")
    return parsed


PICK_MODE_RANDOM = "Random preview"
PICK_MODE_MANUAL = "Manual selection"
PICK_MODES = (PICK_MODE_RANDOM, PICK_MODE_MANUAL)


def _is_manual_mode(pick_mode: str) -> bool:
    text = (pick_mode or "").strip()
    return text in {PICK_MODE_MANUAL, "manual", "手动", "手动指定路径", "手动选择"}


def _final_video_from_vhs(filenames: Any) -> Path:
    if not isinstance(filenames, (list, tuple)) or len(filenames) != 2:
        raise LedgerError("VHS_FILENAMES has an unexpected value.")
    save_output, output_files = filenames
    if not bool(save_output):
        raise LedgerError("VHS VideoCombine is configured to save into temp, not output.")
    if not isinstance(output_files, (list, tuple)) or not output_files:
        raise LedgerError("VHS VideoCombine returned no output files.")
    final_path = Path(str(output_files[-1])).expanduser()
    if final_path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise LedgerError(
            f"The final VHS output is not a supported video file: {final_path}"
        )
    return validate_output_path(
        final_path,
        folder_paths.get_output_directory(),
        minimum_size=1,
    )


def _normalize_subfolder(source_subfolder: str | bool | None) -> str:
    """Normalize input-relative folder; tolerate widget-order corruption.

    After new widgets were inserted, old graphs sometimes bind the recursive
    boolean into source_subfolder → path becomes input/true.
    """
    if isinstance(source_subfolder, bool):
        return ""
    text = str(source_subfolder or "").strip().replace("\\", "/").strip("/")
    if text.lower() in {"true", "false", "none", "null", "undefined"}:
        return ""
    return text


def _hold_key(campaign: str, source_subfolder: str, recursive: bool) -> str:
    return f"{campaign.strip()}|{_normalize_subfolder(source_subfolder)}|{int(bool(recursive))}"


def _preview_image_payload(
    abs_path: str | Path,
    *,
    source_subfolder: str = "",
    job_rel_path: str = "",
) -> dict[str, str]:
    """Map a selected file to ComfyUI /view + LoadImage coordinates.

    abs_path may point at a junction target (outside input after resolve). Always
    prefer source_subfolder + job_rel_path for the public relative path.
    """
    if source_subfolder or job_rel_path:
        return _preview_from_input_relative(
            _input_relative(source_subfolder, job_rel_path or Path(abs_path).name)
        )

    input_root = Path(os.path.abspath(str(folder_paths.get_input_directory())))
    selected = Path(abs_path)
    # Try logical path first (does not follow junction).
    candidates = [
        selected,
        Path(os.path.abspath(str(selected))),
    ]
    try:
        candidates.append(selected.resolve())
    except OSError:
        pass
    for candidate in candidates:
        try:
            # Only succeeds when file is truly under input without leaving via resolve.
            relative = Path(os.path.abspath(str(candidate))).relative_to(input_root)
            return _preview_from_input_relative(relative.as_posix())
        except ValueError:
            continue
    raise LedgerError(
        "Could not map the source image back to ComfyUI/input. "
        "If you use a directory link, source_subfolder must name that link."
    )


def _hold_payload(hold: dict[str, Any] | None, status: dict[str, object] | None = None) -> dict[str, Any]:
    if not hold:
        return {
            "held": False,
            "message": "No image is held. Pick a preview first.",
            "status_text": _status_text(status) if status else "",
            **(status or {}),
        }
    image = hold.get("image") or {}
    out: dict[str, Any] = {
        "held": True,
        "token": hold.get("token"),
        "campaign": hold.get("campaign"),
        "source_subfolder": hold.get("source_subfolder"),
        "recursive": bool(hold.get("recursive")),
        "selected": image.get("selected") or hold.get("selected"),
        "selected_sha256": hold.get("sha256"),
        "image": image,
        "message": f"Preview held: {image.get('selected') or hold.get('selected')}. "
        "Run this image or skip to another one.",
        "status_text": _status_text(status) if status else hold.get("status_text", ""),
    }
    if status:
        out.update(status)
    return out


def _clear_hold_for_token(token: str) -> None:
    dead = [key for key, hold in PREVIEW_HOLD.items() if hold.get("token") == token]
    for key in dead:
        PREVIEW_HOLD.pop(key, None)


def _get_valid_hold(
    campaign: str,
    source_subfolder: str,
    recursive: bool,
) -> dict[str, Any] | None:
    key = _hold_key(campaign, source_subfolder, recursive)
    hold = PREVIEW_HOLD.get(key)
    if not hold:
        return None
    job = _ledger().get_job(str(hold.get("token", "")))
    if job is None:
        PREVIEW_HOLD.pop(key, None)
        return None
    # Refresh paths in case files moved within input.
    hold["abs_path"] = job.abs_path
    hold["rel_path"] = job.rel_path
    hold["sha256"] = job.sha256
    hold["image"] = _preview_image_payload(
        job.abs_path,
        source_subfolder=str(hold.get("source_subfolder") or source_subfolder),
        job_rel_path=job.rel_path,
    )
    hold["selected"] = hold["image"]["selected"]
    return hold


def prepare_preview(
    *,
    campaign: str,
    source_subfolder: str,
    recursive: bool,
    action: str = "pick",
    selection_mode: str = "random_no_repeat",
    shuffle_seed: int = SIMPLE_SHUFFLE_SEED,
    lease_minutes: int = SIMPLE_LEASE_MINUTES,
    extensions: str | None = None,
    sync_legacy_history: bool = True,
    legacy_video_glob: str = SIMPLE_LEGACY_GLOB,
    pick_mode: str = PICK_MODE_RANDOM,
    manual_path: str = "",
) -> dict[str, Any]:
    """Pick / skip / release / status for before-queue preview."""
    campaign = campaign.strip() or SIMPLE_CAMPAIGN
    source_subfolder = _normalize_subfolder(source_subfolder)
    action = (action or "pick").strip().lower()
    if action not in {"pick", "skip", "release", "status"}:
        raise LedgerError(f"Unknown preview action: {action}")

    ledger = _ledger()
    key = _hold_key(campaign, source_subfolder, recursive)
    extensions = extensions if extensions is not None else ",".join(DEFAULT_EXTENSIONS)

    # Manual mode: preview exactly the path the user typed (no random).
    if _is_manual_mode(pick_mode):
        if action in {"skip"}:
            raise LedgerError("Skip is unavailable in manual mode. Choose another path instead.")
        if action == "release":
            PREVIEW_HOLD.pop(key, None)
            status = ledger.status(campaign)
            return {
                **_hold_payload(None, status),
                "manual": True,
                "message": "Manual preview cleared. No file was changed.",
            }
        if action == "status":
            status = ledger.status(campaign)
            hold = PREVIEW_HOLD.get(key)
            if hold and hold.get("manual"):
                return {**_hold_payload(hold, status), "manual": True}
            return {
                **_hold_payload(None, status),
                "manual": True,
                "message": "Manual mode: enter a path, then preview it.",
            }
        annotated, abs_path = _manual_input_path(manual_path)
        image = _preview_from_input_relative(annotated)
        hold = {
            "token": "",
            "campaign": campaign,
            "source_subfolder": source_subfolder,
            "recursive": bool(recursive),
            "manual": True,
            "sha256": "",
            "abs_path": str(abs_path),
            "rel_path": annotated,
            "selected": annotated,
            "image": image,
            "status_text": "",
        }
        PREVIEW_HOLD[key] = hold
        status = ledger.status(campaign)
        payload = _hold_payload(hold, status)
        payload["manual"] = True
        payload["message"] = (
            f"Manual preview: {annotated}. Run this image or use ComfyUI's Queue button."
        )
        return payload

    if action == "status":
        status = ledger.status(campaign) if campaign else {}
        hold = _get_valid_hold(campaign, source_subfolder, recursive)
        return _hold_payload(hold, status if isinstance(status, dict) else None)

    source_root = _source_root(source_subfolder)
    sync = ledger.sync_campaign(
        campaign=campaign,
        source_root=source_root,
        recursive=bool(recursive),
        selection_mode=selection_mode,
        shuffle_seed=int(shuffle_seed),
        extensions=normalize_extensions(extensions),
    )
    if bool(sync_legacy_history) and campaign not in LEGACY_HISTORY_SYNCED:
        legacy_root = _output_root("video")
        if legacy_root.is_dir():
            legacy_report, _legacy_details = import_history(
                ledger=ledger,
                campaign=campaign,
                output_root=legacy_root,
                patterns=legacy_video_glob,
                recursive=True,
                dry_run=False,
            )
            if legacy_report.errors or legacy_report.unresolved:
                raise LedgerError(
                    "Some legacy videos could not be resolved, so selection stopped to avoid repeats. "
                    "Review the history import report or disable sync_legacy_history."
                )
        LEGACY_HISTORY_SYNCED.add(campaign)

    existing = _get_valid_hold(campaign, source_subfolder, recursive)

    if action == "release":
        if existing:
            ledger.fail_job(
                str(existing["token"]),
                "Preview reservation released by user.",
                release=True,
            )
            PREVIEW_HOLD.pop(key, None)
        status = ledger.status(campaign)
        return {
            **_hold_payload(None, status),
            "message": "Preview reservation released.",
            "scanned_paths": sync.scanned_paths,
        }

    skipped = PREVIEW_SKIPPED.setdefault(campaign, set())

    if action == "skip" and existing:
        skipped_sha = ledger.skip_job_to_end(
            str(existing["token"]),
            reason="User skipped preview image",
        )
        if skipped_sha:
            skipped.add(str(skipped_sha).lower())
        elif existing.get("sha256"):
            skipped.add(str(existing["sha256"]).lower())
        PREVIEW_HOLD.pop(key, None)
        existing = None
    elif action == "pick" and existing:
        # Keep current locked preview; do not re-roll.
        status = ledger.status(campaign)
        payload = _hold_payload(existing, status)
        payload["message"] = (
            f"Preview already held: {payload.get('selected')}. "
            "Skip it to draw another image, or run this one."
        )
        payload["scanned_paths"] = sync.scanned_paths
        payload["newly_hashed"] = sync.newly_hashed
        payload["cached_hashes"] = sync.cached_hashes
        payload["skipped_session"] = len(skipped)
        return payload

    try:
        job = ledger.reserve_next(
            campaign=campaign,
            selection_mode=selection_mode,
            lease_minutes=int(lease_minutes),
            auto_queue=False,
            exclude_sha256=list(skipped),
        )
    except NoPendingImages:
        status = ledger.status(campaign)
        raise NoPendingImages(
            f"{_status_text(status)}. No pending images remain in this ledger."
        ) from None

    # If we had to fall back onto a previously skipped hash, clear the skip set
    # so the queue can cycle after every pending image was skipped once.
    job_sha = str(job.sha256).lower()
    if job_sha in skipped and len(skipped) > 0:
        # reserve_next only falls back when nothing else is left.
        skipped.clear()

    image = _preview_image_payload(
        job.abs_path,
        source_subfolder=source_subfolder,
        job_rel_path=job.rel_path,
    )
    hold = {
        "token": job.token,
        "campaign": campaign,
        "source_subfolder": source_subfolder,
        "recursive": bool(recursive),
        "selection_mode": selection_mode,
        "shuffle_seed": int(shuffle_seed),
        "lease_minutes": int(lease_minutes),
        "sha256": job.sha256,
        "abs_path": job.abs_path,
        "rel_path": job.rel_path,
        "selected": image["selected"],
        "image": image,
        "status_text": "",
    }
    PREVIEW_HOLD[key] = hold
    status = ledger.status(campaign)
    payload = _hold_payload(hold, status)
    payload["scanned_paths"] = sync.scanned_paths
    payload["newly_hashed"] = sync.newly_hashed
    payload["cached_hashes"] = sync.cached_hashes
    payload["skipped_session"] = len(skipped)
    payload["message"] = (
        f"Preview selected: {image['selected']}. It has not been processed yet. "
        "Run it or skip to another image."
        + (f" Skipped this session: {len(skipped)}." if skipped else "")
    )
    return payload


class ImageLedgerTrackedImageLoader:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "campaign": (
                    "STRING",
                    {
                        "default": "image-ledger-default",
                        "multiline": False,
                        "display_name": "Ledger name",
                        "hidden": True,
                        "tooltip": "Loaders with the same name share completion history.",
                    },
                ),
                "source_subfolder": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "display_name": "Source folder (under input)",
                        "tooltip": "For example, AI means ComfyUI/input/AI. Leave empty for the input root.",
                    },
                ),
                "recursive": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "display_name": "Scan subfolders",
                        "label_on": "Recursive",
                        "label_off": "Top level only",
                        "tooltip": "Disable this when scanning the input root to avoid temporary folders.",
                    },
                ),
                "selection_mode": (
                    SELECTION_MODES,
                    {
                        "default": "random_no_repeat",
                        "display_name": "Selection mode",
                        "hidden": True,
                        "tooltip": "random_no_repeat is a stable shuffle without replacement.",
                    },
                ),
                "shuffle_seed": (
                    "INT",
                    {
                        "default": SIMPLE_SHUFFLE_SEED,
                        "min": 0,
                        "max": 0x7FFFFFFF,
                        "step": 1,
                        "display_name": "Shuffle seed",
                        "hidden": True,
                    },
                ),
                "lease_minutes": (
                    "INT",
                    {
                        "default": 1440,
                        "min": 10,
                        "max": 10080,
                        "step": 10,
                        "display_name": "Reservation timeout (minutes)",
                        "hidden": True,
                        "tooltip": "How long an uncommitted task stays reserved.",
                    },
                ),
                "auto_queue_next": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "display_name": "Queue next after success",
                        "label_on": "Continuous",
                        "label_off": "One at a time",
                        "tooltip": "Enable only after confirming that the workflow commits its final video.",
                    },
                ),
                "extensions": (
                    "STRING",
                    {
                        "default": ",".join(DEFAULT_EXTENSIONS),
                        "multiline": False,
                        "display_name": "Image extensions",
                        "hidden": True,
                    },
                ),
                "sync_legacy_history": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "display_name": "Import legacy videos on startup",
                        "hidden": True,
                        "tooltip": "Incrementally imports metadata from existing videos once per session.",
                    },
                ),
                "legacy_video_glob": (
                    "STRING",
                    {
                        "default": "*.mp4",
                        "multiline": False,
                        "display_name": "Legacy video glob",
                        "hidden": True,
                    },
                ),
            }
        }

    RETURN_TYPES = ("IMAGE", "MASK", "STRING", "STRING", "INT", "INT", "STRING")
    RETURN_NAMES = (
        "image",
        "mask",
        "job_ticket",
        "source_path",
        "remaining_pending",
        "completed",
        "status",
    )
    FUNCTION = "load_next"
    CATEGORY = "Image Ledger"
    DESCRIPTION = (
        "Selects an unprocessed source image from ComfyUI/input. "
        "The image is marked complete only after the final video is saved successfully."
    )
    # Keep intermediate UI (ledger_status) but avoid a second native image strip:
    # preview is shown only in the custom panel (web/image_ledger.js).
    OUTPUT_NODE = True
    HAS_INTERMEDIATE_OUTPUT = True

    @classmethod
    def IS_CHANGED(
        cls,
        campaign,
        source_subfolder,
        recursive,
        selection_mode,
        shuffle_seed,
        lease_minutes,
        auto_queue_next,
        extensions,
        sync_legacy_history,
        legacy_video_glob,
    ):
        del (
            campaign,
            source_subfolder,
            recursive,
            selection_mode,
            shuffle_seed,
            lease_minutes,
            auto_queue_next,
            extensions,
            sync_legacy_history,
            legacy_video_glob,
        )
        return float("nan")

    def load_next(
        self,
        campaign: str,
        source_subfolder: str,
        recursive: bool,
        selection_mode: str,
        shuffle_seed: int,
        lease_minutes: int,
        auto_queue_next: bool,
        extensions: str,
        sync_legacy_history: bool,
        legacy_video_glob: str,
    ):
        campaign = campaign.strip()
        source_subfolder = _normalize_subfolder(source_subfolder)
        ledger = _ledger()
        source_root = _source_root(source_subfolder)
        sync = ledger.sync_campaign(
            campaign=campaign,
            source_root=source_root,
            recursive=bool(recursive),
            selection_mode=selection_mode,
            shuffle_seed=int(shuffle_seed),
            extensions=normalize_extensions(extensions),
        )
        legacy_report = None
        if bool(sync_legacy_history) and campaign not in LEGACY_HISTORY_SYNCED:
            legacy_root = _output_root("video")
            if legacy_root.is_dir():
                legacy_report, _legacy_details = import_history(
                    ledger=ledger,
                    campaign=campaign,
                    output_root=legacy_root,
                    patterns=legacy_video_glob,
                    recursive=True,
                    dry_run=False,
                )
                if legacy_report.errors or legacy_report.unresolved:
                    raise LedgerError(
                        "Some legacy videos could not be resolved, so selection stopped to avoid repeats. "
                        "Review the history import report or disable sync_legacy_history."
                    )
            LEGACY_HISTORY_SYNCED.add(campaign)
        # Prefer the image the user already previewed & locked (before Queue).
        hold = _get_valid_hold(campaign, source_subfolder, recursive)
        job = None
        if hold is not None:
            job = ledger.get_job(str(hold["token"]))
            if job is not None:
                ledger.set_job_auto_queue(job.token, bool(auto_queue_next))
        if job is None:
            try:
                job = ledger.reserve_next(
                    campaign=campaign,
                    selection_mode=selection_mode,
                    lease_minutes=int(lease_minutes),
                    auto_queue=bool(auto_queue_next),
                )
            except NoPendingImages:
                status = ledger.status(campaign)
                raise NoPendingImages(
                    f"{_status_text(status)}. No pending images remain in this ledger."
                )
            # Keep hold in sync so UI and commit share the same reservation.
            image_meta = _preview_image_payload(
                job.abs_path,
                source_subfolder=source_subfolder,
                job_rel_path=job.rel_path,
            )
            PREVIEW_HOLD[_hold_key(campaign, source_subfolder, recursive)] = {
                "token": job.token,
                "campaign": campaign,
                "source_subfolder": source_subfolder,
                "recursive": bool(recursive),
                "sha256": job.sha256,
                "abs_path": job.abs_path,
                "rel_path": job.rel_path,
                "selected": image_meta["selected"],
                "image": image_meta,
            }

        # abs_path may be the junction target (outside input after resolve).
        selected = Path(job.abs_path)
        try:
            selected_stat = selected.stat()
        except OSError as error:
            ledger.fail_job(
                job.token,
                f"Source image disappeared before loading: {error}",
                release=True,
            )
            raise
        if (
            selected_stat.st_size != job.size_bytes
            or selected_stat.st_mtime_ns != job.mtime_ns
        ):
            ledger.fail_job(
                job.token,
                "Source image changed after it was registered; retrying with a fresh hash.",
                release=True,
            )
            raise LedgerError(
                "The source image changed after indexing. Run again to refresh its hash."
            )

        # Always load via input-relative path so junctions work with LoadImage /view.
        annotated = _input_relative(source_subfolder, job.rel_path)
        preview_meta = _preview_from_input_relative(annotated)

        try:
            image, mask = comfy_nodes.LoadImage().load_image(annotated)
        except Exception as error:
            ledger.fail_job(job.token, f"LoadImage failed: {error}", release=True)
            raise

        status = ledger.status(campaign)
        ticket = json.dumps(
            {
                "v": 1,
                "token": job.token,
                "campaign": campaign,
                "auto_queue": bool(auto_queue_next),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        ui_payload = {
            **status,
            "held": True,
            "selected": preview_meta["selected"],
            "selected_sha256": job.sha256,
            "attempts": job.attempts,
            "scanned_paths": sync.scanned_paths,
            "newly_hashed": sync.newly_hashed,
            "cached_hashes": sync.cached_hashes,
            "legacy_scanned": legacy_report.scanned_videos if legacy_report else 0,
            "legacy_unique_done": legacy_report.unique_done if legacy_report else 0,
            "legacy_repeated_outputs": (
                legacy_report.repeated_outputs if legacy_report else 0
            ),
            "message": (
                f"Generation started: {preview_meta['selected']}; {_status_text(status)}"
            ),
        }
        # Do not send ui.images — that creates a second native preview under the node.
        # The custom panel already shows one large preview from ledger_status.selected.
        return {
            "ui": {
                "ledger_status": [
                    json.dumps(ui_payload, ensure_ascii=False, separators=(",", ":"))
                ],
            },
            "result": (
                image,
                mask,
                ticket,
                str(selected),
                int(status["pending"]),
                int(status["done"]),
                _status_text(status),
            ),
        }


def _manual_input_path(manual_path: str) -> tuple[str, Path]:
    """Return (input-relative posix path, absolute path) for a manual selection."""
    raw = (manual_path or "").strip().replace("\\", "/")
    if not raw:
        raise LedgerError(
            "Manual mode requires a path relative to ComfyUI/input, for example AI/portraits/1.png"
        )
    if Path(raw).is_absolute() or ":" in raw.split("/")[0]:
        raise LedgerError(
            "Manual path must be relative to input (for example AI/1.png), not an absolute path."
        )
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise LedgerError("Manual path cannot contain '..'.")
    annotated = "/".join(parts)
    input_root = Path(os.path.abspath(str(folder_paths.get_input_directory())))
    abs_path = Path(os.path.abspath(str(input_root.joinpath(*parts))))
    try:
        abs_path.relative_to(input_root)
    except ValueError as error:
        raise LedgerError("Manual path cannot leave ComfyUI/input.") from error
    if not abs_path.is_file():
        raise LedgerError(f"Manual image not found: {annotated}")
    return annotated, abs_path


def _result_from_manual_selection(
    *,
    campaign: str,
    annotated: str,
    abs_path: Path,
    auto_queue_next: bool,
    source_subfolder: str,
    recursive: bool,
) -> dict[str, Any]:
    """Load a user-chosen image; reserve in ledger when possible."""
    ledger = _ledger()
    # Best-effort sync so the chosen file is known to the campaign.
    try:
        ledger.sync_campaign(
            campaign=campaign,
            source_root=_source_root(source_subfolder),
            recursive=bool(recursive),
            selection_mode="random_no_repeat",
            shuffle_seed=SIMPLE_SHUFFLE_SEED,
            extensions=normalize_extensions(",".join(DEFAULT_EXTENSIONS)),
        )
    except Exception:
        # Manual load should still work even if folder sync fails.
        pass

    preview_meta = _preview_from_input_relative(annotated)
    selected_stat = abs_path.stat()
    job = None
    item = ledger.find_item_by_abs_path(campaign, abs_path)
    if item is not None and str(item["state"]) == "pending" and int(item["available"]) == 1:
        job = ledger.reserve_existing_item(
            campaign=campaign,
            sha256=str(item["sha256"]),
            lease_minutes=SIMPLE_LEASE_MINUTES,
            auto_queue=bool(auto_queue_next),
            allow_from_states=("pending",),
        )
    elif item is not None and str(item["state"]) == "running":
        existing = ledger.get_job(str(item["current_token"] or ""))
        if existing is not None:
            job = existing
            ledger.set_job_auto_queue(job.token, bool(auto_queue_next))

    try:
        image, mask = comfy_nodes.LoadImage().load_image(annotated)
    except Exception as error:
        if job is not None:
            ledger.fail_job(job.token, f"LoadImage failed: {error}", release=True)
        raise

    status = ledger.status(campaign)
    if job is not None:
        key = _hold_key(campaign, source_subfolder, recursive)
        PREVIEW_HOLD[key] = {
            "token": job.token,
            "campaign": campaign,
            "source_subfolder": _normalize_subfolder(source_subfolder),
            "recursive": bool(recursive),
            "sha256": job.sha256,
            "abs_path": job.abs_path,
            "rel_path": job.rel_path,
            "selected": annotated,
            "image": preview_meta,
        }
        ticket = json.dumps(
            {
                "v": 1,
                "mode": "tracked",
                "token": job.token,
                "campaign": campaign,
                "auto_queue": bool(auto_queue_next),
                "selected": annotated,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        message = (
            f"Manual image reserved in the ledger: {annotated}; {_status_text(status)}"
        )
    else:
        ticket = json.dumps(
            {
                "v": 1,
                "mode": "manual_untracked",
                "token": "",
                "campaign": campaign,
                "auto_queue": False,
                "selected": annotated,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        state_note = ""
        if item is not None:
            state_note = f" (ledger state={item['state']}; unchanged)"
        message = f"Manual image outside the random queue{state_note}: {annotated}; {_status_text(status)}"

    ui_payload = {
        **status,
        "held": True,
        "manual": True,
        "selected": annotated,
        "message": message,
    }
    return {
        "ui": {
            "ledger_status": [
                json.dumps(ui_payload, ensure_ascii=False, separators=(",", ":"))
            ],
        },
        "result": (
            image,
            mask,
            ticket,
            str(abs_path),
            int(status["pending"]),
            int(status["done"]),
            message,
        ),
    }


class ImageLedgerSimpleTrackedImageLoader(ImageLedgerTrackedImageLoader):
    """Workflow entry: random preview queue OR manual path, then full run."""

    @classmethod
    def INPUT_TYPES(cls):
        # Keep first three widgets stable so old workflows
        # [source_subfolder, recursive, auto_queue_next] still map correctly.
        return {
            "required": {
                "source_subfolder": (
                    "STRING",
                    {
                        "default": library_folder(),
                        "multiline": False,
                        "display_name": "Source folder (under input)",
                        "tooltip": (
                            "Random mode scans this directory. For example, AI means input/AI. "
                            "External folders can be linked under ComfyUI/input."
                        ),
                    },
                ),
                "recursive": (
                    "BOOLEAN",
                    {
                        "default": True,
                        "display_name": "Scan subfolders",
                        "label_on": "Recursive",
                        "label_off": "Top level only",
                        "tooltip": "Whether random mode includes nested folders.",
                    },
                ),
                "auto_queue_next": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "display_name": "Queue next after success",
                        "label_on": "Continuous",
                        "label_off": "One at a time",
                        "tooltip": (
                            "In random mode, queues the next image after a successful commit. "
                            "Keep this off for manual selection."
                        ),
                    },
                ),
                "pick_mode": (
                    list(PICK_MODES),
                    {
                        "default": PICK_MODE_RANDOM,
                        "display_name": "Selection mode",
                        "tooltip": (
                            "Random preview draws without repetition. "
                            "Manual selection previews a path relative to input."
                        ),
                    },
                ),
                "manual_path": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "display_name": "Manual path (under input)",
                        "tooltip": (
                            "Used only in manual mode, for example AI/portraits/1.png. "
                            "Absolute paths are not allowed."
                        ),
                    },
                ),
            }
        }

    DESCRIPTION = (
        "Random preview draws without repetition; manual selection uses an input-relative path. "
        "Preview first, then explicitly queue the selected image."
    )

    @classmethod
    def IS_CHANGED(
        cls,
        source_subfolder,
        recursive,
        auto_queue_next,
        pick_mode,
        manual_path,
    ):
        del source_subfolder, recursive, auto_queue_next, pick_mode, manual_path
        return float("nan")

    def load_next(
        self,
        source_subfolder: str,
        recursive: bool,
        auto_queue_next: bool,
        pick_mode: str = PICK_MODE_RANDOM,
        manual_path: str = "",
    ):
        folder = _normalize_subfolder(source_subfolder)
        # Recover common widget-order corruption: boolean leaked into folder field.
        if not folder and Path(folder_paths.get_input_directory(), library_folder()).is_dir():
            folder = library_folder()
        if _is_manual_mode(pick_mode):
            annotated, abs_path = _manual_input_path(manual_path)
            return _result_from_manual_selection(
                campaign=SIMPLE_CAMPAIGN,
                annotated=annotated,
                abs_path=abs_path,
                auto_queue_next=bool(auto_queue_next),
                source_subfolder=folder,
                recursive=bool(recursive),
            )
        if not folder:
            # Empty = input root; but after corruption we prefer AI if present.
            pass
        return super().load_next(
            campaign=SIMPLE_CAMPAIGN,
            source_subfolder=folder,
            recursive=bool(recursive),
            selection_mode="random_no_repeat",
            shuffle_seed=SIMPLE_SHUFFLE_SEED,
            lease_minutes=SIMPLE_LEASE_MINUTES,
            auto_queue_next=bool(auto_queue_next),
            extensions=",".join(DEFAULT_EXTENSIONS),
            sync_legacy_history=True,
            legacy_video_glob=SIMPLE_LEGACY_GLOB,
        )


class ImageLedgerCommitTrackedVideo:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "filenames": (
                    "VHS_FILENAMES",
                    {"display_name": "Final video files"},
                ),
                "job_ticket": (
                    "STRING",
                    {
                        "forceInput": True,
                        "display_name": "Job ticket",
                        "tooltip": "Connect job_ticket from the Tracked Image Loader.",
                    },
                ),
            }
        }

    RETURN_TYPES = ("VHS_FILENAMES", "STRING")
    RETURN_NAMES = ("filenames", "status")
    FUNCTION = "commit"
    CATEGORY = "Image Ledger"
    DESCRIPTION = "Marks the source complete only after the final video exists and is non-empty."
    OUTPUT_NODE = True

    def commit(self, filenames: Any, job_ticket: str):
        ticket = _ticket_payload(job_ticket)
        ledger = _ledger()

        # Manual path not in pending queue: still require real video, skip ledger write.
        if str(ticket.get("mode") or "") == "manual_untracked":
            final_video = _final_video_from_vhs(filenames)
            selected = str(ticket.get("selected") or "")
            campaign = str(ticket.get("campaign") or SIMPLE_CAMPAIGN)
            status = ledger.status(campaign)
            message = (
                f"Manual video saved without changing the random queue: {final_video.name}"
                + (f"; source {selected}" if selected else "")
                + f"; {_status_text(status)}"
            )
            ui_payload = {
                **status,
                "committed": False,
                "idempotent": True,
                "manual": True,
                "output_path": str(final_video),
                "auto_queue": False,
                "message": message,
            }
            return {
                "ui": {
                    "ledger_commit": [
                        json.dumps(ui_payload, ensure_ascii=False, separators=(",", ":"))
                    ]
                },
                "result": (filenames, message),
            }

        token = str(ticket["token"])
        try:
            final_video = _final_video_from_vhs(filenames)
            result = ledger.commit_job(token, final_video)
        except Exception as error:
            ledger.fail_job(
                token,
                f"Final video commit failed: {error}",
                release=True,
            )
            _clear_hold_for_token(token)
            raise

        campaign = str(result["campaign"])
        _clear_hold_for_token(token)
        try:
            item = ledger.item_by_hash(campaign, str(result.get("sha256") or ""))
            if item and item.get("abs_path"):
                from .global_tracker import mark_committed_job

                mark_committed_job(
                    str(item["abs_path"]),
                    str(result.get("output_path") or ""),
                    campaign,
                )
        except Exception as hook_error:
            print(f"[ComfyUI Image Ledger] Commit bridge failed: {hook_error}")
        status = ledger.status(campaign)
        message = (
            f"Committed video: {Path(str(result['output_path'])).name}; "
            f"{_status_text(status)}"
        )
        ui_payload = {
            **status,
            "committed": bool(result["committed"]),
            "idempotent": bool(result["idempotent"]),
            "output_path": str(result["output_path"]),
            "auto_queue": bool(result["auto_queue"]),
            "message": message,
        }
        return {
            "ui": {
                "ledger_commit": [
                    json.dumps(ui_payload, ensure_ascii=False, separators=(",", ":"))
                ]
            },
            "result": (filenames, message),
        }


class ImageLedgerStatus:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "campaign": ("STRING", {"default": "image-ledger-default", "multiline": False}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("status",)
    FUNCTION = "show_status"
    CATEGORY = "Image Ledger"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, campaign):
        del campaign
        return float("nan")

    def show_status(self, campaign: str):
        status = _ledger().status(campaign.strip())
        text = _status_text(status)
        return {"ui": {"text": [text]}, "result": (text,)}


class ImageLedgerControl:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "campaign": ("STRING", {"default": "image-ledger-default", "multiline": False}),
                "action": (
                    [
                        "status_only",
                        "release_previous_session",
                        "release_all_running_for_retry",
                    ],
                ),
                "confirmation": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "tooltip": "Enter RETRY before releasing tasks from the current session.",
                    },
                ),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("result",)
    FUNCTION = "control"
    CATEGORY = "Image Ledger"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, campaign, action, confirmation):
        del campaign, action, confirmation
        return float("nan")

    def control(self, campaign: str, action: str, confirmation: str):
        campaign = campaign.strip()
        ledger = _ledger()
        released = 0
        if action == "release_previous_session":
            released = ledger.release_running(
                campaign,
                include_current_session=False,
            )
        elif action == "release_all_running_for_retry":
            if confirmation.strip() != "RETRY":
                raise LedgerError("Enter RETRY to release images running in this session.")
            released = ledger.release_running(
                campaign,
                include_current_session=True,
            )
        status = ledger.status(campaign)
        text = f"Released {released} task(s); {_status_text(status)}"
        return {"ui": {"text": [text]}, "result": (text,)}


class ImageLedgerImportLegacyVideoHistory:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "campaign": ("STRING", {"default": "image-ledger-default", "multiline": False}),
                "source_subfolder": (
                    "STRING",
                    {"default": "", "multiline": False},
                ),
                "source_recursive": ("BOOLEAN", {"default": False}),
                "output_subfolder": (
                    "STRING",
                    {"default": "video", "multiline": False},
                ),
                "filename_glob": (
                    "STRING",
                    {
                        "default": "*.mp4",
                        "multiline": False,
                    },
                ),
                "dry_run": ("BOOLEAN", {"default": True}),
                "extensions": (
                    "STRING",
                    {
                        "default": ",".join(DEFAULT_EXTENSIONS),
                        "multiline": False,
                    },
                ),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("report",)
    FUNCTION = "run_import"
    CATEGORY = "Image Ledger"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(
        cls,
        campaign,
        source_subfolder,
        source_recursive,
        output_subfolder,
        filename_glob,
        dry_run,
        extensions,
    ):
        del (
            campaign,
            source_subfolder,
            source_recursive,
            output_subfolder,
            filename_glob,
            dry_run,
            extensions,
        )
        return float("nan")

    def run_import(
        self,
        campaign: str,
        source_subfolder: str,
        source_recursive: bool,
        output_subfolder: str,
        filename_glob: str,
        dry_run: bool,
        extensions: str,
    ):
        campaign = campaign.strip()
        ledger = _ledger()
        ledger.sync_campaign(
            campaign=campaign,
            source_root=_source_root(source_subfolder),
            recursive=bool(source_recursive),
            selection_mode="random_no_repeat",
            shuffle_seed=SIMPLE_SHUFFLE_SEED,
            extensions=normalize_extensions(extensions),
        )
        report, _details = import_history(
            ledger=ledger,
            campaign=campaign,
            output_root=_output_root(output_subfolder),
            patterns=filename_glob,
            recursive=True,
            dry_run=bool(dry_run),
        )
        values = report.to_dict()
        text = (
            f"Scanned {values['scanned_videos']}; metadata {values['metadata_found']}; "
            f"hash verified {values['hash_verified']}; newly done {values['unique_done']}; "
            f"duplicate videos {values['repeated_outputs']}; unresolved {values['unresolved']}; "
            f"errors {values['errors']}; cached {values['cached_files']}; "
            f"mode {'dry run' if dry_run else 'written'}"
        )
        return {
            "ui": {
                "text": [text],
                "ledger_history": [
                    json.dumps(values, ensure_ascii=False, separators=(",", ":"))
                ],
            },
            "result": (text,),
        }
