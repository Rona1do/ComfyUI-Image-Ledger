from __future__ import annotations

import os
import traceback
from pathlib import Path

from aiohttp import web

from .ledger import DEFAULT_EXTENSIONS, LedgerError, NoPendingImages, normalize_extensions
from .nodes import (
    SIMPLE_CAMPAIGN,
    _normalize_subfolder,
    _resolve_relative_folder,
    prepare_preview,
)

try:
    import folder_paths
except Exception:  # pragma: no cover
    folder_paths = None  # type: ignore


def _json_error(message: str, status: int = 400) -> web.Response:
    return web.json_response({"ok": False, "error": str(message)}, status=status)


async def preview_handler(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    action = str(body.get("action") or "pick").strip().lower()
    campaign = str(body.get("campaign") or SIMPLE_CAMPAIGN).strip() or SIMPLE_CAMPAIGN
    source_subfolder = str(body.get("source_subfolder") or "").strip()
    recursive = bool(body.get("recursive", True))
    pick_mode = str(body.get("pick_mode") or "Random preview").strip()
    manual_path = str(body.get("manual_path") or "").strip()

    try:
        payload = prepare_preview(
            campaign=campaign,
            source_subfolder=source_subfolder,
            recursive=recursive,
            action=action,
            pick_mode=pick_mode,
            manual_path=manual_path,
        )
        return web.json_response({"ok": True, **payload})
    except NoPendingImages as error:
        return _json_error(str(error), status=409)
    except LedgerError as error:
        return _json_error(str(error), status=400)
    except Exception as error:
        traceback.print_exc()
        return _json_error(f"Internal error: {error}", status=500)


async def list_images_handler(request: web.Request) -> web.Response:
    """List images under ComfyUI/input/<subfolder> for the file browser UI."""
    if folder_paths is None:
        return _json_error("folder_paths unavailable", status=500)

    subfolder = _normalize_subfolder(request.rel_url.query.get("subfolder", "AI"))
    recursive = str(request.rel_url.query.get("recursive", "1")).lower() not in {
        "0",
        "false",
        "no",
    }
    query = str(request.rel_url.query.get("q", "") or "").strip().lower()
    try:
        limit = max(1, min(500, int(request.rel_url.query.get("limit", "200"))))
    except ValueError:
        limit = 200
    try:
        offset = max(0, int(request.rel_url.query.get("offset", "0")))
    except ValueError:
        offset = 0

    try:
        root = _resolve_relative_folder(
            folder_paths.get_input_directory(),
            subfolder,
            must_exist=True,
        )
    except LedgerError as error:
        return _json_error(str(error), status=400)

    allowed = set(normalize_extensions(",".join(DEFAULT_EXTENSIONS)))
    input_root = Path(os.path.abspath(str(folder_paths.get_input_directory())))

    paths: list[Path] = []
    try:
        iterator = root.rglob("*") if recursive else root.iterdir()
        for path in iterator:
            if not path.is_file():
                continue
            if path.suffix.lower() not in allowed:
                continue
            # Skip junk/hidden
            name = path.name
            if name.startswith("."):
                continue
            paths.append(path)
    except OSError as error:
        return _json_error(f"Failed to list folder: {error}", status=500)

    # Sort by relative path for stable browsing
    def rel_key(p: Path) -> str:
        try:
            return p.relative_to(root).as_posix().casefold()
        except ValueError:
            return p.name.casefold()

    paths.sort(key=rel_key)

    items: list[dict[str, str]] = []
    for path in paths:
        try:
            rel_to_input = Path(os.path.abspath(str(path))).relative_to(input_root).as_posix()
        except ValueError:
            # Junction target: rebuild as subfolder + relative to scan root
            try:
                rel_to_root = path.relative_to(root).as_posix()
            except ValueError:
                continue
            rel_to_input = (
                f"{subfolder}/{rel_to_root}" if subfolder else rel_to_root
            ).replace("\\", "/")
        if query and query not in rel_to_input.lower() and query not in path.name.lower():
            continue
        parts = rel_to_input.split("/")
        filename = parts[-1]
        folder = "/".join(parts[:-1])
        items.append(
            {
                "path": rel_to_input,
                "filename": filename,
                "subfolder": folder,
                "name": path.name,
            }
        )

    total = len(items)
    page = items[offset : offset + limit]
    return web.json_response(
        {
            "ok": True,
            "subfolder": subfolder,
            "recursive": recursive,
            "total": total,
            "offset": offset,
            "limit": limit,
            "items": page,
        }
    )


async def global_status_handler(request: web.Request) -> web.Response:
    from .global_settings import GLOBAL_CAMPAIGN, load_settings
    from .global_tracker import last_result, list_pick_folders
    from .nodes import _ledger

    try:
        ledger = _ledger()
        settings = load_settings()
        return web.json_response(
            {
                "ok": True,
                "settings": settings,
                "status": ledger.status(GLOBAL_CAMPAIGN),
                "used": ledger.list_used_rel_paths(GLOBAL_CAMPAIGN),
                "recent": ledger.recent_global_events(GLOBAL_CAMPAIGN, 12),
                "last": last_result(),
                "folders": list_pick_folders(),
            }
        )
    except Exception as error:
        traceback.print_exc()
        return _json_error(f"Internal error: {error}", status=500)


async def global_settings_handler(request: web.Request) -> web.Response:
    from .global_settings import load_settings, save_settings

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    try:
        if body:
            data = save_settings({**load_settings(), **body})
        else:
            data = load_settings()
        return web.json_response({"ok": True, "settings": data})
    except Exception as error:
        traceback.print_exc()
        return _json_error(f"Internal error: {error}", status=500)


async def global_scan_handler(request: web.Request) -> web.Response:
    from .global_settings import load_settings
    from .global_tracker import scan_existing_videos

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    try:
        if "move" in body:
            move = bool(body.get("move"))
        else:
            move = bool(load_settings().get("auto_move", False))
        payload = scan_existing_videos(
            move=move,
            limit=int(body.get("limit") or 0),
        )
        return web.json_response(payload)
    except Exception as error:
        traceback.print_exc()
        return _json_error(f"Internal error: {error}", status=500)


async def global_relocate_handler(request: web.Request) -> web.Response:
    from .global_tracker import relocate_recorded

    try:
        payload = relocate_recorded(force_move=True)
        return web.json_response(payload)
    except Exception as error:
        traceback.print_exc()
        return _json_error(f"Internal error: {error}", status=500)


async def global_undo_handler(request: web.Request) -> web.Response:
    from .global_tracker import undo_last

    try:
        payload = undo_last()
        status = 200 if payload.get("ok") else 409
        return web.json_response(payload, status=status)
    except Exception as error:
        traceback.print_exc()
        return _json_error(f"Internal error: {error}", status=500)


async def global_rerun_last_handler(request: web.Request) -> web.Response:
    from .global_tracker import stage_last_run

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    try:
        payload = stage_last_run(str(body.get("path") or body.get("annotated") or ""))
        status = 200 if payload.get("ok") else 409
        return web.json_response(payload, status=status)
    except Exception as error:
        traceback.print_exc()
        return _json_error(f"Internal error: {error}", status=500)


async def global_mark_poor_handler(request: web.Request) -> web.Response:
    from .global_tracker import mark_poor_quality

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    try:
        payload = mark_poor_quality(str(body.get("path") or body.get("annotated") or ""))
        status = 200 if payload.get("ok") else 409
        return web.json_response(payload, status=status)
    except Exception as error:
        traceback.print_exc()
        return _json_error(f"Internal error: {error}", status=500)


async def global_stage_handler(request: web.Request) -> web.Response:
    from .global_tracker import stage_for_loadimage

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    path = str(body.get("path") or "").strip()
    if not path:
        return _json_error("A source image path is required.")
    try:
        payload = stage_for_loadimage(path)
        status = 200 if payload.get("ok") else 409
        return web.json_response(payload, status=status)
    except Exception as error:
        traceback.print_exc()
        return _json_error(f"Internal error: {error}", status=500)


async def global_thumb_handler(request: web.Request) -> web.Response:
    from .global_tracker import thumbnail_jpeg

    rel = str(request.rel_url.query.get("path", "") or "").strip()
    if not rel:
        return _json_error("missing path")
    try:
        size = int(request.rel_url.query.get("size", "360"))
    except ValueError:
        size = 360
    try:
        data = thumbnail_jpeg(rel, size=size)
        return web.Response(body=data, content_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})
    except FileNotFoundError:
        return _json_error("Source image not found.", status=404)
    except Exception as error:
        traceback.print_exc()
        return _json_error(f"Internal error: {error}", status=500)


async def global_list_folder_handler(request: web.Request) -> web.Response:
    from .global_tracker import list_folder_images

    folder = str(request.rel_url.query.get("folder", "") or "").strip()
    query = str(request.rel_url.query.get("q", "") or "").strip()
    try:
        limit = max(1, min(2000, int(request.rel_url.query.get("limit", "400"))))
    except ValueError:
        limit = 400
    try:
        payload = list_folder_images(folder, query=query, limit=limit)
        status = 200 if payload.get("ok") else 409
        return web.json_response(payload, status=status)
    except Exception as error:
        traceback.print_exc()
        return _json_error(f"Internal error: {error}", status=500)


async def global_random_pick_handler(request: web.Request) -> web.Response:
    from .global_tracker import random_pick_from_folder

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    current = str(body.get("path") or body.get("current") or "").strip()
    folder = str(body.get("folder") or "").strip()
    if not current and not folder:
        return _json_error("Select a category folder first.")
    try:
        payload = random_pick_from_folder(
            current,
            folder=folder,
            skip_current=bool(body.get("skip_current", True)),
        )
        status = 200 if payload.get("ok") else 409
        return web.json_response(payload, status=status)
    except Exception as error:
        traceback.print_exc()
        return _json_error(f"Internal error: {error}", status=500)


async def global_mark_handler(request: web.Request) -> web.Response:
    from .global_tracker import mark_sources

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    annotated = str(body.get("path") or body.get("annotated") or "").strip()
    if not annotated:
        return _json_error("Provide a source path relative to ComfyUI/input.")
    try:
        payload = mark_sources(
            annotated_paths=[annotated],
            workflow_name=str(body.get("workflow_name") or "Manual mark"),
            force_move=body.get("move"),
        )
        return web.json_response(payload)
    except Exception as error:
        traceback.print_exc()
        return _json_error(f"Internal error: {error}", status=500)


def register_routes() -> None:
    try:
        from server import PromptServer
    except Exception as error:
        print(f"[ComfyUI Image Ledger] PromptServer unavailable; preview API disabled: {error}")
        return

    instance = getattr(PromptServer, "instance", None)
    if instance is None:
        print("[ComfyUI Image Ledger] PromptServer.instance is None; preview API not registered.")
        return

    routes = instance.routes
    routes.post("/image_ledger/preview")(preview_handler)
    routes.get("/image_ledger/list_images")(list_images_handler)
    routes.get("/image_ledger/global/status")(global_status_handler)
    routes.post("/image_ledger/global/settings")(global_settings_handler)
    routes.post("/image_ledger/global/scan")(global_scan_handler)
    routes.post("/image_ledger/global/relocate")(global_relocate_handler)
    routes.post("/image_ledger/global/undo")(global_undo_handler)
    routes.post("/image_ledger/global/rerun_last")(global_rerun_last_handler)
    routes.post("/image_ledger/global/mark")(global_mark_handler)
    routes.post("/image_ledger/global/mark_poor")(global_mark_poor_handler)
    routes.post("/image_ledger/global/random_pick")(global_random_pick_handler)
    routes.get("/image_ledger/global/list_folder")(global_list_folder_handler)
    routes.get("/image_ledger/global/thumb")(global_thumb_handler)
    routes.post("/image_ledger/global/stage")(global_stage_handler)
    print(
        "[ComfyUI Image Ledger] Registered preview API and global tracker routes"
    )
