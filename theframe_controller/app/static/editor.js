// Collections editor for The Frame Controller (design 3.8).
// Talks to the add-on's /api/v1 through Ingress with relative URLs. Edits inside a collection (add, remove,
// move, keywords) are queued as changes with IDs: shown at once, kept in the browser while offline, sent in
// order, and a resend never applies twice. The add-on's answers and its "collection" events are the truth;
// queued changes are re-applied on top of them until they are confirmed.

const API = "../api/v1/";
const QUEUE_KEY = "theframe-editor-queue";
const SELECTED_KEY = "theframe-editor-collection";

const state = {
  files: [],                  // uploaded files with metadata, from /files
  byName: new Map(),          // file name -> file
  base: new Map(),            // collection id -> collection as the add-on last reported it
  queue: loadQueue(),         // changes not yet confirmed: {id, change, collection, ...}
  selectedId: load(SELECTED_KEY),
  online: true,
  sending: false,
  retryDelay: 1000,
  playing: null,              // name of the collection the slideshow plays
  filmSelection: new Set(),   // selected files in the filmstrip
  lastClick: null,            // {file, action} for shift-click ranges in the grid
  search: { text: "", keywords: new Set(), place: "", from: "", to: "", scope: "all" },
  tiles: new Map(),           // file name -> grid tile element, reused between renders
  eventsOpen: false,
};

// ---------- helpers ----------

function $(id) { return document.getElementById(id); }

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

function load(key) { try { return localStorage.getItem(key); } catch { return null; } }
function store(key, value) {
  try { value === null ? localStorage.removeItem(key) : localStorage.setItem(key, value); } catch { /* private mode */ }
}
function loadQueue() { try { return JSON.parse(load(QUEUE_KEY) || "[]"); } catch { return []; } }
function saveQueue() { store(QUEUE_KEY, state.queue.length ? JSON.stringify(state.queue) : null); }

// crypto.randomUUID needs a secure context; Home Assistant is often reached over plain http
function newId() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

function thumb(file, size = 240) { return API + "thumbnail/" + encodeURIComponent(file) + "?size=" + size; }

function toast(message, error = false) {
  const el = h("div", { class: "toast" + (error ? " error" : ""), role: error ? "alert" : "status" }, message);
  $("toasts").append(el);
  setTimeout(() => el.remove(), error ? 7000 : 3500);
}

class NetworkError extends Error {}

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(API + path, {
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch (e) {
    throw new NetworkError(e.message);
  }
  // Ingress answers 502/503/504 while the add-on restarts: treat like a lost connection
  if (response.status >= 500) throw new NetworkError("add-on not reachable (" + response.status + ")");
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error((data.error && data.error.message) || response.statusText);
    error.code = data.error && data.error.code;
    error.status = response.status;
    throw error;
  }
  return data;
}

function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? iso.slice(0, 10) : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function label(file) {
  const f = state.byName.get(file);
  return (f && (f.title || f.description)) || file;
}

// ---------- collections: base + queued changes ----------

// file placement like the add-on: after the anchor, at the start for null, at the end without an anchor
function place(files, items, after, hasAfter) {
  const index = !hasAfter || (after !== null && !files.includes(after)) ? files.length
    : after === null ? 0 : files.indexOf(after) + 1;
  files.splice(index, 0, ...items);
}

function applyLocal(collection, change) {
  if (!collection) return null;
  const c = structuredClone(collection);
  const items = [...new Set(change.files || [])];
  const hasAfter = Object.prototype.hasOwnProperty.call(change, "after");
  switch (change.change) {
    case "add":
      if (c.kind === "manual") place(c.files, items.filter((f) => !c.files.includes(f)), change.after, hasAfter);
      break;
    case "remove":
      if (c.kind === "manual") c.files = c.files.filter((f) => !items.includes(f));
      break;
    case "move":
      if (c.kind === "manual") {
        const moving = items.filter((f) => c.files.includes(f));
        const rest = c.files.filter((f) => !moving.includes(f));
        place(rest, moving, change.after, hasAfter && !moving.includes(change.after));
        c.files = rest;
      }
      break;
    case "set_keywords":
      if (c.kind === "keywords") c.keywords = [...new Set(change.keywords)].sort();
      break;
  }
  if (c.kind === "manual") {
    c.missing = c.files.filter((f) => !state.byName.has(f));
    c.count = c.files.length - c.missing.length;
  }
  return c;
}

function collection(id) {
  let c = state.base.get(id) || null;
  for (const change of state.queue) if (change.collection === id) c = applyLocal(c, change);
  return c;
}

function collections() {
  return [...state.base.keys()].map(collection).filter(Boolean)
    .sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" }));
}

function current() { return state.selectedId ? collection(state.selectedId) : null; }

function setBase(id, info, revision) {
  const known = state.base.get(id);
  if (known && revision !== undefined && known.revision > revision) return;   // an older answer arrived late
  if (info) state.base.set(id, info); else state.base.delete(id);
}

// ---------- sending changes ----------

function queueChange(change) {
  const c = current();
  if (!c) return;
  state.queue.push({ id: newId(), collection: c.id, ...change });
  saveQueue();
  render();
  flush();
}

async function flush() {
  if (state.sending) return;
  state.sending = true;
  try {
    while (state.queue.length) {
      const change = state.queue[0];
      try {
        const result = await api("collections/changes", { method: "POST", body: change });
        setBase(result.collection_id, result.collection, result.revision);
        setOnline(true);
      } catch (e) {
        if (e instanceof NetworkError) {
          setOnline(false);
          setTimeout(flush, state.retryDelay);
          state.retryDelay = Math.min(state.retryDelay * 2, 30000);
          return;
        }
        // refused (e.g. the collection was deleted meanwhile): report it instead of guessing
        toast(describeError(e, change), true);
        await reloadCollections();
      }
      state.queue.shift();
      saveQueue();
      render();
    }
  } finally {
    state.sending = false;
    renderSyncState();
  }
}

function describeError(e, change) {
  if (e.code === "collection_deleted") return "Your change was not saved: the collection was deleted meanwhile.";
  if (e.code === "name_taken") return e.message;
  if (change) return "Your change (" + change.change + ") was not saved: " + e.message;
  return e.message;
}

function setOnline(online) {
  if (online) state.retryDelay = 1000;
  if (state.online !== online) {
    state.online = online;
    renderSyncState();
  }
}

// actions that need the add-on at once (they create, delete or rename collections)
async function direct(path, options, success) {
  try {
    const result = await api(path, options);
    setOnline(true);
    // only collection endpoints answer with a collection; the slideshow's answer also has collection_id,
    // but with the collection's name in `collection`
    if (result && result.collection_id !== undefined && typeof result.collection !== "string") {
      setBase(result.collection_id, result.collection, result.revision);
    }
    if (success) toast(success);
    render();
    return result;
  } catch (e) {
    if (e instanceof NetworkError) {
      setOnline(false);
      toast("Not connected to the add-on. Try again when the connection is back.", true);
    } else {
      toast(describeError(e), true);
    }
    return null;
  }
}

function changeRequest(change) {
  return { method: "POST", body: { id: newId(), ...change } };
}

// ---------- loading and live updates ----------

async function reloadFiles() {
  const data = await api("files");
  state.files = data.files;
  state.byName = new Map(state.files.map((f) => [f.file_name, f]));
}

async function reloadCollections() {
  const data = await api("collections");
  state.base = new Map(data.collections.map((c) => [c.id, c]));
  if (state.selectedId && !state.base.has(state.selectedId)) select(null);
}

async function reloadDeleted() {
  try {
    const data = await api("collections/deleted");
    renderDeleted(data.collections);
  } catch { /* shown again on the next reload */ }
}

async function reload() {
  try {
    await Promise.all([reloadFiles(), reloadCollections()]);
    setOnline(true);
    reloadDeleted();
    render();
    flush();
  } catch (e) {
    setOnline(false);
    renderSyncState();
    setTimeout(reload, state.retryDelay);
  }
}

function listen() {
  const events = new EventSource(API + "events");
  events.addEventListener("open", () => { state.eventsOpen = true; reload(); });   // catch up after every (re)connect
  events.addEventListener("collection", (e) => {
    const data = JSON.parse(e.data);
    setBase(data.collection_id, data.collection, data.revision);
    if (!data.collection && data.collection_id === state.selectedId && !state.queue.length) {
      toast("This collection was deleted" + (data.user ? " by " + data.user : "") + ".");
    }
    if (data.change === "delete" || data.change === "undo" || data.change === "restore") reloadDeleted();
    render();
  });
  events.addEventListener("files", () => reloadFiles().then(render).catch(() => {}));
  events.addEventListener("status", (e) => {
    const playing = JSON.parse(e.data).slideshow?.collection ?? null;
    if (playing !== state.playing) { state.playing = playing; renderCollections(); }
  });
  events.addEventListener("error", () => { state.eventsOpen = false; });
  // without the event stream (e.g. a proxy that buffers it), poll instead
  setInterval(() => { if (!state.eventsOpen) reload(); }, 30000);
}

// ---------- search ----------

function matches(file, inCollection) {
  const s = state.search;
  if (s.text) {
    const text = [file.title, file.description, file.file_name, file.place].filter(Boolean).join(" ").toLowerCase();
    if (!s.text.toLowerCase().split(/\s+/).every((word) => text.includes(word))) return false;
  }
  if (s.keywords.size && !(file.keywords || []).some((k) => s.keywords.has(k))) return false;
  if (s.place && file.place !== s.place) return false;
  const day = (file.taken || "").slice(0, 10);
  if (s.from && (!day || day < s.from)) return false;
  if (s.to && (!day || day > s.to)) return false;
  if (s.scope === "in" && !inCollection(file.file_name)) return false;
  if (s.scope === "out" && inCollection(file.file_name)) return false;
  return true;
}

function membership(c) {
  if (!c) return () => false;
  if (c.kind === "manual") {
    const set = new Set(c.files);
    return (name) => set.has(name);
  }
  const keywords = new Set(c.keywords);
  return (name) => (state.byName.get(name)?.keywords || []).some((k) => keywords.has(k));
}

function results() {
  const inCollection = membership(current());
  return state.files.filter((f) => matches(f, inCollection))
    .sort((a, b) => (a.taken || "").localeCompare(b.taken || "") || a.file_name.localeCompare(b.file_name));
}

// ---------- rendering ----------

function render() {
  renderSyncState();
  renderCollections();
  renderHeader();
  renderSearchOptions();
  renderGrid();
  renderFilmstrip();
}

function renderSyncState() {
  const el = $("sync-state");
  const waiting = state.queue.length;
  el.className = "pill" + (!state.online ? " offline" : waiting ? " saving" : "");
  el.textContent = !state.online
    ? "Offline" + (waiting ? " – " + waiting + " change" + (waiting > 1 ? "s" : "") + " waiting" : "")
    : waiting ? "Saving…" : "All changes saved";
}

function renderCollections() {
  const list = $("collection-list");
  list.replaceChildren(...collections().map((c) => h("li", {},
    h("button", {
      "aria-current": c.id === state.selectedId ? "true" : "false",
      dataset: { id: c.id },
      class: c.name === state.playing ? "playing" : null,
      onclick: () => select(c.id),
    },
      h("span", {}, c.name, h("span", { class: "badge" }, c.kind === "keywords" ? "keywords" : "")),
      h("span", { class: "count" }, c.count)))));
}

function renderDeleted(deleted) {
  $("deleted").hidden = !deleted.length;
  $("deleted-list").replaceChildren(...deleted.map((d) => h("li", {},
    h("span", { title: "Deleted by " + (d.deleted_by || "?") + ", " + new Date(d.deleted * 1000).toLocaleString() }, d.name),
    h("button", {
      onclick: async () => {
        const result = await direct("collections/" + d.id + "/restore",
          { method: "POST", body: { revision: d.revision - 1, id: newId() } }, "Restored " + d.name);
        if (result) { select(d.id); reloadDeleted(); }
      },
    }, "Restore"))));
}

function renderHeader() {
  const c = current();
  $("collection-header").hidden = !c;
  $("add-results").hidden = !c || c.kind !== "manual";
  $("search-scope").disabled = !c;
  if (!c) return;
  $("collection-name").textContent = c.name;
  const updated = c.updated ? new Date(c.updated * 1000).toLocaleString() : "";
  $("collection-meta").textContent = (c.kind === "manual" ? "Manual" : "Keywords") + " · " + c.count + " images"
    + (c.missing && c.missing.length ? " · " + c.missing.length + " missing" : "")
    + (updated ? " · changed " + updated + (c.updated_by ? " by " + c.updated_by : "") : "");
  $("keyword-editor").hidden = c.kind !== "keywords";
  if (c.kind === "keywords") {
    const chosen = new Set(c.keywords);
    $("keyword-editor-chips").replaceChildren(...allKeywords().map((k) => h("button", {
      class: "chip", "aria-pressed": chosen.has(k) ? "true" : "false",
      onclick: () => {
        const next = new Set(chosen);
        next.has(k) ? next.delete(k) : next.add(k);
        queueChange({ change: "set_keywords", keywords: [...next] });
      },
    }, k)));
  }
}

function allKeywords() {
  return [...new Set(state.files.flatMap((f) => f.keywords || []))].sort((a, b) => a.localeCompare(b));
}

function renderSearchOptions() {
  const chips = $("search-keywords");
  chips.replaceChildren(...allKeywords().map((k) => h("button", {
    class: "chip", "aria-pressed": state.search.keywords.has(k) ? "true" : "false",
    onclick: () => {
      state.search.keywords.has(k) ? state.search.keywords.delete(k) : state.search.keywords.add(k);
      renderSearchOptions();
      renderGrid();
    },
  }, k)));
  const select = $("search-place");
  const places = [...new Set(state.files.map((f) => f.place).filter(Boolean))].sort();
  const known = [...select.options].slice(1).map((o) => o.value);
  if (places.join("\n") !== known.join("\n")) {
    select.replaceChildren(h("option", { value: "" }, "All places"), ...places.map((p) => h("option", { value: p }, p)));
    select.value = places.includes(state.search.place) ? state.search.place : "";
  }
}

function tile(file) {
  let el = state.tiles.get(file.file_name);
  if (!el) {
    el = h("div", { class: "tile", role: "button", tabindex: "0", dataset: { file: file.file_name } },
      h("img", { src: thumb(file.file_name), loading: "lazy", alt: "" }),
      h("span", { class: "caption" }, h("span", { class: "name" }), h("span", { class: "sub" })),
      h("button", { class: "show", title: "Show on TV", "aria-label": "Show on TV",
        onclick: (e) => { e.stopPropagation(); showOnTv(file.file_name); } }, "▶ TV"));
    el.addEventListener("click", (e) => onTileClick(file.file_name, e.shiftKey));
    el.addEventListener("keydown", (e) => {
      if (e.target === el && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); onTileClick(file.file_name, e.shiftKey); }
    });
    state.tiles.set(file.file_name, el);
  }
  el.querySelector(".name").textContent = file.title || file.description || file.file_name;
  el.querySelector(".sub").textContent = [fmtDate(file.taken), file.place].filter(Boolean).join(" · ") || file.file_name;
  el.title = file.file_name;
  return el;
}

function renderGrid() {
  const c = current();
  const inCollection = membership(c);
  const shown = results();
  for (const file of shown) {
    const el = tile(file);
    const member = inCollection(file.file_name);
    el.classList.toggle("in", member);
    el.setAttribute("aria-pressed", c && c.kind === "manual" ? String(member) : "false");
  }
  $("grid").replaceChildren(...shown.map((f) => state.tiles.get(f.file_name)));
  $("result-count").textContent = shown.length + " of " + state.files.length + " images";
}

function renderFilmstrip() {
  const c = current();
  const panel = $("filmstrip-panel");
  panel.hidden = !c || c.kind !== "manual";
  if (panel.hidden) return;
  for (const f of [...state.filmSelection]) if (!c.files.includes(f)) state.filmSelection.delete(f);
  const missing = new Set(c.missing || []);
  $("filmstrip").replaceChildren(...c.files.map((file, i) => h("li", {
    class: (state.filmSelection.has(file) ? "selected " : "") + (missing.has(file) ? "missing" : ""),
    dataset: { file }, tabindex: "0", title: file + (missing.has(file) ? " (not on the TV)" : ""),
    "aria-label": (i + 1) + ". " + label(file),
    onclick: (e) => toggleFilmSelection(file, e.metaKey || e.ctrlKey || e.shiftKey),
    onkeydown: (e) => filmstripKey(e, file),
  },
    h("span", { class: "handle", "aria-hidden": "true", title: "Drag to reorder" }, "⠿"),
    h("img", { src: thumb(file, 160), loading: "lazy", alt: "" }),
    h("div", { class: "label" }, missing.has(file) ? "missing: " + file : label(file)),
    h("button", { class: "remove", title: "Remove from collection", "aria-label": "Remove " + file,
      onclick: (e) => { e.stopPropagation(); queueChange({ change: "remove", files: [file] }); } }, "×"))));
  const n = state.filmSelection.size;
  $("filmstrip-hint").textContent = n
    ? n + " selected · new images are added after the last selected one"
    : "Drag ⠿ to reorder. Click images to select them; Alt+←/→ moves a selected image.";
  for (const id of ["move-start", "move-end", "remove-selected"]) $(id).disabled = !n;
}

// ---------- interactions ----------

function select(id) {
  state.selectedId = id;
  state.filmSelection.clear();
  state.lastClick = null;
  store(SELECTED_KEY, id);
  render();
}

function insertAnchor(c) {
  const selected = c.files.filter((f) => state.filmSelection.has(f));
  return selected.length ? { after: selected[selected.length - 1] } : {};
}

function onTileClick(file, shift) {
  const c = current();
  if (!c || c.kind !== "manual") return;
  const member = c.files.includes(file);
  if (shift && state.lastClick) {
    const shown = results().map((f) => f.file_name);
    const [a, b] = [shown.indexOf(state.lastClick.file), shown.indexOf(file)].sort((x, y) => x - y);
    if (a >= 0) {
      const range = shown.slice(a, b + 1);
      if (state.lastClick.action === "add") {
        const files = range.filter((f) => !c.files.includes(f));
        if (files.length) queueChange({ change: "add", files, ...insertAnchor(c) });
      } else {
        const files = range.filter((f) => c.files.includes(f));
        if (files.length) queueChange({ change: "remove", files });
      }
      return;
    }
  }
  state.lastClick = { file, action: member ? "remove" : "add" };
  queueChange(member ? { change: "remove", files: [file] } : { change: "add", files: [file], ...insertAnchor(c) });
}

function toggleFilmSelection(file, extend) {
  if (!extend) {
    const only = state.filmSelection.size === 1 && state.filmSelection.has(file);
    state.filmSelection.clear();
    if (!only) state.filmSelection.add(file);
  } else if (state.filmSelection.has(file)) state.filmSelection.delete(file);
  else state.filmSelection.add(file);
  renderFilmstrip();
}

function filmstripKey(e, file) {
  const c = current();
  if (!c) return;
  const i = c.files.indexOf(file);
  if (e.altKey && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
    e.preventDefault();
    if (e.key === "ArrowLeft" && i > 0) queueChange({ change: "move", files: [file], after: i > 1 ? c.files[i - 2] : null });
    if (e.key === "ArrowRight" && i < c.files.length - 1) queueChange({ change: "move", files: [file], after: c.files[i + 1] });
    requestAnimationFrame(() => document.querySelector(`#filmstrip li[data-file="${CSS.escape(file)}"]`)?.focus());
  } else if (e.key === "Delete" || e.key === "Backspace") {
    e.preventDefault();
    queueChange({ change: "remove", files: [file] });
  } else if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    toggleFilmSelection(file, e.metaKey || e.ctrlKey || e.shiftKey);
  }
}

function selectedInOrder() {
  const c = current();
  return c ? c.files.filter((f) => state.filmSelection.has(f)) : [];
}

function setupFilmstrip() {
  if (!window.Sortable) {
    toast("Drag and drop is not available; use the move buttons.", true);
    return;
  }
  window.Sortable.create($("filmstrip"), {
    handle: ".handle",
    animation: 120,
    delayOnTouchOnly: true,
    delay: 120,
    onEnd: (evt) => {
      if (evt.oldIndex === evt.newIndex) return;
      const file = evt.item.dataset.file;
      const before = evt.item.previousElementSibling;
      const moving = state.filmSelection.has(file) ? selectedInOrder() : [file];
      let after = before ? before.dataset.file : null;
      while (after !== null && moving.includes(after) && after !== file) {   // dropped next to another selected one
        const prev = $("filmstrip").querySelector(`li[data-file="${CSS.escape(after)}"]`)?.previousElementSibling;
        after = prev ? prev.dataset.file : null;
      }
      queueChange({ change: "move", files: moving, after });
    },
  });
}

async function showOnTv(file) {
  await direct("show", { method: "POST", body: { file_name: file } }, "Shown on the TV: " + label(file));
}

async function ask(title, fields, okText = "OK") {
  const dialog = $("dialog");
  $("dialog-title").textContent = title;
  $("dialog-ok").textContent = okText;
  const inputs = {};
  $("dialog-body").replaceChildren(...fields.map((f) => {
    if (f.type === "radio") {
      return h("fieldset", { class: "plain" }, h("legend", {}, f.label), ...f.options.map(([value, text]) =>
        h("label", {}, h("input", { type: "radio", name: f.name, value, checked: value === f.value }), " ", text)));
    }
    if (f.type === "html") return f.element;
    const input = h("input", { type: "text", name: f.name, value: f.value || "", required: true, maxlength: "60" });
    inputs[f.name] = input;
    return h("label", {}, f.label, input);
  }));
  dialog.showModal();
  const first = Object.values(inputs)[0];
  if (first) { first.focus(); first.select(); }
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => {
      if (dialog.returnValue !== "ok") return resolve(null);
      const data = Object.fromEntries(new FormData($("dialog-form")).entries());
      resolve(data);
    }, { once: true });
  });
}

async function newCollection() {
  const data = await ask("New collection", [
    { name: "name", label: "Name" },
    { name: "kind", type: "radio", label: "Kind", value: "manual",
      options: [["manual", "Manual: pick images and arrange their order"], ["keywords", "Keywords: all images with chosen keywords"]] },
  ], "Create");
  if (!data) return;
  const result = await direct("collections/changes",
    changeRequest({ change: "create", name: data.name.trim(), kind: data.kind || "manual", files: [], keywords: [] }),
    "Created " + data.name.trim());
  if (result) select(result.collection_id);
}

async function renameCollection() {
  const c = current();
  const data = await ask("Rename collection", [{ name: "name", label: "Name", value: c.name }], "Rename");
  if (!data || data.name.trim() === c.name) return;
  await direct("collections/changes", changeRequest({ change: "rename", collection: c.id, name: data.name.trim() }));
}

async function duplicateCollection() {
  const c = current();
  const data = await ask("Duplicate collection", [{ name: "name", label: "Name of the copy", value: c.name + " (copy)" }], "Duplicate");
  if (!data) return;
  const result = await direct("collections/changes", changeRequest({
    change: "create", name: data.name.trim(), kind: c.kind, files: c.files || [], keywords: c.keywords || [] }),
  "Created " + data.name.trim());
  if (result) select(result.collection_id);
}

async function deleteCollection() {
  const c = current();
  const ok = await ask("Delete " + c.name + "?", [{ type: "html",
    element: h("p", {}, "You can restore it from Recently deleted for 30 days.") }], "Delete");
  if (!ok) return;
  const result = await direct("collections/changes", changeRequest({ change: "delete", collection: c.id }), "Deleted " + c.name);
  if (result) { select(null); reloadDeleted(); }
}

async function undo() {
  const c = current();
  if (!c) return;
  if (state.queue.some((q) => q.collection === c.id)) {
    toast("Wait until your changes are saved, then undo.", true);
    return;
  }
  const result = await direct("collections/" + c.id + "/undo", { method: "POST", body: { id: newId() } }, "Undone");
  if (result && !result.collection) { select(null); reloadDeleted(); }
}

async function showHistory() {
  const c = current();
  let data;
  try { data = await api("collections/" + c.id + "/history"); } catch (e) { toast(describeError(e), true); return; }
  const rows = data.entries.map((e) => h("tr", {},
    h("td", {}, e.revision),
    h("td", {}, new Date(e.time * 1000).toLocaleString()),
    h("td", {}, e.user || ""),
    h("td", {}, e.change + (e.undone ? " (undone)" : "")),
    h("td", {}, e.change === "delete" ? "" : h("button", {
      onclick: async () => {
        $("dialog").close("cancel");
        await direct("collections/" + c.id + "/restore", { method: "POST", body: { revision: e.revision, id: newId() } },
          "Restored revision " + e.revision);
      },
    }, "Restore"))));
  await ask("History of " + c.name, [{ type: "html", element: h("div", { class: "history" },
    h("table", {}, h("tr", {}, h("th", {}, "Rev."), h("th", {}, "When"), h("th", {}, "Who"), h("th", {}, "Change"), h("th", {})), ...rows)) }],
  "Close");
}

async function importCollections(file) {
  let data;
  try { data = JSON.parse(await file.text()); } catch { toast("This file is not a collections export.", true); return; }
  const result = await direct("collections/import", { method: "POST", body: { collections: data.collections || [] } });
  if (result) { toast("Imported: " + (result.imported.join(", ") || "nothing")); await reloadCollections(); render(); }
}

function sortOnce(by) {
  const c = current();
  if (!c || !by) return;
  const key = (f) => {
    const file = state.byName.get(f) || {};
    return by === "taken" ? (file.taken || "9999") : by === "title" ? label(f).toLowerCase() : f.toLowerCase();
  };
  const sorted = [...c.files].sort((a, b) => key(a).localeCompare(key(b)) || a.localeCompare(b));
  queueChange({ change: "move", files: sorted, after: null });
}

function wire() {
  $("new-collection").addEventListener("click", newCollection);
  $("rename").addEventListener("click", renameCollection);
  $("duplicate").addEventListener("click", duplicateCollection);
  $("delete").addEventListener("click", deleteCollection);
  $("undo").addEventListener("click", undo);
  $("history").addEventListener("click", showHistory);
  $("play").addEventListener("click", () => {
    const c = current();
    direct("slideshow", { method: "PUT", body: { collection: c.name, enabled: true } }, "Playing " + c.name + " on the TV")
      .then((slideshow) => { if (slideshow) { state.playing = slideshow.collection ?? null; renderCollections(); } });
  });
  $("import").addEventListener("change", (e) => { if (e.target.files[0]) importCollections(e.target.files[0]); e.target.value = ""; });
  $("add-results").addEventListener("click", () => {
    const c = current();
    const files = results().map((f) => f.file_name).filter((f) => !c.files.includes(f));
    if (files.length) queueChange({ change: "add", files, ...insertAnchor(c) });
  });
  $("move-start").addEventListener("click", () => queueChange({ change: "move", files: selectedInOrder(), after: null }));
  $("move-end").addEventListener("click", () => queueChange({ change: "move", files: selectedInOrder() }));
  $("remove-selected").addEventListener("click", () => {
    queueChange({ change: "remove", files: selectedInOrder() });
    state.filmSelection.clear();
  });
  $("sort").addEventListener("change", (e) => { sortOnce(e.target.value); e.target.value = ""; });
  const search = state.search;
  $("search-text").addEventListener("input", (e) => { search.text = e.target.value.trim(); renderGrid(); });
  $("search-place").addEventListener("change", (e) => { search.place = e.target.value; renderGrid(); });
  $("search-from").addEventListener("change", (e) => { search.from = e.target.value; renderGrid(); });
  $("search-to").addEventListener("change", (e) => { search.to = e.target.value; renderGrid(); });
  $("search-scope").addEventListener("change", (e) => { search.scope = e.target.value; renderGrid(); });
  window.addEventListener("online", flush);
}

wire();
setupFilmstrip();
renderSyncState();
reload();
listen();
