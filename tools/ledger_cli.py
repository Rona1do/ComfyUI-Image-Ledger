from __future__ import annotations

import argparse
import importlib
import json
import sys
import types
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "image_ledger_cli"
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules.setdefault(PACKAGE_NAME, package)

ledger_module = importlib.import_module(f"{PACKAGE_NAME}.ledger")
history_module = importlib.import_module(f"{PACKAGE_NAME}.history")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Synchronize and audit the ImageLedger image ledger."
    )
    parser.add_argument("--db", required=True, help="SQLite ledger path")
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--input-root", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument(
        "--patterns",
        default="*.mp4",
    )
    parser.add_argument("--recursive-input", action="store_true")
    parser.add_argument(
        "--extensions",
        default=",".join(ledger_module.DEFAULT_EXTENSIONS),
    )
    parser.add_argument("--shuffle-seed", type=int, default=1337)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write imported completion records. Default is dry-run.",
    )
    parser.add_argument("--report-json")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    ledger = ledger_module.Ledger(args.db, session_id="standalone-history-import")
    sync = ledger.sync_campaign(
        campaign=args.campaign,
        source_root=args.input_root,
        recursive=args.recursive_input,
        selection_mode="random_no_repeat",
        shuffle_seed=args.shuffle_seed,
        extensions=args.extensions,
    )
    report, details = history_module.import_history(
        ledger=ledger,
        campaign=args.campaign,
        output_root=args.output_root,
        patterns=args.patterns,
        recursive=True,
        dry_run=not args.write,
    )
    payload = {
        "mode": "write" if args.write else "dry_run",
        "database": str(Path(args.db).resolve()),
        "campaign": args.campaign,
        "sync": {
            "scanned_paths": sync.scanned_paths,
            "unique_images": sync.unique_images,
            "duplicate_paths": sync.duplicate_paths,
            "newly_hashed": sync.newly_hashed,
            "cached_hashes": sync.cached_hashes,
        },
        "history": report.to_dict(),
        "status": ledger.status(args.campaign),
        "unresolved_or_errors": [
            item
            for item in details
            if item.get("state") in {"unresolved", "error"}
        ],
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.report_json:
        report_path = Path(args.report_json).expanduser().resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report.errors == 0 and report.unresolved == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
