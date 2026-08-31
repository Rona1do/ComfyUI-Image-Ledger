import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import "../../scripts/domWidget.js";

const LOADER_CLASSES = new Set([
  "ImageLedger_TrackedImageLoader",
  "ImageLedger_SimpleTrackedImageLoader",
]);
const COMMIT_CLASS = "ImageLedger_CommitTrackedVideo";
const STYLE_ID = "image_ledger-image-ledger-style";
const PREVIEW_HEIGHT = 440;
const PANEL_CHROME = 260;
const SIMPLE_CAMPAIGN = "image-ledger-default";
const MODE_RANDOM = "Random preview";
const MODE_MANUAL = "Manual selection";
const IS_ZH = String(navigator.language || "").toLowerCase().startsWith("zh");
const t = (en, zh) => (IS_ZH ? zh : en);

let autoQueueInFlight = false;

function ensureStyle() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = `
    .image_ledger-ledger-panel {
      box-sizing: border-box;
      width: 100%;
      padding: 10px 12px;
      border: 1px solid rgba(93, 197, 255, .42);
      border-radius: 8px;
      background: linear-gradient(145deg, rgba(12, 30, 46, .96), rgba(20, 21, 30, .96));
      color: #d9efff;
      font: 12px/1.45 system-ui, sans-serif;
      overflow: hidden;
    }
    .image_ledger-ledger-panel strong {
      display: block;
      margin-bottom: 4px;
      color: #7ed4ff;
      font-size: 13px;
    }
    .image_ledger-ledger-panel[data-state="done"] {
      border-color: rgba(77, 221, 145, .55);
      background: linear-gradient(145deg, rgba(11, 48, 37, .94), rgba(20, 25, 29, .94));
    }
    .image_ledger-ledger-panel[data-state="done"] strong { color: #71e4a7; }
    .image_ledger-ledger-panel[data-state="error"] {
      border-color: rgba(255, 102, 102, .65);
    }
    .image_ledger-mode-row {
      display: flex;
      gap: 8px;
      margin: 8px 0 6px;
    }
    .image_ledger-mode-row button {
      flex: 1;
      appearance: none;
      border: 1px solid rgba(126, 212, 255, .4);
      border-radius: 8px;
      background: rgba(20, 40, 60, .9);
      color: #cfe9ff;
      font: 600 13px/1.2 system-ui, sans-serif;
      padding: 10px 8px;
      cursor: pointer;
      pointer-events: auto;
      opacity: 1;
    }
    .image_ledger-mode-row button:disabled {
      /* Mode switch must stay clickable even while preview is loading. */
      opacity: 1;
      cursor: pointer;
      pointer-events: auto;
    }
    .image_ledger-mode-row button[data-active="1"] {
      background: rgba(28, 120, 200, .95);
      border-color: rgba(160, 220, 255, .8);
      color: #fff;
      box-shadow: 0 0 0 1px rgba(120, 200, 255, .35) inset;
    }
    .image_ledger-manual-box {
      display: none;
      margin: 6px 0 8px;
      padding: 8px;
      border-radius: 8px;
      border: 1px solid rgba(255, 190, 100, .45);
      background: rgba(60, 40, 15, .45);
    }
    .image_ledger-manual-box[data-open="1"] {
      display: block;
    }
    .image_ledger-manual-box label {
      display: block;
      margin-bottom: 4px;
      color: #ffd9a0;
      font-weight: 600;
    }
    .image_ledger-manual-box input {
      box-sizing: border-box;
      width: 100%;
      border: 1px solid rgba(255, 210, 140, .45);
      border-radius: 6px;
      background: rgba(10, 12, 16, .9);
      color: #fff5e6;
      font: 12px/1.3 ui-monospace, Consolas, monospace;
      padding: 8px 10px;
    }
    .image_ledger-manual-box .image_ledger-manual-help {
      margin-top: 6px;
      color: #e8c898;
      font-size: 11px;
      line-height: 1.45;
    }
    .image_ledger-manual-btns {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 8px;
    }
    .image_ledger-manual-btns button {
      appearance: none;
      border: 1px solid rgba(255, 200, 120, .55);
      border-radius: 6px;
      background: rgba(140, 90, 30, .9);
      color: #fff6e8;
      font: 12px/1.2 system-ui, sans-serif;
      padding: 8px 10px;
      cursor: pointer;
    }
    .image_ledger-manual-btns button:hover {
      background: rgba(170, 110, 40, .95);
    }
    .image_ledger-picker-overlay {
      position: fixed;
      inset: 0;
      z-index: 100000;
      background: rgba(0, 0, 0, .62);
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px;
    }
    .image_ledger-picker {
      width: min(720px, 96vw);
      max-height: min(80vh, 720px);
      display: flex;
      flex-direction: column;
      border-radius: 12px;
      border: 1px solid rgba(120, 200, 255, .45);
      background: #121820;
      color: #e8f4ff;
      box-shadow: 0 16px 48px rgba(0, 0, 0, .45);
      overflow: hidden;
    }
    .image_ledger-picker header {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 12px 14px;
      border-bottom: 1px solid rgba(100, 160, 210, .3);
      font-weight: 600;
    }
    .image_ledger-picker header input {
      flex: 1;
      border: 1px solid rgba(120, 180, 230, .4);
      border-radius: 6px;
      background: #0b1016;
      color: #e8f4ff;
      padding: 7px 10px;
      font: 12px system-ui, sans-serif;
    }
    .image_ledger-picker header button {
      appearance: none;
      border: 1px solid rgba(140, 180, 220, .4);
      border-radius: 6px;
      background: #243040;
      color: #dff0ff;
      padding: 7px 10px;
      cursor: pointer;
    }
    .image_ledger-picker .image_ledger-picker-meta {
      padding: 6px 14px;
      color: #9bb8cc;
      font-size: 11px;
    }
    .image_ledger-picker .image_ledger-picker-list {
      flex: 1;
      overflow: auto;
      padding: 0 8px 12px;
    }
    .image_ledger-picker .image_ledger-picker-item {
      display: grid;
      grid-template-columns: 56px 1fr;
      gap: 10px;
      align-items: center;
      padding: 8px;
      border-radius: 8px;
      cursor: pointer;
      border: 1px solid transparent;
    }
    .image_ledger-picker .image_ledger-picker-item:hover {
      background: rgba(40, 90, 140, .35);
      border-color: rgba(100, 180, 255, .35);
    }
    .image_ledger-picker .image_ledger-picker-item img {
      width: 56px;
      height: 56px;
      object-fit: cover;
      border-radius: 6px;
      background: #000;
    }
    .image_ledger-picker .image_ledger-picker-item .path {
      font: 12px/1.35 ui-monospace, Consolas, monospace;
      word-break: break-all;
    }
    .image_ledger-ledger-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin: 8px 0 6px;
    }
    .image_ledger-ledger-actions button {
      appearance: none;
      border: 1px solid rgba(126, 212, 255, .45);
      border-radius: 6px;
      background: rgba(30, 90, 130, .85);
      color: #eaf7ff;
      font: 12px/1.2 system-ui, sans-serif;
      padding: 7px 10px;
      cursor: pointer;
    }
    .image_ledger-ledger-actions button:hover:not(:disabled) {
      background: rgba(40, 120, 170, .95);
    }
    .image_ledger-ledger-actions button:disabled {
      opacity: .45;
      cursor: not-allowed;
    }
    .image_ledger-ledger-actions button.primary {
      background: rgba(28, 140, 90, .92);
      border-color: rgba(120, 230, 170, .55);
      font-weight: 600;
    }
    .image_ledger-ledger-actions button.primary:hover:not(:disabled) {
      background: rgba(34, 165, 105, .98);
    }
    .image_ledger-ledger-actions button.warn {
      background: rgba(120, 70, 30, .9);
      border-color: rgba(255, 180, 100, .45);
    }
    .image_ledger-ledger-panel .image_ledger-ledger-preview-wrap {
      margin-top: 8px;
      border-radius: 8px;
      overflow: hidden;
      background: rgba(0, 0, 0, .55);
      border: 1px dashed rgba(126, 212, 255, .45);
      min-height: ${PREVIEW_HEIGHT}px;
      height: ${PREVIEW_HEIGHT}px;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .image_ledger-ledger-panel .image_ledger-ledger-preview-wrap[data-has-image="1"] {
      border-style: solid;
      border-color: rgba(126, 212, 255, .35);
    }
    .image_ledger-ledger-panel .image_ledger-ledger-preview {
      display: none;
      width: 100%;
      height: 100%;
      max-height: ${PREVIEW_HEIGHT}px;
      object-fit: contain;
      background: #0b0f14;
    }
    .image_ledger-ledger-panel .image_ledger-ledger-preview[data-show="1"] {
      display: block;
    }
    .image_ledger-ledger-panel .image_ledger-ledger-placeholder {
      color: #8fb0c8;
      font-size: 12px;
      text-align: center;
      padding: 16px;
      line-height: 1.55;
      white-space: pre-line;
    }
    .image_ledger-ledger-panel .image_ledger-ledger-hint {
      margin-top: 6px;
      color: #9bb8cc;
      font-size: 11px;
    }
  `;
  document.head.appendChild(style);
}

function parsePayload(message, key) {
  const raw = message?.[key];
  const value = Array.isArray(raw) ? raw[0] : raw;
  if (!value) return null;
  if (typeof value === "object") return value;
  try {
    return JSON.parse(String(value));
  } catch {
    return null;
  }
}

function buildViewUrl(imageInfo) {
  if (!imageInfo || !imageInfo.filename) return null;
  const params = new URLSearchParams();
  params.set("filename", imageInfo.filename);
  params.set("type", imageInfo.type || "input");
  if (imageInfo.subfolder) params.set("subfolder", imageInfo.subfolder);
  params.set("rand", String(Date.now()));
  try {
    if (api?.apiURL) return api.apiURL(`/view?${params.toString()}`);
  } catch {
    /* fall through */
  }
  return `/view?${params.toString()}`;
}

function imageInfoFromPayload(payload) {
  if (payload?.image?.filename) return payload.image;
  if (payload?.selected) {
    const normalized = String(payload.selected).replace(/\\/g, "/");
    const parts = normalized.split("/");
    const filename = parts.pop();
    const subfolder = parts.join("/");
    return { filename, subfolder, type: "input" };
  }
  return null;
}

function imageInfoFromMessage(message, payload) {
  const images = message?.images;
  if (Array.isArray(images) && images.length > 0 && images[0]?.filename) {
    return images[0];
  }
  return imageInfoFromPayload(payload);
}

/** Clear ComfyUI native node image strip so we only keep the panel preview. */
function clearNativeNodeImages(node) {
  node.imgs = [];
  node.images = [];
  node.imageIndex = null;
  node.setDirtyCanvas?.(true, true);
}

function widgetValue(node, name, fallback = null) {
  const w = node.widgets?.find((item) => item.name === name);
  if (!w) return fallback;
  return w.value;
}

function setWidgetValue(node, name, value) {
  const w = node.widgets?.find((item) => item.name === name);
  if (!w) return false;
  w.value = value;
  w.callback?.(value);
  node.setDirtyCanvas?.(true, true);
  return true;
}

function isManualPickMode(mode) {
  const text = String(mode || "");
  return (
    text.includes("手动") ||
    text === "manual" ||
    text.toLowerCase().includes("manual")
  );
}

function sanitizeSubfolder(value, fallback = "AI") {
  if (typeof value === "boolean") return fallback;
  const text = String(value ?? "")
    .trim()
    .replace(/\\/g, "/")
    .replace(/^\/+|\/+$/g, "");
  if (!text || ["true", "false", "none", "null", "undefined"].includes(text.toLowerCase())) {
    return fallback;
  }
  return text;
}

function sanitizeManualPath(value) {
  if (typeof value === "boolean") return "";
  const text = String(value ?? "")
    .trim()
    .replace(/\\/g, "/");
  if (["true", "false", "none", "null", "undefined"].includes(text.toLowerCase())) {
    return "";
  }
  return text;
}

function readLoaderSettings(node) {
  const isSimple = node.comfyClass === "ImageLedger_SimpleTrackedImageLoader";
  if (isSimple) {
    const pickRaw = widgetValue(node, "pick_mode", MODE_RANDOM);
    const pick_mode =
      typeof pickRaw === "boolean"
        ? MODE_RANDOM
        : isManualPickMode(pickRaw)
          ? MODE_MANUAL
          : MODE_RANDOM;
    const source = sanitizeSubfolder(widgetValue(node, "source_subfolder", "AI"), "AI");
    const folderWidget = node.widgets?.find((w) => w.name === "source_subfolder");
    if (folderWidget && String(folderWidget.value) !== source) {
      folderWidget.value = source;
    }
    // Keep combo on a valid option if it was corrupted.
    const modeWidget = node.widgets?.find((w) => w.name === "pick_mode");
    if (modeWidget && modeWidget.value !== pick_mode) {
      modeWidget.value = pick_mode;
    }
    return {
      campaign: SIMPLE_CAMPAIGN,
      pick_mode,
      manual: isManualPickMode(pick_mode),
      source_subfolder: source,
      recursive: Boolean(widgetValue(node, "recursive", true)),
      manual_path: sanitizeManualPath(widgetValue(node, "manual_path", "")),
    };
  }
  return {
    campaign: String(widgetValue(node, "campaign", SIMPLE_CAMPAIGN) ?? SIMPLE_CAMPAIGN),
    pick_mode: MODE_RANDOM,
    manual: false,
    source_subfolder: sanitizeSubfolder(widgetValue(node, "source_subfolder", ""), ""),
    recursive: Boolean(widgetValue(node, "recursive", false)),
    manual_path: "",
  };
}

function attachPanel(node, heading, idleText = t("Not run yet", "尚未运行"), { withPreview = false } = {}) {
  ensureStyle();
  if (node.__image_ledgerLedgerPanel) return node.__image_ledgerLedgerPanel;

  const root = document.createElement("div");
  root.className = "image_ledger-ledger-panel";
  root.dataset.state = "idle";

  const title = document.createElement("strong");
  title.textContent = heading;

  const body = document.createElement("div");
  body.textContent = idleText;

  // ---- Mode switch (the visible "entry" for manual) ----
  const modeRow = document.createElement("div");
  modeRow.className = "image_ledger-mode-row";
  const btnModeRandom = document.createElement("button");
  btnModeRandom.type = "button";
  btnModeRandom.textContent = t("🎲 Random preview", "🎲 随机预览");
  const btnModeManual = document.createElement("button");
  btnModeManual.type = "button";
  btnModeManual.textContent = t("🖼 Manual selection", "🖼 手动选图");
  modeRow.append(btnModeRandom, btnModeManual);

  // ---- Manual path box + real file browsers ----
  const manualBox = document.createElement("div");
  manualBox.className = "image_ledger-manual-box";
  manualBox.dataset.open = "0";
  const manualLabel = document.createElement("label");
  manualLabel.textContent = t("Selected image (browse or enter a path)", "手动选中的图（可浏览，也可手填路径）");
  const manualInput = document.createElement("input");
  manualInput.type = "text";
  manualInput.placeholder = t("For example: AI/10.png", "浏览选中后会自动填入，例如 AI/10.png");
  manualInput.spellcheck = false;

  const manualBtns = document.createElement("div");
  manualBtns.className = "image_ledger-manual-btns";
  const btnBrowsePc = document.createElement("button");
  btnBrowsePc.type = "button";
  btnBrowsePc.textContent = t("📂 Upload from computer", "📂 从电脑浏览选文件");
  const btnBrowseDir = document.createElement("button");
  btnBrowseDir.type = "button";
  btnBrowseDir.textContent = t("📁 Browse source folder", "📁 从 AI 目录浏览");
  manualBtns.append(btnBrowsePc, btnBrowseDir);

  const fileInput = document.createElement("input");
  fileInput.type = "file";
  fileInput.accept = "image/png,image/jpeg,image/webp,image/bmp,image/gif,.png,.jpg,.jpeg,.webp,.bmp,.gif";
  fileInput.style.display = "none";

  const manualHelp = document.createElement("div");
  manualHelp.className = "image_ledger-manual-help";
  manualHelp.textContent = t(
    "Upload a file or browse the configured source folder. Preview it, then explicitly run it.",
    "可以点「从电脑浏览」弹出系统选文件；或「从 AI 目录浏览」在库里搜索点选。选中后点「① 预览手动路径」确认，再「② 确认跑这张」。",
  );
  manualBox.append(manualLabel, manualInput, manualBtns, fileInput, manualHelp);

  const actions = document.createElement("div");
  actions.className = "image_ledger-ledger-actions";

  const btnPick = document.createElement("button");
  btnPick.type = "button";
  btnPick.textContent = t("① Pick a preview", "① 抽一张预览");

  const btnSkip = document.createElement("button");
  btnSkip.type = "button";
  btnSkip.className = "warn";
  btnSkip.textContent = t("Another", "换一张");
  btnSkip.disabled = true;

  const btnRun = document.createElement("button");
  btnRun.type = "button";
  btnRun.className = "primary";
  btnRun.textContent = t("② Run this image", "② 确认跑这张");
  btnRun.disabled = true;

  const btnRelease = document.createElement("button");
  btnRelease.type = "button";
  btnRelease.textContent = t("Release", "退回锁定");
  btnRelease.disabled = true;

  if (withPreview) {
    actions.append(btnPick, btnSkip, btnRun, btnRelease);
  }

  const hint = document.createElement("div");
  hint.className = "image_ledger-ledger-hint";
  hint.textContent = withPreview
    ? t("Choose random or manual mode, preview an image, then run it.", "入口就在这个蓝框里：先选「随机预览」或「手动选图」，再预览，再确认跑。")
    : "";

  const previewWrap = document.createElement("div");
  previewWrap.className = "image_ledger-ledger-preview-wrap";
  previewWrap.dataset.hasImage = "0";

  const placeholder = document.createElement("div");
  placeholder.className = "image_ledger-ledger-placeholder";
  placeholder.textContent = t(
    "PREVIEW\nRandom: pick an image\nManual: choose a path, then preview",
    "【预览区】\n随机：点①抽一张\n手动：上方切到「手动选图」→ 填路径 → ①预览",
  );

  const preview = document.createElement("img");
  preview.className = "image_ledger-ledger-preview";
  preview.alt = t("Preview", "预览图");
  preview.loading = "eager";

  previewWrap.append(placeholder, preview);

  root.append(title, body);
  if (withPreview) {
    root.append(modeRow, manualBox, actions, hint, previewWrap);
  }

  const minH = () => (withPreview ? PANEL_CHROME + PREVIEW_HEIGHT : 82);
  const maxH = () => (withPreview ? PANEL_CHROME + PREVIEW_HEIGHT + 80 : 100);

  const widget = node.addDOMWidget(
    "image_ledger_ledger_panel",
    "IMAGE_LEDGER_LEDGER_PANEL",
    root,
    {
      serialize: false,
      hideOnZoom: false,
      getMinHeight: minH,
      getMaxHeight: maxH,
    },
  );

  const panel = {
    root,
    title,
    body,
    hint,
    preview,
    previewWrap,
    placeholder,
    widget,
    withPreview,
    btnPick,
    btnSkip,
    btnRun,
    btnRelease,
    btnModeRandom,
    btnModeManual,
    manualBox,
    manualInput,
    btnBrowsePc,
    btnBrowseDir,
    fileInput,
    busy: false,
    held: false,
    manual: false,
  };
  node.__image_ledgerLedgerPanel = panel;

  const width = Math.max(Number(node.size?.[0]) || 0, withPreview ? 560 : 420);
  const height = Math.max(Number(node.size?.[1]) || 0, withPreview ? 820 : 240);
  node.setSize?.([width, height]);

  if (withPreview) {
    wirePreviewActions(node, panel);
    // Sync initial mode UI from widgets.
    applyModeUi(node, panel, readLoaderSettings(node).manual);
  }
  return panel;
}

function forcePickModeWidget(node, manual) {
  const wanted = manual ? MODE_MANUAL : MODE_RANDOM;
  const w = node.widgets?.find((item) => item.name === "pick_mode");
  if (!w) return wanted;
  // Combo widgets may store values in options.values / options / itself.
  const options =
    w.options?.values ||
    w.options ||
    (Array.isArray(w.values) ? w.values : null);
  if (Array.isArray(options) && options.length && !options.includes(wanted)) {
    // Fall back to closest label.
    const hit = options.find((v) =>
      manual
        ? /manual|手动/i.test(String(v))
        : /random|随机/i.test(String(v)),
    );
    w.value = hit ?? options[0];
  } else {
    w.value = wanted;
  }
  try {
    w.callback?.(w.value);
  } catch {
    /* ignore widget callback errors */
  }
  return w.value;
}

function applyModeUi(node, panel, manual) {
  panel.manual = Boolean(manual);
  if (!panel.withPreview) return;
  panel.btnModeRandom.dataset.active = manual ? "0" : "1";
  panel.btnModeManual.dataset.active = manual ? "1" : "0";
  // Never leave mode buttons disabled — user must always be able to switch.
  panel.btnModeRandom.disabled = false;
  panel.btnModeManual.disabled = false;
  panel.manualBox.dataset.open = manual ? "1" : "0";
  if (manual) {
    panel.title.textContent = t("★ Manual selection", "★ 手动选图模式（浏览/填路径 → 预览 → 开跑）");
    panel.placeholder.textContent = t(
      "MANUAL PREVIEW\nUpload, browse, or enter a path\nThen preview it",
      "【手动预览区】\n点「从电脑浏览」或「从 AI 目录浏览」\n或手填路径后点①预览",
    );
    const path = sanitizeManualPath(widgetValue(node, "manual_path", ""));
    if (panel.manualInput && panel.manualInput.value !== path) {
      panel.manualInput.value = path;
    }
  } else {
    panel.title.textContent = t("★ Random preview", "★ 随机预览模式（抽图 → 换图 → 开跑）");
    panel.placeholder.textContent = t(
      "RANDOM PREVIEW\nPick an image\nSkip it if needed",
      "【随机预览区】\n点「① 抽一张预览」\n不满意就「换一张」",
    );
  }
  setButtons(panel, {
    held: panel.held,
    busy: panel.busy,
    manual,
  });
  try {
    panel.widget?.onResize?.();
  } catch {
    /* ignore */
  }
  node.setDirtyCanvas?.(true, true);
}

function switchMode(node, panel, manual) {
  // Mode switch always unlocks a stuck busy state from a hung preview/upload.
  panel.busy = false;
  const applied = forcePickModeWidget(node, manual);
  const nowManual = isManualPickMode(applied) || Boolean(manual);
  // Switching mode clears "held" so action buttons match the new mode cleanly.
  panel.held = false;
  panel.root.dataset.state = "idle";
  applyModeUi(node, panel, nowManual);
  setButtons(panel, { held: false, busy: false, manual: nowManual });
  if (nowManual) {
    panel.body.textContent = t(
      "Manual selection enabled. Upload, browse, or enter an input-relative path.",
      "已切换到手动选图。用「从电脑浏览 / 从 AI 目录浏览」选图，或手填路径。",
    );
    panel.manualInput?.focus?.();
  } else {
    panel.body.textContent = t(
      "Random preview enabled. Pick an image to begin.",
      "已切换到随机预览。点「① 抽一张预览」即可，不必再管手动路径。",
    );
  }
  node.setDirtyCanvas?.(true, true);
}

function setButtons(panel, { held = false, busy = false, manual = false } = {}) {
  panel.held = held;
  panel.busy = busy;
  panel.manual = Boolean(manual);
  if (!panel.withPreview) return;
  panel.btnPick.disabled = busy;
  panel.btnSkip.disabled = busy || manual || !held;
  panel.btnRun.disabled = busy || !held;
  panel.btnRelease.disabled = busy || !held;
  // IMPORTANT: mode toggles must remain clickable during busy/preview.
  panel.btnModeRandom.disabled = false;
  panel.btnModeManual.disabled = false;
  if (manual) {
    panel.btnPick.textContent = t("① Preview manual path", "① 预览手动路径");
    panel.btnSkip.style.display = "none";
  } else {
    panel.btnPick.textContent = held
      ? t("① Keep current preview", "① 保持当前预览")
      : t("① Pick a preview", "① 抽一张预览");
    panel.btnSkip.style.display = "";
  }
}

function setPreview(panel, imageInfo) {
  if (!panel?.withPreview) return;
  const url = buildViewUrl(imageInfo);
  if (!url) {
    panel.preview.removeAttribute("src");
    panel.preview.dataset.show = "0";
    panel.previewWrap.dataset.hasImage = "0";
    if (panel.placeholder) panel.placeholder.style.display = "";
    return;
  }
  if (panel.placeholder) panel.placeholder.style.display = "none";
  panel.preview.src = url;
  panel.preview.dataset.show = "1";
  panel.previewWrap.dataset.hasImage = "1";
  panel.preview.onload = () => {
    try {
      panel.widget?.onResize?.();
    } catch {
      /* ignore */
    }
  };
}

function updatePanel(node, payload, state, message = null) {
  const panel = node.__image_ledgerLedgerPanel;
  if (!panel) return;
  // Widget/mode buttons are the source of truth — never let a stale payload
  // force the UI back into "manual" after the user switched to random.
  const settings = readLoaderSettings(node);
  const manual = settings.manual;
  if (payload) {
    panel.root.dataset.state = state || (payload.held ? "running" : "idle");
    const selected = payload.selected ? t(`Current: ${payload.selected}\n`, `当前：${payload.selected}\n`) : "";
    const stats = payload.status_text || payload.message || "";
    panel.body.textContent = `${selected}${stats}`.trim();
    const held = Boolean(payload.held || payload.selected);
    applyModeUi(node, panel, manual);
    setButtons(panel, {
      held,
      busy: panel.busy,
      manual,
    });
  } else {
    applyModeUi(node, panel, manual);
  }
  if (panel.withPreview) {
    const info = imageInfoFromMessage(message, payload);
    setPreview(panel, info);
    // One preview only: panel DOM image, not the native strip under the node.
    clearNativeNodeImages(node);
  }
  node.setDirtyCanvas?.(true, true);
}

async function callPreviewApi(node, action) {
  const panel = node.__image_ledgerLedgerPanel;
  if (!panel) return;
  // Allow a second click to not hard-lock forever; only block concurrent calls.
  if (panel.busy) {
    // Soft-unlock if stuck longer than 30s
    if (!panel._busySince) panel._busySince = Date.now();
    if (Date.now() - panel._busySince < 30000) return;
    panel.busy = false;
  }
  panel._busySince = Date.now();

  // Push panel path into node widget before API call.
  if (panel.manualInput) {
    setWidgetValue(node, "manual_path", sanitizeManualPath(panel.manualInput.value));
  }

  const settings = readLoaderSettings(node);
  setButtons(panel, {
    held: panel.held,
    busy: true,
    manual: settings.manual,
  });
  panel.body.textContent =
    action === "skip"
      ? t("Picking another image…", "正在换一张…")
      : action === "release"
        ? t("Releasing reservation…", "正在退回锁定…")
        : settings.manual
          ? t("Previewing the manual path…", "正在预览手动路径（不跑视频）…")
          : t("Picking a preview…", "正在抽图预览（不跑视频）…");
  panel.root.dataset.state = "idle";

  try {
    const response = await api.fetchApi("/image_ledger/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action,
        campaign: settings.campaign,
        source_subfolder: settings.source_subfolder,
        recursive: settings.recursive,
        pick_mode: settings.manual ? MODE_MANUAL : MODE_RANDOM,
        manual_path:
          settings.manual_path || sanitizeManualPath(panel.manualInput?.value),
      }),
    });
    const data = await response.json();
    if (!response.ok || data.ok === false) {
      throw new Error(data.error || `HTTP ${response.status}`);
    }
    // Re-read mode after request — user may have switched mid-flight.
    const modeNow = readLoaderSettings(node).manual;
    updatePanel(node, data, data.held ? "running" : "idle", null);
    setButtons(panel, {
      held: Boolean(data.held || data.selected),
      busy: false,
      manual: modeNow,
    });
  } catch (error) {
    console.error("ComfyUI Image Ledger preview failed:", error);
    panel.root.dataset.state = "error";
    panel.body.textContent = t(`Preview failed: ${error?.message || error}`, `预览失败：${error?.message || error}`);
    setButtons(panel, {
      held: false,
      busy: false,
      manual: readLoaderSettings(node).manual,
    });
  } finally {
    panel._busySince = 0;
    panel.busy = false;
    const modeNow = readLoaderSettings(node).manual;
    setButtons(panel, {
      held: panel.held,
      busy: false,
      manual: modeNow,
    });
    // Ensure mode toggles are never left disabled.
    if (panel.btnModeRandom) panel.btnModeRandom.disabled = false;
    if (panel.btnModeManual) panel.btnModeManual.disabled = false;
  }
}

function thumbUrl(item) {
  const params = new URLSearchParams();
  params.set("filename", item.filename || item.name);
  params.set("type", "input");
  if (item.subfolder) params.set("subfolder", item.subfolder);
  params.set("preview", "256");
  try {
    if (api?.apiURL) return api.apiURL(`/view?${params.toString()}`);
  } catch {
    /* fall through */
  }
  return `/view?${params.toString()}`;
}

async function selectManualPath(node, panel, path) {
  const cleaned = sanitizeManualPath(path);
  if (!cleaned) return;
  if (panel.manualInput) panel.manualInput.value = cleaned;
  setWidgetValue(node, "pick_mode", MODE_MANUAL);
  setWidgetValue(node, "manual_path", cleaned);
  applyModeUi(node, panel, true);
  panel.body.textContent = t(`Selected ${cleaned}; previewing…`, `已选中：${cleaned}，正在预览…`);
  await callPreviewApi(node, "pick");
}

async function uploadLocalFileAndSelect(node, panel, file) {
  const settings = readLoaderSettings(node);
  const subfolder = settings.source_subfolder || "AI";
  panel.body.textContent = t(`Uploading ${file.name} to input/${subfolder}…`, `正在上传 ${file.name} 到 input/${subfolder} …`);
  setButtons(panel, { held: panel.held, busy: true, manual: true });
  try {
    const body = new FormData();
    body.append("image", file, file.name);
    body.append("overwrite", "true");
    body.append("type", "input");
    body.append("subfolder", subfolder);
    const resp = await api.fetchApi("/upload/image", { method: "POST", body });
    if (!resp.ok) {
      throw new Error(t(`Upload failed: HTTP ${resp.status}`, `上传失败 HTTP ${resp.status}`));
    }
    const data = await resp.json();
    // ComfyUI returns {name, subfolder, type} (sometimes nested).
    const name = data.name || data.filename || file.name;
    const folder = (data.subfolder || subfolder || "").replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
    const path = folder ? `${folder}/${name}` : name;
    await selectManualPath(node, panel, path);
  } catch (error) {
    console.error("ComfyUI Image Ledger upload failed:", error);
    panel.root.dataset.state = "error";
    panel.body.textContent = t(`File selection failed: ${error?.message || error}`, `从电脑选文件失败：${error?.message || error}`);
    setButtons(panel, { held: false, busy: false, manual: true });
  }
}

function openDirectoryPicker(node, panel) {
  const settings = readLoaderSettings(node);
  const subfolder = settings.source_subfolder || "AI";
  const recursive = settings.recursive !== false;

  const overlay = document.createElement("div");
  overlay.className = "image_ledger-picker-overlay";
  const picker = document.createElement("div");
  picker.className = "image_ledger-picker";

  const header = document.createElement("header");
  const title = document.createElement("div");
  title.textContent = t(`Browse ${subfolder || "input"}`, `浏览 ${subfolder || "input"}`);
  const search = document.createElement("input");
  search.type = "search";
  search.placeholder = t("Search filename or path…", "搜索文件名 / 路径…");
  const btnClose = document.createElement("button");
  btnClose.type = "button";
  btnClose.textContent = t("Close", "关闭");
  header.append(title, search, btnClose);

  const meta = document.createElement("div");
  meta.className = "image_ledger-picker-meta";
  meta.textContent = t("Loading…", "加载中…");

  const list = document.createElement("div");
  list.className = "image_ledger-picker-list";

  picker.append(header, meta, list);
  overlay.append(picker);
  document.body.append(overlay);

  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    overlay.remove();
  };
  btnClose.addEventListener("click", close);
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) close();
  });

  let timer = null;
  const load = async (q = "") => {
    meta.textContent = t("Loading…", "加载中…");
    list.innerHTML = "";
    try {
      const params = new URLSearchParams({
        subfolder,
        recursive: recursive ? "1" : "0",
        q,
        limit: "200",
        offset: "0",
      });
      const resp = await api.fetchApi(
        `/image_ledger/list_images?${params.toString()}`,
      );
      const data = await resp.json();
      if (!resp.ok || data.ok === false) {
        throw new Error(data.error || `HTTP ${resp.status}`);
      }
      meta.textContent = t(
        `${data.total} total; showing ${data.items.length}. Use search to narrow results.`,
        `共 ${data.total} 张，显示前 ${data.items.length} 张（可搜索缩小范围）`,
      );
      if (!data.items.length) {
        list.textContent = t("No images found. Check the source folder setting.", "没有找到图片。请检查原图目录设置。" );
        return;
      }
      for (const item of data.items) {
        const row = document.createElement("div");
        row.className = "image_ledger-picker-item";
        const img = document.createElement("img");
        img.loading = "lazy";
        img.alt = item.filename;
        img.src = thumbUrl(item);
        const path = document.createElement("div");
        path.className = "path";
        path.textContent = item.path;
        row.append(img, path);
        row.addEventListener("click", async () => {
          close();
          await selectManualPath(node, panel, item.path);
        });
        list.append(row);
      }
    } catch (error) {
      meta.textContent = t(`Load failed: ${error?.message || error}`, `加载失败：${error?.message || error}`);
    }
  };

  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => load(search.value.trim()), 250);
  });
  load("");
}

function wirePreviewActions(node, panel) {
  panel.btnModeRandom.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    switchMode(node, panel, false);
  });

  panel.btnModeManual.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    switchMode(node, panel, true);
  });

  panel.manualInput?.addEventListener("change", () => {
    setWidgetValue(node, "manual_path", sanitizeManualPath(panel.manualInput.value));
  });
  panel.manualInput?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      setWidgetValue(node, "manual_path", sanitizeManualPath(panel.manualInput.value));
      callPreviewApi(node, "pick");
    }
  });

  panel.btnBrowsePc?.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    panel.fileInput?.click();
  });
  panel.fileInput?.addEventListener("change", async () => {
    const file = panel.fileInput.files?.[0];
    panel.fileInput.value = "";
    if (!file) return;
    await uploadLocalFileAndSelect(node, panel, file);
  });
  panel.btnBrowseDir?.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    openDirectoryPicker(node, panel);
  });

  panel.btnPick.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    callPreviewApi(node, "pick");
  });
  panel.btnSkip.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    callPreviewApi(node, "skip");
  });
  panel.btnRelease.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    callPreviewApi(node, "release");
  });
  panel.btnRun.addEventListener("click", async (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (!panel.held || panel.busy) return;
    if (panel.manualInput) {
      setWidgetValue(node, "manual_path", sanitizeManualPath(panel.manualInput.value));
    }
    panel.body.textContent = t("Confirmed; queueing the workflow…", "已确认，正在 Queue 整条工作流…");
    setButtons(panel, { held: true, busy: true, manual: panel.manual });
    try {
      await app.queuePrompt(0, 1);
      panel.body.textContent = panel.manual
        ? t("Queued with the selected manual path.", "已提交：将用你填写的手动路径开跑。")
        : t("Queued with the held preview; it will not be redrawn.", "已提交：将用当前预览锁定的图开跑（不会重随机）。");
      panel.root.dataset.state = "running";
    } catch (error) {
      console.error("ComfyUI Image Ledger queue failed:", error);
      panel.root.dataset.state = "error";
      panel.body.textContent = t(`Queue failed: ${error?.message || error}`, `Queue 失败：${error?.message || error}`);
    } finally {
      setButtons(panel, {
        held: panel.held,
        busy: false,
        manual: panel.manual,
      });
    }
  });
}

function installLoader(nodeType) {
  const originalCreated = nodeType.prototype.onNodeCreated;
  nodeType.prototype.onNodeCreated = function () {
    const result = originalCreated?.apply(this, arguments);
    this.showAdvanced = false;
    attachPanel(
      this,
      t("★ Image Ledger queue", "★ 在这个蓝框里操作（随机 / 手动）"),
      t("Choose random preview or manual selection.", "先点「随机预览」或「手动选图」。"),
      { withPreview: true },
    );
    setTimeout(() => {
      callPreviewApi(this, "status").catch(() => {});
    }, 300);
    return result;
  };

  const originalExecuted = nodeType.prototype.onExecuted;
  nodeType.prototype.onExecuted = function (message) {
    const result = originalExecuted?.apply(this, arguments);
    const payload = parsePayload(message, "ledger_status") || {};
    if (!payload.selected && message?.images?.[0]) {
      const img = message.images[0];
      payload.selected = [img.subfolder, img.filename].filter(Boolean).join("/");
      payload.held = true;
      payload.message = payload.message || t("Generation started", "已开始生成");
    }
    updatePanel(this, payload, "running", message);
    if (this.__image_ledgerLedgerPanel) {
      this.__image_ledgerLedgerPanel.title.textContent = payload.manual
        ? t("★ Generating from manual selection…", "★ 正在用手动图生成…")
        : t("★ Generating from held preview…", "★ 正在用预览锁定的图生成…");
    }
    if (payload && this.outputs?.length >= 6) {
      this.outputs[4].label = t(`Pending ${payload.pending ?? "?"}`, `待处理 ${payload.pending ?? "?"}`);
      this.outputs[5].label = t(`Done ${payload.done ?? "?"}`, `已完成 ${payload.done ?? "?"}`);
    }
    return result;
  };
}

function installCommit(nodeType) {
  const originalCreated = nodeType.prototype.onNodeCreated;
  nodeType.prototype.onNodeCreated = function () {
    const result = originalCreated?.apply(this, arguments);
    attachPanel(
      this,
      t("Automatic commit", "自动记账（无需操作）"),
      t("Marks the source complete after the final video is saved.", "最终视频保存成功后，这里会自动把对应原图标记为已完成。"),
      { withPreview: false },
    );
    return result;
  };

  const originalExecuted = nodeType.prototype.onExecuted;
  nodeType.prototype.onExecuted = function (message) {
    const result = originalExecuted?.apply(this, arguments);
    const payload = parsePayload(message, "ledger_commit");
    updatePanel(
      this,
      payload,
      payload?.committed || payload?.idempotent ? "done" : "idle",
      message,
    );

    if (
      payload?.auto_queue &&
      Number(payload.pending) > 0 &&
      Number(payload.running) === 0 &&
      !autoQueueInFlight
    ) {
      autoQueueInFlight = true;
      window.setTimeout(async () => {
        try {
          await app.queuePrompt(0, 1);
        } catch (error) {
          console.error("ComfyUI Image Ledger: auto queue failed.", error);
        } finally {
          autoQueueInFlight = false;
        }
      }, 350);
    }
    return result;
  };
}

app.registerExtension({
  name: "ComfyUI.ImageLedger",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (LOADER_CLASSES.has(nodeData.name)) installLoader(nodeType);
    if (nodeData.name === COMMIT_CLASS) installCommit(nodeType);
  },
});
