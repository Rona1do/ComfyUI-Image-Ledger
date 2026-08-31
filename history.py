from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from .ledger import Ledger, LedgerError


_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class HistoryReport:
    scanned_videos: int = 0
    metadata_found: int = 0
    hash_verified: int = 0
    unique_done: int = 0
    repeated_outputs: int = 0
    unresolved: int = 0
    errors: int = 0
    cached_files: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class LegacySource:
    filename: str
    sha256: str | None
    node_id: str


def find_ffmpeg() -> str:
    forced = os.environ.get("COMFYUI_IMAGE_LEDGER_FFMPEG", "").strip()
    if forced and Path(forced).is_file():
        return str(Path(forced).resolve())
    try:
        from imageio_ffmpeg import get_ffmpeg_exe

        candidate = get_ffmpeg_exe()
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    except Exception:
        pass
    candidate = shutil.which("ffmpeg")
    if candidate:
        return candidate
    raise LedgerError(
        "FFmpeg was not found. VideoHelperSuite/imageio-ffmpeg must be installed."
    )


def unescape_ffmetadata(value: str) -> str:
    output: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\" and index + 1 < len(value):
            following = value[index + 1]
            if following in ("\\", ";", "#", "="):
                output.append(following)
                index += 2
                continue
        output.append(char)
        index += 1
    return "".join(output)


def parse_ffmetadata_text(text: str) -> dict[str, object]:
    comment: str | None = None
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("comment="):
            continue
        value = line[len("comment=") :]
        while value.endswith("\\") and index + 1 < len(lines):
            index += 1
            value = value[:-1] + "\n" + lines[index]
        comment = unescape_ffmetadata(value)
        break
    if not comment:
        raise LedgerError("The video does not contain a ComfyUI comment metadata field.")
    parsed = json.loads(comment)
    if not isinstance(parsed, dict):
        raise LedgerError("The video comment metadata is not a JSON object.")
    return parsed


def read_video_metadata(
    video_path: str | os.PathLike[str],
    ffmpeg_path: str | None = None,
    timeout_seconds: int = 30,
) -> dict[str, object]:
    ffmpeg = ffmpeg_path or find_ffmpeg()
    process = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(Path(video_path).resolve()),
            "-map_metadata",
            "0",
            "-f",
            "ffmetadata",
            "-",
        ],
        capture_output=True,
        check=False,
        timeout=timeout_seconds,
        creationflags=_CREATE_NO_WINDOW,
    )
    if process.returncode != 0:
        error = process.stderr.decode("utf-8", errors="replace").strip()
        raise LedgerError(error or "FFmpeg could not read video metadata.")
    text = process.stdout.decode("utf-8", errors="replace")
    return parse_ffmetadata_text(text)


def _parse_prompt(metadata: dict[str, object]) -> dict[str, object]:
    raw_prompt = metadata.get("prompt")
    if isinstance(raw_prompt, str):
        parsed = json.loads(raw_prompt)
    elif isinstance(raw_prompt, dict):
        parsed = raw_prompt
    else:
        raise LedgerError("ComfyUI prompt metadata is missing.")
    if not isinstance(parsed, dict):
        raise LedgerError("ComfyUI prompt metadata is not an object.")
    return parsed


def _hash_from_is_changed(value: object) -> str | None:
    values: list[object]
    if isinstance(value, (list, tuple)):
        values = list(value)
    else:
        values = [value]
    for candidate in values:
        text = str(candidate).strip()
        if _SHA256_RE.fullmatch(text):
            return text.lower()
    return None


def find_legacy_first_frame(metadata: dict[str, object]) -> LegacySource:
    prompt = _parse_prompt(metadata)
    selected_id: str | None = None
    selected_node: dict[str, object] | None = None

    for node_id, raw_node in prompt.items():
        if not isinstance(raw_node, dict):
            continue
        meta = raw_node.get("_meta")
        title = meta.get("title") if isinstance(meta, dict) else None
        if title == "First-Frame-Image":
            selected_id = str(node_id)
            selected_node = raw_node
            break

    if selected_node is None:
        raw_node = prompt.get("23")
        if isinstance(raw_node, dict) and raw_node.get("class_type") == "LoadImage":
            selected_id = "23"
            selected_node = raw_node

    if selected_node is None or selected_id is None:
        raise LedgerError("First-Frame-Image was not found in embedded prompt metadata.")

    inputs = selected_node.get("inputs")
    if not isinstance(inputs, dict):
        raise LedgerError("First-Frame-Image inputs are missing.")
    filename = str(inputs.get("image", "")).strip()
    if not filename:
        raise LedgerError("First-Frame-Image filename is missing.")
    content_hash = _hash_from_is_changed(selected_node.get("is_changed"))
    return LegacySource(
        filename=filename,
        sha256=content_hash,
        node_id=selected_id,
    )


def discover_videos(
    output_root: str | os.PathLike[str],
    patterns: str | Iterable[str],
    recursive: bool,
) -> list[Path]:
    root = Path(output_root).expanduser().resolve()
    if not root.is_dir():
        raise LedgerError(f"Output folder does not exist: {root}")
    if isinstance(patterns, str):
        matchers = [
            value.strip()
            for value in patterns.replace(";", ",").split(",")
            if value.strip()
        ]
    else:
        matchers = [str(value).strip() for value in patterns if str(value).strip()]
    if not matchers:
        matchers = ["*.mp4"]
    iterator = root.rglob("*") if recursive else root.iterdir()
    videos = [
        path
        for path in iterator
        if path.is_file()
        and any(fnmatch.fnmatch(path.name.casefold(), mask.casefold()) for mask in matchers)
    ]
    videos.sort(key=lambda path: path.as_posix().casefold())
    return videos


def import_history(
    ledger: Ledger,
    campaign: str,
    output_root: str | os.PathLike[str],
    patterns: str | Iterable[str],
    recursive: bool = True,
    dry_run: bool = False,
    ffmpeg_path: str | None = None,
) -> tuple[HistoryReport, list[dict[str, object]]]:
    report = HistoryReport()
    details: list[dict[str, object]] = []
    dry_run_seen_hashes: set[str] = set()
    ffmpeg = ffmpeg_path or find_ffmpeg()
    videos = discover_videos(output_root, patterns, recursive)

    for video in videos:
        report.scanned_videos += 1
        stat = video.stat()
        cached = ledger.history_file_cache(video, stat.st_size, stat.st_mtime_ns)
        if cached and not dry_run:
            report.cached_files += 1
            state = str(cached.get("state", cached.get("cache_state", "")))
            if state == "imported":
                report.metadata_found += 1
                report.hash_verified += 1
                if bool(cached.get("newly_done")):
                    report.unique_done += 1
                else:
                    report.repeated_outputs += 1
            elif state == "unresolved":
                report.unresolved += 1
            elif state == "error":
                report.errors += 1
            details.append(cached)
            continue

        detail: dict[str, object] = {
            "path": str(video),
            "state": "error",
            "filename": "",
            "sha256": "",
            "newly_done": False,
        }
        try:
            metadata = read_video_metadata(video, ffmpeg)
            source = find_legacy_first_frame(metadata)
            report.metadata_found += 1
            detail["filename"] = source.filename
            content_hash = source.sha256 or ledger.hash_for_source_name(
                campaign, source.filename
            )
            if not content_hash:
                detail["state"] = "unresolved"
                detail["error"] = "No verified source hash could be resolved."
                report.unresolved += 1
            else:
                item = ledger.item_by_hash(campaign, content_hash)
                if not item:
                    detail["state"] = "unresolved"
                    detail["sha256"] = content_hash
                    detail["error"] = "Embedded source hash is not present in the input set."
                    report.unresolved += 1
                else:
                    if source.sha256:
                        filename_hash = ledger.hash_for_source_name(
                            campaign, source.filename
                        )
                        if filename_hash and filename_hash != source.sha256:
                            raise LedgerError(
                                "Embedded SHA-256 conflicts with the current source filename."
                            )
                    report.hash_verified += 1
                    detail["sha256"] = content_hash
                    if dry_run:
                        was_done = (
                            item["state"] == "done"
                            or content_hash in dry_run_seen_hashes
                        )
                        result = {
                            "matched": True,
                            "newly_done": not was_done,
                            "repeated": was_done,
                        }
                        dry_run_seen_hashes.add(content_hash)
                    else:
                        result = ledger.import_completed(
                            campaign, content_hash, video
                        )
                    detail["state"] = "imported"
                    detail["newly_done"] = bool(result["newly_done"])
                    if result["newly_done"]:
                        report.unique_done += 1
                    else:
                        report.repeated_outputs += 1
        except Exception as error:
            detail["state"] = "error"
            detail["error"] = str(error)
            report.errors += 1

        details.append(detail)
        if not dry_run:
            ledger.record_history_file(
                video,
                stat.st_size,
                stat.st_mtime_ns,
                str(detail["state"]),
                detail,
            )

    return report, details
