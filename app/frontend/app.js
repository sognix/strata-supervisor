"use strict";
// Strata Supervisor web UI. Plain JS, no build step. Text from the server always goes in through textContent;
// innerHTML is only used for the constant icons below.

const $ = (id) => document.getElementById(id);
const GIB = 1024 ** 3;
// RAM estimate, all in GiB. The rules come from Strata's docs (AMD_HIP_PERFORMANCE.md: the resident RAM mode keeps the
// complement of the static GPU cache plus the prompt path's lendable slots in RAM) and the desktop app's native-fit.ts
// (KV cache per session). The sizes are what the engine logged for coder IQ1_M on the RX 7900 XTX (engine 0.1.55,
// 2026-11-07); other cards and engines can differ, so the model page also shows the measured values after a load.
const EST = {
  denseGib: 2.4,          // dense weights and buffers on the cards (measured 2.3 GiB)
  mtpGib: 1.03,           // draft layer 839 MiB + draft head 218 MiB
  reserveMib: 700,        // the engine's default VRAM reserve
  afterCacheGib: 0.25,    // "256 MiB for what is allocated after the cache"
  sessions: 3,            // slot sessions when parallel is 0 (the engine chose 3)
  lendShare: 0.22,        // part of the GPU cache that stays in RAM as well (3.39 of 15.44 GiB)
  conversationGib: 8,     // conversation cache, "sized from the free RAM", at most 8 GiB seen
  baseGib: 1.5,           // the engine's own process memory (anon)
};
// Bytes per token and layer of the KV cache by format, 13 attention layers, 5.4 % overhead, 121 MiB per session
// (apps/host/src/lib/native-fit.ts, cacheGb).
const KV_BYTES = { fp16: 2112, int8: 1056, q4_0: 576, k8v4: 816 };
let idleVramUsed = {};  // VRAM other programs used on each card while no model was loaded (card index -> MiB)
// Where Strata keeps the models it downloads itself (docs/USER-GUIDE.md, section 4).
const STRATA_MODELS_DIR = "/data/Strata-data/models";

// Sizes the UI computes are in MiB and GiB (1024). Sizes Strata itself prints (download sizes, plan texts) are shown as Strata gives them.
const ICONS = {
  models: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 3 4 7.5v9L12 21l8-4.5v-9z"/><path d="M4 7.5 12 12l8-4.5M12 12v9"/></svg>',
  logs: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M6 3h9l4 4v14H6z"/><path d="M9 11h7M9 15h7M9 7h3"/></svg>',
  chip: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="6" y="6" width="12" height="12" rx="1.5"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/></svg>',
  theme: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 0 0 16z" fill="currentColor"/></svg>',
  sidebar: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/></svg>',
  play: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M7 4.5v15l12-7.5z"/></svg>',
  stop: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="6" y="6" width="12" height="12" rx="1.5"/></svg>',
  open: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 5v14M5 12h14"/></svg>',
  edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13 7 4 4"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg>',
  disk: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 14h18"/><circle cx="17" cy="16.5" r="0.8" fill="currentColor"/></svg>',
  caret: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m9 6 6 6-6 6"/></svg>',
};

const RAM_MODES = [
  { id: "normal", title: "Normal", slow: false,
    text: "All experts stay in RAM, including those on the cards. Uses the most RAM. No reads from the SSD." },
  { id: "resident-experts", title: "Only what no card holds", slow: false,
    text: "Experts kept on a graphics card are left out of RAM. Uses much less RAM. No reads from the SSD." },
  { id: "budget", title: "RAM budget", slow: true,
    text: "Keeps up to N GiB of experts in RAM and reads the rest from the SSD when needed." },
  { id: "mmap-experts", title: "From the model files", slow: true,
    text: "Reads experts straight from the model files. The system frees this RAM when other programs need it. Uses the least fixed RAM." },
];

const DEFAULTS = { model: "", family: "", model_path: "", context: "32k", kv: "int8", vision: "off", mtp: "", parallel: 0,
  gpu_roles: "", ram_mode: "normal", ram_budget_gib: 0, vram_reserve_mib: null, vram_reserve_later_mib: null, extra_set: [], notes: "" };

const NAME_PATTERN = /^[a-z][a-z0-9_]*(-[a-z0-9_]+)*$/;

let status = null;   // /api/status
let models = null;   // /api/models
let view = null;     // the current page: { update(), poll() } (both optional)
let activeLaunch = [];  // launch output of the active model, for the progress line

// ---- helpers

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) node.setAttribute(k, v === true ? "" : v);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function icon(name) {
  const span = el("span", { "data-icon": name });
  span.innerHTML = ICONS[name]; // constant markup, never server data
  return span;
}

async function api(path, body) {
  const opts = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const response = await fetch(path, opts);
  let data = {};
  try { data = await response.json(); } catch (e) { /* empty body */ }
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

const fmtGiB = (bytes) => (bytes / GIB).toFixed(1) + " GiB";
const go = (hash) => { location.hash = hash; };

function setPre(pre, lines, emptyText) {
  const atBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 30;
  pre.textContent = lines.length ? lines.join("\n") : emptyText;
  if (atBottom) pre.scrollTop = pre.scrollHeight;
}

// ---- shared building blocks (DESIGN.md, section 5: shared classes, no per-page styles)

const badge = (text, kind = "") => el("span", { class: "badge " + kind }, text);

/** Card header: optional number in a blue circle, bold title, extra nodes, grey hint on the right. */
function cardHead({ num, title, hint, big, extra = [] }) {
  return el("div", { class: "card-head" },
    num ? el("span", { class: "num" }, num) : null,
    title !== undefined ? el("h2", { class: big ? "big" : "" }, title) : null,
    ...extra,
    hint ? el("span", { class: "hint" }, hint) : null);
}

function card(head, ...body) {
  const parts = body.filter((x) => x !== null && x !== undefined && x !== false);
  return el("section", { class: "card" }, cardHead(head), parts.length ? el("div", { class: "card-body" }, ...parts) : null);
}

/** A row of chips with one chosen. Options are [id, label, small text]. */
function chips(options, current, onpick) {
  return el("div", { class: "seg" }, ...options.map(([id, label, small]) =>
    el("a", { class: "chip" + (id === current ? " on" : ""), role: "button", onclick: () => onpick(id) },
      label, small ? el("small", {}, small) : null)));
}

/** A labelled form field: short bold label, the input, a small grey explanation below. */
function field(label, input, explain) {
  return el("label", { class: "f" }, el("b", {}, label), input, explain ? el("i", {}, explain) : null);
}

function facts(rows) {
  return el("dl", { class: "facts" }, ...rows.flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v instanceof Node ? v : String(v))]));
}

function meter(pct) {
  return el("div", { class: "meter" }, el("div", { style: `width:${Math.max(0, Math.min(100, pct)).toFixed(1)}%` }));
}

// ---- model state

function modelState(name) {
  if (!status) return "stopped";
  const m = status.model;
  if (m.active !== name) return "stopped"; // only the active model is asked live (IMPLEMENTATION.md, 5.5)
  if (m.busy) return "unloading";
  if (m.launch_running) return "loading";
  return m.state || "unknown";
}

const stateText = (st) => {
  if (st === "stopped") return "idle";
  return st === "loading" && downloading() ? "loading (downloading)" : st;
};
const stateBadge = (st) => badge(stateText(st), st);

function canLoad() {
  if (!status) return false;
  const m = status.model;
  return status.checks.can_load && !m.busy && !m.launch_running && (!m.active || m.state === "stopped");
}

function titleOf(cfg, name) {
  if (!cfg) return name;
  const row = catalogRow(cfg);
  if (row && !cfg.model_path) return `${groupOf(row).title} · ${versionLabel(row)}`;
  return [cfg.family, cfg.model].filter(Boolean).join(" ") || cfg.model_path || name;
}

// The last progress line of the running launch. Strata says "Downloading the model files" for a new
// download and "Getting the model files" when it resumes one.
const PROGRESS = /(Downloading the model files|Getting the model files|Checking that the model files are complete[^:]*|Downloading the draft layer[^:]*):\s*([\d.]+) of ([\d.]+) GB/;
function progressOf(lines) {
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i].trim();
    const m = PROGRESS.exec(line);
    if (m) return { text: line, pct: Math.min(100, (100 * Number(m[2])) / Number(m[3])) };
    if (line && !line.startsWith("[")) return { text: line, pct: null };
  }
  return null;
}

// Second sign of a download: the model's folder grows between two polls (status.model.download). A model that is
// already on disk and only loads into memory does not count.
let lastDownloadBytes = null, folderGrowing = false;
function trackDownload() {
  const d = status && status.model.download;
  const bytes = d ? d.bytes : null;
  folderGrowing = bytes !== null && lastDownloadBytes !== null && bytes > lastDownloadBytes;
  lastDownloadBytes = bytes;
}

// Whether the active launch downloads right now: Strata's own progress line says so, or the folder grows.
function downloading() {
  const p = progressOf(activeLaunch);
  return folderGrowing || Boolean(p && /^(Downloading|Getting) the model files/.test(p.text));
}

async function load(name) {
  try { await api(`/api/models/${name}/load`, {}); await tick(); }
  catch (e) { alert(e.message); }
}

async function unloadActive() {
  const m = status.model;
  if (!m.active) return;
  if (!confirm(m.launch_running ? `Cancel loading ${m.active}?` : `Unload ${m.active}?`)) return;
  try { await api("/api/unload", {}); await tick(); } catch (e) { alert(e.message); }
}

function loadButton(name, withText) {
  const state = modelState(name);
  if (status && status.model.active === name && state !== "stopped") {
    const label = state === "unloading" ? "Unloading…" : state === "loading" ? "Cancel" : "Unload";
    return el("button", { class: withText ? "danger big" : "icon", title: label, disabled: state === "unloading",
      onclick: (e) => { e.stopPropagation(); unloadActive(); } }, icon("stop"), withText ? label : null);
  }
  const cfg = models && (models.configs.find((c) => c.name === name) || {}).config;
  return el("button", { class: withText ? "primary big" : "icon", title: "Load", disabled: !canLoad() || !cfg,
    onclick: (e) => { e.stopPropagation(); load(name); } }, icon("play"), withText ? "Load" : null);
}

// ---- Strata's prepared models, grouped like the desktop app (apps/host/src/lib/native-catalog.ts, v0.2.13)

const VERSION_LABELS = { Q2_0: "Fastest", IQ2_XS: "Compact", IQ3_XXS: "Balanced", IQ3_S: "High quality", IQ1_M: "Coding",
  "Gyro-S": "Smallest", "Gyro-M": "Small memory", "IST-DAS-1.75bpw": "Smallest", "IST-DAS-2.00bpw": "Balanced", "IST-DAS-2.25bpw": "Best quality",
  "ISTA-DAS-Coder": "ISTA-DAS Coder",
  NVFP4: "NVFP4 (Strata’s version)", "NVFP4-NVIDIA": "NVFP4 (NVIDIA’s version)", "NVFP4-QAD": "NVFP4 (NVIDIA, trained for FP4)" };
const EDITIONS = { swift: "Swift 1.5", coder: "Coder", unsloth: "Unsloth", gyro: "Gyro" };
const GROUPS = [
  { id: "qwen3.8", title: "Qwen3.8-Flash-Next" },
  { id: "qwen3.6", title: "Qwen3.6-35B-A3B" },
  { id: "deepseek41", title: "DeepSeek-V4.1-Flash" },
];

function groupOf(row) {
  const text = `${row.family_title || ""} ${row.family || ""}`;
  if (row.family === "deepseek41") return { id: "deepseek41", title: row.family_title || "DeepSeek-V4.1-Flash" };
  if (/qwen3[._-]?6|35b[-_]?a3b/i.test(text)) return GROUPS[1];
  if (["qwen", "swift", "coder", "unsloth", "gyro"].includes(row.family) || /qwen3[._-]?8|flash.next/i.test(row.family_title || "")) return GROUPS[0];
  return { id: row.family, title: row.family_title || row.family };
}

function versionLabel(row) {
  const label = VERSION_LABELS[row.model] || row.model;
  const edition = groupOf(row).id === "qwen3.8" ? EDITIONS[row.family] || "" : "";
  return [edition, label === row.model && edition ? "" : label].filter(Boolean).join(" · ");
}

/** The rows `strata launch` can start, in the app's groups. The app hides DeepSeek's UD-Q8 sizes. */
function catalogGroups() {
  const rows = (models && models.catalog.rows) || [];
  const groups = new Map();
  for (const row of rows) {
    if (row.family === "deepseek41" && /^UD-Q8/i.test(row.model || "")) continue;
    const g = groupOf(row);
    if (!groups.has(g.id)) groups.set(g.id, { ...g, rows: [] });
    groups.get(g.id).rows.push(row);
  }
  const order = GROUPS.map((g) => g.id);
  return [...groups.values()].sort((a, b) => (order.indexOf(a.id) + 1 || 99) - (order.indexOf(b.id) + 1 || 99));
}

function catalogRow(cfg) {
  if (!models || !cfg) return null;
  const rows = models.catalog.rows.filter((r) => r.model && r.model.toLowerCase() === (cfg.model || "").toLowerCase());
  return rows.find((r) => r.family === cfg.family) || (rows.length === 1 ? rows[0] : null);
}

function suggestName(row, context) {
  const model = (row.model || "").toLowerCase().replace(/[^a-z0-9_-]+/g, "_");
  const prefix = row.family && row.family !== "qwen" && !model.startsWith(row.family) ? row.family + "-" : "";
  let name = `${prefix}${model}-${(context || "").toLowerCase()}`.replace(/-+/g, "-").replace(/^[^a-z]+/, "").replace(/-$/, "");
  return NAME_PATTERN.test(name) ? name.slice(0, 48) : "";
}

// ---- shell: sidebar, top bar, banners

// A small icon button at the right end of a sidebar link. It does its own thing instead of following the link.
function navAction(name, title, action) {
  const node = icon(name);
  node.className = "nav-act";
  node.title = title;
  node.setAttribute("role", "button");
  node.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); action(); });
  return node;
}

async function deleteConfig(name) {
  if (!confirm(`Delete the settings “${name}”? The model files in /data are not touched.`)) return;
  try {
    await api(`/api/models/${name}/delete`, {});
    await refreshModels();
    if (location.hash === `#/configs/${name}` || location.hash.startsWith(`#/configs/${name}/`)) go("#/configs");
    else { renderShell(); if (view && view.update) view.update(); }
  } catch (e) { alert(e.message); }
}

function renderShell() {
  const route = (location.hash.slice(2).split("/")[0]) || "configs";
  document.querySelectorAll(".nav[data-route]").forEach((a) => a.classList.toggle("active", a.dataset.route === route));
  const nav = $("model-nav");
  nav.replaceChildren(...(models ? models.configs : []).map((c) => {
    const st = modelState(c.name);
    const active = location.hash === `#/configs/${c.name}` || location.hash.startsWith(`#/configs/${c.name}/`);
    return el("a", { class: "nav" + (active ? " active" : ""), href: `#/configs/${c.name}`, title: c.name },
      el("span", { class: "dot " + st }), el("span", { class: "name" }, c.name),
      navAction("edit", "Settings", () => go(`#/configs/${c.name}/settings`)),
      navAction("trash", "Delete", () => deleteConfig(c.name)));
  }));
  if (!status) return;
  const m = status.model;
  const st = m.active ? modelState(m.active) : "stopped";
  $("top-dot").className = "dot " + st;
  $("top-state").className = "badge " + st;
  $("top-state").textContent = m.active ? `${m.active} · ${stateText(st)}` : "no model loaded";

  const box = $("banners");
  box.replaceChildren();
  const add = (kind, text) => box.append(el("div", { class: "banner " + kind }, text));
  status.checks.errors.forEach((t) => add("error", t));
  if (status.placeholder.error && !m.active) add("error", status.placeholder.error);
  if (status.gpu_error) add("error", status.gpu_error);
  if (m.error && m.active) add("error", `Strata reports for ${m.active}: ${m.error}`);
  if (status.unload.error) add("error", status.unload.error);
  if (status.unload.result && status.unload.result.state !== "stopped")
    add("error", `Unload did not finish: the model is ${status.unload.result.state}. A new model can be loaded only after it is stopped.`);
}

function setCrumb(...parts) {
  $("crumb").replaceChildren(...parts.flatMap((p, i) => [
    i ? el("span", { class: "rest" }, " / ") : null,
    p.href ? el("a", { href: p.href }, p.text) : el("span", { class: i ? "rest" : "" }, p.text)]).filter((x) => x !== null));
}

// ---- page: model list

function modelsPage() {
  setCrumb({ text: "Model configs" });
  const rows = el("div", { class: "rows" });
  let head = el("div");
  const box = el("section", { class: "card" }, head, rows);
  $("view").replaceChildren(box);

  function update() {
    const list = models ? models.configs : [];
    const ready = list.filter((c) => modelState(c.name) === "ready").length;
    head.replaceWith(head = cardHead({ title: "Model configs", extra: [
      el("span", { class: "count" }, `(${list.length})`),
      ready ? badge("1 ready", "ok") : badge("none loaded"),
      el("div", { class: "grow" }),
      el("button", { disabled: !(status && status.model.active) || (status && status.model.busy), onclick: unloadActive }, icon("stop"), "Unload"),
      el("button", { class: "primary", onclick: () => go("#/new") }, icon("plus"), "New config")] }));
    const ignored = (models && models.invalid_files) || [];
    const ignoredNote = ignored.length ? el("div", { class: "row", style: "cursor:default" }, el("div", { class: "banner warn grow" },
      `Ignored in /config/models: ${ignored.join(", ")}. Model names use lowercase letters, digits, '-' and '_' and start with a letter. Rename the file to use it.`)) : null;
    if (!list.length) {
      rows.replaceChildren(...[el("div", { class: "empty" }, "No model configs yet. Use “New config”."), ignoredNote].filter(Boolean));
      return;
    }
    rows.replaceChildren(...[...list.map((c) => {
      const st = modelState(c.name);
      const prog = st === "loading" ? progressOf(activeLaunch) : null;
      return el("div", { class: "row", onclick: () => go(`#/configs/${c.name}`) },
        el("div", { class: "grow" },
          el("div", { class: "title" }, titleOf(c.config, c.name)),
          el("div", { class: "sub" }, c.error ? el("span", { class: "error-text" }, c.error)
            : [el("span", { class: "mono" }, c.name), ` · ${c.config.context} · ${ramModeLabel(c.config)}`])),
        prog ? el("div", { style: "width:280px;display:grid;gap:4px" },
          el("div", { class: "progress", title: prog.text }, prog.text),
          prog.pct !== null ? meter(prog.pct) : null) : null,
        stateBadge(st),
        el("button", { class: "icon", title: "Open", onclick: (e) => { e.stopPropagation(); go(`#/configs/${c.name}`); } }, icon("open")),
        loadButton(c.name, false));
    }), ignoredNote].filter(Boolean));
  }
  update();
  return { update };
}

function ramModeLabel(cfg) {
  if (!cfg) return "";
  const mode = RAM_MODES.find((x) => x.id === cfg.ram_mode);
  return cfg.ram_mode === "budget" ? `RAM budget ${cfg.ram_budget_gib} GiB` : (mode ? mode.title : cfg.ram_mode);
}

// ---- page: one model

function modelPage(name, tab) {
  const entry = models && models.configs.find((c) => c.name === name);
  setCrumb({ text: "Model configs", href: "#/configs" }, { text: name });
  if (!entry) {
    $("view").replaceChildren(el("section", { class: "card" }, el("div", { class: "empty" }, `There are no settings named ${name}.`)));
    return null;
  }
  tab = tab || "overview";
  let head = el("div");
  const headProgress = el("div", { class: "card-body hidden" });
  const header = el("section", { class: "card" }, head, headProgress);
  const tabs = el("div", { class: "seg" }, ...["overview", "logs", "settings"].map((t) =>
    el("a", { class: "chip" + (t === tab ? " on" : ""), href: `#/configs/${name}/${t}` }, t[0].toUpperCase() + t.slice(1))));
  const body = el("div", { class: "stack" });
  $("view").replaceChildren(el("div", { class: "stack" }, header, tabs, body));

  function updateHeader() {
    const st = modelState(name);
    head.replaceWith(head = cardHead({ title: titleOf(entry.config, name), big: true, extra: [
      el("span", { class: "mono muted" }, name), stateBadge(st), el("div", { class: "grow" }), loadButton(name, true)] }));
    const prog = st === "loading" ? progressOf(activeLaunch) : null;
    headProgress.classList.toggle("hidden", !prog);
    if (prog) headProgress.replaceChildren(el("div", { style: "display:grid;gap:6px" },
      el("div", { class: "small muted" }, prog.text), prog.pct !== null ? meter(prog.pct) : null));
  }
  updateHeader();
  const inner = tab === "logs" ? modelLogsTab(body, name) : tab === "settings" ? settingsForm(body, name, entry) : overviewTab(body, name, entry);
  return {
    update() { updateHeader(); if (inner && inner.update) inner.update(); },
    poll() { if (inner && inner.poll) inner.poll(); },
  };
}

function overviewTab(body, name, entry) {
  const cfg = entry.config;
  if (!cfg) {
    body.append(el("div", { class: "banner error" }, entry.error), el("p", {}, "Fix the file by hand or open ", el("a", { href: `#/configs/${name}/settings` }, "Settings"), "."));
    return null;
  }
  const roleText = () => {
    if (!cfg.gpu_roles) return "Strata's choice";
    return cfg.gpu_roles.split(",").map((part) => {
      const [i, r] = part.split("=");
      const g = status && status.gpus.find((x) => String(x.index) === i);
      return `${r}: card ${i}${g ? " (" + g.name + ")" : ""}`;
    }).join(", ");
  };
  const mono = (t) => el("span", { class: "mono" }, t);
  const settings = facts([
    ["Model", titleOf(cfg, name)], ["Version", cfg.model ? mono([cfg.family, cfg.model].filter(Boolean).join(" ")) : "—"],
    ["Own model files", cfg.model_path ? mono(cfg.model_path) : "—"], ["Context", mono(cfg.context)],
    ["KV format", mono(cfg.kv)], ["Pictures", cfg.vision], ["Draft layer", cfg.mtp || "Strata's choice"],
    ["Parallel requests", cfg.parallel || "fit to the cards"], ["Cards", roleText()],
    ["RAM mode", ramModeLabel(cfg)],
    ["VRAM reserve", reserveText(cfg)], ["Advanced", cfg.extra_set.length ? mono(cfg.extra_set.join(" ")) : "—"],
  ]);
  const notes = cfg.notes ? el("p", { class: "muted", style: "white-space:pre-wrap;margin:0" }, cfg.notes) : null;
  const memBody = el("div", { style: "display:grid;gap:12px" });
  const memHint = el("span", { class: "hint" });
  let measured = null;
  const checkResult = el("div", { style: "display:grid;gap:8px" });
  const checkBtn = el("button", { onclick: runCheck }, "Check download");
  body.append(
    card({ title: "Settings", extra: [el("div", { class: "grow" }), el("a", { href: `#/configs/${name}/settings`, class: "small" }, "Edit")] }, settings, notes),
    card({ title: "Memory", extra: [memHint] }, memBody),
    card({ title: "Download and warnings", extra: [el("div", { class: "grow" }), checkBtn] },
      el("p", { class: "hint", style: "margin:0" }, "Asks Strata what loading this model would need: how much is still to download, and warnings such as a tight fit or a context beyond the trained length. Nothing is downloaded or started."),
      checkResult));

  function renderMemory() {
    const est = memoryEstimate(cfg);
    const live = status && status.model.active === name;
    const m = measured || {};
    const mGib = (x) => (typeof x === "number" ? gib(x) : "—");
    memHint.textContent = !measured ? "not loaded yet" : live ? "measured: running now" : `measured: last run, ${m.started || "?"}`;
    const measuredTotal = typeof m.ram_experts_gib === "number"
      ? m.ram_experts_gib + (m.conversation_gib || 0) + EST.baseGib : null;
    const rows = [
      ["Experts on the cards", est ? `≈ ${gib(est.onCards)}` : "—", mGib(m.vram_experts_gib)],
      ["Experts in RAM", est ? `≈ ${gib(est.ramExperts[cfg.ram_mode])}` : "—", mGib(m.ram_experts_gib)],
      ["Conversation cache", `up to ${gib(EST.conversationGib)}`, mGib(m.conversation_gib)],
      ["Engine itself", `≈ ${gib(EST.baseGib)}`, "≈ " + gib(EST.baseGib)],
      ["RAM in total", est ? `≈ ${gib(est.total(cfg.ram_mode))}` : "unknown", measuredTotal !== null ? "≈ " + gib(measuredTotal) : "—"],
    ];
    const table = el("div", { style: "display:grid;grid-template-columns:max-content 1fr 1fr;gap:7px 24px" },
      el("span"), el("b", { class: "small" }, "Estimate"), el("b", { class: "small" }, "Strata reported"),
      ...rows.flatMap(([k, a, b], i) => [el("span", { class: i === rows.length - 1 ? "" : "muted" }, k),
        el("span", { class: "mono" }, a), el("span", { class: "mono" }, b)]));
    const usage = status && status.container_memory;
    const now = live && usage && usage.anon !== undefined ? el("div", { class: "seg", style: "gap:6px;align-items:center" }, "Container now:",
      badge(`${gib((usage.anon + usage.shmem) / GIB)} held`, "plain"), badge(`${gib(usage.file_cache / GIB)} file cache`, "plain")) : null;
    memBody.replaceChildren(...[table, now].filter(Boolean),
      el("p", { class: "hint", style: "margin:0" }, "The estimate follows Strata's rules with sizes measured on one card; the real values come from the engine log after a load. "
        + "The file cache (model files read at load) is not counted: the system frees it when RAM is needed."));
  }
  async function loadMeasured() {
    try { measured = (await api(`/api/logs/${name}`)).memory; } catch (e) { /* keep the last */ }
    renderMemory();
  }
  renderMemory();
  loadMeasured();

  async function runCheck() {
    checkBtn.disabled = true;
    checkResult.replaceChildren(el("span", { class: "muted" }, "Asking Strata…"));
    try { renderPlan(checkResult, await api(`/api/models/${name}/plan`, {})); }
    catch (e) { checkResult.replaceChildren(el("div", { class: "banner error" }, e.message)); }
    finally { checkBtn.disabled = false; }
  }
  // While the model runs, read the engine's figures every 5th poll (about 10 s); once more right after it stops.
  let wasLive = false, polls = 0;
  return {
    poll() {
      const live = Boolean(status && status.model.active === name && status.model.state === "ready");
      if ((live && polls++ % 5 === 0) || (live && !wasLive) || (!live && wasLive)) loadMeasured(); else renderMemory();
      wasLive = live;
    },
  };
}

/** Strata's answer to `--plan --output json`: {"schema_version", "ok", "plan": {...}}, a refusal is {"ok": false, "error": {...}}. */
function renderPlan(box, r) {
  box.replaceChildren();
  if (!r.plan) { box.append(el("div", { class: "banner error" }, r.text || `strata launch --plan ended with exit code ${r.exit_code}.`)); return; }
  if (r.plan.ok === false && r.plan.error) { box.append(el("div", { class: "banner error" }, r.plan.error.message || JSON.stringify(r.plan.error))); return; }
  const p = r.plan.plan || {};
  const warnings = p.warnings || [];
  warnings.forEach((w) => box.append(el("div", { class: "banner warn" }, w)));
  if (!warnings.length) box.append(el("div", { class: "banner note" }, "No warnings."));
  const rows = [];
  if (p.model && p.model.title) rows.push(["Model", p.model.title]);
  if (p.download) rows.push(["Download", p.download.text || (p.download.gb != null ? p.download.gb + " GB" : "unknown")]);
  if (p.drafting) rows.push(["Draft layer", String(p.drafting)]);
  if (p.speed) rows.push(["Speed", String(p.speed)]);
  if (p.flags) rows.push(["Strata settings", el("span", { class: "mono" }, String(p.flags))]);
  box.append(facts(rows));
}

function reserveText(cfg) {
  const parts = [];
  if (cfg.vram_reserve_mib !== null && cfg.vram_reserve_mib !== undefined) parts.push(`first card ${cfg.vram_reserve_mib} MiB`);
  if (cfg.vram_reserve_later_mib !== null && cfg.vram_reserve_later_mib !== undefined) parts.push(`later cards ${cfg.vram_reserve_later_mib} MiB`);
  return parts.length ? parts.join(", ") : "Strata's default";
}

// ---- log panels (model page and Logs page)

function logPanel(title, hintNode) {
  const pre = el("pre");
  const node = el("section", { class: "card logpanel" }, cardHead({ title, extra: [hintNode] }), pre);
  return { node, pre };
}

function logSwitcher(container, grid, options, panels, start) {
  let mode = start;
  function layout() {
    container.replaceChildren(chips(options, mode, (id) => { mode = id; layout(); }));
    const shown = mode === "both" ? [panels.launch, panels.engine] : [panels[mode]];
    grid.className = "logs" + (shown.length === 2 ? " two" : "");
    grid.replaceChildren(...shown.map((p) => p.node));
  }
  layout();
}

function modelLogsTab(body, name) {
  const switcher = el("div");
  const engineNote = el("span", { class: "hint" });
  const panels = { launch: logPanel("Launch output", el("span", { class: "hint" }, "this container run")), engine: logPanel("Engine log", engineNote) };
  const grid = el("div", { class: "logs" });
  body.append(switcher, grid);
  logSwitcher(switcher, grid, [["both", "Both"], ["launch", "Launch"], ["engine", "Engine"]], panels, "both");
  async function poll() {
    try {
      const r = await api(`/api/logs/${name}`);
      setPre(panels.launch.pre, r.launch, "No launch of this model since the container started.");
      setPre(panels.engine.pre, r.engine, "No engine log for this model yet.");
      engineNote.textContent = r.active ? "live"
        : r.engine_mtime ? `from the last run, ${new Date(r.engine_mtime * 1000).toLocaleString()}` : "";
    } catch (e) { setPre(panels.launch.pre, [], e.message); }
  }
  poll();
  return { poll };
}

// ---- settings form (edit and new), DESIGN.md sections 1 to 4

function settingsForm(body, name, entry) {
  const cfg = { ...DEFAULTS, ...((entry && entry.config) || {}) };
  if (!entry && status && status.gpus.length) cfg.gpu_roles = defaultRoles();
  const cat = models ? models.catalog : { rows: [], contexts: [], error: null };
  const limits = (status && status.limits) || {};
  let source = cfg.model_path ? "gguf" : "prepared";
  let roles = parseRoles(cfg.gpu_roles);
  let autoName = "";  // the last suggested name; replaced on the next pick as long as nobody typed over it

  const input = (attrs) => el("input", { autocomplete: "off", ...attrs });
  const select = (fname, value, options) => el("select", { name: fname },
    ...options.map(([v, t]) => el("option", { value: v, selected: v === value }, t)));

  // 1. Model
  const ctx = cat.contexts.length ? cat.contexts.map((t) => (t % 1024 === 0 ? t / 1024 + "k" : String(t))) : ["32k", "64k", "128k", "256k"];
  const lists = [el("datalist", { id: "dl-contexts" }, ...ctx.map((x) => el("option", { value: x })))];
  const nameInput = input({ name: "config_name", class: "mono", required: true, pattern: NAME_PATTERN.source.slice(1, -1), maxlength: 48, value: name || "", readonly: Boolean(entry) });
  // Family and version are Strata's own names (`--family`, `--model`): lists from Strata's catalog, text fields only
  // when the catalog could not be read. A value the catalog does not know (an older settings file) stays selectable.
  const useLists = cat.rows.length > 0;
  const familyInput = useLists ? el("select", { name: "family", class: "mono" }) : input({ name: "family", class: "mono", placeholder: "e.g. qwen", value: cfg.family });
  const modelInput = useLists ? el("select", { name: "model", class: "mono" }) : input({ name: "model", class: "mono", placeholder: "e.g. Q2_0", value: cfg.model });
  // The folder of own GGUF files: a list of the folders in /models, like the other lists.
  const pathInput = el("select", { name: "model_path", class: "mono" });
  const sourceTabs = el("div");
  const chosen = el("div", { class: "chosen" });
  const picker = el("div", { class: "picker hidden" });
  let ownModels = null;       // /api/own-models
  const folderField = field("Folder", pathInput, "a folder in /models with the .gguf files of one model");
  const versionExplain = el("i");
  const modelFields = el("div", { class: "fields" },
    field("Name", nameInput, entry ? "fixed once saved" : "lowercase, used for Strata's preset"),
    folderField,
    field("Family", familyInput, "Strata's model family; empty only when the version is unique"),
    el("label", { class: "f" }, el("b", {}, "Version"), modelInput, versionExplain));
  const preparedBox = el("div", { style: "display:grid;gap:12px" }, chosen, picker);
  const section1 = card({ num: 1, title: "Model", hint: "what to run" }, sourceTabs, preparedBox, modelFields,
    cat.error ? el("div", { class: "banner note" }, cat.error) : null);

  // 2. Run
  const ctxExplain = el("i", {}, "e.g. 32k, 128k or tokens");
  const section2 = card({ num: 2, title: "Run", hint: "context and engine" }, el("div", { class: "fields" },
    field("Context", input({ name: "context", class: "mono", list: "dl-contexts", value: cfg.context }), ctxExplain),
    field("KV format", select("kv", cfg.kv, [["int8", "int8 (default)"], ["k8v4", "k8v4 (23 % less than int8)"], ["q4_0", "q4_0 (about half of int8)"], ["fp16", "fp16 (twice int8)"]])),
    field("Pictures (vision)", select("vision", cfg.vision, [["off", "off"], ["on", "on"], ["gpu", "on, reader on the card"], ["cpu", "on, reader on the CPU"]])),
    field("Draft layer (MTP)", select("mtp", cfg.mtp, [["", "Strata's choice"], ["on", "on"], ["off", "off (saves about 1 GB VRAM, slower)"]])),
    field("Parallel requests", input({ name: "parallel", class: "mono", type: "number", min: 0, max: 8, value: cfg.parallel }), "0 = fit to the cards")));

  // 3. Cards
  const quick = el("div");
  const gpuTiles = el("div", { class: "tiles" });
  const section3 = card({ num: 3, title: "Cards", hint: "which GPU does what" }, quick, gpuTiles,
    el("div", { class: "hint" }, status && status.gpus.length
      ? "Exactly one card is main. The numbers are the ones --gpu-roles expects; engine logs (CUDA0, CUDA1) and plan texts (GPU 1, GPU 2) count differently."
      : "No cards found. Strata chooses."));

  // 4. Memory
  const reserveFirst = el("b", {}, "First card");
  const reserveLater = el("b", {}, "Later cards");
  const ramTiles = el("div", { class: "tiles two" });
  const budgetMax = limits.ram_bytes ? Math.floor((limits.ram_bytes + (limits.swap_bytes || 0)) / GIB) : 4096;
  const budgetDefault = cfg.ram_budget_gib || (limits.ram_bytes ? Math.max(1, Math.floor(limits.ram_bytes / GIB)) : 28);
  const budgetInput = input({ type: "number", name: "ram_budget_gib", class: "budget", min: 1, max: budgetMax, value: budgetDefault });
  const section4 = card({ num: 4, title: "Memory", hint: "VRAM reserve and RAM" },
    el("div", { class: "fields two" },
      el("label", { class: "f" }, reserveFirst, input({ name: "vram_reserve_mib", class: "mono", type: "number", min: 0, max: 262144, placeholder: "MiB", value: cfg.vram_reserve_mib ?? "" }),
        el("i", {}, "first card in Strata's order · empty = Strata's default")),
      el("label", { class: "f" }, reserveLater, input({ name: "vram_reserve_later_mib", class: "mono", type: "number", min: 0, max: 262144, placeholder: "MiB", value: cfg.vram_reserve_later_mib ?? "" }),
        el("i", {}, "later cards · empty = Strata's default"))),
    ramTiles,
    el("div", { class: "hint" }, "Estimates include the conversation cache (up to 8 GiB) and the engine itself, not the file cache the system frees when needed. After a load, the model page shows what Strata reported."));

  // 5. Advanced (collapsed)
  const advanced = el("details", { class: "card", open: Boolean(cfg.extra_set.length || cfg.notes) },
    el("summary", { class: "card-head" }, el("span", { class: "caret" }, icon("caret")), el("h2", {}, "Advanced"),
      el("span", { class: "hint" }, "engine settings (--set) and notes")),
    el("div", { class: "card-body" },
      field("Engine settings", el("textarea", { name: "extra_set", class: "mono", placeholder: "prefill=65536" }, cfg.extra_set.join("\n")), "one KEY=VALUE per line, passed as --set"),
      field("Notes", el("textarea", { name: "notes" }, cfg.notes))));

  const planBox = el("div", { style: "display:grid;gap:8px" });
  const planCard = el("section", { class: "card hidden" }, cardHead({ title: "Plan", hint: "what loading these settings would need" }), el("div", { class: "card-body" }, planBox));
  const err = el("span", { class: "error-text" });
  const planBtn = el("button", { type: "button", class: "big", onclick: plan }, "Plan");
  const form = el("form", { autocomplete: "off", novalidate: true },
    entry && status && status.model.active === name ? el("div", { class: "banner note" }, "This model is loaded. Saved changes apply at its next load.") : null,
    section1, section2, section3, section4, advanced, planCard,
    el("div", { class: "actionbar" }, el("button", { type: "submit", class: "primary big" }, "Save"), planBtn, err,
      el("span", { class: "hint" }, entry ? "Saved changes apply at the next load." : "Nothing is loaded until you press Load on the model page.")),
    ...lists);
  body.append(form);
  if (entry) {
    body.append(el("div", { class: "danger-zone" }, el("div", { class: "grow" }, el("strong", {}, "Delete these settings"),
      el("div", { class: "hint" }, "Removes only the settings file. The model files in /data stay.")),
      el("button", { class: "danger", disabled: status && status.model.active === name, onclick: remove }, "Delete")));
  }
  const f = form.elements;

  // ---- checks while typing: a field turns red as soon as its value is wrong, and back when it is right.
  // The rules mirror model_config.py, so most mistakes show before Save. Hovering a red field tells why.
  const CONTEXT_RE = /^(\d{1,8}|\d{1,5}[kKmM])$/;
  const SET_RE = /^[A-Za-z0-9_][A-Za-z0-9_.-]*(=[^\s\x00-\x1f\x7f]*)?$/;
  const MANAGED = new Set(["resident-experts", "resident-budget-gib", "mmap-experts", "vram-reserve-mib", "vram-reserve-later-mib",
    "host", "port", "parallel", "gpu", "gpu-roles", "api-key", "vision", "mtp", "kv", "max-context"]);
  const whole = (v, lo, hi) => /^\d+$/.test(v) && Number(v) >= lo && Number(v) <= hi;
  const rules = new Map([
    [nameInput, (v, saving) => !v ? (saving ? "Give the settings a name." : "")
      : NAME_PATTERN.test(v) ? "" : "Only lowercase letters, digits, '-' and '_', starting with a letter (no capitals, no spaces)."],
    [pathInput, (v, saving) => saving && source === "gguf" && !v ? "Choose the folder with your GGUF files." : ""],
    [modelInput, (v, saving) => saving && !v ? (source === "gguf" ? "Choose which of Strata's versions these files are." : "Choose a model.") : ""],
    [f.context, (v) => CONTEXT_RE.test(v) ? "" : "A context size such as 32k, 128k, 1m or a number of tokens."],
    [f.parallel, (v) => v === "" || whole(v, 0, 8) ? "" : "0 (fit to the cards) or 1 to 8."],
    [f.vram_reserve_mib, (v) => v === "" || whole(v, 0, 262144) ? "" : "Empty or a whole number of MiB, 0 to 262144."],
    [f.vram_reserve_later_mib, (v) => v === "" || whole(v, 0, 262144) ? "" : "Empty or a whole number of MiB, 0 to 262144."],
    [budgetInput, (v) => whole(v, 1, budgetMax) ? "" : `1 to ${budgetMax} GiB, the RAM plus swap this container has.`],
    [f.extra_set, (v) => {
      const lines = v.split("\n").map((x) => x.trim()).filter(Boolean);
      if (lines.length > 32) return "At most 32 lines.";
      for (const line of lines) {
        if (!SET_RE.test(line)) return `${line}: not KEY=VALUE (no spaces).`;
        const key = line.split("=")[0].replace(/^-+/, "").toLowerCase().replace(/_/g, "-");
        if (MANAGED.has(key)) return `${key} is set by the form, not here.`;
      }
      return "";
    }],
    [f.notes, (v) => v.length > 2000 ? "At most 2000 characters." : ""],
  ]);

  /** Check one field; marks it red with the reason as tooltip, or clears the mark. Returns the reason or "". */
  function check(node, saving = false) {
    const rule = rules.get(node);
    const why = rule ? rule(node.value.trim(), saving) : "";
    markField(node, why);
    return why;
  }

  function markField(node, why) {
    node.classList.toggle("invalid", Boolean(why));
    if (why) node.title = why; else node.removeAttribute("title");
  }

  /** The field a message from the server is about, so it can be marked as well. */
  function fieldOfMessage(text) {
    const t = text.toLowerCase();
    const map = [[/context size/, f.context], [/is not a version|set a version/, modelInput], [/model family/, familyInput],
      [/model folder/, pathInput], [/^parallel/, f.parallel], [/ram budget/, budgetInput], [/^vram_reserve_later_mib/, f.vram_reserve_later_mib],
      [/^vram_reserve_mib/, f.vram_reserve_mib], [/key=value|advanced|set by the form|extra_set/, f.extra_set], [/^notes/, f.notes],
      [/card role|one card the role main|two roles/, gpuTiles], [/^kv /, f.kv], [/^vision /, f.vision], [/^mtp /, f.mtp]];
    const hit = map.find(([re]) => re.test(t));
    return hit ? hit[1] : null;
  }

  function showError(e) {
    err.textContent = e.message;
    const node = e.field || fieldOfMessage(e.message);
    if (!node) return;
    markField(node, e.message);
    if (node.closest("details")) node.closest("details").open = true;
    node.scrollIntoView({ behavior: "smooth", block: "center" });
    if (node.focus) node.focus({ preventScroll: true });
  }

  for (const node of rules.keys()) node.addEventListener("input", () => check(node));
  for (const node of [pathInput, modelInput]) node.addEventListener("change", () => check(node));

  // ---- section 1 behaviour

  function renderSource() {
    sourceTabs.replaceChildren(chips([["prepared", "Prepared Strata model"], ["gguf", "GGUF file"]], source, (id) => { source = id; renderSource(); }));
    preparedBox.classList.toggle("hidden", source !== "prepared");
    folderField.classList.toggle("hidden", source !== "gguf");
    versionExplain.textContent = source === "gguf"
      ? "which of Strata's versions these files are; Strata needs it for its prepared data"
      : "Strata's name for the version, e.g. Q2_0, IQ3_S, Gyro-S";
    renderChosen();
    if (source === "gguf" && !ownModels) loadOwnModels();
  }

  // ---- family and version lists

  function fillFamilies(selected) {
    if (!useLists) return;
    const fams = new Map();
    for (const r of cat.rows) if (r.family && !fams.has(r.family)) fams.set(r.family, r.family_title || r.family);
    const opts = [["", "— Strata's choice —"], ...[...fams].map(([id, title]) => [id, `${id} · ${title}`])];
    if (selected && !fams.has(selected)) opts.push([selected, `${selected} (not in Strata's list)`]);
    familyInput.replaceChildren(...opts.map(([v, t]) => el("option", { value: v, selected: v === selected }, t)));
  }

  function fillVersions(selected) {
    if (!useLists) return;
    const fam = familyInput.value;
    const seen = new Set();
    const rows = cat.rows.filter((r) => (!fam || r.family === fam) && !seen.has(r.model) && seen.add(r.model));
    const opts = [["", "— choose —"], ...rows.map((r) => {
      const label = VERSION_LABELS[r.model];
      return [r.model, label && label !== r.model ? `${r.model} · ${label}` : r.model];
    })];
    if (selected && !rows.some((r) => r.model === selected)) opts.push([selected, `${selected} (not in Strata's list${fam ? " for " + fam : ""})`]);
    modelInput.replaceChildren(...opts.map(([v, t]) => el("option", { value: v, selected: v === selected }, t)));
  }

  function setModel(family, model) {
    if (useLists) { fillFamilies(family); familyInput.value = family; fillVersions(model); }
    else familyInput.value = family;
    modelInput.value = model;
  }

  // ---- own GGUF folders under /models (DESIGN.md, section 1: chosen from a list, no free path)

  async function loadOwnModels() {
    fillFolders("loading");
    try { ownModels = await api("/api/own-models"); } catch (e) { ownModels = { error: e.message, folders: [] }; }
    fillFolders();
    updateContextInfo();
  }

  /**
   * The longest context the model offers, shown under the Context field. The model file's header is the truth
   * (own GGUF folders, and Strata models already downloaded); for a model not downloaded yet, Strata's catalog.
   */
  function contextLimit() {
    if (source === "gguf") {
      const folder = ((ownModels && ownModels.folders) || []).find((fo) => fo.path === pathInput.value);
      const n = folder && folder.header && folder.header.context_length;
      return typeof n === "number" ? { tokens: n, from: "file" } : null;
    }
    const row = catalogRow({ model: modelInput.value.trim(), family: familyInput.value.trim() });
    if (row && typeof row.trained_context === "number") return { tokens: row.trained_context, from: "file" };
    if (row && typeof row.max_context === "number") return { tokens: row.max_context, from: "catalog" };
    return null;
  }

  function updateContextInfo() {
    const limit = contextLimit();
    const ctxField = f.context;
    const short = (n) => (n % 1048576 === 0 ? `${n / 1048576}M` : n % 1024 === 0 ? `${n / 1024}K` : String(n));
    if (!limit) ctxExplain.textContent = "e.g. 32k, 128k or tokens";
    else if (limit.from === "file") ctxExplain.textContent = `the model was trained for ${short(limit.tokens)} (${limit.tokens.toLocaleString("en")} tokens), says its file`;
    else ctxExplain.textContent = `Strata offers up to ${short(limit.tokens)} for this model (not downloaded yet)`;
    // More than that is allowed, Strata only warns: yellow, not red.
    const over = limit && !ctxField.classList.contains("invalid") && contextTokens(ctxField.value) > limit.tokens;
    ctxField.classList.toggle("warn-field", Boolean(over));
    if (over) ctxField.title = limit.from === "file"
      ? "More than the model was trained for: allowed, but answers can get less reliable."
      : "More than Strata offers for this model.";
    else if (!ctxField.classList.contains("invalid")) ctxField.removeAttribute("title");
  }

  /** The folder list. A folder that cannot be used stays visible but cannot be chosen; the reason is in its text. */
  function fillFolders(state) {
    const current = pathInput.value || cfg.model_path || "";
    const om = ownModels || { folders: [] };
    const opts = [];
    if (state === "loading") {
      opts.push(current ? [current, current, false] : ["", "loading…", true]);
      pathInput.replaceChildren(...opts.map(([v, t, off]) => el("option", { value: v, disabled: off, selected: true }, t)));
      return;
    }
    if (om.error) opts.push(["", `— ${om.error} —`, true]);
    else if (!om.exists) opts.push(["", "— nothing is mounted at /models —", true]);
    else if (!om.folders.length && !(om.broken || []).length) opts.push(["", "— no folders in /models —", true]);
    else opts.push(["", "— choose —", false]);
    for (const fo of om.folders || []) {
      const problem = fo.error ? "no usable .gguf files" : fo.missing.length ? `shards missing: ${fo.missing.join(", ")}` : "";
      opts.push([fo.path, problem ? `${fo.name} (${problem})` : fo.name, Boolean(problem)]);
    }
    for (const b of om.broken || []) opts.push(["", `${b.name} (a link that leads nowhere in the container)`, true]);
    for (const name of om.loose_files || []) opts.push(["", `${name} (a file, not a folder)`, true]);
    if (current && !(om.folders || []).some((fo) => fo.path === current)) opts.push([current, `${current} (not in /models)`, false]);
    pathInput.replaceChildren(...opts.map(([v, t, off]) => el("option", { value: v, disabled: off, selected: v === current && v !== "" }, t)));
  }

  /** Strata's family for a GGUF header: its name and repository named against the catalog. null when not clear. */
  function familyFromHeader(h) {
    if (!h) return null;
    const text = [h.name, h.repo_url, h.basename, h.finetune].filter(Boolean).join(" ").toLowerCase();
    const families = new Set(cat.rows.map((r) => r.family));
    const words = [["coder", "coder"], ["swift", "swift"], ["unsloth", "unsloth"], ["gyro", "gyro"], ["nvfp4", "nvfp4"], ["deepseek", "deepseek41"]];
    const hits = words.filter(([w, fam]) => families.has(fam) && text.includes(w)).map(([, fam]) => fam);
    if (hits.length === 1) return hits[0];
    if (!hits.length && families.has("qwen") && /qwen3[._-]?8|flash.next|gsq-rco/.test(text)) return "qwen";
    return null;
  }

  function versionFromHeader(family, h) {
    const rows = cat.rows.filter((r) => r.family === family);
    if (rows.length === 1) return rows[0].model;
    const ft = h && h.file_type_name ? h.file_type_name.toLowerCase() : "";
    const match = rows.find((r) => (r.model || "").toLowerCase() === ft);
    return match ? match.model : "";
  }

  /** A folder was chosen: family and version from its header when that is clear. */
  function pickFolder() {
    const folder = ((ownModels && ownModels.folders) || []).find((fo) => fo.path === pathInput.value);
    if (!folder) return;
    const h = folder.header || {};
    const fam = familyFromHeader(h);
    if (fam) {
      const version = versionFromHeader(fam, h);
      setModel(fam, version || (familyInput.value === fam ? modelInput.value : ""));
    }
    if (!entry && (!nameInput.value || nameInput.value === autoName)) {
      const row = catalogRow({ model: modelInput.value, family: familyInput.value });
      autoName = row ? suggestName(row, f.context.value.trim()) : folder.name.toLowerCase().replace(/[^a-z0-9_-]+/g, "_").replace(/^[^a-z]+/, "");
      if (!NAME_PATTERN.test(autoName)) autoName = "";
      nameInput.value = autoName;
    }
    renderChosen();
    updateEstimates();
  }

  function renderChosen() {
    updateContextInfo();
    const row = catalogRow({ model: modelInput.value.trim(), family: familyInput.value.trim() });
    const toggle = el("button", { type: "button", onclick: () => { picker.classList.toggle("hidden"); renderPicker(); } },
      picker.classList.contains("hidden") ? (row ? "Change" : "Choose a model") : "Close list");
    if (!cat.rows.length) { chosen.replaceChildren(el("span", { class: "hint" }, "Strata's model list is not available. Type family and version below.")); return; }
    if (!row) {
      chosen.replaceChildren(...[toggle, modelInput.value.trim() ? el("span", { class: "hint" }, "Not in Strata's list. Check family and version below.") : null].filter(Boolean));
      return;
    }
    chosen.replaceChildren(toggle, el("b", {}, `${groupOf(row).title} · ${versionLabel(row)}`), el("span", { class: "mono muted" }, `${row.family} ${row.model}`),
      ...locationInfo(row));
  }

  function locationInfo(row) {
    if (row.installed) return [badge("on disk", "ok"), el("span", { class: "hint" }, "in ", el("span", { class: "mono" }, STRATA_MODELS_DIR))];
    const gb = typeof row.download_gb === "number" ? `${row.download_gb.toFixed(1)} GB` : "size unknown";
    return [badge("download", "acc"), el("span", { class: "hint" }, `downloaded at the first load, ${gb}`)];
  }

  function renderPicker() {
    if (picker.classList.contains("hidden")) { renderChosen(); return; }
    const current = catalogRow({ model: modelInput.value.trim(), family: familyInput.value.trim() });
    const settings = formConfig();
    const fitBadge = { ok: "ok", slow: "warn", no: "bad" };
    const legend = status && status.limits && status.limits.ram_bytes ? el("div", { class: "legend" },
      badge("fits", "ok"), "on the cards and in RAM", badge("SSD reads", "warn"), "only with RAM budget or from the model files, slower",
      badge("too big", "bad"), "for this container", el("span", { class: "hint" }, "· with the cards and context chosen below")) : null;
    picker.replaceChildren(...[legend].filter(Boolean), ...catalogGroups().flatMap((g) => [
      el("div", { class: "group" }, g.title),
      ...g.rows.map((row) => [row, fitOf({ ...settings, model: row.model, family: row.family || "" })]).map(([row, fit]) => el("div", {
          class: "item" + (row === current ? " on" : "") + (fit ? " fit-" + fit.level : ""), title: fit ? fit.text : null, onclick: () => pick(row) },
        el("div", { class: "grow" }, el("div", {}, el("b", {}, versionLabel(row)), " ", el("span", { class: "mono muted small" }, `${row.family} ${row.model}`)),
          row.about ? el("div", { class: "about" }, row.about) : null),
        row.experimental ? badge("experimental", "warn") : null,
        row.images ? badge("pictures") : null,
        typeof row.download_gb === "number" ? badge(`${row.download_gb.toFixed(1)} GB`, "plain") : null,
        row.installed ? badge("on disk", "ok") : null,
        fit ? badge(fit.label, fitBadge[fit.level]) : null)),
    ]));
    renderChosen();
  }

  function pick(row) {
    setModel(row.family || "", row.model);
    if (!entry && (!nameInput.value || nameInput.value === autoName)) {
      autoName = suggestName(row, f.context.value.trim());
      nameInput.value = autoName;
    }
    picker.classList.add("hidden");
    renderChosen();
    updateEstimates();
  }

  // ---- section 3 behaviour

  function renderCards() {
    const gpus = status ? status.gpus : [];
    const current = rolesText();
    const options = gpus.map((g) => [`only-${g.index}`, `Only ${g.index}`, g.name]);
    if (gpus.length > 1) options.push(["split", "Split over all cards"]);
    const onlyText = (i) => gpus.map((x) => `${x.index}=${x.index === i ? "main" : "off"}`).join(",");
    const chosenQuick = gpus.length > 1 && current === defaultRoles() ? "split"
      : (gpus.find((g) => current === onlyText(g.index)) ? `only-${gpus.find((g) => current === onlyText(g.index)).index}` : "");
    quick.replaceChildren(gpus.length ? chips(options, chosenQuick, (id) => {
      roles = parseRoles(id === "split" ? defaultRoles() : onlyText(Number(id.slice(5))));
      renderCards();
    }) : el("span"));
    gpuTiles.classList.remove("invalid");
    gpuTiles.replaceChildren(...gpus.map((g) => {
      const role = roles[g.index] || "off";
      return el("div", { class: "tile" + (role !== "off" ? " on" : "") },
        el("div", { class: "h" }, el("span", { class: "idx" }, String(g.index)), el("span", {}, g.name)),
        el("div", { class: "hint" }, g.vram_total_mib ? `${(g.vram_total_mib / 1024).toFixed(0)} GiB VRAM` : "VRAM unknown"),
        el("div", { class: "roles" }, ...["main", "helper", "off"].map((r) =>
          el("a", { class: "chip" + (r === role ? " on" : ""), role: "button", onclick: () => setRole(g.index, r) }, r))));
    }));
    updateReserveLabels();
    updateEstimates();
  }

  function setRole(index, role) {
    if (role === "main") for (const k of Object.keys(roles)) if (roles[k] === "main") roles[k] = "helper";
    roles[index] = role;
    renderCards();
  }

  function rolesText() {
    const gpus = status ? status.gpus : [];
    return gpus.map((g) => `${g.index}=${roles[g.index] || "off"}`).join(",");
  }

  function updateReserveLabels() {
    const order = engineOrder(rolesText());
    const nm = (g) => `card ${g.index} (${g.name})`;
    reserveFirst.textContent = order.length ? `VRAM reserve, ${nm(order[0])}` : "VRAM reserve, first card";
    reserveLater.textContent = order.length > 1 ? `VRAM reserve, ${order.slice(1).map(nm).join(", ")}` : "VRAM reserve, later cards (none with these roles)";
  }

  // ---- section 4 behaviour

  function renderRam(selected) {
    const avail = limits.ram_bytes ? el("p", { class: "seg", style: "gap:6px;align-items:center" }, "Available:",
      badge(`${Math.floor(limits.ram_bytes / GIB)} GiB RAM`, "plain"), badge(`${Math.floor((limits.swap_bytes || 0) / GIB)} GiB swap`, "plain")) : null;
    if (avail) avail.title = `RAM from the ${limits.ram_from === "container" ? "container limit" : "host"}, swap from the ${limits.swap_from === "container" ? "container limit" : "host"}`;
    ramTiles.replaceChildren(...RAM_MODES.map((m) => {
      const radio = el("input", { type: "radio", name: "ram_mode", value: m.id, checked: m.id === selected, onchange: () => renderRam(m.id) });
      return el("label", { class: "tile" + (m.id === selected ? " on" : "") }, radio,
        el("div", { class: "h" }, el("span", { class: "radio" }), m.title,
          m.id === "budget" ? el("span", { class: "seg", style: "gap:6px;align-items:center" }, budgetInput, "GiB") : null,
          el("span", { class: "grow" }), el("span", { class: "badge", "data-est": m.id }), m.slow ? badge("can be slower", "warn") : null),
        el("p", {}, m.text),
        m.id === "budget" ? avail : null);
    }));
    updateEstimates();
  }

  function updateEstimates() {
    const c = formConfig();
    c.ram_budget_gib = Number(budgetInput.value || 0); // show the budget even while another mode is picked
    const est = ramEstimates(c);
    for (const node of ramTiles.querySelectorAll("[data-est]")) {
      const text = est ? est[node.dataset.est] : (node.dataset.est === "normal" ? "size unknown" : "");
      node.textContent = text;
      node.classList.toggle("hidden", !text);
    }
    if (!picker.classList.contains("hidden")) renderPicker();
  }

  // ---- save, plan, delete

  function formConfig() {
    const num = (v) => (v === "" ? null : Number(v));
    const mode = form.querySelector("input[name=ram_mode]:checked");
    return {
      model: modelInput.value.trim(), family: familyInput.value.trim(), model_path: source === "gguf" ? pathInput.value.trim() : "",
      context: f.context.value.trim(), kv: f.kv.value, vision: f.vision.value, mtp: f.mtp.value,
      parallel: Number(f.parallel.value || 0), gpu_roles: status && status.gpus.length ? rolesText() : "",
      ram_mode: mode ? mode.value : "normal",
      ram_budget_gib: mode && mode.value === "budget" ? Number(budgetInput.value || 0) : 0,
      vram_reserve_mib: num(f.vram_reserve_mib.value), vram_reserve_later_mib: num(f.vram_reserve_later_mib.value),
      extra_set: f.extra_set.value.split("\n").map((x) => x.trim()).filter(Boolean), notes: f.notes.value,
    };
  }

  /** All checks before Save or Plan. The first wrong field is reported with the field, so it can be marked. */
  function checkForm() {
    const c = formConfig();
    for (const node of rules.keys()) {
      if (node === budgetInput && c.ram_mode !== "budget") { markField(node, ""); continue; }
      const why = check(node, true);
      if (why) throw Object.assign(new Error(why), { field: node });
    }
    gpuTiles.classList.remove("invalid");
    return [nameInput.value.trim(), c];
  }

  async function save(event) {
    event.preventDefault();
    try {
      const [target, c] = checkForm();
      if (!entry && models.configs.some((x) => x.name === target)) throw Object.assign(new Error(`Settings named ${target} already exist.`), { field: nameInput });
      await api(`/api/models/${target}`, c);
      err.textContent = "";
      await refreshModels();
      go(`#/configs/${target}`);
    } catch (e) { showError(e); }
  }

  async function plan() {
    planBtn.disabled = true;
    try {
      const [target, c] = checkForm();
      err.textContent = "";
      planCard.classList.remove("hidden");
      planBox.replaceChildren(el("span", { class: "muted" }, "Asking Strata…"));
      renderPlan(planBox, await api(`/api/models/${target}/plan`, { config: c }));
      planCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (e) { showError(e); }
    finally { planBtn.disabled = false; }
  }

  const remove = () => deleteConfig(name);

  form.addEventListener("submit", save);
  nameInput.addEventListener("input", () => { autoName = ""; });
  familyInput.addEventListener("input", () => { if (useLists) fillVersions(modelInput.value); });
  for (const n of [modelInput, familyInput]) n.addEventListener("input", () => { renderChosen(); updateEstimates(); });
  f.context.addEventListener("input", updateContextInfo);
  pathInput.addEventListener("change", pickFolder);
  fillFolders("loading");
  fillFamilies(cfg.family);
  fillVersions(cfg.model);
  // The KV cache per session depends on context, format and parallel requests; the draft layer takes VRAM too.
  for (const n of ["vram_reserve_mib", "vram_reserve_later_mib", "context", "kv", "parallel", "mtp"]) f[n].addEventListener("input", updateEstimates);
  budgetInput.addEventListener("input", updateEstimates);
  renderSource();
  renderRam(cfg.ram_mode);
  renderCards();
  if (!entry && source === "prepared" && !cfg.model && cat.rows.length) { picker.classList.remove("hidden"); renderPicker(); }
  return null;
}

function newModelPage() {
  setCrumb({ text: "Model configs", href: "#/configs" }, { text: "New config" });
  const body = el("div", { class: "stack" });
  $("view").replaceChildren(body);
  settingsForm(body, "", null);
  return null;
}

function parseRoles(text) {
  const roles = {};
  for (const part of (text || "").split(",")) {
    const [i, r] = part.split("=").map((x) => (x || "").trim());
    if (i !== "" && r) roles[i] = r;
  }
  return roles;
}

function defaultRoles() {
  // Without Strata's speed rule: the card with the most VRAM is main, the others helpers (IMPLEMENTATION.md, 6).
  const sorted = [...status.gpus].sort((a, b) => (b.vram_total_mib || 0) - (a.vram_total_mib || 0));
  const roles = Object.fromEntries(sorted.map((g, i) => [g.index, i === 0 ? "main" : "helper"]));
  return status.gpus.map((g) => `${g.index}=${roles[g.index]}`).join(",");
}

function engineOrder(rolesText) {
  // Strata passes the helpers first and the main card last (IMPLEMENTATION.md, 6, "How the desktop app does it").
  const roles = parseRoles(rolesText);
  const byIndex = Object.fromEntries((status ? status.gpus : []).map((g) => [String(g.index), g]));
  const pick = (role) => Object.keys(roles).filter((i) => roles[i] === role).map((i) => byIndex[i]).filter(Boolean);
  return [...pick("helper"), ...pick("main")];
}

function contextTokens(text) {
  const m = /^(\d+)([km]?)$/i.exec(String(text || "").trim());
  if (!m) return 32768;
  return Number(m[1]) * ({ k: 1024, m: 1048576 }[m[2].toLowerCase()] || 1);
}

function sessionGib(cfg) {
  const bytes = KV_BYTES[cfg.kv] || KV_BYTES.int8;
  return (121 + contextTokens(cfg.context) * 13 * bytes * 1.054 / 2 ** 20) / 1024;
}

/** Where the experts of these settings would go, in GiB. null when the version is not in Strata's list. */
function memoryEstimate(cfg) {
  const row = catalogRow(cfg);
  let experts = null;
  if (row && typeof row.experts_gb === "number" && row.experts_gb > 0) experts = row.experts_gb;
  else if (row && typeof row.memory_gb === "number" && row.memory_gb > 1) experts = row.memory_gb - 1;
  if (experts === null) return null;
  const active = status && status.model.active;
  const cards = engineOrder(cfg.gpu_roles);
  let free = 0;
  cards.forEach((g, i) => {
    // Other programs' VRAM: measured now when nothing is loaded, else the last value seen while idle.
    const others = !active ? (g.vram_used_mib || 0) : (idleVramUsed[g.index] || 0);
    const reserve = (i === 0 ? cfg.vram_reserve_mib : cfg.vram_reserve_later_mib) ?? EST.reserveMib;
    free += Math.max(0, ((g.vram_total_mib || 0) - others - reserve) / 1024 - EST.afterCacheGib);
  });
  const sessions = (cfg.parallel || EST.sessions) * sessionGib(cfg);
  const mtp = cfg.mtp === "off" ? 0 : EST.mtpGib;
  const onCards = cards.length ? Math.min(experts, Math.max(0, free - EST.denseGib - sessions - mtp)) : 0;
  const ramExperts = {
    "normal": experts,
    "resident-experts": Math.min(experts, Math.max(0, experts - onCards * (1 - EST.lendShare))),
    "budget": Math.min((cfg.ram_budget_gib || 0), experts),
    "mmap-experts": 0,
  };
  return { experts, onCards, ramExperts, conversation: EST.conversationGib, base: EST.baseGib,
    total: (mode) => ramExperts[mode] + EST.conversationGib + EST.baseGib };
}

const gib = (x) => `${x.toFixed(1)} GiB`;

// Strata page-locks the RAM copy only with this much RAM to spare (AMD_HIP_PERFORMANCE.md: "uses 4 GiB of headroom").
const PIN_HEADROOM_GIB = 4;

/**
 * Does a model fit this container with these settings (cards, context, reserve)? Measured against what the
 * container may use: its memory limit and swap limit, the host's values only when the container has no limit.
 *   ok:   the experts fit on the cards and in RAM ("Only what no card holds"), nothing is read from the SSD
 *   slow: runs only with "RAM budget" or "From the model files", so part of the experts is read from the SSD
 *   no:   the experts that do not fit on the cards are more than the container's RAM and swap together
 * null when the version or the container's RAM is unknown, and for DeepSeek (see below).
 */
function fitOf(cfg) {
  const limits = (status && status.limits) || {};
  if (!limits.ram_bytes) return null;
  // DeepSeek-V4.1-Flash is spread over every card and the SSD by its own plan (strata_control/dsv41_fit.py); its
  // catalog figures do not follow the rules above, so it gets no mark. "Plan" asks Strata instead.
  const row = catalogRow(cfg);
  if (row && (row.multi_file || row.family === "deepseek41")) return null;
  const est = memoryEstimate({ ...cfg, ram_mode: "resident-experts" });
  if (!est) return null;
  const ram = limits.ram_bytes / GIB, swap = (limits.swap_bytes || 0) / GIB;
  const need = est.ramExperts["resident-experts"] + est.base + PIN_HEADROOM_GIB;
  const where = `${limits.ram_from === "container" ? "container limit" : "host, no container limit"}`;
  const text = `About ${gib(need)} RAM needed without SSD reads (experts ${gib(est.ramExperts["resident-experts"])}, engine ${gib(est.base)}, `
    + `${PIN_HEADROOM_GIB} GiB headroom); RAM ${gib(ram)} (${where}), swap ${gib(swap)}.`;
  if (need <= ram) return { level: "ok", label: "fits", text };
  if (est.experts - est.onCards > ram + swap) return { level: "no", label: "too big", text };
  return { level: "slow", label: "SSD reads", text };
}

/** Badge text per RAM mode for the form's tiles. */
function ramEstimates(cfg) {
  const est = memoryEstimate(cfg);
  if (!est) return null;
  const out = {};
  for (const m of RAM_MODES) out[m.id] = m.id === "mmap-experts" ? `≈ ${gib(est.total(m.id))} RAM + file cache` : `≈ ${gib(est.total(m.id))} RAM`;
  return out;
}

// ---- page: models (the files on disk: Strata's downloads and the own GGUF folders)

function modelFilesPage() {
  setCrumb({ text: "Models" });
  const box = el("div", { class: "stack" });
  $("view").replaceChildren(box);
  let data = null;
  let error = null;

  async function load() {
    try { data = await api("/api/model-files"); error = null; } catch (e) { error = e.message; }
    render();
  }

  async function remove(entry, list) {
    const pack = entry.pack ? ` and its prepared data ${entry.pack} (${gib((entry.pack_bytes || 0) / GIB)})` : "";
    const what = entry.kind === "link" ? `the link ${entry.path} (the files it points to stay)` : `${entry.path} (${gib(entry.size_bytes / GIB)})${pack}`;
    const configs = (models ? models.configs : []).filter((c) => c.config && usesEntry(c.config, entry, list)).map((c) => c.name);
    const note = configs.length ? `\n\nUsed by: ${configs.join(", ")}. ${list === "strata" ? "Strata downloads it again at their next load." : "Their next load fails until the folder is back."}` : "";
    if (!confirm(`Delete ${what}?\n\nThis cannot be undone.${note}`)) return;
    try {
      const r = await api("/api/model-files/delete", { path: entry.path });
      await refreshModels();
      await load();
      if (r.freed_bytes) $("banners").append(el("div", { class: "banner note" }, `Deleted ${r.deleted}, ${gib(r.freed_bytes / GIB)} freed.`));
    } catch (e) { alert(e.message); }
  }

  function usesEntry(cfg, entry, list) {
    if (list === "own") return cfg.model_path === entry.path;
    const row = catalogRow(cfg);
    return !cfg.model_path && row && String(row.folder || "").toLowerCase() === entry.name.toLowerCase();
  }

  function rows(entries, list, emptyText) {
    if (!entries.length) return [el("div", { class: "empty" }, emptyText)];
    const busy = status && (status.model.active || status.model.busy || status.model.launch_running);
    const readOnly = list === "own" && data && !data.own_writable;
    return entries.map((e) => {
      const used = (models ? models.configs : []).filter((c) => c.config && usesEntry(c.config, e, list)).map((c) => c.name);
      const blocked = busy ? "Unload the model first." : readOnly ? `${data.own_dir} is mounted read-only.` : "";
      return el("div", { class: "row", style: "cursor:default" },
        el("div", { class: "grow" },
          el("div", { class: "title mono" }, e.name),
          el("div", { class: "sub mono" }, e.path, e.kind === "link" ? ` → ${e.link_target}` : "", e.pack ? ` + ${e.pack}` : "")),
        used.length ? badge(`used by ${used.join(", ")}`, "acc plain") : null,
        e.hard_linked ? badge("hard-linked", "warn") : null,
        e.kind === "link" ? badge("link", "warn") : null,
        badge(gib((e.size_bytes + (e.pack_bytes || 0)) / GIB), "plain"),
        el("button", { class: "icon danger", title: blocked || "Delete", disabled: Boolean(blocked), onclick: () => remove(e, list) }, icon("trash")));
    });
  }

  function render() {
    if (error || !data) { box.replaceChildren(el("section", { class: "card" }, el("div", { class: "empty" }, error || "Loading…"))); return; }
    box.replaceChildren(
      el("section", { class: "card" }, cardHead({ title: "Strata models", hint: data.strata_dir }),
        el("div", { class: "rows" }, ...rows(data.strata, "strata", "Strata has not downloaded a model yet."))),
      el("section", { class: "card" }, cardHead({ title: "Own GGUF files", hint: data.own_dir }),
        el("div", { class: "rows" }, ...rows(data.own, "own", data.own_exists ? "No folders in /models." : "Nothing is mounted at /models."))));
  }

  load();
  return { update: render };
}

// ---- page: logs

function logsPage() {
  setCrumb({ text: "Logs" });
  const switcher = el("div");
  const engineNote = el("span", { class: "hint" });
  const panels = {
    supervisor: logPanel("Supervisor", null),
    launch: logPanel("Launch output", el("span", { class: "hint" }, "last launch")),
    engine: logPanel("Engine log", engineNote),
  };
  const grid = el("div", { class: "logs" });
  $("view").replaceChildren(el("div", { class: "stack" }, switcher, grid));
  logSwitcher(switcher, grid, [["both", "Launch & Engine"], ["launch", "Launch"], ["engine", "Engine"], ["supervisor", "Supervisor"]], panels, "both");
  async function poll() {
    try {
      const r = await api("/api/logs");
      setPre(panels.supervisor.pre, r.supervisor, "Nothing yet.");
      setPre(panels.launch.pre, r.launch, "No launch since the container started.");
      setPre(panels.engine.pre, r.engine, r.model ? "No engine log yet." : "No model is loaded. Each model's last engine log is on its own page.");
      engineNote.textContent = r.model ? r.model : "";
    } catch (e) { setPre(panels.supervisor.pre, [], e.message); }
  }
  poll();
  return { poll };
}

// ---- page: hardware

function hardwarePage() {
  setCrumb({ text: "Hardware" });
  const box = el("div", { class: "stack" });
  $("view").replaceChildren(box);
  function update() {
    if (!status) return;
    const s = status;
    const mem = s.memory;
    const lim = s.limits || {};
    const usage = (used, total) => (total ? meter((100 * used) / total) : null);
    const memTile = (title, badges, text, bar) => el("div", { class: "tile" }, el("div", { class: "h" }, title, el("span", { class: "grow" }), ...badges), el("p", {}, text), bar);
    const memory = [
      memTile("RAM", [badge(mem.total ? fmtGiB(mem.total) : "unknown", "plain")],
        mem.total ? `${fmtGiB(mem.total - mem.available)} used` : "unknown", usage(mem.total - mem.available, mem.total)),
      memTile("Swap", [badge(mem.swap_total ? fmtGiB(mem.swap_total) : "none", "plain")],
        mem.swap_total ? `${fmtGiB(mem.swap_total - mem.swap_free)} used` : "No swap on the host.", usage(mem.swap_total - mem.swap_free, mem.swap_total)),
      memTile("Container limit", [s.checks.memory_limit_bytes ? badge(fmtGiB(s.checks.memory_limit_bytes), "plain") : badge("none")],
        lim.ram_bytes ? `Usable: ${Math.floor(lim.ram_bytes / GIB)} GiB RAM (${lim.ram_from}), ${Math.floor((lim.swap_bytes || 0) / GIB)} GiB swap (${lim.swap_from}).` : "unknown", null),
    ];
    const cards = s.gpus.map((g) => {
      const pct = g.vram_total_mib ? (100 * (g.vram_used_mib || 0)) / g.vram_total_mib : 0;
      return el("div", { class: "tile" },
        el("div", { class: "h" }, el("span", { class: "idx" }, String(g.index)), el("span", {}, g.name), el("span", { class: "grow" }),
          badge(g.vram_total_mib ? `${(g.vram_total_mib / 1024).toFixed(0)} GiB` : "VRAM unknown", "plain")),
        el("p", {}, el("span", { class: "mono" }, g.pci_bus_id || ""), g.vram_total_mib ? ` · ${g.vram_used_mib} / ${g.vram_total_mib} MiB used` : ""),
        meter(pct));
    });
    const checks = [
      ...s.checks.errors.map((t) => el("div", { class: "banner error" }, t)),
      ...s.checks.warnings.map((t) => el("div", { class: "banner warn" }, t)),
      ...s.checks.notes.map((t) => el("div", { class: "banner note" }, t)),
    ];
    const checkBadge = s.checks.errors.length ? badge(`${s.checks.errors.length} failed`, "bad")
      : s.checks.warnings.length ? badge("passed with warnings", "warn") : badge("all passed", "ok");
    box.replaceChildren(
      card({ title: "Start checks", extra: [checkBadge] },
        checks.length ? el("div", { style: "display:grid;gap:8px" }, ...checks) : null,
        facts([
          ["Engine backend", el("span", { class: "mono" }, s.checks.backend || "unknown")],
          ["Ports", el("span", { class: "mono" }, `API ${s.ports.api} · web UI ${s.ports.ui}`)],
          ["API port now", s.placeholder.held ? "placeholder (no model)" : s.model.active ? "Strata" : "free"],
        ])),
      card({ title: "Graphics cards", extra: [badge(`${s.gpus.length} found`, s.gpus.length ? "acc" : "")] },
        cards.length ? el("div", { class: "tiles" }, ...cards) : el("div", { class: "muted" }, "No cards found.")),
      card({ title: "Memory" }, el("div", { class: "tiles" }, ...memory)));
  }
  update();
  return { update };
}

// ---- router and polling

function route() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (parts[0] === "configs" && parts[1]) view = modelPage(decodeURIComponent(parts[1]), parts[2]);
  else if (parts[0] === "models") view = modelFilesPage();
  else if (parts[0] === "new") view = newModelPage();
  else if (parts[0] === "logs") view = logsPage();
  else if (parts[0] === "hardware") view = hardwarePage();
  else view = modelsPage();
  renderShell();
}

async function refreshModels() {
  try { models = await api("/api/models"); } catch (e) { console.error(e); }
}

// Bottom of the sidebar: the Strata base and this web UI (web/api.py, versions()). Fixed per image, so set once.
function renderVersions(v) {
  const box = $("versions");
  if (!v || box.childElementCount) return;
  box.replaceChildren(
    el("span", {}, "Strata version"), el("span", { class: "mono" }, v.strata),
    el("span", {}, "Supervisor version"), el("span", { class: "mono" }, v.supervisor));
  if (v.flavor) box.title = `Strata image flavor: ${v.flavor}`;
}

async function tick() {
  try {
    const before = status ? JSON.stringify(status.model) : "";
    status = await api("/api/status");
    renderVersions(status.versions);
    const m = status.model;
    if (!m.active) idleVramUsed = Object.fromEntries(status.gpus.map((g) => [g.index, g.vram_used_mib || 0]));
    trackDownload();
    activeLaunch = m.active && (m.launch_running || m.busy) ? (await api(`/api/logs/${m.active}`)).launch : [];
    if (before && before !== JSON.stringify(status.model)) await refreshModels();
    renderShell();
    if (view && view.update) view.update();
    if (view && view.poll) view.poll();
  } catch (e) {
    console.error(e);
    $("top-dot").className = "dot unknown";
    $("top-state").className = "badge unknown";
    $("top-state").textContent = "web UI not reachable";
  }
}

// ---- wiring

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem("theme", theme); } catch (e) { /* private mode */ }
}
let savedTheme = null;
try { savedTheme = localStorage.getItem("theme"); } catch (e) { /* private mode */ }
applyTheme(savedTheme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
document.querySelectorAll("[data-icon]").forEach((n) => { n.innerHTML = ICONS[n.dataset.icon]; });
$("theme").addEventListener("click", (e) => { e.preventDefault(); applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"); });
$("toggle-side").addEventListener("click", () => $("app").classList.toggle("collapsed"));
$("show-key").addEventListener("click", async () => {
  const key = $("key");
  if (!key.classList.contains("hidden")) { key.classList.add("hidden"); $("show-key").textContent = "API key"; return; }
  try { key.textContent = (await api("/api/key", {})).api_key || "none"; key.classList.remove("hidden"); $("show-key").textContent = "Hide key"; }
  catch (e) { alert(e.message); }
});
document.querySelector('.nav[data-route="configs"]').append(navAction("plus", "New config", () => go("#/new")));
window.addEventListener("hashchange", route);

(async () => {
  await refreshModels();
  try {
    status = await api("/api/status");
    if (!status.model.active) idleVramUsed = Object.fromEntries(status.gpus.map((g) => [g.index, g.vram_used_mib || 0]));
  } catch (e) { /* tick reports it */ }
  route();
  // One request round at a time: the next starts 2 s after the last one ended, so slow answers
  // (a busy `strata runtime status`) never pile up.
  for (;;) {
    await tick();
    await new Promise((r) => setTimeout(r, 2000));
  }
})();
