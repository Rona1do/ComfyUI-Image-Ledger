# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-09-26

### Added

- ComfyUI Registry metadata and publish workflow, so the extension can be installed from ComfyUI-Manager.
- Global panel action and command to rerun the last completed source image, including files already moved into `_used`.
- `Library folder` setting, so the source library no longer has to be `input/AI`. Nested paths such as `Pictures/AI` work too.
- The panel shows how many images are still pending in the selected category.
- The panel collapses to its title bar and stays collapsed after a reload.
- `Esc` closes the gallery.
- "Move tracked to _used" asks for confirmation before moving files.

### Fixed

- With move-on-success off (the default), completed images no longer reappear in the gallery, in random pick, or in pending counts.
- The execution hook accepts any `PromptExecutor` signature and falls back to `execute` on older ComfyUI builds, so a ComfyUI update no longer breaks every run.
- `_used`, `_trash` and `_thumbs` are hidden only from input listings. Model folders with those names show up again.
- The pick remembered at queue time follows the same first-frame rules as tracking, so a last-frame or mask loader is never remembered.

- The LoadImage list keeps the current selection when that image is already marked done, so a rerun can target a file inside `_used`.
- The panel writes the selected image to the first-frame or source `LoadImage`, and skips last-frame and mask loaders.
- “Scan existing videos” follows the move-source setting. With that setting off, a scan records completed sources and leaves the files where they are.

## [0.1.0] - 2026-08-31

### Added

- Global source-image tracking for video-producing ComfyUI workflows.
- Full-screen gallery, category selection, random pick, reject, and undo actions.
- SQLite-backed no-repeat queue with SHA-256 content deduplication.
- Optional move-on-success and `_rejected` organization.
- Explicit loader/commit nodes, crash recovery, and legacy video import.
- English interface with automatic Simplified Chinese localization.
- Security hardening for input-relative paths and thumbnail size limits.

[Unreleased]: https://github.com/Rona1do/ComfyUI-Image-Ledger/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Rona1do/ComfyUI-Image-Ledger/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Rona1do/ComfyUI-Image-Ledger/releases/tag/v0.1.0
