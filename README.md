# ComfyUI Image Ledger

[![CI](https://github.com/Rona1do/ComfyUI-Image-Ledger/actions/workflows/ci.yml/badge.svg)](https://github.com/Rona1do/ComfyUI-Image-Ledger/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

[简体中文](README.zh-CN.md) · [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md)

ComfyUI Image Ledger tracks which source images have successfully produced a final video. It adds a global gallery and a durable, no-repeat queue without forcing you to edit every workflow.

> Status: public beta (`0.1.0`). Tracking is enabled by default; moving source files is **off by default**.

## Why it exists

Large image-to-video libraries are surprisingly hard to manage: a source may be renamed, copied, interrupted halfway through a run, or accidentally processed twice. Image Ledger treats loading as “in progress” and only treats a source as complete after a real, non-empty output video exists.

## Highlights

- Global tracking for ordinary `LoadImage` workflows—no custom nodes required.
- Full-screen source gallery, category picker, random pick, skip, run, reject, and undo.
- Optional move-on-success into a sibling `_used` folder.
- SHA-256 content deduplication, so renamed copies count as the same image.
- SQLite transactions and reservations prevent concurrent jobs from selecting the same source.
- Crash-safe recovery: unfinished reservations return to the queue after restart.
- Existing-video import through embedded ComfyUI metadata.
- Advanced nodes for workflows that need explicit commit semantics and continuous queuing.
- English UI with automatic Simplified Chinese localization.
- Local only: no telemetry, accounts, or network requests.

## Installation

Clone this repository into `ComfyUI/custom_nodes`, then restart ComfyUI:

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/Rona1do/ComfyUI-Image-Ledger.git
```

No extra Python package is required. Pillow, SQLite, and aiohttp are supplied by ComfyUI. Video-history import reuses FFmpeg from VideoHelperSuite or `imageio-ffmpeg` when available.

## Quick start: global mode

1. Put your source library under `ComfyUI/input/AI`, grouped into category folders:

   ```text
   ComfyUI/input/AI/
   ├── portraits/
   │   ├── 001.png
   │   └── 002.png
   └── illustrations/
       └── 001.webp
   ```

   A directory link/junction under `input/AI` may point to a library on another drive.

2. Restart ComfyUI. The **Global Image Ledger** panel appears in the lower-right corner.
3. Choose a category, then open the gallery or pick a random image.
4. Click **Run this**. The selected image is written to the most likely source `LoadImage` node and the workflow is queued.
5. When the workflow finishes with a saved video, the image is recorded as complete.

Image Ledger prefers `LoadImage` nodes titled like “source”, “first frame”, “源图”, or “首帧”, and ignores nodes titled like “last frame”, “mask”, “末帧”, or “遮罩”. If a workflow has only one active `LoadImage`, it is used as a fallback.

### Safe file handling

By default, completion is recorded without moving anything. To archive completed sources, enable:

`Settings → Image Ledger → Global tracking → Move source`

Then `input/AI/portraits/001.png` moves to `input/AI/portraits/_used/001.png`.

Rejected images move to `_used/_rejected`. The latest operation can be undone from the panel. Test move behavior on a small backup library before enabling it for valuable data.

## Advanced mode: explicit workflow commit

Use the nodes under **Image Ledger** when you need exact queue/commit ordering:

```text
Image Ledger · Visual Queue.image
    → your image-to-video workflow
    → VHS_VideoCombine.Filenames
    → Image Ledger · Commit Finished Video.filenames

Image Ledger · Visual Queue.job_ticket
    → Image Ledger · Commit Finished Video.job_ticket
```

The commit node verifies that the final video exists, is inside ComfyUI output, and is non-empty before marking the image complete. Put it directly after the final video writer and before any cleanup node.

Additional maintenance nodes provide status, reservation recovery, and dry-run import of old video metadata.

## Data and privacy

Runtime data is stored outside the repository:

```text
ComfyUI/user/default/image_ledger/ledger.sqlite3
ComfyUI/user/default/image_ledger/settings.json
ComfyUI/user/default/image_ledger/thumbs/
```

Set `COMFYUI_IMAGE_LEDGER_DIR` to override the data directory. Set `COMFYUI_IMAGE_LEDGER_FFMPEG` to override the FFmpeg executable used for history import.

The extension reads files only under ComfyUI input/output roots (including directory links placed there), writes its local SQLite database and thumbnail cache, and optionally moves source images after explicit opt-in. It sends no analytics or image data over the network.

## Compatibility and limitations

- Python 3.10+ and a recent ComfyUI release are recommended.
- The global gallery currently uses `ComfyUI/input/AI` as its library convention.
- Automatic tracking requires a successful workflow result that contains a saved video entry.
- Metadata import can only recover sources from videos that retained compatible prompt metadata.
- ComfyUI internal execution APIs can change; include your ComfyUI commit/date when reporting a compatibility issue.

## Development

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
python -m compileall -q .
node --check web/global_tracker.js
node --check web/image_ledger.js
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the project workflow and [ROADMAP.md](ROADMAP.md) for planned work.

## License

[MIT](LICENSE). ComfyUI is a separate project and is not bundled with this repository.
