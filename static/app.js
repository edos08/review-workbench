"use strict";

/* ---------- helpers ---------- */
const $app = document.getElementById("app");
const h = (tag, attrs = {}, ...kids) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(kid));
  return el;
};
const esc = s => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
const api = async (method, url, body) => {
  const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
};
const apiRaw = async (method, url, body, ctype) => {
  const r = await fetch(url, { method, headers: ctype ? { "Content-Type": ctype } : {}, body });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
};
function toast(msg) {
  const t = h("div", { class: "toast" }, msg);
  document.body.append(t);
  setTimeout(() => t.remove(), 2200);
}
function download(name, text, type = "application/json") {
  const a = h("a", { href: URL.createObjectURL(new Blob([text], { type })), download: name });
  document.body.append(a); a.click(); a.remove();
}
const fmtDate = t => t ? new Date(t * 1000).toLocaleString() : "";
const slug = s => (s || "review").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 50) || "review";

/* ---------- minimal markdown (preview only) ---------- */
function inlineMd(s) {
  s = esc(s);
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  s = s.replace(/\$\$([^$]+)\$\$|\$([^$\n]+)\$/g, (m, a, b) => `<span class="tex">${a || b}</span>`);
  s = s.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<i>$2</i>");
  s = s.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  return s;
}
function renderMd(src) {
  const out = []; let list = null, code = null;
  const closeList = () => { if (list) { out.push(`</${list}>`); list = null; } };
  for (const line of src.split("\n")) {
    if (line.trim().startsWith("```")) {
      if (code === null) { closeList(); code = []; } else { out.push(`<pre>${esc(code.join("\n"))}</pre>`); code = null; }
      continue;
    }
    if (code !== null) { code.push(line); continue; }
    let m;
    if ((m = line.match(/^(#{1,4})\s+(.*)/))) { closeList(); out.push(`<h${m[1].length}>${inlineMd(m[2])}</h${m[1].length}>`); }
    else if ((m = line.match(/^\s*[-*]\s+(.*)/))) { if (list !== "ul") { closeList(); out.push("<ul>"); list = "ul"; } out.push(`<li>${inlineMd(m[1])}</li>`); }
    else if ((m = line.match(/^\s*\d+[.)]\s+(.*)/))) { if (list !== "ol") { closeList(); out.push("<ol>"); list = "ol"; } out.push(`<li>${inlineMd(m[1])}</li>`); }
    else if (!line.trim()) closeList();
    else { closeList(); out.push(`<p>${inlineMd(line)}</p>`); }
  }
  closeList();
  if (code !== null) out.push(`<pre>${esc(code.join("\n"))}</pre>`);
  return out.join("\n");
}

/* help text: ALL-CAPS lines become headings, "- " lines become bullets */
function renderHelp(text) {
  const body = h("div", { class: "body" });
  let ul = null, para = [];
  const flushP = () => { if (para.length) { body.append(h("p", { html: inlineMd(para.join(" ")) })); para = []; } };
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd(), t = line.trim();
    if (!t) { flushP(); ul = null; continue; }
    if (t === t.toUpperCase() && /[A-Z]{3}/.test(t) && !/[a-z]/.test(t)) { flushP(); ul = null; body.append(h("h4", {}, t)); continue; }
    const b = t.match(/^-\s+(.*)/);
    if (b) { flushP(); if (!ul) { ul = h("ul"); body.append(ul); } ul.append(h("li", { html: inlineMd(b[1]) })); continue; }
    if (ul && /^\s/.test(line)) { ul.lastChild.insertAdjacentHTML("beforeend", " " + inlineMd(t)); continue; }
    ul = null; para.push(t);
  }
  flushP();
  return h("details", { class: "help" }, h("summary", {}, "What does this mean?"), body);
}

/* ---------- legacy browser-stored PDFs (older versions kept them in IndexedDB; uploaded to the server on first open) ---------- */
const pdfDB = () => new Promise((res, rej) => {
  const rq = indexedDB.open("review-workbench", 1);
  rq.onupgradeneeded = () => rq.result.createObjectStore("pdfs");
  rq.onsuccess = () => res(rq.result); rq.onerror = () => rej(rq.error);
});
async function pdfGet(id) {
  try { const db = await pdfDB(); return await new Promise(r => { const q = db.transaction("pdfs").objectStore("pdfs").get(id); q.onsuccess = () => r(q.result || null); q.onerror = () => r(null); }); }
  catch { return null; }
}
async function pdfDel(id) { try { const db = await pdfDB(); db.transaction("pdfs", "readwrite").objectStore("pdfs").delete(id); } catch {} }

/* ---------- home ---------- */
const KIND_LABEL = { conference: "Conference", journal: "Journal", general: "General" };
const FIDELITY = {
  form: { text: "Follows the published form", cls: "ok", tip: "Modelled on the review form the venue published. Venues change wording and scales between years: follow the form you actually receive." },
  guidelines: { text: "From public guidelines · verify wording", cls: "warn", tip: "Built from the venue's public reviewer guidelines; the official form could not be verified word for word. Check field names and scales against the real form." },
  generic: { text: "Generic template", cls: "plain", tip: "Not tied to a specific venue." },
};
const slugId = name => name.replace(/\.[^.]+$/, "").toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60);
function ago(t) {
  if (!t) return "";
  const s = Date.now() / 1000 - t;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 7 * 86400) return `${Math.floor(s / 86400)} d ago`;
  return new Date(t * 1000).toLocaleDateString();
}
const groupTemplates = list => [
  ["My maps", list.filter(t => t.custom)],
  ["Conferences", list.filter(t => !t.custom && t.kind === "conference")],
  ["Journals", list.filter(t => !t.custom && t.kind === "journal")],
  ["General", list.filter(t => !t.custom && t.kind === "general")],
].filter(g => g[1].length);

/* searchable, grouped dropdown for choosing a review form */
function templatePicker(templates, initialId, onChange) {
  let current = templates.find(t => t.id === initialId) || null;   // nothing preselected unless you used one before
  let visible = [], active = 0, isOpen = false;
  const label = h("span", { class: "combo-label" }), meta = h("span", { class: "combo-meta" });
  const btn = h("button", { type: "button", class: "combo-btn", "aria-haspopup": "listbox", "aria-expanded": "false", onclick: () => setOpen(!isOpen) },
    h("span", { class: "combo-text" }, label, meta), h("span", { class: "chev", "aria-hidden": "true" }, "▾"));
  const search = h("input", { type: "search", class: "combo-search", placeholder: "Search: NeurIPS, journal, ACL, …", "aria-label": "Search review forms",
    oninput: () => { active = 0; renderList(); }, onkeydown: e => {
      if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(visible.length - 1, active + 1); renderList(true); }
      else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(0, active - 1); renderList(true); }
      else if (e.key === "Enter") { e.preventDefault(); if (visible[active]) choose(visible[active]); }
      else if (e.key === "Escape") { setOpen(false); btn.focus(); }
    } });
  const list = h("div", { class: "combo-list", role: "listbox" });
  const legend = h("div", { class: "combo-legend" }, h("span", {}, h("span", { class: "dot-fid ok" }), " follows the published form"), h("span", {}, h("span", { class: "dot-fid warn" }), " from public guidelines: verify wording"));
  const pop = h("div", { class: "combo-pop", hidden: true }, search, list, legend);
  const root = h("div", { class: "combo" }, btn, pop);
  const outside = e => { if (!root.contains(e.target)) setOpen(false); };
  const metaText = t => [t.venue, t.year].filter(Boolean).join(" · ");
  function paintButton() {
    label.textContent = current ? current.label : "Choose a review form…"; meta.textContent = current ? metaText(current) : `${templates.length} forms: conferences, journals and generic`;
    btn.classList.toggle("placeholder", !current);
  }
  function setOpen(v) {
    isOpen = v; pop.hidden = !v; btn.setAttribute("aria-expanded", String(v));
    if (v) { search.value = ""; active = Math.max(0, visible.findIndex(t => t.id === current?.id)); renderList(true); search.focus(); document.addEventListener("mousedown", outside); }
    else document.removeEventListener("mousedown", outside);
  }
  function choose(t) { current = t; paintButton(); setOpen(false); onChange(t); }
  function renderList(scroll) {
    const q = search.value.trim().toLowerCase();
    const match = t => !q || [t.label, t.venue, t.description, KIND_LABEL[t.kind], t.year].join(" ").toLowerCase().includes(q);
    const groups = groupTemplates(templates.filter(match));
    visible = groups.flatMap(g => g[1]);
    active = Math.min(active, Math.max(0, visible.length - 1));
    list.replaceChildren(...(groups.length ? groups.flatMap(([name, items]) => [
      h("div", { class: "combo-group" }, name),
      ...items.map(t => h("div", { class: "combo-opt" + (visible[active] === t ? " active" : "") + (t.id === current?.id ? " current" : ""), role: "option", "aria-selected": String(t.id === current?.id),
        onmousedown: e => { e.preventDefault(); choose(t); }, onmousemove: () => { const i = visible.indexOf(t); if (i !== active) { active = i; renderList(); } } },
        h("span", { class: "opt-main" }, h("span", { class: "opt-label" }, t.label),
          h("span", { class: "opt-meta" }, metaText(t) + (t.error ? " · has an error" : ""))),
        h("span", { class: "dot-fid " + (FIDELITY[t.fidelity] || FIDELITY.generic).cls, title: (FIDELITY[t.fidelity] || FIDELITY.generic).text })))])
      : [h("div", { class: "combo-empty" }, "No form matches. Try another word, or add your own map.")]));
    if (scroll) list.querySelector(".combo-opt.active")?.scrollIntoView({ block: "nearest" });
  }
  paintButton();
  return { el: root, get current() { return current; }, set(id) { current = templates.find(t => t.id === id) || current; paintButton(); onChange(current); } };
}

async function showHome(token) {
  document.title = "Review Workbench";
  const [templates0, reviews] = await Promise.all([api("GET", "/api/templates"), api("GET", "/api/reviews")]);
  if (token !== navToken) return;
  let templates = templates0, filter = "all", query = "";
  let remembered = null; try { remembered = localStorage.getItem("rw.template"); } catch {}

  /* --- new review --- */
  const previewEl = h("aside", { class: "tpl-preview" });
  const titleIn = h("input", { type: "text", class: "text-in", placeholder: "Paper title or submission number (optional)", "aria-label": "Paper title (optional)", onkeydown: e => { if (e.key === "Enter") start(); } });
  const startBtn = h("button", { class: "btn-primary big", onclick: () => start() }, "Start review");
  const mapIn = h("input", { type: "file", accept: ".map,.txt,text/plain", style: "display:none", onchange: async e => {
    const f = e.target.files[0]; e.target.value = ""; if (!f) return;
    const id = slugId(f.name);
    if (!id) return alert("Could not derive a name from that file name.");
    const clash = templates.find(t => t.id === id && !t.custom);
    if (clash && !confirm(`"${clash.label}" is one of the bundled forms. Your file will take its place (you can remove your copy later to get the original back). Continue?`)) return;
    try { await apiRaw("PUT", `/api/templates/${id}`, await f.text(), "text/plain"); await reloadTemplates(id); toast("Map added to your list"); }
    catch (err) { alert("Map not added: " + err.message); }
  } });
  let picker = templatePicker(templates, remembered, t => renderPreview(t));
  async function reloadTemplates(selectId) {
    templates = await api("GET", "/api/templates");
    picker = templatePicker(templates, selectId || picker.current?.id, t => renderPreview(t));
    pickerHost.replaceChildren(picker.el);
    renderPreview(picker.current);
  }
  const pickerHost = h("div", {}, picker.el);
  function renderPreview(t) {
    startBtn.disabled = !t || !!t.error;
    if (!t) {
      const n = k => templates.filter(x => !x.custom && x.kind === k).length;
      previewEl.replaceChildren(h("div", { class: "preview-empty" }, h("div", { class: "empty-icon", "aria-hidden": "true" }, "🗂️"),
        h("h3", {}, "Pick a form to get started"),
        h("p", { class: "tpl-desc" }, `${n("conference")} conferences, ${n("journal")} journals and a few generic forms, each with explanations for every field. `
          + "Choose one on the left to see what it contains and how closely it follows the venue's own form."),
        h("p", { class: "tpl-desc" }, "Your venue not listed? Add your own map, or start from the blank review.")));
      return;
    }
    const fid = FIDELITY[t.fidelity] || FIDELITY.generic;
    previewEl.replaceChildren(
      h("div", { class: "chips" }, h("span", { class: "chip kind-" + t.kind }, t.custom ? "My map" : KIND_LABEL[t.kind]), t.year && h("span", { class: "chip" }, String(t.year)),
        t.overrides && h("span", { class: "chip warn" }, "replaces the bundled one")),
      h("h3", {}, t.label),
      t.error ? h("p", { class: "err" }, "This map has an error: " + t.error) : h("p", { class: "tpl-desc" }, t.description || "No description."),
      !t.error && h("div", { class: "stats" }, h("span", {}, h("b", {}, String(t.fields)), " fields"), h("span", {}, h("b", {}, String(t.required)), " required")),
      h("div", { class: "fid " + fid.cls, title: fid.tip }, h("span", { class: "dot-fid " + fid.cls }), fid.text),
      h("div", { class: "tpl-links" }, t.source && h("a", { href: t.source, target: "_blank", rel: "noopener noreferrer" }, "Venue guidelines ↗"),
        t.custom && h("button", { class: "linkbtn danger", onclick: async () => {
          if (!confirm(`Remove "${t.label}" from your maps? Reviews already created from it keep their own copy.`)) return;
          try { await api("DELETE", `/api/templates/${t.id}`); await reloadTemplates(); } catch (e) { alert(e.message); }
        } }, "Remove my map")));
  }
  async function start() {
    const t = picker.current; if (!t || t.error) return;
    startBtn.disabled = true;
    try {
      const title = titleIn.value.trim();
      const r = await api("POST", "/api/reviews", { template: t.id, title });
      if (title) await api("PUT", `/api/reviews/${r.id}`, { valuesPatch: { paper_title: title } });
      try { localStorage.setItem("rw.template", t.id); } catch {}
      location.hash = `#/review/${r.id}`;
    } catch (e) { alert("Could not start the review: " + e.message); startBtn.disabled = false; }
  }

  const newCard = h("section", { class: "panel new-panel" },
    h("div", { class: "panel-head" }, h("h2", {}, "Start a new review"), h("p", { class: "muted" }, "Pick the form of the venue you are reviewing for. Every review keeps its own copy, so you can reshape it later.")),
    h("div", { class: "new-grid" },
      h("div", { class: "new-form" },
        h("label", { class: "field-label" }, "Review form"), pickerHost,
        h("label", { class: "field-label" }, "Paper"), titleIn,
        h("div", { class: "row" }, startBtn, h("button", { class: "btn-ghost", onclick: () => mapIn.click(), title: "Add a form of your own to this list" }, "Add my own map…"), mapIn)),
      previewEl));
  renderPreview(picker.current);

  /* --- restore --- */
  const fileIn = h("input", { type: "file", accept: ".zip,.json,application/zip,application/json", style: "display:none", onchange: async e => {
    const f = e.target.files[0]; if (!f) return;
    try {
      const isZip = /\.zip$/i.test(f.name);
      const r = isZip ? await apiRaw("POST", "/api/reviews/import", f, "application/zip")
                      : await apiRaw("POST", "/api/reviews/import", await f.text(), "application/json");
      location.hash = `#/review/${r.id}`;
    } catch (err) { alert("Could not restore: " + err.message); }
    e.target.value = "";
  } });

  /* --- saved reviews --- */
  const gridEl = h("div", { class: "rgrid" });
  const countEl = h("span", { class: "count" }, String(reviews.length));
  const isComplete = r => r.progress && r.progress.required > 0 && r.progress.filled >= r.progress.required;
  function renderReviews() {
    const q = query.trim().toLowerCase();
    const shown = reviews.filter(r => (filter === "all" || (filter === "done" ? isComplete(r) : !isComplete(r)))
      && (!q || [r.title, r.mapLabel, r.paperFile].join(" ").toLowerCase().includes(q)));
    gridEl.replaceChildren(...(shown.length ? shown.map(r => {
      const p = r.progress, pct = p && p.required ? Math.min(100, Math.round(100 * p.filled / p.required)) : 0, done = isComplete(r);
      return h("article", { class: "rcard" + (done ? " done" : "") },
        h("div", { class: "rcard-top" }, h("h3", { class: "rtitle", onclick: () => location.hash = `#/review/${r.id}`, title: "Open" }, r.title || "Untitled review"),
          r.mapLabel && h("span", { class: "chip subtle", title: r.mapLabel }, r.mapLabel.replace(/ - .*$/, ""))),
        h("div", { class: "rmeta" }, `Updated ${ago(r.updated)}`, r.hasPdf && h("span", { class: "chip pdf", title: r.paperFile || "PDF attached" }, "PDF")),
        p ? h("div", { class: "prog" }, h("div", { class: "bar" }, h("div", { class: "fill", style: `width:${pct}%` })),
              h("span", { class: "prog-text" }, done ? "Complete ✓" : `${p.filled} of ${p.required} required`))
          : h("div", { class: "prog" }, h("span", { class: "err" }, "map cannot be read")),
        h("div", { class: "ractions" },
          h("button", { class: "btn-primary", onclick: () => location.hash = `#/review/${r.id}` }, "Open"),
          h("a", { class: "btnlink", href: `/api/reviews/${r.id}/backup.zip`, download: "", title: "Download the whole review folder as a zip" }, "Backup"),
          h("button", { class: "btn-ghost danger", onclick: async () => {
            if (!confirm(`Delete "${r.title}" with its map and PDF? This cannot be undone.`)) return;
            await api("DELETE", `/api/reviews/${r.id}`); pdfDel(r.id); showHome(navToken);
          } }, "Delete")));
    }) : [h("div", { class: "empty" }, reviews.length ? h("p", {}, "No review matches.") : [h("div", { class: "empty-icon", "aria-hidden": "true" }, "📄"),
        h("p", {}, h("b", {}, "No reviews yet.")), h("p", { class: "muted" }, "Pick a form above and press Start review. Your reviews appear here and are saved automatically.")])]));
  }
  const tab = (key, text) => h("button", { class: "tab" + (filter === key ? " on" : ""), onclick: e => {
    filter = key; e.currentTarget.parentElement.querySelectorAll(".tab").forEach(b => b.classList.remove("on")); e.currentTarget.classList.add("on"); renderReviews();
  } }, text);
  const reviewsCard = h("section", { class: "panel" },
    h("div", { class: "revhead" }, h("h2", {}, "Your reviews ", countEl),
      reviews.length > 0 && h("div", { class: "tabs2" }, tab("all", "All"), tab("todo", "In progress"), tab("done", "Complete")),
      reviews.length > 0 && h("input", { type: "search", class: "text-in rsearch", placeholder: "Search your reviews", "aria-label": "Search your reviews", oninput: e => { query = e.target.value; renderReviews(); } })),
    gridEl);
  renderReviews();

  $app.replaceChildren(h("div", { class: "home2" },
    h("header", { class: "hero" },
      h("div", { class: "brand" }, h("img", { src: "/favicon.svg", alt: "", width: 44, height: 44 }),
        h("div", {}, h("h1", {}, "Review Workbench"), h("p", { class: "muted" }, "Write peer reviews with the paper beside you."))),
      h("div", { class: "hero-actions" }, h("button", { class: "btn-ghost", onclick: () => fileIn.click(), title: "Import a review from a backup .zip" }, "Restore from backup"), fileIn)),
    newCard, reviewsCard));
}

/* ---------- review ---------- */
let current = null; // { cleanup }
let navToken = 0;   // bumped on every navigation; async loaders bail out when it has moved on

const isPrivate = (node, inherited) => inherited || !!node.attrs.private;

function collectFields(node, inheritedPrivate = false, out = []) {
  const priv = isPrivate(node, inheritedPrivate);
  if (["Text", "Textarea", "Markdown", "Choice", "Checkbox"].includes(node.type)) out.push({ node, private: priv });
  if (node.type !== "Choice") node.children.forEach(c => collectFields(c, priv, out));
  return out;
}

function isFilled(f, v) {
  if (v == null) return false;
  if (f.node.type === "Checkbox") return true;
  return String(v).trim() !== "";
}

function exportText(map, review) {
  const parts = [];
  for (const f of collectFields(map)) {
    if (f.private) continue;
    const v = review.values[f.node.attrs.id];
    let txt;
    if (f.node.type === "Choice") txt = (f.node.children.find(o => String(o.attrs.value) === String(v)) || { attrs: {} }).attrs.label || "(not set)";
    else if (f.node.type === "Checkbox") txt = v ? "Yes" : "No";
    else txt = (v || "").trim() || "(empty)";
    parts.push(`## ${f.node.attrs.label}\n\n${txt}`);
  }
  return parts.join("\n\n") + "\n";
}

async function showReview(id, token) {
  let review;
  try { review = await api("GET", `/api/reviews/${id}`); }
  catch (e) { if (token !== navToken) return; $app.replaceChildren(h("div", { class: "home" }, h("p", { class: "err" }, "Review not found."), h("a", { href: "#/" }, "Back"))); return; }
  if (token !== navToken) return;
  let map;
  const templates = await api("GET", "/api/templates").catch(() => []);
  try { map = await api("GET", `/api/reviews/${id}/map`); }
  catch (e) {
    if (token !== navToken) return;
    $app.replaceChildren(h("div", { class: "home" }, h("p", { class: "err" }, "This review's map cannot be loaded: " + e.message),
      h("div", { class: "row" },
        h("button", { class: "primary", onclick: async () => { await api("DELETE", `/api/reviews/${id}/map`); route(); } }, "Reset to its template"),
        h("a", { href: "#/" }, "Back"))));
    return;
  }
  if (token !== navToken) return;
  const fields = collectFields(map);
  const required = fields.filter(f => f.node.attrs.required);

  review.annotations ||= [];
  // Only what changed is sent, and answers are merged key by key on the server, so a stale tab
  // (e.g. one that merely scrolled its PDF) cannot overwrite answers written elsewhere.
  const dirtyVals = new Set(), dirtyMeta = new Set();
  const payload = () => {
    const p = {};
    if (dirtyVals.size) { p.valuesPatch = {}; dirtyVals.forEach(k => { p.valuesPatch[k] = review.values[k] ?? null; }); }
    for (const k of dirtyMeta) p[k] = review[k];
    return p;
  };

  /* autosave */
  const status = h("span", { class: "status" }, "Saved");
  let timer = null, dirty = false, saving = false;
  const flush = async () => {
    if (!dirty || saving) return;
    saving = true; dirty = false; status.className = "status"; status.textContent = "Saving…";
    const sentVals = [...dirtyVals], sentMeta = [...dirtyMeta], body = payload();
    dirtyVals.clear(); dirtyMeta.clear();
    try {
      await api("PUT", `/api/reviews/${id}`, body);
      status.textContent = dirty ? "Unsaved changes" : "Saved";
    } catch (e) { sentVals.forEach(k => dirtyVals.add(k)); sentMeta.forEach(k => dirtyMeta.add(k)); dirty = true; status.className = "status err"; status.textContent = "Save failed!"; }
    saving = false;
    if (dirty) schedule();
  };
  const mark = (kind, key) => { if (kind === "v") dirtyVals.add(key); else dirtyMeta.add(kind); schedule(); };
  const schedule = () => { dirty = true; status.textContent = "Unsaved changes"; clearTimeout(timer); timer = setTimeout(flush, 600); };
  const onUnload = () => {
    if (dirty) navigator.sendBeacon ? fetch(`/api/reviews/${id}`, { method: "PUT", keepalive: true, headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload()) }) : 0;
  };
  window.addEventListener("beforeunload", onUnload);
  // Cmd/Ctrl+S: save the review now instead of the browser's "save page" dialog
  const onKeySave = e => {
    if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === "s") {
      e.preventDefault();
      clearTimeout(timer); dirty = true;
      flush().then(() => toast(status.classList.contains("err") ? "Save failed!" : "Saved"));
    }
  };
  document.addEventListener("keydown", onKeySave);

  /* progress */
  const progress = h("span", { class: "progress" });
  const updateProgress = () => {
    const n = required.filter(f => isFilled(f, review.values[f.node.attrs.id])).length;
    progress.textContent = `Required: ${n}/${required.length}`;
    progress.className = "progress" + (n === required.length ? " done" : "");
  };
  const setValue = (fid, v) => { review.values[fid] = v; updateProgress(); mark("v", fid); };

  /* title: follows paper title field until edited by hand */
  const titleIn = h("input", { class: "title", value: review.title, title: "Review name", oninput: e => { review.title = e.target.value; titleTouched = true; mark("title"); } });
  let titleTouched = review.title !== "Untitled review";

  /* PDF pane (PDF.js viewer with annotations and back-navigation: see pdfviewer.js) */
  const root = h("div", { class: "review" });
  const pdfpane = h("div", { class: "pdfpane" });
  const pdfInput = h("input", { type: "file", accept: "application/pdf,.pdf", style: "display:none", onchange: e => {
    const f = e.target.files[0]; if (f) openPdf(f, true); e.target.value = "";
  } });
  const viewer = createPdfViewer(pdfpane, {
    annotations: review.annotations,
    onAnnotations: () => mark("annotations"),
    onState: st => { review.pdfState = st; mark("pdfState"); },
    onChangeFile: () => pdfInput.click(),
    onClose: async () => { viewer.close(); root.classList.remove("has-pdf"); try { await api("DELETE", `/api/reviews/${id}/pdf`); } catch (e) { alert("Could not remove the PDF: " + e.message); } review.paperFile = null; review.pdfState = null; mark("paperFile"); mark("pdfState"); },
  });
  current = { cleanup: () => { window.removeEventListener("beforeunload", onUnload); document.removeEventListener("keydown", onKeySave); clearTimeout(timer); flush(); viewer.destroy(); } };
  const openPdf = async (blob, persist) => {
    if (persist) {
      status.className = "status"; status.textContent = "Uploading PDF…";
      try { await apiRaw("PUT", `/api/reviews/${id}/pdf`, blob, "application/pdf"); }
      catch (e) { alert("Could not upload the PDF: " + e.message); status.textContent = "Upload failed"; status.className = "status err"; return false; }
      review.paperFile = blob.name || "paper.pdf"; review.pdfState = null; mark("paperFile"); mark("pdfState");
    }
    root.classList.add("has-pdf");
    restoreNote?.remove();
    try { await viewer.open(blob, persist ? null : review.pdfState); viewer.refreshAnnotations(); }
    catch (e) { alert("Could not open the PDF: " + e.message); viewer.close(); root.classList.remove("has-pdf"); }
    return true; // stored on the server (or already was)
  };
  const pdfBtn = h("button", { onclick: () => pdfInput.click() }, "Select file (PDF)…");

  /* form pane */
  const inner = h("div", { class: "inner" });
  const formpane = h("div", { class: "formpane" }, inner);
  const restoreNote = review.paperFile ? h("div", { class: "restore" }, `Last paper used: ${review.paperFile}. `, h("a", { href: "#", onclick: e => { e.preventDefault(); pdfInput.click(); } }, "Select it again")) : null;
  if (restoreNote) inner.append(restoreNote);

  const renderNode = (node, inheritedPrivate) => {
    const a = node.attrs, priv = isPrivate(node, inheritedPrivate);
    switch (node.type) {
      case "Frame":
        return h("section", { class: "frame" },
          h("h2", {}, a.label || "", priv && h("span", { class: "private-tag" }, "private")),
          node.children.map(c => renderNode(c, priv)));
      case "Info":
        return h("div", { class: "info" }, a.label && h("b", {}, a.label), a.text || "");
      case "Text": {
        const inp = h("input", { type: "text", value: review.values[a.id] || "", placeholder: a.placeholder || "",
          oninput: e => {
            setValue(a.id, e.target.value);
            if (a.id === "paper_title" && !titleTouched) { review.title = e.target.value || "Untitled review"; titleIn.value = review.title; mark("title"); }
          } });
        return fieldWrap(a, priv, inp, false);
      }
      case "Textarea": case "Markdown": {
        const ta = h("textarea", { class: a.id === "review" ? "tall" : "", rows: a.rows || 6, oninput: e => { setValue(a.id, e.target.value); count.textContent = `${e.target.value.length} chars`; } });
        ta.value = review.values[a.id] || "";
        const count = h("span", {}, `${ta.value.length} chars`);
        const copy = h("button", { class: "small", onclick: async () => { await navigator.clipboard.writeText(ta.value); toast("Copied to clipboard"); } }, "Copy");
        let el = ta;
        let tabs = null;
        if (node.type === "Markdown") {
          const prev = h("div", { class: "preview", style: "display:none" });
          const wBtn = h("button", { class: "on", onclick: () => { ta.style.display = ""; prev.style.display = "none"; wBtn.className = "on"; pBtn.className = ""; } }, "Write");
          const pBtn = h("button", { onclick: () => { prev.innerHTML = renderMd(ta.value) || "<i>Nothing to preview</i>"; ta.style.display = "none"; prev.style.display = ""; pBtn.className = "on"; wBtn.className = ""; } }, "Preview");
          tabs = h("div", { class: "tabs" }, wBtn, pBtn);
          el = h("div", {}, ta, prev);
        }
        return fieldWrap(a, priv, el, true, tabs, h("div", { class: "tools" }, count, copy,
          node.type === "Markdown" && h("span", {}, "· Markdown + LaTeX ($…$), as in OpenReview")));
      }
      case "Choice": {
        const wrap = h("div", { class: "choice" });
        const sync = () => wrap.querySelectorAll(".opt").forEach(o => o.classList.toggle("sel", String(o.dataset.v) === String(review.values[a.id])));
        node.children.forEach(o => {
          const v = String(o.attrs.value);
          const radio = h("input", { type: "radio", name: `${id}-${a.id}`, value: v, checked: String(review.values[a.id]) === v,
            onchange: () => { setValue(a.id, v); sync(); } });
          wrap.append(h("label", { class: "opt", "data-v": v }, radio, h("span", {}, o.attrs.label, o.attrs.help && h("span", { class: "ohelp" }, o.attrs.help))));
        });
        setTimeout(sync);
        const clear = h("button", { class: "small", onclick: () => { delete review.values[a.id]; wrap.querySelectorAll("input").forEach(i => i.checked = false); sync(); updateProgress(); mark("v", a.id); } }, "Clear");
        return fieldWrap(a, priv, wrap, true, null, h("div", { class: "tools" }, clear));
      }
      case "Checkbox": {
        const cb = h("input", { type: "checkbox", checked: !!review.values[a.id], onchange: e => setValue(a.id, e.target.checked) });
        return h("div", { class: "field" },
          h("label", { class: "check" }, cb, h("span", { class: "lbl" }, a.label, priv && h("span", { class: "private-tag" }, "private"))),
          a.hint && h("div", { class: "hint" }, a.hint), a.help && renderHelp(a.help));
      }
    }
    return null;
  };
  function fieldWrap(a, priv, control, showHelpFirst, tabs, tools) {
    return h("div", { class: "field" },
      h("div", { class: "lbl" }, a.label, a.required && h("span", { class: "req", title: "required" }, "*"), priv && h("span", { class: "private-tag" }, "private")),
      a.hint && h("div", { class: "hint" }, a.hint),
      a.help && renderHelp(a.help), tabs, control, tools);
  }
  map.children.forEach(c => inner.append(renderNode(c, false)));

  /* top bar */
  const exportBtn = h("button", { onclick: async () => {
    const txt = exportText(map, review);
    try { await navigator.clipboard.writeText(txt); toast("Review text copied to clipboard"); } catch { }
    download(`${slug(review.title)}.review.md`, txt, "text/markdown");
  } }, "Export text");
  const mapIn = h("input", { type: "file", accept: ".map,.txt,text/plain", style: "display:none", onchange: async e => {
    const f = e.target.files[0]; e.target.value = ""; if (!f) return;
    if (!confirm(`Replace this review's map with "${f.name}"? Your answers are kept (fields are matched by id).`)) return;
    try { await flush(); await apiRaw("PUT", `/api/reviews/${id}/map`, await f.text(), "text/plain"); route(); }
    catch (err) { alert("Map not replaced: " + err.message); }
  } });
  const tplSelect = h("select", { class: "menu-select", "aria-label": "Switch to another review form" },
    ...groupTemplates(templates).map(([name, items]) => h("optgroup", { label: name }, ...items.filter(t => !t.error).map(t => h("option", { value: t.id }, t.label)))));
  tplSelect.value = review.template && templates.some(t => t.id === review.template) ? review.template : "";
  const baseTpl = templates.find(t => t.id === review.template);
  const mapMenu = h("details", { class: "menu" }, h("summary", {}, "Form"),
    h("div", { class: "menubox" },
      h("div", { class: "menu-head" }, "Based on", h("b", {}, baseTpl ? baseTpl.label : (map.attrs.label || "a custom map"))),
      h("label", { class: "menu-label" }, "Switch to another form"),
      tplSelect,
      h("button", { onclick: async () => {
        if (!tplSelect.value || tplSelect.value === review.template) return;
        if (!confirm("Switch this review to the selected form? Your answers are kept where the field ids match (for example summary, strengths, weaknesses).")) return;
        try { await flush(); await api("POST", `/api/reviews/${id}/template`, { template: tplSelect.value }); route(); } catch (err) { alert(err.message); }
      } }, "Use this form"),
      h("hr", {}),
      h("button", { onclick: () => mapIn.click() }, "Upload a map file…"),
      h("a", { class: "btnlink", href: `/api/reviews/${id}/map.map`, download: "review.map" }, "Download this map"),
      h("button", { onclick: async () => {
        if (!confirm(`Reset the map of this review to "${baseTpl ? baseTpl.label : "its template"}"? Your answers are kept.`)) return;
        try { await flush(); await api("DELETE", `/api/reviews/${id}/map`); route(); } catch (err) { alert(err.message); }
      } }, baseTpl ? `Reset to “${baseTpl.label}”` : "Reset to its template")), mapIn);
  const topbar = h("div", { class: "topbar" },
    h("a", { href: "#/", onclick: async e => { await flush(); } }, "← Home"),
    titleIn, h("span", { class: "meta", style: "color:var(--muted);font-size:13px" }, map.attrs.venue || map.attrs.label || ""),
    progress, status, pdfBtn, pdfInput,
    mapMenu,
    h("a", { class: "btnlink", href: `/api/reviews/${id}/backup.zip`, download: "", title: "Download this review's whole folder (answers, map, PDF)", onclick: async () => { await flush(); } }, "Backup (.zip)"),
    exportBtn);

  /* splitter */
  const splitter = h("div", { class: "splitter", title: "Drag to resize" });
  splitter.addEventListener("mousedown", e => {
    e.preventDefault(); splitter.classList.add("drag"); document.body.classList.add("dragging");
    const rect = split.getBoundingClientRect();
    const move = ev => {
      const pct = Math.min(80, Math.max(20, ((ev.clientX - rect.left) / rect.width) * 100));
      formpane.style.flex = `0 0 ${pct}%`; pdfpane.style.flex = "1 1 0";
    };
    const up = () => { splitter.classList.remove("drag"); document.body.classList.remove("dragging"); window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); };
    window.addEventListener("mousemove", move); window.addEventListener("mouseup", up);
  });
  const split = h("div", { class: "split" }, formpane, splitter, pdfpane);
  root.append(topbar, split);
  $app.replaceChildren(root);
  document.title = review.title + " – Review Workbench";
  updateProgress();

  // open the PDF stored on the server; fall back to a copy left in this browser by older versions
  try {
    const r = await fetch(`/api/reviews/${id}/pdf`);
    if (token !== navToken) return;
    if (r.ok) await openPdf(new File([await r.blob()], review.paperFile || "paper.pdf", { type: "application/pdf" }), false);
    else {
      const legacy = await pdfGet(id);
      if (token !== navToken) return;
      if (legacy) {
        // delete the browser copy only once the server really has it
        if (await openPdf(new File([legacy], review.paperFile || "paper.pdf", { type: "application/pdf" }), true)) {
          pdfDel(id); toast("PDF moved from this browser to the server");
        }
      }
    }
  } catch (e) { console.error("PDF load failed", e); }

}

/* ---------- router ---------- */
async function route() {
  if (current) { current.cleanup(); current = null; }
  const m = location.hash.match(/^#\/review\/([A-Za-z0-9_-]+)/);
  const token = ++navToken;
  try { m ? await showReview(m[1], token) : await showHome(token); }
  catch (e) { $app.replaceChildren(h("div", { class: "home" }, h("p", { class: "err" }, "Error: " + e.message), h("a", { href: "#/" }, "Home"))); }
}
window.addEventListener("hashchange", route);
route();
