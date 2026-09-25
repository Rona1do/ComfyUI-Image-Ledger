# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Global panel action and command to rerun the last completed source image, including files already moved into `_used`.

### Fixed

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

[Unreleased]: https://github.com/Rona1do/ComfyUI-Image-Ledger/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Rona1do/ComfyUI-Image-Ledger/releases/tag/v0.1.0
