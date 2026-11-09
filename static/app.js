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
async function showHome(token) {
  document.title = "Review Workbench";
  const [info, reviews] = await Promise.all([api("GET", "/api/info"), api("GET", "/api/reviews")]);
  if (token !== navToken) return;
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
  $app.replaceChildren(h("div", { class: "home" },
    h("h1", {}, "Review Workbench"),
    h("p", { class: "sub" }, "Each review is a folder holding your answers, its own map and the paper PDF. Back it up with the .zip button."),
    h("h2", {}, "New review"),
    h("div", { class: "card" },
      h("div", { class: "grow" }, h("b", {}, info.defaultMapLabel), h("div", { class: "meta" }, "default map (you can replace it per review later)")),
      h("button", { class: "primary", onclick: async () => {
        const r = await api("POST", "/api/reviews", {});
        location.hash = `#/review/${r.id}`;
      } }, "Start review")),
    h("div", { class: "row" }, h("h2", { style: "flex:1" }, "Saved reviews"),
      h("button", { onclick: () => fileIn.click() }, "Restore from backup (.zip or .json)"), fileIn),
    reviews.length === 0 && h("p", { class: "sub" }, "Nothing yet."),
    ...reviews.map(r => h("div", { class: "card" },
      h("div", { class: "grow" }, h("b", {}, r.title || "Untitled review"),
        h("div", { class: "meta" }, `updated ${fmtDate(r.updated)}${r.paperFile ? " · " + r.paperFile : ""}`)),
      h("button", { class: "primary", onclick: () => location.hash = `#/review/${r.id}` }, "Open"),
      h("a", { class: "btnlink", href: `/api/reviews/${r.id}/backup.zip`, download: "" }, "Backup (.zip)"),
      h("button", { class: "danger", onclick: async () => {
        if (!confirm(`Delete "${r.title}" with its map and PDF? This cannot be undone.`)) return;
        await api("DELETE", `/api/reviews/${r.id}`); pdfDel(r.id); showHome(navToken);
      } }, "Delete")))
  ));
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
  try { map = await api("GET", `/api/reviews/${id}/map`); }
  catch (e) {
    if (token !== navToken) return;
    $app.replaceChildren(h("div", { class: "home" }, h("p", { class: "err" }, "This review's map cannot be loaded: " + e.message),
      h("div", { class: "row" },
        h("button", { class: "primary", onclick: async () => { await api("DELETE", `/api/reviews/${id}/map`); route(); } }, "Reset to the default map"),
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
  const mapMenu = h("details", { class: "menu" }, h("summary", {}, "Map"),
    h("div", { class: "menubox" },
      h("button", { onclick: () => mapIn.click() }, "Upload a map…"),
      h("a", { class: "btnlink", href: `/api/reviews/${id}/map.map`, download: "review.map" }, "Download this map"),
      h("button", { onclick: async () => {
        if (!confirm("Go back to the default map? Your answers are kept.")) return;
        try { await flush(); await api("DELETE", `/api/reviews/${id}/map`); route(); } catch (err) { alert(err.message); }
      } }, "Reset to default map")), mapIn);
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
