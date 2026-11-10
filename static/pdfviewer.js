"use strict";
/* Advanced PDF pane: PDF.js viewer + text annotations + citation "back" navigation.
   Relies on h() from app.js (loaded first). Annotations are stored as fractions of the
   page size, so they stay put at any zoom level. */
(function () {
  const BASE = "/vendor/pdfjs/";
  const COLORS = { yellow: "#ffe14d", orange: "#ffa53d", red: "#ff5c5c", green: "#7ddc6f", blue: "#6ec6ff" };
  const TYPES = { highlight: "Highlight", underline: "Underline", strike: "Strikethrough" };
  const SCALE_KEYWORDS = ["page-width", "page-fit", "page-actual", "auto"];

  let libPromise = null;
  function loadLib() {
    if (!libPromise) {
      libPromise = (async () => {
        const pdfjsLib = await import(BASE + "pdf.min.mjs");
        pdfjsLib.GlobalWorkerOptions.workerSrc = BASE + "pdf.worker.min.mjs";
        globalThis.pdfjsLib = pdfjsLib; // pdf_viewer.mjs expects this global
        const viewer = await import(BASE + "pdf_viewer.mjs");
        return { pdfjsLib, viewer };
      })();
      libPromise.catch(() => { libPromise = null; });
    }
    return libPromise;
  }

  const contains = (o, r) => o.left <= r.left + 1 && o.right >= r.right - 1 && o.top <= r.top + 1 && o.bottom >= r.bottom - 1;
  const uid = () => Math.random().toString(36).slice(2, 10);

  window.createPdfViewer = function (host, opts) {
    const anns = opts.annotations;           // mutated in place, shared with the review
    anns.forEach(a => { if (a.color === "pink") a.color = "red"; });  // pink was replaced by red
    let tool = "select", color = "yellow";
    let pdfDoc = null, pdfViewer = null, linkService = null, eventBus = null, openToken = 0, pendingState = null;
    const backStack = [];
    let stateTimer = null;
    const undoStack = [], redoStack = [];
    const snap = () => { undoStack.push(JSON.stringify(anns)); if (undoStack.length > 100) undoStack.shift(); redoStack.length = 0; };
    function restore(from, to) {
      if (!from.length) return false;
      to.push(JSON.stringify(anns));
      anns.splice(0, anns.length, ...JSON.parse(from.pop()));
      hidePop(); hideSel(); paintAll();
      return true;
    }
    const undo = () => restore(undoStack, redoStack);
    const redo = () => restore(redoStack, undoStack);

    /* ---------- DOM ---------- */
    const container = h("div", { class: "pdfcontainer" });
    const viewerEl = h("div", { class: "pdfViewer" });
    container.append(viewerEl);

    const pageIn = h("input", { type: "number", min: 1, value: 1, class: "pg", title: "Page", onchange: () => { if (pdfViewer) pdfViewer.currentPageNumber = +pageIn.value || 1; } });
    const pageTot = h("span", { class: "pgtot" }, "/ –");
    const zoomSel = h("select", { title: "Zoom", onchange: () => { if (pdfViewer && zoomSel.value !== "custom") pdfViewer.currentScaleValue = zoomSel.value; } },
      [["page-width", "Fit width"], ["page-fit", "Fit page"], ["0.75", "75%"], ["1", "100%"], ["1.25", "125%"], ["1.5", "150%"], ["2", "200%"], ["3", "300%"]]
        .map(([v, t]) => h("option", { value: v }, t)));
    const backBtn = h("button", { class: "backbtn", style: "display:none", title: "Return to where you were (Alt+←)", onclick: () => goBack() }, "↩ Back");
    const markBtn = h("button", { title: "Remember this spot so you can come back with Back after scrolling away", onclick: () => { pushBack(); toastMsg("Spot saved – use Back to return"); } }, "📍");

    const toolBtns = {};
    const mkTool = (key, label, title) => (toolBtns[key] = h("button", { class: "tool", title, onclick: () => setTool(key) }, label));
    const dots = Object.entries(COLORS).map(([k, c]) => h("button", { class: "dot", "data-c": k, style: `background:${c}`, title: k, onclick: () => setColor(k) }));
    const countEl = h("span", {}, "0");
    const listBtn = h("button", { title: "List of annotations", onclick: () => panel.classList.toggle("open") }, "☰ Annotations (", countEl, ")");

    const toolbar = h("div", { class: "pdftb" },
      h("button", { title: "Previous page", onclick: () => pdfViewer && pdfViewer.previousPage() }, "◀"), pageIn, pageTot,
      h("button", { title: "Next page", onclick: () => pdfViewer && pdfViewer.nextPage() }, "▶"),
      h("button", { title: "Zoom out", onclick: () => pdfViewer && pdfViewer.decreaseScale() }, "−"), zoomSel,
      h("button", { title: "Zoom in", onclick: () => pdfViewer && pdfViewer.increaseScale() }, "+"),
      h("span", { class: "sep" }),
      mkTool("select", "Select", "Select text (a small bar appears to annotate the selection)"),
      mkTool("highlight", "Highlight", "Highlighter: selected text is highlighted immediately"),
      mkTool("underline", "Underline", "Underline: selected text is underlined immediately"),
      mkTool("strike", "Strike", "Strikethrough: selected text is struck out immediately"),
      ...dots,
      h("span", { class: "sep" }), markBtn, backBtn, h("span", { class: "grow" }), listBtn,
      h("button", { title: "Hide the panel (the PDF stays attached; reopen it from the tab on the right edge)", onclick: () => opts.onHide && opts.onHide() }, "Hide"),
      h("button", { onclick: () => opts.onChangeFile && opts.onChangeFile() }, "Change"),
      h("button", { title: "Remove the PDF from this review", onclick: () => opts.onClose && opts.onClose() }, "Remove"));

    const listEl = h("div", { class: "annlist" });
    const panel = h("div", { class: "annpanel" },
      h("div", { class: "annhead" }, h("b", {}, "Annotations"),
        h("button", { class: "small", title: "Copy all annotations as Markdown", onclick: copyAll }, "Copy all")),
      listEl);
    const wrap = h("div", { class: "pdfwrap" }, container);
    const main = h("div", { class: "pdfmain" }, wrap, panel);
    const selbar = h("div", { class: "selbar", style: "display:none" });
    const pop = h("div", { class: "annpop", style: "display:none" });
    host.replaceChildren(toolbar, main, selbar, pop);

    function toastMsg(m) { if (typeof toast === "function") toast(m); }
    function setTool(t) { tool = t; syncToolbar(); }
    function setColor(c) { color = c; syncToolbar(); }
    function syncToolbar() {
      for (const [k, b] of Object.entries(toolBtns)) b.classList.toggle("on", k === tool);
      dots.forEach(d => d.classList.toggle("on", d.dataset.c === color));
      container.dataset.tool = tool;
    }
    syncToolbar();

    /* ---------- viewer setup ---------- */
    function ensureViewer(lib) {
      if (pdfViewer) return;
      const { PDFViewer, PDFLinkService, EventBus } = lib.viewer;
      eventBus = new EventBus();
      class TrackingLinkService extends PDFLinkService {
        goToDestination(dest) { pushBack(); return super.goToDestination(dest); }
      }
      linkService = new TrackingLinkService({ eventBus, externalLinkTarget: 2, externalLinkRel: "noopener noreferrer" });
      pdfViewer = new PDFViewer({
        container, viewer: viewerEl, eventBus, linkService,
        annotationEditorMode: lib.pdfjsLib.AnnotationEditorType.DISABLE,
        removePageBorders: false,
      });
      linkService.setViewer(pdfViewer);
      eventBus.on("pagesinit", () => {
        const s = pendingState || {};
        pdfViewer.currentScaleValue = s.scale || "page-width";
        if (s.page > 1 && s.page <= pdfDoc.numPages) pdfViewer.currentPageNumber = s.page;
        syncZoom();
      });
      eventBus.on("pagechanging", e => { pageIn.value = e.pageNumber; emitState(); });
      eventBus.on("scalechanging", () => { syncZoom(); emitState(); });
      eventBus.on("pagerendered", e => paintPage(e.source.div));
      new ResizeObserver(() => {
        if (!pdfViewer || !pdfViewer.pdfDocument || !container.clientWidth) return;
        if (SCALE_KEYWORDS.includes(pdfViewer.currentScaleValue)) requestAnimationFrame(() => { pdfViewer.currentScaleValue = pdfViewer.currentScaleValue; });
      }).observe(container);
    }
    function syncZoom() {
      if (!pdfViewer) return;
      const v = String(pdfViewer.currentScaleValue);
      let opt = [...zoomSel.options].find(o => o.value === v);
      if (!opt) {
        opt = [...zoomSel.options].find(o => o.value === "custom") || zoomSel.appendChild(h("option", { value: "custom" }));
        opt.textContent = Math.round(pdfViewer.currentScale * 100) + "%";
        opt = zoomSel.querySelector('option[value="custom"]');
      }
      zoomSel.value = opt.value;
    }
    function emitState() {
      clearTimeout(stateTimer);
      stateTimer = setTimeout(() => {
        if (pdfViewer && pdfViewer.pdfDocument && opts.onState) opts.onState({ page: pdfViewer.currentPageNumber, scale: String(pdfViewer.currentScaleValue) });
      }, 500);
    }

    /* ---------- back navigation (citation tracking) ---------- */
    function position() {
      return { page: pdfViewer.currentPageNumber, top: container.scrollTop, left: container.scrollLeft, scale: pdfViewer.currentScale };
    }
    function pushBack() {
      if (!pdfViewer || !pdfViewer.pdfDocument) return;
      const p = position(), last = backStack[backStack.length - 1];
      if (last && last.page === p.page && Math.abs(last.top - p.top) < 5 && last.scale === p.scale) return;
      backStack.push(p);
      if (backStack.length > 50) backStack.shift();
      updateBack();
    }
    function goBack() {
      const p = backStack.pop();
      if (!p) return;
      const f = pdfViewer.currentScale / p.scale;
      container.scrollTo({ top: p.top * f, left: p.left * f });
      updateBack();
    }
    function updateBack() {
      const top = backStack[backStack.length - 1];
      backBtn.style.display = top ? "" : "none";
      backBtn.textContent = top ? `↩ Back to p.${top.page}${backStack.length > 1 ? ` (${backStack.length})` : ""}` : "↩ Back";
    }
    const onKey = e => {
      if (e.altKey && e.key === "ArrowLeft" && backStack.length) { e.preventDefault(); goBack(); }
      if (e.key === "Escape") { hideSel(); hidePop(); }
      // Cmd/Ctrl+Z, Shift+Cmd/Ctrl+Z, Ctrl+Y: undo/redo annotations (text fields keep native undo)
      const mod = e.metaKey || e.ctrlKey, k = e.key.toLowerCase();
      if (mod && !e.altKey && (k === "z" || k === "y") && !e.target.closest("input, textarea, [contenteditable]")) {
        const did = (k === "y" || e.shiftKey) ? redo() : undo();
        if (did) e.preventDefault();
      }
    };
    document.addEventListener("keydown", onKey);

    /* ---------- annotation painting ---------- */
    function paintPage(pageDiv) {
      if (!pageDiv) return;
      const n = +pageDiv.dataset.pageNumber;
      let layer = pageDiv.querySelector(":scope > .ann-layer");
      if (!layer) {
        layer = h("div", { class: "ann-layer" });
        const cw = pageDiv.querySelector(":scope > .canvasWrapper");
        cw ? cw.after(layer) : pageDiv.append(layer);
      }
      layer.replaceChildren();
      for (const a of anns) {
        if (a.page !== n) continue;
        const c = COLORS[a.color] || a.color || COLORS.yellow;
        a.rects.forEach(([x, y, w, hh]) => layer.append(h("div", { class: "ann ann-" + a.type,
          style: `left:${x * 100}%;top:${y * 100}%;width:${w * 100}%;height:${hh * 100}%;--c:${c}` })));
        if (a.note && a.rects.length) {
          const [x, y, w] = a.rects[0];
          layer.append(h("div", { class: "ann-pin", title: a.note, style: `left:${(x + w) * 100}%;top:${y * 100}%` }, "💬"));
        }
      }
    }
    function paintAll(save = true) {
      viewerEl.querySelectorAll(".page").forEach(paintPage);
      renderList();
      if (save && opts.onAnnotations) opts.onAnnotations(anns);
    }

    /* ---------- creating annotations from the selection ---------- */
    function selectionGroups() {
      const sel = getSelection();
      if (!sel || !sel.rangeCount || sel.isCollapsed) return null;
      const range = sel.getRangeAt(0);
      if (!container.contains(range.commonAncestorContainer)) return null;
      const pages = [...viewerEl.querySelectorAll(".page")].map(p => ({ n: +p.dataset.pageNumber, tl: p.querySelector(":scope > .textLayer") }))
        .filter(p => p.tl).map(p => ({ n: p.n, box: p.tl.getBoundingClientRect() }));
      const byPage = new Map();
      for (const r of range.getClientRects()) {
        if (r.width <= 1 || r.height <= 2) continue;
        const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
        const pg = pages.find(p => cx >= p.box.left && cx <= p.box.right && cy >= p.box.top && cy <= p.box.bottom);
        if (!pg) continue;
        if (!byPage.has(pg.n)) byPage.set(pg.n, { box: pg.box, list: [] });
        byPage.get(pg.n).list.push(r);
      }
      const out = [];
      for (const [n, { box, list }] of [...byPage].sort((a, b) => a[0] - b[0])) {
        const kept = list.filter((r, i) => !list.some((o, j) => j !== i && contains(o, r) && (!contains(r, o) || j < i)));
        out.push({ page: n, rects: kept.map(r => [(r.left - box.left) / box.width, (r.top - box.top) / box.height, r.width / box.width, r.height / box.height].map(v => +v.toFixed(5))) });
      }
      return out.length ? out : null;
    }
    function addFromSelection(type, colorKey) {
      const groups = selectionGroups();
      if (!groups) return null;
      const text = getSelection().toString().replace(/\s+/g, " ").trim();
      const made = groups.map((g, i) => ({ id: uid(), page: g.page, type, color: colorKey, rects: g.rects, text: i === 0 ? text : "", note: "", created: Date.now() }));
      snap();
      anns.push(...made);
      getSelection().removeAllRanges();
      hideSel();
      paintAll();
      return made[0];
    }

    let selTimer = null;
    container.addEventListener("mouseup", () => { clearTimeout(selTimer); selTimer = setTimeout(onSelection, 10); });
    container.addEventListener("scroll", hideSel, { passive: true });
    document.addEventListener("selectionchange", () => { const s = getSelection(); if (!s || s.isCollapsed) hideSel(); });
    function onSelection() {
      const groups = selectionGroups();
      if (!groups) return hideSel();
      if (tool !== "select") { addFromSelection(tool, color); return; }
      const r = getSelection().getRangeAt(0).getBoundingClientRect();
      selbar.replaceChildren(
        ...Object.entries(COLORS).map(([k, c]) => h("button", { class: "dot", style: `background:${c}`, title: "Highlight " + k, onmousedown: e => e.preventDefault(), onclick: () => addFromSelection("highlight", k) })),
        h("button", { title: "Underline", onmousedown: e => e.preventDefault(), onclick: () => addFromSelection("underline", color) }, h("u", {}, "U")),
        h("button", { title: "Strikethrough", onmousedown: e => e.preventDefault(), onclick: () => addFromSelection("strike", color) }, h("s", {}, "S")),
        h("button", { title: "Highlight and add a comment", onmousedown: e => e.preventDefault(), onclick: () => { const a = addFromSelection("highlight", color); if (a) openPop(a, r.left + r.width / 2, r.bottom); } }, "💬"));
      selbar.style.display = "flex";
      const w = selbar.offsetWidth;
      selbar.style.left = Math.max(8, Math.min(innerWidth - w - 8, r.left + r.width / 2 - w / 2)) + "px";
      selbar.style.top = (r.top > 56 ? r.top - 44 : r.bottom + 8) + "px";
    }
    function hideSel() { selbar.style.display = "none"; }

    /* ---------- click on an existing annotation: edit popover ---------- */
    container.addEventListener("click", e => {
      if (e.target.closest("a")) return;
      const s = getSelection();
      if (s && !s.isCollapsed) return;
      const pageDiv = e.target.closest(".page");
      const tl = pageDiv && pageDiv.querySelector(":scope > .textLayer");
      if (!tl) return hidePop();
      const r = tl.getBoundingClientRect();
      const fx = (e.clientX - r.left) / r.width, fy = (e.clientY - r.top) / r.height, n = +pageDiv.dataset.pageNumber;
      const hit = anns.slice().reverse().find(a => a.page === n && a.rects.some(q => fx >= q[0] && fx <= q[0] + q[2] && fy >= q[1] && fy <= q[1] + q[3]));
      hit ? openPop(hit, e.clientX, e.clientY) : hidePop();
    });
    function hidePop() { pop.style.display = "none"; }
    function openPop(a, x, y) {
      let snapped = false;
      const edit = fn => { if (!snapped) { snap(); snapped = true; } fn(); paintAll(); };
      const note = h("textarea", { rows: 3, placeholder: "Comment (shown on hover, kept with the review)…", oninput: () => edit(() => { a.note = note.value; }) });
      note.value = a.note || "";
      const typeRow = h("div", { class: "row" }, ...Object.entries(TYPES).map(([k, t]) =>
        h("button", { class: "small" + (a.type === k ? " on" : ""), onclick: () => { edit(() => { a.type = k; }); openPop(a, x, y); } }, t)));
      const colorRow = h("div", { class: "row" }, ...Object.entries(COLORS).map(([k, c]) =>
        h("button", { class: "dot" + (a.color === k ? " on" : ""), style: `background:${c}`, title: k, onclick: () => { edit(() => { a.color = k; }); openPop(a, x, y); } })));
      pop.replaceChildren(
        h("div", { class: "poptext" }, a.text ? `“${a.text.slice(0, 160)}${a.text.length > 160 ? "…" : ""}”` : `Page ${a.page}`),
        typeRow, colorRow, note,
        h("div", { class: "row" },
          h("button", { class: "small danger", onclick: () => { remove(a); hidePop(); } }, "Delete"),
          h("span", { class: "grow" }),
          h("button", { class: "small", onclick: hidePop }, "Done")));
      pop.style.display = "block";
      pop.style.left = Math.max(8, Math.min(innerWidth - pop.offsetWidth - 8, x - 20)) + "px";
      pop.style.top = Math.max(8, Math.min(innerHeight - pop.offsetHeight - 8, y + 10)) + "px";
      note.focus();
    }
    function remove(a) { snap(); const i = anns.indexOf(a); if (i >= 0) anns.splice(i, 1); paintAll(); }

    /* ---------- annotation list ---------- */
    function sorted() { return anns.slice().sort((a, b) => a.page - b.page || (a.rects[0]?.[1] || 0) - (b.rects[0]?.[1] || 0)); }
    function renderList() {
      countEl.textContent = anns.length;
      listEl.replaceChildren(...(anns.length ? sorted().map(a => h("div", { class: "annitem", onclick: () => jump(a) },
        h("div", { class: "annmeta" }, h("span", { class: "swatch", style: `background:${COLORS[a.color] || a.color}` }), `p.${a.page} · ${TYPES[a.type] || a.type}`,
          h("button", { class: "small danger", title: "Delete", onclick: e => { e.stopPropagation(); remove(a); } }, "✕")),
        a.text && h("div", { class: "anntext" }, a.text.length > 140 ? a.text.slice(0, 140) + "…" : a.text),
        a.note && h("div", { class: "annnote" }, a.note)))
        : [h("p", { class: "hint", style: "padding:8px" }, "Select text in the paper to highlight, underline or strike it.")]));
    }
    function jump(a) {
      if (!pdfViewer || !pdfViewer.pdfDocument) return;
      pushBack();
      const pv = pdfViewer.getPageView(a.page - 1);
      if (!pv) return;
      const pr = pv.div.getBoundingClientRect(), cr = container.getBoundingClientRect();
      const y = (a.rects[0]?.[1] || 0) * pr.height;
      container.scrollTo({ top: container.scrollTop + pr.top - cr.top + y - 90 });
    }
    function copyAll() {
      const md = sorted().map(a => `- p.${a.page} [${TYPES[a.type] || a.type}]${a.text ? ` "${a.text}"` : ""}${a.note ? ` — ${a.note}` : ""}`).join("\n");
      navigator.clipboard.writeText(md || "(no annotations)").then(() => toastMsg("Annotations copied"));
    }
    renderList();

    /* ---------- public API ---------- */
    return {
      async open(blob, state) {
        const token = ++openToken;
        const lib = await loadLib();
        if (token !== openToken) return;
        ensureViewer(lib);
        pendingState = state || null;
        const data = new Uint8Array(await blob.arrayBuffer());
        const doc = await lib.pdfjsLib.getDocument({ data, cMapUrl: BASE + "cmaps/", cMapPacked: true, standardFontDataUrl: BASE + "standard_fonts/" }).promise;
        if (token !== openToken) { doc.destroy(); return; }
        const old = pdfDoc;
        pdfDoc = doc;
        backStack.length = 0; updateBack();
        pageTot.textContent = "/ " + doc.numPages;
        linkService.setDocument(doc);
        pdfViewer.setDocument(doc);
        if (old) old.destroy();
      },
      close() {
        openToken++;
        if (pdfViewer) { pdfViewer.setDocument(null); linkService.setDocument(null); }
        if (pdfDoc) { pdfDoc.destroy(); pdfDoc = null; }
        backStack.length = 0; updateBack(); hideSel(); hidePop();
      },
      destroy() { this.close(); document.removeEventListener("keydown", onKey); clearTimeout(stateTimer); },
      refreshAnnotations() { paintAll(false); },
      undo, redo,
    };
  };
})();
