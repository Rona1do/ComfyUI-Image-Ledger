import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const STYLE_ID = "image_ledger-global-tracker-style-v4";
const PANEL_ID = "image_ledger-global-tracker-panel";
const GALLERY_ID = "image_ledger-global-gallery";
const SETTING_ENABLED = "ImageLedger.GlobalTracker.Enabled";
const SETTING_MOVE = "ImageLedger.GlobalTracker.AutoMove";
const SETTING_HIDE = "ImageLedger.GlobalTracker.HideUsed";

let usedSet = new Set();
let lastStatus = null;
let lastPick = null;
let folderOptions = [];
const FOLDER_KEY = "image_ledger.global.pickFolder";
const IS_ZH = String(navigator.language || "").toLowerCase().startsWith("zh");
const t = (en, zh) => (IS_ZH ? zh : en);

function selectedFolder() {
  const panel = document.getElementById(PANEL_ID);
  const select = panel?.querySelector("select[data-role=folder]");
  return String(select?.value || localStorage.getItem(FOLDER_KEY) || "").trim();
}

function setSelectedFolder(folder) {
  if (!folder) return;
  localStorage.setItem(FOLDER_KEY, folder);
  const panel = document.getElementById(PANEL_ID);
  const select = panel?.querySelector("select[data-role=folder]");
  if (select && [...select.options].some((opt) => opt.value === folder)) {
    select.value = folder;
  }
}

function ensureStyle() {
  const stale = document.getElementById("image_ledger-global-tracker-style");
  if (stale) stale.remove();
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = `
    #${PANEL_ID} {
      position: fixed;
      right: 16px;
      bottom: 16px;
      z-index: 1200;
      width: 360px;
      padding: 10px 12px 12px;
      border: 1px solid rgba(93, 197, 255, .42);
      border-radius: 10px;
      background: linear-gradient(145deg, rgba(12, 30, 46, .96), rgba(20, 21, 30, .96));
      color: #d9efff;
      font: 12px/1.45 system-ui, sans-serif;
      box-shadow: 0 8px 24px rgba(0,0,0,.35);
    }
    #${PANEL_ID} strong { color: #7ed4ff; font-size: 13px; }
    #${PANEL_ID} .image_ledger-global-row { margin: 6px 0 8px; color: #c5def0; }
    #${PANEL_ID} .image_ledger-global-btns { display: flex; flex-wrap: wrap; gap: 6px; }
    #${PANEL_ID} button {
      appearance: none;
      border: 1px solid rgba(126, 212, 255, .4);
      border-radius: 7px;
      background: rgba(20, 40, 60, .9);
      color: #cfe9ff;
      font: 600 12px/1.2 system-ui, sans-serif;
      padding: 7px 8px;
      cursor: pointer;
    }
    #${PANEL_ID} button:disabled { opacity: .55; cursor: wait; }
    #${PANEL_ID} button[data-primary="1"] {
      background: rgba(28, 120, 200, .95);
      border-color: rgba(160, 220, 255, .8);
      color: #fff;
    }
    #${PANEL_ID} button[data-warn="1"] {
      background: rgba(120, 50, 40, .95);
      border-color: rgba(255, 160, 120, .7);
      color: #ffe6d6;
    }
    #${PANEL_ID} .image_ledger-global-toast { margin-top: 8px; color: #71e4a7; }
    #${PANEL_ID} .image_ledger-global-toast[data-kind="error"] { color: #ff9b9b; }
    #${PANEL_ID} .image_ledger-global-close {
      position: absolute;
      top: 6px;
      right: 8px;
      border: none;
      background: transparent;
      color: #9cc7df;
      padding: 2px 6px;
    }
    #${PANEL_ID} .image_ledger-preview-wrap {
      margin: 8px 0;
      min-height: 120px;
      max-height: 280px;
      border-radius: 8px;
      overflow: hidden;
      background: rgba(0,0,0,.35);
      border: 1px solid rgba(126, 212, 255, .25);
      display: none;
    }
    #${PANEL_ID} .image_ledger-preview-wrap[data-open="1"] { display: block; }
    #${PANEL_ID} .image_ledger-preview-wrap img {
      display: block;
      width: 100%;
      max-height: 280px;
      object-fit: contain;
      background: #111;
    }
    #${PANEL_ID} .image_ledger-preview-path {
      margin-top: 4px;
      color: #9cc7df;
      word-break: break-all;
      font-family: ui-monospace, Consolas, monospace;
      font-size: 11px;
    }
    #${PANEL_ID} .image_ledger-global-more {
      margin-top: 8px;
      opacity: .85;
    }
    #${PANEL_ID} .image_ledger-folder-row {
      display: flex;
      flex-direction: column;
      gap: 4px;
      margin: 8px 0;
    }
    #${PANEL_ID} .image_ledger-folder-row label {
      color: #9cc7df;
      font-size: 11px;
    }
    #${GALLERY_ID} {
      position: fixed;
      inset: 0;
      z-index: 2000;
      display: none;
      background: rgba(4, 8, 14, .92);
      color: #e8f6ff;
      font: 13px/1.4 system-ui, sans-serif;
    }
    #${GALLERY_ID}[data-open="1"] { display: flex; flex-direction: column; }
    #${GALLERY_ID} .image_ledger-gal-bar {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 10px 16px;
      border-bottom: 1px solid rgba(126, 212, 255, .25);
    }
    #${GALLERY_ID} .image_ledger-gal-bar strong { color: #7ed4ff; font-size: 16px; }
    #${GALLERY_ID} .image_ledger-gal-bar span { color: #9cc7df; }
    #${GALLERY_ID} .image_ledger-gal-bar button { margin-left: auto; }
    #${GALLERY_ID} .image_ledger-gal-body {
      flex: 1;
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(300px, 36vw);
      min-height: 0;
    }
    #${GALLERY_ID} .image_ledger-gal-grid {
      overflow: auto;
      padding: 12px;
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
      gap: 12px;
      align-content: start;
      min-width: 0;
    }
    #${GALLERY_ID} .image_ledger-gal-card {
      appearance: none;
      box-sizing: border-box;
      width: 100%;
      min-width: 0;
      aspect-ratio: 1 / 1;
      border: 2px solid transparent;
      border-radius: 10px;
      background: #111;
      padding: 0;
      margin: 0;
      overflow: hidden;
      cursor: pointer;
    }
    #${GALLERY_ID} .image_ledger-gal-card[data-active="1"] {
      border-color: #7ed4ff;
      box-shadow: 0 0 0 2px rgba(126, 212, 255, .35);
    }
    #${GALLERY_ID} .image_ledger-gal-card img {
      display: block;
      width: 100%;
      height: 100%;
      object-fit: contain;
      object-position: center;
      background: #111;
    }
    #${GALLERY_ID} .image_ledger-gal-detail {
      border-left: 1px solid rgba(126, 212, 255, .2);
      padding: 12px;
      overflow: auto;
      background: rgba(8, 14, 22, .9);
    }
    #${GALLERY_ID} .image_ledger-gal-detail img {
      width: 100%;
      max-height: calc(100vh - 180px);
      object-fit: contain;
      background: #000;
      border-radius: 8px;
    }
    #${GALLERY_ID} .image_ledger-gal-detail p {
      margin: 8px 0;
      color: #9cc7df;
      word-break: break-all;
    }
    #${GALLERY_ID} .image_ledger-gal-bar button,
    #${GALLERY_ID} .image_ledger-gal-detail button {
      appearance: none;
      border: 1px solid rgba(126, 212, 255, .4);
      border-radius: 8px;
      background: rgba(20, 40, 60, .95);
      color: #cfe9ff;
      font: 600 13px/1.2 system-ui, sans-serif;
      padding: 8px 12px;
      cursor: pointer;
    }
    #${GALLERY_ID} button[data-primary="1"] {
      background: rgba(28, 120, 200, .95);
      color: #fff;
    }
    #${PANEL_ID} .image_ledger-folder-row select {
      width: 100%;
      border: 1px solid rgba(126, 212, 255, .4);
      border-radius: 7px;
      background: rgba(10, 16, 24, .95);
      color: #e8f6ff;
      font: 12px/1.3 system-ui, sans-serif;
      padding: 6px 8px;
    }
  `;
  document.head.appendChild(style);
}

async function fetchJson(url, options) {
  const response = await api.fetchApi(url, options);
  const data = await response.json();
  if (!response.ok || data?.ok === false) {
    throw new Error(data?.error || `HTTP ${response.status}`);
  }
  return data;
}

function applyUsedList(used) {
  usedSet = new Set();
  for (const item of used || []) {
    const rel = String(item || "").replaceAll("\\", "/").replace(/^\/+|\/+$/g, "");
    if (!rel) continue;
    usedSet.add(rel);
    usedSet.add(rel.toLowerCase());
    const name = rel.split("/").pop();
    if (name) {
      usedSet.add(name);
      usedSet.add(name.toLowerCase());
    }
  }
}

function isUsedName(value) {
  const rel = String(value || "").replaceAll("\\", "/").replace(/^\/+|\/+$/g, "");
  if (!rel) return false;
  if (usedSet.has(rel) || usedSet.has(rel.toLowerCase())) return true;
  const name = rel.split("/").pop();
  return !!(name && (usedSet.has(name) || usedSet.has(name.toLowerCase())));
}

function sourceLoaders() {
  const nodes = app.graph?._nodes || [];
  return nodes.filter((node) => {
    const type = node.comfyClass || node.type;
    if (type !== "LoadImage") return false;
    if (node.mode === 2 || node.mode === 4) return false;
    return true;
  });
}

function pickSourceNode() {
  const loaders = sourceLoaders();
  const scored = loaders.map((node) => {
    const title = String(node.title || "");
    let score = 0;
    if (/选择源图|first-frame|首帧|原图/i.test(title)) score += 10;
    if (node.mode === 0) score += 1;
    return { node, score };
  });
  scored.sort((a, b) => b.score - a.score);
  return scored[0]?.node || null;
}

function imageWidget(node) {
  return node?.widgets?.find((widget) => widget.name === "image") || null;
}

function currentImagePath() {
  const widget = imageWidget(pickSourceNode());
  return String(widget?.value || "").replace(/^\[(?:done|已跑)\]\s*/i, "").replaceAll("\\", "/");
}

function viewUrl(image) {
  const path = image?.selected || [image?.subfolder, image?.filename].filter(Boolean).join("/");
  return thumbUrl(path, 1280);
}

function thumbUrl(path, size = 360) {
  const params = new URLSearchParams({ path: path || "", size: String(size), v: "pad2" });
  const url = `/image_ledger/global/thumb?${params.toString()}`;
  return api?.apiURL ? api.apiURL(url) : url;
}

function itemFromPath(path) {
  const parts = String(path || "").replaceAll("\\", "/").split("/").filter(Boolean);
  return {
    path,
    name: parts[parts.length - 1] || "",
    image: {
      filename: parts[parts.length - 1] || "",
      subfolder: parts.slice(0, -1).join("/"),
      type: "input",
      selected: path,
    },
  };
}

function showPreview(pick) {
  const panel = document.getElementById(PANEL_ID);
  if (!panel) return;
  const wrap = panel.querySelector(".image_ledger-preview-wrap");
  const img = panel.querySelector(".image_ledger-preview-wrap img");
  const pathLine = panel.querySelector(".image_ledger-preview-path");
  if (!pick?.image) {
    wrap.dataset.open = "0";
    pathLine.textContent = "";
    return;
  }
  wrap.dataset.open = "1";
  img.src = thumbUrl(pick.selected || pick.image?.selected || "", 900);
  img.alt = pick.selected || "";
  const left = pick.remaining != null
    ? t(`, about ${pick.remaining} remaining`, `，本夹还剩约 ${pick.remaining} 张可换`)
    : "";
  pathLine.textContent = `${pick.selected || ""}${left}`;
}

async function refreshStatus() {
  const data = await fetchJson("/image_ledger/global/status");
  lastStatus = data.status || {};
  applyUsedList(data.used || []);
  renderPanel(data);
  if (lastPick) showPreview(lastPick);
  return data;
}

function toast(text, kind) {
  const panel = document.getElementById(PANEL_ID);
  if (!panel) return;
  let line = panel.querySelector(".image_ledger-global-toast");
  if (!line) {
    line = document.createElement("div");
    line.className = "image_ledger-global-toast";
    panel.appendChild(line);
  }
  line.dataset.kind = kind || "ok";
  line.textContent = text;
}

async function syncSettingsToBackend() {
  const enabled = app.ui.settings.getSettingValue(SETTING_ENABLED, true);
  const autoMove = app.ui.settings.getSettingValue(SETTING_MOVE, false);
  const hideUsed = app.ui.settings.getSettingValue(SETTING_HIDE, true);
  await fetchJson("/image_ledger/global/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      enabled,
      auto_move: autoMove,
      hide_used_in_picker: hideUsed,
    }),
  });
  const panel = document.getElementById(PANEL_ID);
  if (panel) panel.style.display = enabled ? "block" : "none";
}

async function relocateUsed() {
  toast(t("Moving tracked sources into their _used folders…", "正在把已记账原图分到各分类自己的 _used …"));
  const data = await fetchJson("/image_ledger/global/relocate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  await refreshStatus();
  toast(t(
    `Moved ${data.moved}; skipped ${data.skipped}; missing ${data.missing}.`,
    `已移动 ${data.moved} 张到 _used，跳过 ${data.skipped}，没对上 ${data.missing}`,
  ));
}

async function scanHistory() {
  toast(t("Scanning existing videos…", "正在扫描已有成片并分类，视频多时可能要几分钟…"));
  const data = await fetchJson("/image_ledger/global/scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ move: true }),
  });
  lastStatus = data.status || lastStatus;
  applyUsedList((await refreshStatus()).used);
  const relocated = data.relocated?.moved || 0;
  toast(t(
    `Scan complete: ${data.scanned} videos, ${data.marked} marked, ${relocated} moved.`,
    `扫描完成：成片 ${data.scanned}，新标记 ${data.marked}，已分到 _used ${relocated} 张`,
  ));
}

async function undoLast() {
  const data = await fetchJson("/image_ledger/global/undo", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  await refreshStatus();
  toast(data.restored
    ? t(`Restored: ${data.restored}`, `已撤回：${data.restored}`)
    : t("Undid the latest record.", "已撤回上一张记录"));
}

async function randomPick(skipCurrent = true) {
  const folder = selectedFolder();
  const current = currentImagePath() || lastPick?.selected || "";
  if (!folder && !current) {
    throw new Error(t("Select a category folder first.", "请先选择分类文件夹。"));
  }
  const response = await api.fetchApi("/image_ledger/global/random_pick", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      path: current,
      folder,
      skip_current: skipCurrent,
    }),
  });
  const data = await response.json();
  if (data.folders) fillFolderSelect(data.folders, data.folder || folder);
  if (!response.ok || data?.ok === false) {
    throw new Error(data?.error || `HTTP ${response.status}`);
  }
  if (data.folder) setSelectedFolder(data.folder);
  lastPick = data;
  showPreview(data);
  toast(data.message || t(`Selected ${data.selected}`, `抽到 ${data.selected}`));
  return data;
}

function pickFromList(item) {
  const parsed = itemFromPath(item.path);
  lastPick = {
    selected: parsed.path,
    folder: selectedFolder(),
    image: parsed.image,
  };
  showPreview(lastPick);
  toast(t(`Selected ${item.path}. Run it when ready.`, `已指定 ${item.path}，点「用这张跑」`));
}

function closeGallery() {
  document.getElementById(GALLERY_ID)?.remove();
}

function selectGalleryItem(item, card, gal) {
  pickFromList(item);
  gal.querySelectorAll("[data-card]").forEach((el) => {
    el.style.outline = el === card ? "3px solid #7ed4ff" : "none";
  });
  const big = gal.querySelector("[data-role=big]");
  const path = gal.querySelector("[data-role=path]");
  if (big) {
    big.src = thumbUrl(item.path, 1280);
    big.style.cssText = "width:100%;max-height:calc(100vh - 160px);object-fit:contain;background:#000;border-radius:8px;display:block;";
  }
  if (path) path.textContent = item.path;
}

async function openGallery() {
  const folder = selectedFolder();
  if (!folder) throw new Error(t("Select a category folder first.", "请先选择分类文件夹。"));
  closeGallery();

  const gal = document.createElement("div");
  gal.id = GALLERY_ID;
  gal.style.cssText = "position:fixed;left:0;top:0;right:0;bottom:0;z-index:2147483646;display:flex;flex-direction:column;background:#070b10;";

  const bar = document.createElement("div");
  bar.style.cssText = "display:flex;align-items:center;gap:12px;padding:10px 16px;background:#101820;color:#d9efff;flex:0 0 auto;";
  const title = document.createElement("div");
  title.style.cssText = "font:700 16px/1.2 system-ui,sans-serif;color:#7ed4ff;";
  title.textContent = folder;
  const count = document.createElement("div");
  count.style.cssText = "color:#9cc7df;font:13px system-ui,sans-serif;";
  count.textContent = t("Loading…", "加载中…");
  const closeBtn = document.createElement("div");
  closeBtn.textContent = t("Close", "关闭");
  closeBtn.style.cssText = "margin-left:auto;padding:8px 14px;background:#1c3c5c;color:#fff;border-radius:8px;cursor:pointer;font:600 13px system-ui,sans-serif;";
  closeBtn.addEventListener("click", closeGallery);
  bar.append(title, count, closeBtn);

  const body = document.createElement("div");
  body.style.cssText = "flex:1;display:flex;min-height:0;min-width:0;";

  const grid = document.createElement("div");
  grid.style.cssText = "flex:1;min-width:0;overflow:auto;padding:16px;display:flex;flex-wrap:wrap;gap:12px;align-content:start;";

  const detail = document.createElement("div");
  detail.style.cssText = "flex:0 0 380px;width:380px;padding:12px;background:#0c1218;border-left:1px solid #234;overflow:auto;";
  const big = document.createElement("img");
  big.setAttribute("data-role", "big");
  big.alt = t("Large preview", "大图预览");
  big.style.cssText = "width:100%;max-height:calc(100vh - 160px);object-fit:contain;background:#000;border-radius:8px;display:block;min-height:240px;";
  const pathLine = document.createElement("div");
  pathLine.setAttribute("data-role", "path");
  pathLine.style.cssText = "margin:10px 0;color:#9cc7df;word-break:break-all;font:12px/1.4 ui-monospace,Consolas,monospace;";
  pathLine.textContent = t("Select a thumbnail, then use this image.", "点缩略图看完整大图，再点「用这张」");
  const useBtn = document.createElement("div");
  useBtn.textContent = t("Use this image", "用这张");
  useBtn.style.cssText = "padding:10px 16px;background:#1c78c8;color:#fff;border-radius:8px;cursor:pointer;font:700 14px system-ui,sans-serif;display:inline-block;";
  useBtn.addEventListener("click", async () => {
    if (!lastPick?.selected) {
      toast(t("Select an image first.", "先点一张图"), "error");
      return;
    }
    closeGallery();
    showPreview(lastPick);
    try {
      await runPicked();
    } catch (error) {
      toast(String(error.message || error), "error");
    }
  });
  detail.append(big, pathLine, useBtn);

  body.append(grid, detail);
  gal.append(bar, body);
  document.body.appendChild(gal);

  const params = new URLSearchParams({ folder, limit: "2000" });
  let data;
  try {
    data = await fetchJson(`/image_ledger/global/list_folder?${params}`);
  } catch (error) {
    count.textContent = "";
    grid.textContent = t(`Load failed: ${error.message || error}`, `加载失败：${error.message || error}`);
    return;
  }
  const items = data.items || [];
  count.textContent = t(`${items.length} available (_used excluded)`, `${items.length} 张待选（已排除 _used）`);
  if (!items.length) {
    grid.textContent = t("No pending images in this category.", "这个分类没有待选原图");
    return;
  }
  for (const item of items) {
    const card = document.createElement("div");
    card.setAttribute("data-card", "1");
    card.title = item.path;
    card.style.cssText = "width:220px;height:220px;flex:0 0 220px;background:#111;border-radius:10px;overflow:hidden;cursor:pointer;box-sizing:border-box;";
    const img = document.createElement("img");
    img.loading = "lazy";
    img.alt = item.name;
    img.style.cssText = "width:220px;height:220px;object-fit:contain;display:block;background:#111;";
    img.src = thumbUrl(item.path, 440);
    img.onerror = () => {
      img.replaceWith(Object.assign(document.createElement("div"), {
        textContent: t("Preview failed", "预览失败"),
        style: "width:220px;height:220px;display:flex;align-items:center;justify-content:center;color:#888;font:12px system-ui",
      }));
    };
    card.appendChild(img);
    card.addEventListener("click", () => selectGalleryItem(item, card, gal));
    grid.appendChild(card);
  }
}

function fillFolderSelect(folders, preferred) {
  folderOptions = folders || [];
  const panel = document.getElementById(PANEL_ID);
  const select = panel?.querySelector("select[data-role=folder]");
  if (!select) return;
  const want = preferred || localStorage.getItem(FOLDER_KEY) || "";
  select.innerHTML = "";
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = t("Select a category folder…", "选择分类文件夹…");
  select.appendChild(placeholder);
  for (const item of folderOptions) {
    const option = document.createElement("option");
    option.value = item.path;
    option.textContent = t(`${item.name} (${item.pending} pending)`, `${item.name}（${item.pending} 张待抽）`);
    select.appendChild(option);
  }
  if (want && [...select.options].some((opt) => opt.value === want)) {
    select.value = want;
  }
}

async function applyPickedToLoader(path) {
  const node = pickSourceNode();
  const widget = imageWidget(node);
  if (!node || !widget) {
    throw new Error(t("No source LoadImage node was found in this workflow.", "当前工作流没有找到源图 LoadImage 节点。"));
  }
  if (Array.isArray(widget.options?.values) && !widget.options.values.includes(path)) {
    widget.options.values = [path, ...widget.options.values];
  }
  widget.value = path;
  if (typeof widget.callback === "function") widget.callback(path);
  node.setDirtyCanvas?.(true, true);
  app.graph?.setDirtyCanvas?.(true, true);
}

async function runPicked() {
  const path = lastPick?.selected || currentImagePath();
  if (!path) throw new Error(t("No image is selected. Pick one first.", "还没有预览图。先点「随机抽一张」。"));
  const staged = await fetchJson("/image_ledger/global/stage", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  await applyPickedToLoader(staged.load_name);
  toast(t(`Selected ${staged.source || path}; queueing…`, `已选中 ${staged.source || path}，开始排队…`));
  if (typeof app.queuePrompt === "function") {
    await app.queuePrompt(0);
    return;
  }
  const button = document.querySelector("#queue-button, button.queue-btn, .queue-button");
  if (button) button.click();
  else throw new Error(t("Queue button not found; use ComfyUI's Queue button.", "找不到 Queue 按钮，请手动点右上角 Queue。"));
}

async function markPoor() {
  const path = lastPick?.selected || currentImagePath();
  const data = await fetchJson("/image_ledger/global/mark_poor", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  await refreshStatus();
  toast(data.message || t(`Moved to rejected: ${data.moved_to}`, `已标为效果不佳：${data.moved_to}`));
}

function renderPanel(data) {
  ensureStyle();
  let panel = document.getElementById(PANEL_ID);
  if (!panel) {
    panel = document.createElement("div");
    panel.id = PANEL_ID;
    panel.innerHTML = `
      <button class="image_ledger-global-close" title="${t("Hide", "暂时隐藏")}">×</button>
      <strong>${t("Global Image Ledger", "全局原图台账")}</strong>
      <div class="image_ledger-global-row" data-role="stats">${t("Loading…", "加载中…")}</div>
      <div class="image_ledger-folder-row">
        <label>${t("Category (gallery and random pick exclude _used)", "分类（浏览大图 / 随机抽都只在这个夹，排除 _used）")}</label>
        <select data-role="folder"></select>
      </div>
      <div class="image_ledger-global-btns">
        <button data-act="browse" data-primary="1">${t("Open gallery", "浏览大图选图")}</button>
        <button data-act="pick">${t("Random pick", "随机抽一张")}</button>
        <button data-act="skip">${t("Another", "换一张")}</button>
        <button data-act="run" data-primary="1">${t("Run this", "用这张跑")}</button>
        <button data-act="poor" data-warn="1">${t("Reject", "效果不佳")}</button>
      </div>
      <div class="image_ledger-preview-wrap">
        <img alt="${t("Preview", "预览")}">
      </div>
      <div class="image_ledger-preview-path"></div>
      <div class="image_ledger-global-btns image_ledger-global-more">
        <button data-act="relocate">${t("Move tracked to _used", "立刻分到 _used")}</button>
        <button data-act="scan">${t("Scan existing videos", "补扫成片")}</button>
        <button data-act="undo">${t("Undo latest", "撤回上一张")}</button>
      </div>
      <div class="image_ledger-global-toast"></div>
    `;
    document.body.appendChild(panel);
    panel.querySelector(".image_ledger-global-close").addEventListener("click", () => {
      panel.style.display = "none";
    });
    panel.querySelector("select[data-role=folder]").addEventListener("change", (event) => {
      setSelectedFolder(event.target.value);
      lastPick = null;
      showPreview(null);
      toast(event.target.value
        ? t(`Category set to ${event.target.value}.`, `已锁定 ${event.target.value}，点「浏览大图选图」`)
        : t("Select a category.", "请选定分类"));
    });
    panel.addEventListener("click", async (event) => {
      const button = event.target.closest("button[data-act]");
      if (!button) return;
      button.disabled = true;
      try {
        if (button.dataset.act === "browse") await openGallery();
        if (button.dataset.act === "pick") await randomPick(true);
        if (button.dataset.act === "skip") await randomPick(true);
        if (button.dataset.act === "run") await runPicked();
        if (button.dataset.act === "poor") await markPoor();
        if (button.dataset.act === "relocate") await relocateUsed();
        if (button.dataset.act === "scan") await scanHistory();
        if (button.dataset.act === "undo") await undoLast();
      } catch (error) {
        toast(String(error.message || error), "error");
      } finally {
        button.disabled = false;
      }
    });
  }
  const enabled = app.ui.settings.getSettingValue(SETTING_ENABLED, true);
  panel.style.display = enabled ? "block" : "none";
  const status = data?.status || lastStatus || {};
  const done = Number(status.done || 0);
  panel.querySelector("[data-role=stats]").textContent =
    t(
      `${done} completed. Select a category, then open the gallery or pick randomly.`,
      `已跑 ${done} 张。先选定分类，再点「浏览大图选图」看图挑选。`,
    );
  if (data?.folders) fillFolderSelect(data.folders, selectedFolder());
}

function decorateLoadImageWidgets(node) {
  if (!node?.widgets) return;
  const hide = app.ui.settings.getSettingValue(SETTING_HIDE, true);
  for (const widget of node.widgets) {
    if (!widget || widget.name !== "image" || !Array.isArray(widget.options?.values)) continue;
    const raw = widget.options.values;
    const next = [];
    for (const value of raw) {
      const text = String(value);
      const clean = text.replace(/^\[(?:done|已跑)\]\s*/i, "");
      const used = isUsedName(clean);
      if (used && hide) continue;
      next.push(used ? `[${t("done", "已跑")}] ${clean}` : clean);
    }
    widget.options.values = next;
    if (typeof widget.value === "string" && isUsedName(widget.value.replace(/^\[(?:done|已跑)\]\s*/i, ""))) {
      if (hide && next.length) widget.value = next[0];
      else if (!hide && !/^\[(?:done|已跑)\]/i.test(String(widget.value))) {
        widget.value = `[${t("done", "已跑")}] ${widget.value.replace(/^\[(?:done|已跑)\]\s*/i, "")}`;
      }
    }
  }
}

app.registerExtension({
  name: "ImageLedger.GlobalImageTracker",
  settings: [
    {
      id: SETTING_ENABLED,
      name: t("Enable Global Image Ledger", "启用全局原图台账"),
      type: "boolean",
      defaultValue: true,
      category: ["Image Ledger", "Global tracking", "Enabled"],
      tooltip: t("Track source images after a workflow saves a video.", "任意图生视频工作流成片成功后，自动记录用过的原图，不用改每个工作流。"),
      onChange: () => { syncSettingsToBackend().catch(() => {}); },
    },
    {
      id: SETTING_MOVE,
      name: t("Move source to _used after success", "成片后把原图移到 _used 文件夹"),
      type: "boolean",
      defaultValue: false,
      category: ["Image Ledger", "Global tracking", "Move source"],
      tooltip: t("Opt-in file move: input/AI/portraits/1.png → input/AI/portraits/_used/1.png.", "选择性移动文件：input/AI/角色/1.png → input/AI/角色/_used/1.png。"),
      onChange: () => { syncSettingsToBackend().catch(() => {}); },
    },
    {
      id: SETTING_HIDE,
      name: t("Hide completed images in LoadImage", "在 LoadImage 列表里隐藏已跑原图"),
      type: "boolean",
      defaultValue: true,
      category: ["Image Ledger", "Global tracking", "Hide completed"],
      tooltip: t("When disabled, completed entries remain visible with a [done] prefix.", "关闭后，已完成条目会保留并带 [已跑] 前缀。"),
    },
  ],
  commands: [
    {
      id: "image_ledger.global.scan",
      label: t("Image Ledger: scan existing videos", "扫描已有成片到原图台账"),
      function: () => scanHistory().catch((error) => toast(String(error.message || error), "error")),
    },
    {
      id: "image_ledger.global.undo",
      label: t("Image Ledger: undo latest", "撤回上一张已跑原图"),
      function: () => undoLast().catch((error) => toast(String(error.message || error), "error")),
    },
    {
      id: "image_ledger.global.pick",
      label: t("Image Ledger: random pick", "从当前文件夹随机抽一张原图"),
      function: () => randomPick(true).catch((error) => toast(String(error.message || error), "error")),
    },
    {
      id: "image_ledger.global.poor",
      label: t("Image Ledger: reject current image", "标记上一张为效果不佳"),
      function: () => markPoor().catch((error) => toast(String(error.message || error), "error")),
    },
  ],
  async setup() {
    ensureStyle();
    try {
      await syncSettingsToBackend();
      await refreshStatus();
    } catch (error) {
      renderPanel({});
      toast(t(`Image Ledger API unavailable: ${error.message || error}`, `台账接口不可用：${error.message || error}`), "error");
    }
    api.addEventListener("image_ledger_global_used", async (event) => {
      const payload = event.detail || {};
      const names = (payload.marked || []).map((item) => item.rel_path).filter(Boolean);
      try {
        await refreshStatus();
      } catch (_) {
        /* keep previous */
      }
      if (names.length) {
        const moved = (payload.marked || []).some((item) => item.moved);
        toast(t(
          `Tracked: ${names.join(", ")}${moved ? "; moved to _used" : ""}`,
          `已记录：${names.join("、")}${moved ? "，并已移到 _used" : ""}`,
        ));
      }
    });
  },
  nodeCreated(node) {
    const name = node?.comfyClass || node?.type;
    if (name === "LoadImage" || name === "LoadImageMask") {
      decorateLoadImageWidgets(node);
    }
  },
});
