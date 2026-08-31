from .nodes import (
    ImageLedgerCommitTrackedVideo,
    ImageLedgerControl,
    ImageLedgerStatus,
    ImageLedgerImportLegacyVideoHistory,
    ImageLedgerSimpleTrackedImageLoader,
    ImageLedgerTrackedImageLoader,
)

try:
    from .server_routes import register_routes

    register_routes()
except Exception as error:
    print(f"[ComfyUI Image Ledger] Failed to register preview API: {error}")

try:
    from .global_tracker import register_hooks

    register_hooks()
except Exception as error:
    print(f"[ComfyUI Image Ledger] Failed to register global tracker: {error}")


NODE_CLASS_MAPPINGS = {
    "ImageLedger_TrackedImageLoader": ImageLedgerTrackedImageLoader,
    "ImageLedger_SimpleTrackedImageLoader": ImageLedgerSimpleTrackedImageLoader,
    "ImageLedger_CommitTrackedVideo": ImageLedgerCommitTrackedVideo,
    "ImageLedger_Status": ImageLedgerStatus,
    "ImageLedger_Control": ImageLedgerControl,
    "ImageLedger_ImportLegacyVideoHistory": ImageLedgerImportLegacyVideoHistory,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "ImageLedger_TrackedImageLoader": "Image Ledger · Tracked Image Loader",
    "ImageLedger_SimpleTrackedImageLoader": "Image Ledger · Visual Queue",
    "ImageLedger_CommitTrackedVideo": "Image Ledger · Commit Finished Video",
    "ImageLedger_Status": "Image Ledger · Status",
    "ImageLedger_Control": "Image Ledger · Recovery",
    "ImageLedger_ImportLegacyVideoHistory": "Image Ledger · Import Video History",
}

WEB_DIRECTORY = "./web"

__all__ = [
    "NODE_CLASS_MAPPINGS",
    "NODE_DISPLAY_NAME_MAPPINGS",
    "WEB_DIRECTORY",
]
