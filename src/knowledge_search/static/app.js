// Ferret UI. Plain ES module, no build step. All document text is inserted with
// textContent (never innerHTML) so indexed content cannot inject markup.
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const el = (tag, props = {}, ...kids) => {
  const n = Object.assign(document.createElement(tag), props);
  for (const k of kids) n.append(k);
  return n;
};
// ⓘ with a longer explanation (hover, keyboard focus or tap); see .info in app.css
const info = (text) => { const b = el("button", { type: "button", className: "info", textContent: "i" });
  b.dataset.tip = text; b.setAttribute("aria-label", "More: " + text); return b; };
let me = null;
let pollTimer = null;
let topicFilter = null;  // Summaries tab: only the documents the local model gave this topic
let staleNote = false;  // the results were shown again because a document was re-indexed after the search
let lastQuery = "";

async function api(path, opts = {}) {
  const headers = { "x-ks-csrf": "1", ...(opts.json !== undefined ? { "content-type": "application/json" } : {}) };
  const r = await fetch(path, { ...opts, headers, body: opts.json !== undefined ? JSON.stringify(opts.json) : opts.body });
  if (r.status === 401 && !path.startsWith("/api/auth/") && !path.startsWith("/api/setup")) { showLogin(); throw new Error("signed out"); }
  const body = r.headers.get("content-type")?.includes("json") ? await r.json() : null;
  if (!r.ok && !(body && body.outcome)) {
    const d = body?.detail;
    const msg = typeof d === "string" ? d : d?.code ? `${d.code}: ${d.detail}` : Array.isArray(d) ? d.map((x) => x.msg).join("; ") : `HTTP ${r.status}`;
    throw new Error(msg);
  }
  return body;
}

function only(view) { for (const v of ["#login-view", "#setup-view", "#app-view"]) $(v).hidden = v !== view; }
function showLogin() { only("#login-view"); }

async function boot() {
  try { me = await api("/api/me"); } catch {
    const s = await api("/api/setup").catch(() => ({}));
    return s.needs_admin ? only("#setup-view") : showLogin();
  }
  only("#app-view");
  openTab("search");  // always start on Search; never show a previous user's tab or results
  closeDrawer(); $("#outcome").hidden = true; $("#weak").hidden = true; $("#q").value = "";
  for (const id of ["#results", "#weak-results", "#upload-list", "#docs", "#status", "#d-body"]) $(id).replaceChildren();
  $("#timing").textContent = ""; $("#search-title").textContent = "Search";
  renderMe();
  searchHint();
}

async function searchHint() {
  const box = $("#search-hint");
  box.hidden = false;
  const st = await api("/api/status").catch(() => null);
  searchModelNote(st?.search_model);
  const c = st?.coverage;
  $("#hint-scope").textContent = c ? `${c.indexed.toLocaleString()} document${c.indexed === 1 ? "" : "s"} indexed in ${st.collections.length} collection${st.collections.length === 1 ? "" : "s"} you can read` + (c.pending ? ` · ${c.pending} still being indexed` : "") + (c.failed ? ` · ${c.failed} failed` : "") : "";
}

// First start: the search model downloads in the background; until it is ready, search matches words only.
let modelTimer = null;
function searchModelNote(state) {
  const box = $("#search-model-note");
  clearTimeout(modelTimer);
  box.hidden = !state || state === "ready";
  if (box.hidden) return;
  box.textContent = state.startsWith("error")
    ? "The search model could not be downloaded, so documents cannot be indexed yet." + (me?.is_admin ? " Check the internet connection, then press Download in Settings → Embedding model; waiting documents are indexed after it." : " Ask an administrator.")
    : state === "downloading"
      ? "Downloading the search model (first start only, a few hundred MB). Documents you add are indexed as soon as it is ready."
      : "Loading the search model…";
  if (!state.startsWith("error")) modelTimer = setTimeout(async () => searchModelNote((await api("/api/status").catch(() => null))?.search_model), 5000);
}

// Re-read the current user's collections/permissions without changing the visible tab.
async function refreshMe() { me = await api("/api/me"); renderMe(); }

function renderMe() {
  $("#who").textContent = me.username;
  $("#role").textContent = me.is_admin ? "Administrator" : "Member";
  $("#avatar").textContent = me.username.slice(0, 1);
  $$(".admin-only").forEach((b) => { b.hidden = !me.is_admin; });
  const scope = $("#scope");
  scope.replaceChildren(el("legend", { textContent: "Search in" }));
  for (const c of me.collections) {
    const cb = el("input", { type: "checkbox", value: c.id, checked: true });
    cb.addEventListener("change", scopeLabel);
    scope.append(el("label", { title: c.description || "" }, cb, c.name));
  }
  if (!me.collections.length) scope.append(el("span", { className: "muted", textContent: me.is_admin ? "No collections yet. Create one under Users & access." : "You have no access to any collection yet." }));
  scopeLabel();
  const sel = $("#upload-collection");
  sel.replaceChildren(...me.collections.filter((c) => c.can_write).map((c) => el("option", { value: c.id, textContent: c.name })));
  $("#folder-collection").replaceChildren(...[...sel.options].map((o) => o.cloneNode(true)));
  $("#upload-form").hidden = $("#upload-actions").hidden = sel.options.length === 0;
  $("#no-writable").hidden = sel.options.length > 0;
}

// The collection menu's button says what is searched: all collections, one by name, or "2 of 5".
function scopeLabel() {
  const boxes = $$("#scope input"), on = boxes.filter((b) => b.checked);
  $("#scope-label").textContent = !boxes.length ? "No collections" : on.length === boxes.length ? "All collections"
    : on.length === 1 ? on[0].parentElement.textContent : `${on.length} of ${boxes.length} collections`;
}
document.addEventListener("click", (e) => {  // close an open menu when clicking elsewhere
  for (const d of $$("details.dropdown[open], details.menu[open]")) if (!d.contains(e.target)) d.open = false;
});

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try {
    await api("/api/auth/login", { method: "POST", json: { username: f.get("username"), password: f.get("password") } });
    $("#login-error").textContent = ""; e.target.reset(); boot();
  } catch (err) { $("#login-error").textContent = err.message; }
});

$("#setup-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  if (f.get("password") !== f.get("password2")) { $("#setup-error").textContent = "Passwords do not match."; return; }
  try {
    await api("/api/setup/admin", { method: "POST", json: { username: f.get("username"), password: f.get("password") } });
    e.target.reset(); boot();
  } catch (err) { $("#setup-error").textContent = err.message; }
});

$("#logout").addEventListener("click", async () => { await api("/api/auth/logout", { method: "POST" }).catch(() => {}); showLogin(); });

function openTab(name) {
  $$(".tab").forEach((t) => (t.dataset.tab === name ? t.setAttribute("aria-current", "page") : t.removeAttribute("aria-current")));
  for (const t of ["search", "documents", "summaries", "status", "assistants", "settings", "admin", "folders", "crawler", "database", "logs"]) $("#tab-" + t).hidden = t !== name;
  clearInterval(pollTimer);
  if (name === "folders") loadFolders();
  if (name === "documents") { loadDocs(); pollTimer = setInterval(loadDocs, 3000); }
  if (name === "summaries") { loadSummaries(); pollTimer = setInterval(loadSummaries, 5000); }
  if (name === "status") loadStatus();
  if (name === "assistants") loadAssistants();
  if (name === "settings") { loadSettings(); pollTimer = setInterval(loadModels, 3000); }
  if (name === "admin") loadAdmin();
  if (name === "logs") showLogView(logView);
  if (name === "database") loadDatabase();
  if (name === "crawler" && !$("#crawler-frame").src) $("#crawler-frame").src = "/crawler/";  // site2md's own page
  menu(false);
}
// The Crawler tab's page asks to open another tab (e.g. Documents after adding a copy)
window.addEventListener("message", (e) => {
  if (e.origin === window.location.origin && e.source === $("#crawler-frame").contentWindow && ["documents"].includes(e.data?.open)) openTab(e.data.open);
});
$$(".tab[data-tab]").forEach((b) => b.addEventListener("click", () => { topicFilter = null; openTab(b.dataset.tab); }));

// Wide screens: the sidebar can fold to a rail of icons (remembered in this browser). Key: [
function setNav(collapsed) {
  $("#app-view").classList.toggle("nav-collapsed", collapsed);
  const b = $("#nav-toggle");
  b.setAttribute("aria-expanded", String(!collapsed));
  b.title = collapsed ? "Expand the sidebar ( [ )" : "Collapse the sidebar ( [ )";
  b.querySelector("span").textContent = collapsed ? "Expand" : "Collapse";
  $$("#sidebar nav .tab").forEach((t) => { t.title = collapsed ? t.textContent.trim() : ""; });
  try { localStorage.setItem("ks-nav-collapsed", collapsed ? "1" : ""); } catch { /* not kept */ }
}
$("#nav-toggle").addEventListener("click", () => setNav(!$("#app-view").classList.contains("nav-collapsed")));
try { if (localStorage.getItem("ks-nav-collapsed")) setNav(true); } catch { /* private window */ }

// Phone and tablet: the sidebar folds behind a menu button.
function menu(open) {
  $("#app-view").classList.toggle("menu-open", open);
  $("#menu-button").setAttribute("aria-expanded", String(open));
}
$("#menu-button").addEventListener("click", (e) => { e.stopPropagation(); menu(!$("#app-view").classList.contains("menu-open")); });
document.addEventListener("click", (e) => { if (!$("#sidebar").contains(e.target)) menu(false); });

// ---------------------------------------------------------------- search
// Snippets come back with \x02 / \x03 as highlight delimiters; render them as <mark> safely.
function highlighted(text) {
  const frag = document.createDocumentFragment();
  text.split(/(\x02[^\x03]*\x03)/).forEach((part) => {
    if (part.startsWith("\x02")) frag.append(el("mark", { textContent: part.slice(1, -1) }));
    else if (part) frag.append(part.replace(/[\x02\x03]/g, ""));
  });
  return frag;
}

// Passages are Markdown-like source (Markdown, and Word/PowerPoint/HTML converted to it; PDF text with a
// line break per printed line). Render the common parts as DOM nodes, never as HTML: paragraphs (soft line
// breaks joined), lists, code, tables, headings; links show their label. Plain-text files stay as they are.
const unmark = (s) => s.replace(/[\x02\x03]/g, "");
function inline(text) {
  const frag = document.createDocumentFragment();
  text.split(/(`[^`\n]+`)/).forEach((part, i) => {
    if (i % 2) return frag.append(el("code", { textContent: unmark(part.slice(1, -1)) }));
    frag.append(highlighted(part.replace(/!\[[^\]]*\]\([^)]*\)/g, "").replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
      .replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (_, a, b) => b || a).replace(/(\*\*|__)(?=\S)(.+?)\1/g, "$2")));
  });
  return frag;
}
function prose(text, mediaType) {
  if (mediaType === "text/plain") return [el("p", { className: "plain" }, highlighted(text))];
  const out = [];
  let para = [], list = null, code = null, table = null, lists = [];  // lists: open [indent, element], outermost first
  const flush = () => { if (para.length) out.push(el("p", {}, inline(para.join(" ")))); para = []; };
  for (const line of text.split("\n")) {
    if (code) {
      if (/^\s*(```|~~~)/.test(line)) { out.push(el("pre", {}, el("code", { textContent: code.join("\n") }))); code = null; }
      else code.push(unmark(line));
      continue;
    }
    if (/^\s*(```|~~~)/.test(line)) { flush(); list = table = null; code = []; continue; }
    if (/^\s*\|.*\|\s*$/.test(line)) {
      flush(); list = null;
      if (/^[\s|:-]+$/.test(line)) {  // the |---|---| separator: the row above is the header
        table?.querySelectorAll("tr:only-child > td").forEach((td) => { const th = el("th"); th.append(...td.childNodes); td.replaceWith(th); });
        continue;
      }
      if (!table) { table = el("table"); out.push(el("div", { className: "table-wrap" }, table)); }
      // cells split on | but not on \|; <br> inside a cell is a line break
      const cells = line.trim().slice(1, -1).split(/(?<!\\)\|/).map((c) => el("td", {}, ...c.trim().replaceAll("\\|", "|")
        .split(/<br\s*\/?>/i).flatMap((part, i) => i ? [el("br"), inline(part)] : [inline(part)])));
      table.append(el("tr", {}, ...cells));
      continue;
    }
    table = null;
    const li = line.match(/^(\s*)(?:[-*+]|\d+[.)])\s+(.*)/);
    if (li) {  // nested by indentation (organisation charts from slides keep their levels this way)
      flush();
      const indent = li[1].length;
      if (!list) lists = [];
      while (lists.length && lists.at(-1)[0] > indent) lists.pop();
      if (!lists.length || lists.at(-1)[0] < indent) {
        const parentItem = lists.length ? lists.at(-1)[1].lastChild : null;
        list = el(/^\s*\d/.test(line) ? "ol" : "ul");
        (parentItem || { append: (n) => out.push(n) }).append(list);
        lists.push([indent, list]);
      }
      list = lists.at(-1)[1];
      list.append(el("li", {}, inline(li[2])));
      continue;
    }
    const h = line.match(/^#{1,6}\s+(.*)/);
    if (h) { flush(); list = null; out.push(el("p", { className: "h" }, inline(h[1]))); continue; }
    if (!line.trim()) { flush(); list = null; continue; }
    if (list) { list.lastChild.append(" ", inline(line.trim())); continue; }  // continuation of the item
    para.push(line.trim());
  }
  if (code) out.push(el("pre", {}, el("code", { textContent: code.join("\n") })));
  flush();
  return out;
}

const BASIS = {
  exact_identifier: "exact identifier", strong_semantic: "strong semantic match",
  semantic_and_lexical_agreement: "semantic + keyword agreement", all_query_terms_and_semantic: "all words match",
  reranker_judged_relevant: "reranker: relevant",
  reranker_and_similarity_agree: "reranker + similarity agree",
};

// "Title > Section > Sub" → "Section > Sub" (the title is shown above it)
const section = (path, title) => (path === title ? "" : path.startsWith(title + " > ") ? path.slice(title.length + 3) : path);
const location = (collection, path, title, page, version) => {
  const s = section(path || "", title);
  return [collection, s.toLowerCase() === `page ${page}` ? "" : s,  // PDF sections are named "Page N"
    page ? `page ${page}` : "", version > 1 ? `version ${version}` : ""].filter(Boolean).join(" · ");
};

// One result: rank, title and its one evidence mark, where the passage is, the passage itself with the
// matched words marked, then what the local model wrote and the connected documents.
function resultItem(r) {
  const loc = location(r.collection, r.heading_path, r.title, r.page, r.version_no);
  const badges = el("span", { className: "badges" });
  if (r.supported && r.match_basis.length)
    badges.append(el("span", { className: "badge ok", textContent: BASIS[r.match_basis[0]] || r.match_basis[0],
      title: "Why this counts as evidence: " + r.match_basis.map((b) => BASIS[b] || b).join(", ") }));
  if (r.signals.linked_from) badges.append(el("span", { className: "badge", textContent: "linked from " + r.signals.linked_from }));
  if (r.outdated_version) badges.append(el("span", { className: "badge warn", textContent: "newer version not yet indexed" }));
  if (r.metadata?.version || r.metadata?.effective_date)
    badges.append(el("span", { className: "badge", textContent: [r.metadata.version && `version ${r.metadata.version}`, r.metadata.effective_date && `effective ${r.metadata.effective_date}`].filter(Boolean).join(", ") }));
  const view = el("button", { className: "small", type: "button", textContent: "View passage" });
  view.addEventListener("click", () => openPassage(r.chunk_id));
  const related = el("div", { className: "r-related", hidden: true });
  relatedDocs(related, r.document_id, r.chunk_id);
  return el("li", { className: "result" },
    el("span", { className: "rank", textContent: r.rank }),
    el("div", { className: "r-body" },
      el("div", { className: "r-head" },
        el("a", { className: "r-title", href: r.source_url, target: "_blank", rel: "noopener", title: "Open the document" }, docTitle(r.title, r.clearer_title)), badges),
      el("div", { className: "meta" }, r.doc_type ? el("span", { className: "kind", textContent: r.doc_type, title: "Type written by the local model" }) : "",
        r.doc_type && loc ? " · " : "", loc),
      el("p", { className: "snippet" }, inline(oneLine(r.snippet))),
      writtenSummary(r),
      related,
      el("div", { className: "r-actions" }, view, el("a", { href: r.source_url, target: "_blank", rel: "noopener", textContent: "Open document" }))),
  );
}

// Title, summary and topics written by the local language model (enrich.py): labelled as such and never
// shown as evidence. A clearer title is only written for vague ones ("Overview", a file name).
function docTitle(title, clearer) {
  return clearer ? el("strong", { textContent: clearer, title: `Title written by the local model; the document's own title is “${title}”` })
    : el("strong", { textContent: title });
}
function writtenSummary(r, labelled = true) {
  if (!r.summary) return "";
  const topics = r.topics.map((t) => {
    const b = el("button", { className: "chip topic", type: "button", textContent: t, title: "Documents with this topic" });
    b.addEventListener("click", () => { closeDrawer(); topicFilter = t; openTab("summaries"); });
    return b;
  });
  if (!labelled) return el("div", { className: "r-summary" }, el("p", { textContent: r.summary }),
    topics.length ? el("div", { className: "rel-row" }, ...topics) : "");
  return el("div", { className: "r-summary" },
    el("p", { className: "rel-row" }, el("span", { className: "rel-label" }, "AI summary", info("Written by the local language model from the whole document. Not evidence: check the passage.")), el("span", { textContent: r.summary })),
    topics.length ? el("div", { className: "rel-row" }, el("span", { className: "rel-label", textContent: "Topics" }), ...topics) : "");
}

// The documents connected to a result, shown with it: what it links to, what refers to it, and what is
// related in meaning. Up to RELATED_SHOWN per group; the passage view lists them all.
const RELATED_SHOWN = 4;
const linksCache = new Map();
function docLinks(docId) {
  if (!linksCache.has(docId)) linksCache.set(docId, api(`/api/documents/${docId}/links`).catch(() => null));
  return linksCache.get(docId);
}

async function relatedDocs(box, docId, chunkId) {
  const links = await docLinks(docId);
  if (!links) return;
  const seen = new Set([docId]);
  const pick = (items) => items.filter((i) => !seen.has(i.document_id) && seen.add(i.document_id));
  const groups = [
    ["Links to", pick(links.outgoing)],
    ["Referenced by", pick([...links.incoming, ...(links.mentions || []).filter((m) => m.how === "mentions this")])],
    ["Mentions", pick((links.mentions || []).filter((m) => m.how === "mentioned here"))],
    ["Related in meaning", pick(links.similar || [])],
  ].filter(([, items]) => items.length);
  for (const [label, items] of groups) {
    const row = el("div", { className: "rel-row" }, el("span", { className: "rel-label", textContent: label }));
    for (const i of items.slice(0, RELATED_SHOWN)) {
      const b = el("button", { className: "chip rel", type: "button", textContent: i.title, title: i.collection });
      b.addEventListener("click", () => openPassage(i.chunk_id));
      row.append(b);
    }
    if (items.length > RELATED_SHOWN) {
      const more = el("button", { className: "link", type: "button", textContent: `+${items.length - RELATED_SHOWN} more` });
      more.addEventListener("click", () => openPassage(chunkId));
      row.append(more);
    }
    box.append(row);
  }
  box.hidden = !groups.length;
}

// The search's verdict in a few words; the full sentence (also what assistants get) is behind the ⓘ.
const HEADLINE = { evidence_found: "Relevant passages found", conflicting_evidence: "Sources disagree: check which one applies",
  no_adequate_evidence: "No supporting evidence in your documents", index_coverage_incomplete: "No supporting evidence yet: indexing is not complete" };
// what a search or the status page cannot see yet, e.g. "36 documents still indexing · 1 failed"
const notCovered = (c) => {
  const n = (v, what) => (v ? `${v.toLocaleString()} document${v === 1 ? "" : "s"} ${what}` : "");
  return [n(c.pending, "still indexing"), n(c.outdated, "indexed with older settings"), n(c.failed, "could not be indexed")].filter(Boolean).join(" · ");
};
$("#search-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if ($("#tab-search").hidden) { topicFilter = null; openTab("search"); }
  $("#scope-menu").open = false;
  const ids = $$("#scope input:checked").map((c) => +c.value);
  const all = ids.length === me.collections.length;
  const out = $("#outcome");
  $("#search-hint").hidden = true;
  $("#search-title").textContent = "Results";
  out.hidden = false; out.className = "outcome"; out.replaceChildren(el("span", { className: "spinner" }), el("span", { textContent: "Searching…" }));
  const slow = setTimeout(() => { if (out.textContent.includes("Searching")) out.append(el("span", { className: "meta", textContent: "Still working: the first search after a start or a model change loads the model." })); }, 3000);
  $("#results").replaceChildren(); $("#weak").hidden = true; $("#timing").textContent = "";
  linksCache.clear();
  if (!ids.length) { out.textContent = "Select at least one collection."; return; }
  let res;
  try { res = await api("/api/search", { method: "POST", json: { query: $("#q").value, collection_ids: all ? null : ids } }); }
  catch (err) { clearTimeout(slow); out.className = "outcome bad"; out.textContent = "Search failed: " + err.message; return; }
  clearTimeout(slow);
  out.className = "outcome " + res.outcome;
  const cov = res.coverage, why = [res.message, res.reason_codes?.length ? "Reasons: " + res.reason_codes.join(", ") + "." : "",
    cov ? `${cov.indexed} of ${cov.documents} documents in this scope are indexed with the current settings.` : ""].filter(Boolean).join(" ");
  out.replaceChildren(el("div", {}, el("strong", { textContent: HEADLINE[res.outcome] || res.message }), " ", info(why)));
  lastQuery = $("#q").value;
  if (staleNote) out.append(el("div", { className: "meta", textContent: "A document was re-indexed after your search, so its passages changed; these are the results again." }));
  staleNote = false;
  if (cov && cov.complete === false) out.append(el("div", { className: "meta", textContent: notCovered(cov) }));
  const strong = res.results.filter((r) => r.supported), weak = res.results.filter((r) => !r.supported);
  $("#results").replaceChildren(...strong.map(resultItem));
  if (weak.length) {
    $("#weak").hidden = false; $("#weak").open = !strong.length;
    $("#weak summary").textContent = strong.length
      ? `${weak.length} more match${weak.length > 1 ? "es" : ""} (not confirmed as evidence)`
      : `Closest matches — not confirmed as evidence (${weak.length})`;
    $("#weak-results").replaceChildren(...weak.map(resultItem));
  }
  if (res.timings_ms) Object.assign($("#timing"), { textContent: `${Math.round(res.timings_ms.total)} ms`,
    title: res.index.embedding_model + (res.index.reranker ? " · reranker " + res.index.reranker : "") });
});

// A passage on one line: list items (slides, org charts) separated by " · " instead of running together.
const oneLine = (t) => t.replace(/^\s*[-*+]\s+/, "").replace(/\s*\n\s*[-*+]\s+/g, " · ").replace(/\s*\n\s*/g, " ");

// Documents opened from inside the drawer (connections, graph) keep a trail, so Back returns to the
// document the search result opened.
let shown = null, trail = [];
const openFromDrawer = (id) => { if (shown) trail.push(shown); openPassage(id, true); };
$("#d-back").addEventListener("click", () => { const prev = trail.pop(); if (prev) { shown = null; openPassage(prev.id, true); } });

async function openPassage(id, keepTrail = false) {
  if (!keepTrail) trail = [];
  let p;
  try { p = await api(`/api/chunks/${id}`); }
  catch (err) {
    if (err.message === "signed out") return;
    if (err.message !== "not found") { $("#outcome").append(el("div", { className: "meta error", textContent: "Could not open the passage: " + err.message })); return; }
    // re-indexing replaces a document's passages; search again rather than show a dead result
    closeDrawer();
    if (lastQuery) { staleNote = true; $("#q").value = lastQuery; $("#search-form").requestSubmit(); }
    return;
  }
  $("#d-title").replaceChildren(docTitle(p.doc_title, p.clearer_title));
  $("#d-loc").textContent = location(p.collection, p.heading_path, p.doc_title, p.page, p.version_no);
  $("#d-body").replaceChildren(...p.context.map((c) => {
    const where = c.id !== p.id && section(c.heading_path || "", p.doc_title);
    return el("div", { className: c.id === p.id ? "hit" : "ctx" },
      where && where !== section(p.heading_path || "", p.doc_title) ? el("div", { className: "ctx-loc meta", textContent: where }) : "",
      ...prose(c.text, p.media_type));
  }));
  $("#d-summary").replaceChildren(writtenSummary(p));
  $("#d-open").href = `/api/documents/${p.document_id}/view` + (p.page ? `#page=${p.page}` : p.anchor ? `#${p.anchor}` : "");
  $("#d-download").href = `/api/documents/${p.document_id}/source?download=1&version=indexed`;
  $("#d-uri").replaceChildren(...(/^https?:\/\//.test(p.source_uri || "")
    ? [el("a", { href: p.source_uri, target: "_blank", rel: "noopener noreferrer", textContent: "Original page ↗", title: p.source_uri })] : []));
  shown = { id, title: p.clearer_title || p.doc_title };
  const back = $("#d-back");
  back.hidden = !trail.length;
  if (trail.length) back.textContent = "← " + trail.at(-1).title;
  $("#drawer").hidden = $("#backdrop").hidden = false;
  $("#d-body .hit")?.scrollIntoView({ block: "center" });
  renderLinks(p.document_id);
}

const KIND = { links_to: "links to", depends_on: "depends on", related: "related", calls: "calls",
  owned_by: "owned by", part_of: "part of", see_also: "see also", mentions: "mentions", similar: "is related in meaning to" };

async function renderLinks(docId) {
  const box = $("#d-links");
  box.hidden = true; box.replaceChildren();
  const [links, imp] = await Promise.all([docLinks(docId), api(`/api/documents/${docId}/impact?depth=2`)])
    .catch(() => [null, null]);
  if (!links) return;
  const item = (title, meta, chunkId) => {
    const b = el("button", { className: "link", textContent: title });
    if (chunkId) b.addEventListener("click", () => openFromDrawer(chunkId));
    return el("li", {}, b, el("span", { className: "meta", textContent: " " + meta }));
  };
  const section = (heading, items) => items.length && box.append(el("h4", { textContent: heading }), el("ul", {}, ...items));
  section("Links from this document", links.outgoing.map((o) => item(o.title, `${KIND[o.kind] || o.kind}${o.anchor ? " #" + o.anchor : ""} · ${o.collection}`, o.chunk_id)));
  const byDoc = new Map();  // one line per linking document, listing all its link kinds
  for (const i of links.incoming) {
    const g = byDoc.get(i.document_id) || { ...i, kinds: [] };
    g.kinds.push(KIND[i.kind] || i.kind); byDoc.set(i.document_id, g);
  }
  section("Linked from", [...byDoc.values()].map((g) => item(g.title, `${g.kinds.join(", ")} this · ${g.collection}`, g.chunk_id)));
  section("Mentions", (links.mentions || []).map((m) => item(m.title, `${m.how} · ${m.collection}`, m.chunk_id)));
  section("Related by meaning", (links.similar || []).map((m) => item(m.title, `similarity ${m.similarity.toFixed(2)} · ${m.collection}`, m.chunk_id)));
  section("Depends on this", imp.nodes.map((n) => item(n.title, `${n.depth === 1 ? "directly" : "indirectly"} · ${KIND[n.kind] || n.kind} · ${n.collection}`, n.chunk_id)));
  if (links.unresolved.length)
    box.append(el("p", { className: "meta", textContent: "Not found in documents you can access: " + links.unresolved.map((u) => u.label || u.target).join(", ") }));
  box.hidden = !box.childElementCount;
  if (box.hidden) return;
  const graph = el("div", { className: "graph" });
  const hops = el("div", { className: "graph-hops" });
  for (const d of [1, 2]) {
    const b = el("button", { className: "chip", type: "button", textContent: d === 1 ? "1 hop" : "2 hops" });
    b.addEventListener("click", () => drawGraph(graph, docId, d, hops));
    hops.append(b);
  }
  const asGraph = el("details", { className: "graph-box" }, el("summary", { textContent: "Show as graph" }), hops, graph);
  asGraph.addEventListener("toggle", () => asGraph.open && !graph.childElementCount && drawGraph(graph, docId, 1, hops), { once: true });
  box.prepend(el("h3", { textContent: "Connections" }));
  box.append(asGraph);
}

// Neighbourhood graph (SVG, no library): the document in the centre, directly linked documents on the
// inner ring, second-hop documents on the outer ring next to the document that connects them. A small graph
// shows every link; a larger one shows only the links that explain the layout (centre to inner ring, each
// outer document to the one that connects it) with labels along the radius, and a document's other links
// appear while it is hovered or focused, so 60 documents stay readable.
const SVG = "http://www.w3.org/2000/svg";
const sv = (tag, attrs = {}, ...kids) => {
  const n = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  for (const k of kids) n.append(k);
  return n;
};
// angles for items wanting `want` (sorted), at least `gap` apart around the circle
function spread(want, gap) {
  const n = want.length;
  if (n * gap >= 2 * Math.PI * 0.95) return want.map((_, i) => want[0] + (2 * Math.PI * i) / n);
  const a = [...want];
  for (let i = 1; i < n; i++) a[i] = Math.max(a[i], a[i - 1] + gap);
  const shift = (a.reduce((x, y) => x + y, 0) - want.reduce((x, y) => x + y, 0)) / n;
  return a.map((x) => x - shift);
}
async function drawGraph(box, docId, depth, hops) {
  hops.querySelectorAll("button").forEach((b, i) => b.setAttribute("aria-pressed", String(i + 1 === depth)));
  box.replaceChildren(el("p", { className: "meta", textContent: "Loading…" }));
  const req = box.dataset.req = String(Date.now() + Math.random());  // a later click wins
  const g = await api(`/api/documents/${docId}/graph?depth=${depth}`).catch(() => null);
  if (box.dataset.req !== req) return;
  if (!g || g.nodes.length < 2) { box.replaceChildren(); return; }
  const dense = g.nodes.length > 12;
  const ring1 = g.nodes.filter((n) => n.depth === 1), ring2 = g.nodes.filter((n) => n.depth === 2);
  const r1 = Math.max(dense ? 130 : 120, (ring1.length * 16) / (2 * Math.PI));
  const r2 = Math.max(r1 + (dense ? 160 : 130), (ring2.length * 14) / (2 * Math.PI));
  const pos = new Map([[g.nodes[0].id, [0, 0]]]), angle = new Map();
  ring1.forEach((n, i) => angle.set(n.id, (2 * Math.PI * i) / ring1.length - Math.PI / 2));
  // each outer document hangs off one inner document that links it: the one with the fewest so far, so
  // they spread around the circle instead of crowding behind the first inner document
  const parent = new Map(), load = new Map();
  for (const n of ring2) {
    let best = null;
    for (const e of g.edges) {
      const other = e.source === n.id ? e.target : e.target === n.id ? e.source : null;
      if (other && angle.has(other) && (best === null || (load.get(other) || 0) < (load.get(best) || 0))) best = other;
    }
    if (best) { parent.set(n.id, best); load.set(best, (load.get(best) || 0) + 1); }
  }
  const outer = ring2.map((n) => [n, parent.has(n.id) ? angle.get(parent.get(n.id)) : 0]).sort((x, y) => x[1] - y[1]);
  spread(outer.map((o) => o[1]), Math.max(14 / r2, (2 * Math.PI) / (ring2.length * 1.6))).forEach((a, i) => angle.set(outer[i][0].id, a));
  for (const n of ring1) pos.set(n.id, [r1 * Math.cos(angle.get(n.id)), r1 * Math.sin(angle.get(n.id))]);
  for (const n of ring2) pos.set(n.id, [r2 * Math.cos(angle.get(n.id)), r2 * Math.sin(angle.get(n.id))]);
  const short = (t, max) => (t.length > max ? t.slice(0, max - 1) + "…" : t);
  // [x, y, anchor, rotation, text]; a dense graph writes labels along the radius, outwards
  const labelOf = (n) => {
    const [x, y] = pos.get(n.id);
    if (n.depth === 0) return [x, y + 26, "middle", 0, short(n.title, 28)];
    const a = angle.get(n.id), text = short(n.title, n.depth === 1 && ring2.length ? 22 : 30);
    if (dense) {
      const right = Math.cos(a) >= 0, deg = (a * 180) / Math.PI + (right ? 0 : 180);
      return [x + 11 * Math.cos(a), y + 11 * Math.sin(a) + 4, right ? "start" : "end", deg, text];
    }
    const c = Math.cos(a), sn = Math.sin(a);
    if (n.depth === 1 && ring2.length) return [x, y + (sn < -0.5 ? -14 : 22), "middle", 0, short(n.title, 20)];
    return c > 0.35 ? [x + 12, y + 4, "start", 0, text] : c < -0.35 ? [x - 12, y + 4, "end", 0, text] : [x, y + (sn < 0 ? -14 : 22), "middle", 0, text];
  };
  const xs = [], ys = [];
  for (const n of g.nodes) {  // extent of each node and its label (units per character at 11 or 13 px, rotated)
    const [x, y] = pos.get(n.id), [lx, ly, anchor, deg, text] = labelOf(n), w = text.length * (dense ? 7.4 : 6.2);
    const dir = anchor === "end" ? -1 : 1, rad = (deg * Math.PI) / 180;
    const ex = anchor === "middle" ? [lx - w / 2, lx + w / 2] : [lx, lx + dir * w * Math.cos(rad)];
    const ey = anchor === "middle" ? [ly - 12, ly + 6] : [ly - 12, ly + dir * w * Math.sin(rad) + 6];
    xs.push(x - 12, x + 12, ...ex); ys.push(y - 12, y + 12, ...ey);
  }
  const x0 = Math.min(...xs) - 8, y0 = Math.min(...ys) - 8, W = Math.max(...xs) - x0 + 8, H = Math.max(...ys) - y0 + 8;
  const svg = sv("svg", { viewBox: `${x0} ${y0} ${W} ${H}`, role: "img", "aria-label": `Link graph of ${g.document.title}` },
    sv("defs", {}, sv("marker", { id: "arrow", viewBox: "0 0 10 10", refX: "16", refY: "5", markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse" },
      sv("path", { d: "M0,0 L10,5 L0,10 z", class: "g-arrow" }))));
  const pairs = new Map();  // one line per source→target, styled by its strongest relation
  for (const e of g.edges) pairs.set(e.source + ">" + e.target, [...(pairs.get(e.source + ">" + e.target) || []), e]);
  const title = (id) => g.nodes.find((n) => n.id === id).title;
  const root = g.nodes[0].id, lines = [];
  for (const es of pairs.values()) {
    const s = es[0].source, t = es[0].target, a = pos.get(s), b = pos.get(t), kinds = es.map((e) => e.kind);
    if (!a || !b) continue;
    const cls = kinds.includes("depends_on") ? "dep" : kinds.every((k) => k === "links_to") ? "link"
      : kinds.every((k) => k === "mentions") ? "men" : kinds.every((k) => k === "similar") ? "sim" : "rel";
    const visible = !dense || s === root || t === root || parent.get(s) === t || parent.get(t) === s;
    const line = sv("line", { x1: a[0], y1: a[1], x2: b[0], y2: b[1], class: `g-edge g-${cls}${visible ? "" : " g-extra"}`, ...(cls === "sim" ? {} : { "marker-end": "url(#arrow)" }) },
      sv("title", {}, `${title(s)} ${kinds.map((k) => KIND[k] || k).join(", ")} ${title(t)}`));
    line.ends = [s, t];
    lines.push(line);
    svg.append(line);
  }
  const nodes = new Map();
  const focus = (id) => {
    svg.classList.toggle("focus", !!id);
    const near = new Set([id]);
    for (const l of lines) { const on = !!id && l.ends.includes(id); l.classList.toggle("on", on); if (on) l.ends.forEach((e) => near.add(e)); }
    for (const [nid, node] of nodes) node.classList.toggle("on", !!id && near.has(nid));
  };
  for (const n of g.nodes) {
    const [x, y] = pos.get(n.id), [lx, ly, anchor, deg, text] = labelOf(n);
    const node = sv("g", { class: "g-node" + (n.depth === 0 ? " g-root" : ""), tabindex: "0", role: "button", "aria-label": `Open ${n.title}` },
      sv("title", {}, `${n.title} · ${n.collection}`), sv("circle", { cx: x, cy: y, r: n.depth === 0 ? 10 : 7 }),
      sv("text", { x: lx, y: ly, "text-anchor": anchor, ...(deg ? { transform: `rotate(${deg.toFixed(1)} ${lx.toFixed(1)} ${ly.toFixed(1)})` } : {}) }, text));
    node.addEventListener("mouseenter", () => focus(n.id));
    node.addEventListener("focus", () => focus(n.id));
    node.addEventListener("mouseleave", () => focus(null));
    node.addEventListener("blur", () => focus(null));
    if (n.depth && n.chunk_id) {
      node.addEventListener("click", () => openFromDrawer(n.chunk_id));
      node.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); openFromDrawer(n.chunk_id); } });
    }
    nodes.set(n.id, node);
    svg.append(node);
  }
  const hidden = lines.filter((l) => l.classList.contains("g-extra")).length;
  const view = el("div", { className: "graph-view" + (dense ? " dense" : "") }, svg);
  // a large graph opens fitted to the panel; Full size shows it at reading size, scrolling
  const fit = dense ? el("button", { type: "button", className: "link g-fit" }) : "";
  const size = (whole) => {
    Object.assign(svg.style, { width: whole ? "100%" : `${Math.round(W)}px`, minWidth: "100%", maxHeight: whole ? "68vh" : "" });
    fit.textContent = whole ? "Full size" : "Fit to panel";
    if (!whole) view.scrollLeft = (view.scrollWidth - view.clientWidth) / 2;
  };
  if (fit) { fit.addEventListener("click", () => size(fit.textContent === "Fit to panel")); size(true); }
  box.replaceChildren(view, el("p", { className: "meta g-legend" }, fit, fit ? " · " : "",
    el("span", { className: "g-key g-dep" }), " depends on  ", el("span", { className: "g-key g-link" }), " links to  ",
    el("span", { className: "g-key g-rel" }), " related / part of / other  ", el("span", { className: "g-key g-men" }), " mentions  ",
    el("span", { className: "g-key g-sim" }), " related in meaning",
    hidden ? ` · ${hidden} more links appear when you point at a document` : "",
    g.truncated ? " · the nearest 60 documents" : ""));
}
function closeDrawer() { $("#drawer").hidden = $("#backdrop").hidden = true; trail = []; shown = null; }
$("#d-close").addEventListener("click", closeDrawer);
$("#backdrop").addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("#drawer").hidden) closeDrawer();
  else if (e.key === "Escape") { menu(false); for (const d of $$("details.dropdown[open], details.menu[open]")) d.open = false; }
  else if (e.key === "[" && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName) && !$("#app-view").hidden) {
    setNav(!$("#app-view").classList.contains("nav-collapsed"));
  }
  else if (e.key === "/" && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName) && !$("#app-view").hidden) {
    e.preventDefault(); $("#q").focus(); $("#q").select();
  }
});

// ---------------------------------------------------------------- documents
// Upload: the two buttons, or drop files or a folder anywhere on the Documents page.
const fileInput = $("#file-input"), folderInput = $("#folder-input"), dropOverlay = $("#dropzone");
$("#files-button").addEventListener("click", () => fileInput.click());
$("#folder-button").addEventListener("click", () => folderInput.click());
fileInput.addEventListener("change", () => { uploadFiles([...fileInput.files]); fileInput.value = ""; });
folderInput.addEventListener("change", () => { uploadFiles([...folderInput.files]); folderInput.value = ""; });
const canDrop = (e) => !$("#tab-documents").hidden && !$("#upload-form").hidden && e.dataTransfer?.types.includes("Files");
let dragDepth = 0;
document.addEventListener("dragenter", (e) => {
  if (!canDrop(e)) return;
  e.preventDefault(); dragDepth++;
  $("#drop-target").textContent = $("#upload-collection").selectedOptions[0]?.textContent || "";
  dropOverlay.hidden = false;
});
document.addEventListener("dragover", (e) => { if (canDrop(e)) e.preventDefault(); });
document.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; dropOverlay.hidden = true; } });
document.addEventListener("drop", async (e) => {
  dragDepth = 0; dropOverlay.hidden = true;
  if (!canDrop(e)) return;
  e.preventDefault();
  uploadFiles(await droppedFiles(e.dataTransfer));
});
// Files of a dropped folder (and its subfolders), read through the entries API; plain files as they are.
async function droppedFiles(dt) {
  const entries = [...dt.items].map((i) => i.webkitGetAsEntry?.()).filter(Boolean);
  if (!entries.some((x) => x.isDirectory)) return [...dt.files];
  const out = [];
  const walk = async (entry) => {
    if (entry.isFile) return out.push(Object.assign(await new Promise((ok, bad) => entry.file(ok, bad)), { relPath: entry.fullPath }));
    const reader = entry.createReader();
    for (let batch; (batch = await new Promise((ok, bad) => reader.readEntries(ok, bad))).length;)
      for (const x of batch) await walk(x);
  };
  for (const x of entries) await walk(x);
  return out;
}
const SUPPORTED = /\.(md|markdown|txt|html?|docx|pptx|pdf)$/i;
const TYPE_EXT = { markdown: ["md", "markdown"], text: ["txt"], html: ["html", "htm"], word: ["docx"], powerpoint: ["pptx"], pdf: ["pdf"] };
const chosenTypes = () => $$("#upload-types input:checked").map((x) => x.value);
try { const t = JSON.parse(localStorage.getItem("ks-folder-types") || "null"); if (t) $$("#upload-types input").forEach((x) => { x.checked = t.includes(x.value); }); } catch { /* defaults */ }
$("#upload-types").addEventListener("change", () => { try { localStorage.setItem("ks-folder-types", JSON.stringify(chosenTypes())); } catch { /* not kept */ } });

// One line per upload (not per file): counts while it runs; only rejected files are listed by name.
async function uploadFiles(files) {
  const list = $("#upload-list");
  // a file from a folder has a path (kit/docs/a.md): it identifies the document, so same-named files stay apart
  const rel = (f) => (f.relPath || f.webkitRelativePath || "").replace(/^\/+/, "");
  const hidden = files.filter((f) => rel(f).split("/").some((x) => /^[._]/.test(x)));
  const exts = new Set(chosenTypes().flatMap((t) => TYPE_EXT[t]));
  const ok = (f) => SUPPORTED.test(f.name) && (!rel(f) || exts.has(f.name.split(".").pop().toLowerCase()));
  files = files.filter((f) => !hidden.includes(f));
  const other = files.filter((f) => !ok(f));
  // in folder and name order (numbers in order: 2 before 10), so they are indexed in the order you see them
  const path = (f) => rel(f) || f.name;
  files = files.filter(ok).sort((a, b) => collator.compare(path(a), path(b)));
  const skipped = (other.length ? ` · skipped ${other.length} of other types (${[...new Set(other.map((f) => (f.name.split(".").pop() || "").toLowerCase()))].join(", ")})` : "")
    + (hidden.length ? ` · left out ${hidden.length} in folders or files starting with . or _` : "");
  const line = el("span", { className: "meta" }), errors = el("ul", { className: "upload-errors" });
  list.replaceChildren(el("li", {}, line, errors));
  let queued = 0, unchanged = 0, rejected = 0;
  const show = (done) => {
    line.textContent = (done ? `Uploaded ${files.length} file(s): ` : `Uploading ${queued + unchanged + rejected + 1} of ${files.length}… `)
      + `${queued} queued for indexing` + (unchanged ? `, ${unchanged} unchanged` : "") + (rejected ? `, ${rejected} rejected` : "") + skipped;
    line.className = "meta " + (done && !rejected ? "ok-text" : "");
  };
  for (const f of files) {
    show(false);
    const fd = new FormData();
    fd.append("file", f); fd.append("collection_id", $("#upload-collection").value);
    if (rel(f)) fd.append("path", rel(f));
    const uri = $("#upload-form [name=source_uri]").value; if (uri) fd.append("source_uri", uri);
    try {
      const r = await api("/api/documents", { method: "POST", body: fd });
      if (r.outcome === "unchanged") unchanged++; else queued++;
    } catch (err) {
      rejected++;
      errors.append(el("li", { className: "meta error" }, el("strong", { textContent: f.name }), " — " + err.message));
    }
  }
  show(true);
  loadDocs();
}

$("#upload-more").addEventListener("click", (e) => {
  const open = $("#upload-options").hidden;
  $("#upload-options").hidden = !open; e.target.setAttribute("aria-expanded", open);
});

// Synced folders (administrators): a folder on the server's disk, indexed by path; Sync takes in the difference.
// While a folder syncs it shows a spinner, how far it got, and Stop; otherwise Sync and Remove.
const count = (n) => Number(n || 0).toLocaleString();
const TYPE_NAME = { markdown: "Markdown", text: "Text", html: "HTML", word: "Word", powerpoint: "PowerPoint", pdf: "PDF" };
function syncText(f) {
  const r = f.result;
  if (!r) return "Not synced yet";
  if (r.error) return r.error;
  const parts = [r.queued && `${count(r.queued)} new or changed`, r.removed && `${count(r.removed)} removed`,
    r.rejected.length && `${r.rejected.length} could not be read`].filter(Boolean);
  if (r.stopped !== undefined) return `Stopped ${ago(new Date(f.synced_at))} after ${count(r.stopped)} of ${count(r.files)} files · Sync to finish`;
  return `Synced ${ago(new Date(f.synced_at))} · ${count(r.files)} files` + (parts.length ? ` · ${parts.join(", ")}` : " · no changes");
}
function folderDetails(f) {
  const r = f.result || {}, left = Object.entries(r.skipped || {}).map(([k, n]) => `${n} ${k}`);
  const d = el("details", { className: "folder-more" }, el("summary", { textContent: "Details" }),
    el("p", { className: "meta", textContent: `Takes ${f.types.map((t) => TYPE_NAME[t]).join(", ")}` + (left.length ? ` · left out: ${left.join(", ")}` : "") }));
  if (r.rejected?.length) d.append(el("ul", { className: "upload-errors" }, ...r.rejected.map((x) => el("li", { className: "meta error", textContent: x }))));
  return d;
}
let folderTimer = null;
async function loadFolders() {
  clearTimeout(folderTimer);
  if (!me?.is_admin) return;
  const rows = await api("/api/admin/folders");
  $("#folders-empty").hidden = rows.length > 0 || !$("#folder-form").hidden;
  $("#folders").replaceChildren(...rows.map((f) => {
    const name = f.path.split(/[\\/]/).filter(Boolean).pop() || f.path;
    const act = (text, cls, fn) => { const b = el("button", { type: "button", className: cls, textContent: text }); b.addEventListener("click", fn); return b; };
    const status = f.syncing
      ? el("span", { className: "meta syncing" }, el("span", { className: "spinner", ariaHidden: "true" }),
          f.progress?.total ? ` Syncing… ${count(f.progress.done)} of ${count(f.progress.total)}` : " Syncing…")
      : el("span", { className: "meta" + (f.result?.error ? " error" : ""), textContent: syncText(f) });
    const actions = f.syncing
      ? [act("Stop", "button", async (e) => { e.target.disabled = true; e.target.textContent = "Stopping…"; await api(`/api/admin/folders/${f.id}/stop`, { method: "POST" }); loadFolders(); })]
      : [act("Sync now", "button", async (e) => { e.target.disabled = true; await api(`/api/admin/folders/${f.id}/sync`, { method: "POST" }); loadFolders(); }),
         act("Remove", "link danger", async () => {
           if (!confirm(`Stop syncing ${f.path}? The files on disk are not touched.`)) return;
           const docs = confirm(`Also remove its documents from the index? OK removes them; Cancel keeps them searchable.`);
           await api(`/api/admin/folders/${f.id}?documents=${docs}`, { method: "DELETE" }); loadFolders();
         })];
    return el("li", { className: "folder" },
      el("div", { className: "folder-main" },
        el("div", {}, el("strong", { textContent: name }), el("span", { className: "muted", textContent: ` into ${f.collection}` })),
        el("div", { className: "mono sub", textContent: f.path, title: f.path })),
      el("div", { className: "folder-status" }, status, f.syncing ? "" : folderDetails(f)),
      el("div", { className: "actions" }, ...actions));
  }));
  if (rows.some((f) => f.syncing) && !$("#tab-folders").hidden) folderTimer = setTimeout(loadFolders, 1500);
}
const folderForm = (open) => {
  $("#folder-form").hidden = !open; $("#folder-add").hidden = open; $("#folder-msg").hidden = true;
  if (open) { $("#folders-empty").hidden = true; $("#folder-form [name=path]").focus(); }
};
$("#folder-add").addEventListener("click", () => folderForm(true));
$("#folder-cancel").addEventListener("click", () => { $("#folder-form").reset(); folderForm(false); loadFolders(); });
$("#folder-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = $("#folder-msg"); msg.hidden = true;
  const path = new FormData(e.target).get("path").trim();
  try {
    await api("/api/admin/folders", { method: "POST", json: { path, collection_id: Number($("#folder-collection").value),
      types: $$("#folder-types input:checked").map((x) => x.value) } });
    e.target.reset(); folderForm(false); loadFolders();
  } catch (err) { msg.textContent = err.message; msg.hidden = false; }
});

// Documents: state tabs (counts from the documents you can see), a name filter, one row per document with
// its status and a row menu. The server filters, sorts and pages (100 rows, then "Show more"). Refreshed every
// 3 s while the tab is open, except while a row menu is open.
let docFilter = "all", docLimit = 100, docSeq = 0;
const DOC_FILTERS = [["all", "All"], ["indexing", "Indexing"], ["failed", "Failed"], ["outdated", "Outdated settings"]];
$("#doc-filter").addEventListener("input", () => { docLimit = 100; loadDocs(); });
// Sorted by a column header (remembered in this browser); by name until one is chosen. Waiting documents in
// queue order, so the list shows what is indexed next.
let docSort = { key: "name", dir: 1 };
try { docSort = JSON.parse(localStorage.getItem("ks-doc-sort")) || docSort; } catch { /* private window */ }
const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
for (const th of $$("#docs-head th[data-sort]")) th.querySelector("button").addEventListener("click", () => {
  const key = th.dataset.sort;
  docSort = { key, dir: docSort.key === key ? -docSort.dir : key === "updated" || key === "chunks" ? -1 : 1 };
  try { localStorage.setItem("ks-doc-sort", JSON.stringify(docSort)); } catch { /* not kept */ }
  docLimit = 100; loadDocs();
});
function sortHeaders() {
  for (const th of $$("#docs-head th[data-sort]")) {
    const on = th.dataset.sort === docSort.key;
    if (on) th.setAttribute("aria-sort", docSort.dir > 0 ? "ascending" : "descending"); else th.removeAttribute("aria-sort");
    const b = th.querySelector("button");
    b.querySelector("svg")?.remove();
    if (on) b.append(icon("down"));
  }
}
$("#doc-type").addEventListener("change", () => { docLimit = 100; loadDocs(); });
$("#docs-more button").addEventListener("click", () => { docLimit += 100; loadDocs(); });
async function loadDocs() {
  if ($("#docs details.menu[open]")) return;
  const seq = ++docSeq;
  const qs = new URLSearchParams({ status: docFilter === "all" ? "" : docFilter, q: $("#doc-filter").value.trim(),
    doc_type: $("#doc-type").value, sort: docSort.key, desc: docSort.dir < 0, limit: docLimit });
  const [docs, n] = await Promise.all([api("/api/documents?" + qs), api("/api/documents/counts?" + qs)]);
  if (seq !== docSeq) return;  // a newer request (typing in the filter) is on its way
  if (me.is_admin && n.indexing) {
    const st = await api("/api/status").catch(() => ({}));
    const busy = Object.entries(st.models || {}).filter(([, v]) => !v.startsWith("ready") && v !== "not loaded");
    $("#model-banner").hidden = !busy.length;
    $("#model-banner").textContent = busy.map(([k, v]) => `${k}: ${v}. Indexing continues automatically when the model is ready; large models can take several minutes to download.`).join(" ");
  } else $("#model-banner").hidden = true;
  const tabs = DOC_FILTERS.filter(([key]) => key === "all" || key === docFilter || n[key]);
  $("#doc-filters").replaceChildren(...tabs.map(([key, label]) => {
    const b = el("button", { className: "subtab", type: "button", role: "tab" }, label, el("span", { className: "count", textContent: n[key].toLocaleString() }));
    b.setAttribute("aria-selected", String(key === docFilter));
    b.addEventListener("click", () => { docFilter = key; docLimit = 100; loadDocs(); });
    return b;
  }));
  const typeBox = $("#doc-type"), kind = typeBox.value;
  typeBox.replaceChildren(el("option", { value: "", textContent: "All types" }),
    ...n.types.map((k) => el("option", { value: k, textContent: k[0].toUpperCase() + k.slice(1) })));
  typeBox.value = n.types.includes(kind) ? kind : ""; typeBox.hidden = !n.types.length;
  sortHeaders();
  const empty = $("#docs-empty");
  empty.hidden = docs.length > 0;
  empty.textContent = !n.all ? "No documents yet. Upload files, or drop them on this page." : "No documents match.";
  const more = $("#docs-more");
  more.hidden = docs.length >= n.matching;
  more.querySelector("span").textContent = `Showing ${docs.length.toLocaleString()} of ${n.matching.toLocaleString()}.`
    + (docs.length >= 1000 ? " Filter by name or type to find the others." : "");
  more.querySelector("button").hidden = docs.length >= 1000;
  $("#docs").replaceChildren(...docs.map((d) => {
    const status = el("td", {}, el("span", { className: "badge st-" + d.status, textContent: STATUS[d.status] || d.status }));
    const again = d.adding_summary ? "adding its summary to the passages · " : "";
    if (d.status === "processing" && d.stage) {
      const pct = d.progress_total ? Math.round((100 * d.progress_done) / d.progress_total) : null;
      const label = again + (d.stage === "embedding" && d.progress_total ? `embedding ${d.progress_done}/${d.progress_total} passages` : d.stage);
      const bar = el("div", { className: "progress" + (pct === null ? " indeterminate" : "") }, el("span", { style: pct === null ? "" : `width:${pct}%` }));
      status.append(el("div", { className: "meta", textContent: label }), bar);
    } else if (d.status === "pending") {
      status.append(el("div", { className: "meta", textContent: again + (d.queue_position ? `waiting, ${d.queue_position} ahead in queue` : "waiting to start") }));
    } else if (d.summary_stage === "writing" || d.summary_stage === "failed") status.append(...summaryStage(d));
    if (d.needs_reindex) status.append(el("div", { className: "meta", textContent: "indexed with older settings" }));
    if (d.error_code) status.append(el("div", { className: "meta error", textContent: `${d.error_code}: ${d.error_detail || ""}` }));
    const ver = d.indexed_version && d.indexed_version !== d.latest_version ? `v${d.latest_version} (indexed v${d.indexed_version})` : `v${d.latest_version}`;
    const updated = new Date(d.updated_at);
    return el("tr", {},
      el("td", {}, sourceLink(d), el("div", { className: "sub" }, el("span", { className: "phone", textContent: `${d.collection} · ${d.doc_type ? d.doc_type[0].toUpperCase() + d.doc_type.slice(1) + " · " : ""}${fileType(d.media_type)} · ` }), `${d.source_key} · ${ver}`)),
      typeCell(d), el("td", { className: "wide", textContent: d.collection }), status,
      el("td", { className: "num wide", textContent: d.chunks ?? "—" }),
      el("td", { className: "num wide muted", textContent: ago(updated), title: updated.toLocaleString() }),
      el("td", { className: "actions-cell" }, docMenu(d)));
  }));
}

// The type written by the local model (runbook, policy, ...) over the file format.
const typeCell = (d) => el("td", { className: "wide" }, d.doc_type ? el("div", { className: "kind", textContent: d.doc_type, title: "Type written by the local model" }) : "",
  el("div", { className: d.doc_type ? "muted small" : "", textContent: fileType(d.media_type) }));
const STATUS = { indexed: "Indexed", pending: "Waiting", processing: "Indexing", failed: "Failed" };
function docMenu(d) {
  const items = [el("a", { href: `/api/documents/${d.id}/view`, target: "_blank", rel: "noopener", textContent: "Open" }),
    el("a", { href: `/api/documents/${d.id}/source?download=1&version=latest`, textContent: "Download original" })];
  if (d.can_write && d.status === "failed") {
    const b = el("button", { type: "button", textContent: "Retry" });
    b.addEventListener("click", async () => { await api(`/api/documents/${d.id}/retry`, { method: "POST" }); b.closest("details").open = false; loadDocs(); });
    items.push(b);
  }
  if (d.can_write) {
    const b = el("button", { type: "button", className: "danger", textContent: "Delete" });
    b.addEventListener("click", async () => {
      b.closest("details").open = false;
      if (confirm(`Delete "${d.title}" and all its versions?`)) { await api(`/api/documents/${d.id}`, { method: "DELETE" }); loadDocs(); }
    });
    items.push(b);
  }
  const summary = el("summary", { className: "button icon", title: "Actions" });
  summary.setAttribute("aria-label", `Actions for ${d.title}`);
  summary.append(icon("more"));
  return el("details", { className: "menu" }, summary, el("div", { className: "menu-pop" }, ...items));
}

// "3 min ago", "yesterday", "12 Mar": a date that reads at a glance; the exact time is in the tooltip.
function ago(t) {
  const s = (Date.now() - t) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 172800) return "yesterday";
  return t.toLocaleDateString(undefined, { day: "numeric", month: "short", ...(s > 300 * 86400 ? { year: "numeric" } : {}) });
}

const TYPES = { "application/pdf": "PDF", "text/markdown": "Markdown", "text/plain": "Text", "text/html": "HTML",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "Word",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation": "PowerPoint" };
const fileType = (mt) => TYPES[mt] || mt || "";
const icon = (name) => sv("svg", { class: "i", "aria-hidden": "true" }, sv("use", { href: "#i-" + name }));
const sourceLink = (d) => el("a", { className: "doc-name", href: `/api/documents/${d.id}/view`, target: "_blank", rel: "noopener", textContent: d.clearer_title || d.title,
  title: d.clearer_title ? `Title written by the local model; the document's own title is “${d.title}”` : "" });
function summaryStage(d) {  // after indexing: the local model's summary (optional)
  const out = [el("div", { className: "meta" + (d.summary_stage === "failed" ? " error" : ""), textContent: {
    waiting: "summary queued (after indexing)", writing: "writing summary…",
    done: "summarised", failed: "summary could not be written" }[d.summary_stage] })];
  if (d.summary_stage === "writing") out.push(el("div", { className: "progress indeterminate" }, el("span")));
  return out;
}

// ---------------------------------------------------------------- summaries
// Same key as enrich.topic_key: case, hyphens and a plural last word do not matter.
function topicKey(t) {
  const w = t.toLowerCase().replace(/[-_/]+/g, " ").split(/\s+/).filter(Boolean);
  if (w.length && w.at(-1).length > 3 && w.at(-1).endsWith("s") && !w.at(-1).endsWith("ss")) w[w.length - 1] = w.at(-1).slice(0, -1);
  return w.join(" ");
}
// What the local model wrote: count tiles and a per-collection table like Index status, then the documents at
// one stage (writing / could not / summarised), filtered by name or topic and paged by the server.
const kpi = (label, value, note) => el("div", { className: "kpi" }, el("span", { className: "kpi-label", textContent: label }),
  el("span", { className: "kpi-value" + (value ? "" : " zero"), textContent: value.toLocaleString() }), el("span", { className: "kpi-note", textContent: note }));
const statusSection = (title, ...kids) => el("section", { className: "status-section" }, el("h2", { textContent: title }), ...kids);
const numCell = (v) => el("td", { className: "num", textContent: v.toLocaleString() });
const updatedNow = () => el("span", { className: "muted small", textContent: `Updated ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` });
function timeLeft(s) {
  const m = Math.round(s / 60);
  return m < 1 ? "under a minute" : m < 60 ? `about ${m} min` : `about ${Math.floor(m / 60)} h${m % 60 ? ` ${m % 60} min` : ""}`;
}
let sumStage = "done", sumLimit = 100, sumSeq = 0;
$("#sum-filter").addEventListener("input", () => { sumLimit = 100; loadSummaries(); });
$("#sum-more button").addEventListener("click", () => { sumLimit += 100; loadSummaries(); });
async function loadSummaries() {
  const seq = ++sumSeq;
  const qs = new URLSearchParams({ stage: topicFilter ? "done" : sumStage, q: $("#sum-filter").value.trim(), topic: topicFilter || "", limit: sumLimit });
  const [st, page] = await Promise.all([api("/api/summaries/status"), api("/api/summaries?" + qs)]);
  if (seq !== sumSeq) return;  // a newer request (typing in the filter) is on its way
  const sum = (k) => st.collections.reduce((n, r) => n + r[k], 0);
  const t = { indexed: sum("indexed"), done: sum("summarised"), waiting: sum("waiting"), writing: sum("writing"), failed: sum("failed") };
  $("#tab-summaries .title-row").replaceChildren(el("h1", { textContent: "Summaries" }), updatedNow());
  const left = t.waiting + t.writing;
  const pace = !st.model ? st.line
    : !st.in_hours ? `Paused until ${st.hours.split("-")[0]}:00 (Settings)`
    : left && st.seconds_per_document ? `Writing summaries: ${timeLeft(left * st.seconds_per_document)} left at the current pace (estimate)`
    : left ? "Writing summaries" : "Every indexed document has its summary";
  const head = [el("p", { className: "outcome " + (st.model && !left ? "evidence_found" : "index_coverage_incomplete") }, el("span", { textContent: pace }))];
  if (st.model) head.push(
    el("div", { className: "kpis" },
      kpi("Indexed documents", t.indexed, `in ${st.collections.length} collection${st.collections.length === 1 ? "" : "s"}`),
      kpi("Summarised", t.done, "with topics"), kpi("Waiting", t.waiting, "queued"), kpi("Writing", t.writing, "now"),
      kpi("Could not", t.failed, t.failed ? "see the list" : "none")),
    statusSection("Collections", el("table", { className: "data" },
      el("thead", {}, el("tr", {}, ...["Collection", "Indexed", "Summarised", "Waiting", "Could not"].map((h, i) => el("th", { className: i ? "num" : "", textContent: h })))),
      el("tbody", {}, ...st.collections.map((r) => el("tr", {}, el("td", { textContent: r.name }), numCell(r.indexed), numCell(r.summarised), numCell(r.waiting + r.writing), numCell(r.failed)))))),
    el("p", { className: "muted small figures", textContent: `Model: ${st.line}` }));
  if (topicFilter) {
    const all = el("button", { className: "link", type: "button", textContent: "Show all" });
    all.addEventListener("click", () => { topicFilter = null; loadSummaries(); });
    const find = el("button", { className: "link", type: "button", textContent: `Search the text for “${topicFilter}”` });
    find.addEventListener("click", () => { const q = topicFilter; topicFilter = null; openTab("search"); $("#q").value = q; $("#search-form").requestSubmit(); });
    head.push(el("div", { className: "topic-filter" }, el("strong", { textContent: `Topic “${topicFilter}”: ${page.total} document${page.total === 1 ? "" : "s"}` }),
      el("span", { className: "muted", textContent: "Topics are written by the local model." }), all, find));
  }
  $("#summaries-head").replaceChildren(...head);
  $("#sum-controls").hidden = !st.model && !page.total && !$("#sum-filter").value;
  const stages = st.model && !topicFilter ? [["writing", "Writing", t.writing], ["failed", "Could not", t.failed], ["done", "Summarised", t.done]] : [];
  $("#sum-stages").replaceChildren(...stages.map(([key, label, n]) => {
    const b = el("button", { className: "subtab", type: "button", role: "tab" }, label, el("span", { className: "count", textContent: n.toLocaleString() }));
    b.setAttribute("aria-selected", String(key === sumStage));
    b.addEventListener("click", () => { sumStage = key; sumLimit = 100; loadSummaries(); });
    return b;
  }));
  const rows = page.rows;
  const none = $("#sum-filter").value ? "No documents match." : topicFilter || sumStage === "done" ? "No summaries yet."
    : sumStage === "writing" ? "Nothing is being written right now." : "No summary failed.";
  $("#summaries").replaceChildren(rows.length ? el("table", { className: "data summaries" },
    el("thead", {}, el("tr", {}, ...["Document", "Type", "Summary and topics by the local model", ""].map((h, i) => el("th", { className: i === 1 ? "wide" : "", textContent: h })))),
    el("tbody", {}, ...rows.map((d) => el("tr", {},
      el("td", {}, sourceLink(d), el("div", { className: "sub", textContent: d.collection })),
      typeCell(d),
      el("td", {}, writtenSummary({ summary: d.summary_text, topics: d.topics }, false) || el("span", { className: "meta", textContent: "—" })),
      el("td", {}, ...(d.summary_stage !== "done" ? summaryStage(d) : [])))))) : el("p", { className: "empty", textContent: none }));
  const more = $("#sum-more");
  more.hidden = rows.length >= page.total;
  more.querySelector("span").textContent = `Showing ${rows.length.toLocaleString()} of ${page.total.toLocaleString()}.`;
  more.querySelector("button").hidden = rows.length >= 1000;
}

// ---------------------------------------------------------------- status
async function loadStatus() {
  const s = await api("/api/status");
  const c = s.coverage;
  const section = statusSection, num = numCell;
  $("#tab-status .title-row").replaceChildren(el("h1", { textContent: "Index status" }), updatedNow());
  const box = $("#status");
  box.replaceChildren(
    el("p", { className: "outcome " + (c.complete ? "evidence_found" : "index_coverage_incomplete") },
      el("span", { textContent: c.complete ? "Every document you can read is indexed with the current settings."
        : c.pending || c.outdated ? "Indexing is not complete, so a search cannot yet rule something out."
        : `${notCovered(c)}, so searches cannot see inside ${c.failed === 1 ? "it" : "them"}.` })),
    el("div", { className: "kpis" },
      kpi("Documents", c.documents ?? c.indexed + c.pending + c.failed, `in ${s.collections.length} collection${s.collections.length === 1 ? "" : "s"}`),
      kpi("Indexed", c.indexed, "searchable"), kpi("Waiting", c.pending, "queued or indexing"),
      kpi("Failed", c.failed, c.failed ? "see Documents" : "none"), kpi("Outdated", c.outdated, "older settings")),
    section("Collections", el("table", { className: "data" },
      el("thead", {}, el("tr", {}, ...["Collection", "Documents", "Indexed", "Waiting", "Failed"].map((h, i) => el("th", { className: i ? "num" : "", textContent: h })))),
      el("tbody", {}, ...s.collections.map((r) => el("tr", {}, el("td", { textContent: r.name }), num(r.documents), num(r.indexed), num(r.pending), num(r.failed)))))),
    section("Active configuration", el("dl", { className: "kv" }, ...Object.entries(s.index).flatMap(([k, v]) => [el("dt", { textContent: k.replaceAll("_", " ") }), el("dd", { textContent: v })]))),
  );
  if (me.is_admin) {
    const a = await api("/api/admin/status");
    box.append(section("Job queue", el("dl", { className: "kv" },
      ...Object.entries(a.jobs).flatMap(([k, v]) => [el("dt", { textContent: k }), el("dd", { textContent: v })]),
      el("dt", { textContent: "oldest queued job" }), el("dd", { textContent: a.oldest_queued_job_seconds ? Math.round(a.oldest_queued_job_seconds) + " s" : "—" }))));
  }
}

// ---------------------------------------------------------------- settings (admin)
let settings = null;
function fillSettings(v) {
  $("#s-model").value = v.embed_model; $("#s-chunk").value = v.chunk_size;
  $("#s-overlap-on").checked = v.chunk_overlap > 0; $("#s-overlap").value = v.chunk_overlap; $("#s-overlap").disabled = v.chunk_overlap === 0;
  $("#s-ctx").value = v.context_chars;
  $("#s-rerank-on").checked = v.rerank_enabled; $("#s-rerank").value = v.rerank_model; $("#s-rerank-n").value = v.rerank_candidates;
  $("#s-graph-on").checked = v.graph_expansion;
  $("#s-enrich-on").checked = v.enrich_enabled; $("#s-hours").value = v.enrich_hours || "";
  $("#s-llm").value = summaryModel(v); llmNote();
  $("#s-g-high").value = v.gate_dense_high; $("#s-g-min").value = v.gate_dense_min; $("#s-g-cov").value = v.gate_lex_coverage;
  $("#s-g-all").value = v.gate_all_terms_min;
  $("#s-g-rr").value = v.gate_rerank_min; $("#s-g-mode").value = v.gate_mode; $("#s-g-lex").value = v.gate_lexical;
}
// The summary model: the one chosen here, else the one running (installed by setup), else the default.
const summaryModel = (v) => v.enrich_model || settings.options.llms.find((m) => m.file === settings.llm.current)?.name || settings.llm.default;
const llmLabel = (m) => m.file.replace(/-Q\d.*\.gguf$/i, "").replace(/-it$/, "");
function llmNote() {
  const m = settings.options.llms.find((x) => x.name === $("#s-llm").value);
  $("#s-llm-note").textContent = settings.llm.external
    ? `Set by KS_LLM_URL (another local server): ${settings.llm.current || "its own model"}.`
    : m ? m.note.split("; ").slice(1).join("; ") + "." + (m.installed ? "" : " Not on this computer yet: use Download below, or it downloads when saved.")
      + (m.name !== summaryModel(settings.values) ? " Every document is summarised and labelled again by the new model." : "") : "";
}
$("#s-llm").addEventListener("change", llmNote);
function showSettings(s) {
  settings = s;
  const rec = (name, key) => (s.recommended[key] === name ? " · recommended" : "");
  $("#s-model").replaceChildren(...s.options.embed_models.map((m) => el("option", { value: m.name, textContent: `${m.name} — ${m.dim}d, ~${m.size_mb} MB, ${m.license}${rec(m.name, "embed_model")}` })));
  $("#s-rerank").replaceChildren(...s.options.rerankers.map((m) => el("option", { value: m.name, textContent: `${m.name} — ~${m.size_mb} MB, ${m.license}, ${m.note}${rec(m.name, "rerank_model")}` })));
  $("#s-llm").replaceChildren(...s.options.llms.map((m) => el("option", { value: m.name,
    textContent: `${llmLabel(m)} — ${m.note.split(";")[0]}${m.name === s.llm.default ? " · recommended" : ""}` })));
  $("#s-llm").disabled = s.llm.external;
  fillSettings(s.values);
  const st = Object.entries(s.model_status).map(([k, v]) => `${k}: ${v}`).join(" · ");
  const ready = Object.values(s.model_status).every((v) => v === "ready");
  $("#s-model-status").replaceChildren(st ? (ready ? "Models ready" : "Models: " + st) : "", st && ready ? info(st) : "",
    s.thresholds_tuned ? "" : (st ? " · " : "") + "Evidence thresholds not yet tuned for this model; run the evaluation to tune them.");
  const ix = s.index;
  $("#reindex-info").textContent = `${ix.indexed}/${ix.documents} documents indexed with the current settings · ${ix.outdated} outdated · ${ix.pending} pending · ${ix.failed} failed`;
  const b = $("#settings-banner");
  b.hidden = !ix.outdated; b.className = "outcome index_coverage_incomplete";
  b.textContent = ix.outdated ? `${ix.outdated} document(s) were indexed with different settings. Reindex them so search uses the new settings everywhere.` : "";
}
async function loadSettings() { showSettings(await api("/api/admin/settings")); loadModels(); }

// Every model with a Download button, so a model is fetched before it is chosen, not when it is first needed.
const BUSY = /^(downloading|loading|starting)/;
async function loadModels() {
  const all = await api("/api/admin/models");
  for (const [kind, select] of [["embed", "#s-model"], ["rerank", "#s-rerank"], ["llm", "#s-llm"]]) {
    const rows = all[kind];
    $(`#ml-${kind}-sum`).textContent = `Models on this computer: ${rows.filter((m) => m.downloaded).length} of ${rows.length}`
      + (rows.some((m) => BUSY.test(m.state)) ? " · working…" : "");
    $(`#ml-${kind}`).replaceChildren(...rows.map((m) => {
      let status;
      if (BUSY.test(m.state)) status = el("span", { className: "busy" }, el("span", { className: "spinner" }), m.state.startsWith("downloading") ? "Downloading…" : "Loading…");
      else if (m.state.startsWith("error")) status = el("span", { className: "error-text", textContent: m.state, title: m.state });
      else if (m.in_use) status = el("span", { className: "badge st-indexed", textContent: "in use" });
      else if (m.downloaded) status = el("span", { className: "muted", textContent: "downloaded" });
      const cell = el("td", { className: "actions-cell" }, status || "");
      if (!m.downloaded && !BUSY.test(m.state)) {
        const b = el("button", { type: "button", className: "small", textContent: m.state.startsWith("error") ? "Retry" : "Download" });
        b.addEventListener("click", async () => {
          b.disabled = true;
          try { await api("/api/admin/models/download", { method: "POST", json: { kind, name: m.name } }); }
          catch (err) { $("#settings-msg").textContent = err.message; }
          loadModels();
        });
        cell.append(b);
      }
      return el("tr", {}, el("td", {}, kind === "llm" ? m.name : m.name.split("/").pop()), el("td", { className: "num muted", textContent: m.size }), cell);
    }));
    for (const o of $(select).options) {  // mark the choices that would have to download first
      o.dataset.base ??= o.textContent.replace(" · not downloaded", "");
      const m = rows.find((r) => r.name === o.value);
      o.textContent = o.dataset.base + (m && !m.downloaded ? " · not downloaded" : "");
    }
  }
}
$("#s-overlap-on").addEventListener("change", (e) => { $("#s-overlap").disabled = !e.target.checked; if (e.target.checked && +$("#s-overlap").value === 0) $("#s-overlap").value = 150; });
$("#settings-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const v = {
    embed_model: $("#s-model").value, chunk_size: +$("#s-chunk").value,
    chunk_overlap: $("#s-overlap-on").checked ? +$("#s-overlap").value : 0, context_chars: +$("#s-ctx").value,
    rerank_enabled: $("#s-rerank-on").checked, rerank_model: $("#s-rerank").value, rerank_candidates: +$("#s-rerank-n").value,
    graph_expansion: $("#s-graph-on").checked, enrich_enabled: $("#s-enrich-on").checked, enrich_hours: $("#s-hours").value.trim(),
    ...($("#s-llm").disabled || $("#s-llm").value === summaryModel(settings.values) ? {} : { enrich_model: $("#s-llm").value }),
    gate_dense_high: +$("#s-g-high").value, gate_dense_min: +$("#s-g-min").value, gate_lex_coverage: +$("#s-g-cov").value, gate_all_terms_min: +$("#s-g-all").value,
    gate_rerank_min: +$("#s-g-rr").value, gate_mode: $("#s-g-mode").value, gate_lexical: $("#s-g-lex").value,
  };
  const changed = Object.fromEntries(Object.entries(v).filter(([k, x]) => x !== settings.values[k]));
  if (!Object.keys(changed).length) { $("#settings-msg").textContent = "No changes."; return; }
  try { showSettings(await api("/api/admin/settings", { method: "PUT", json: changed })); $("#settings-msg").textContent = "Saved."; }
  catch (err) { $("#settings-msg").textContent = "Not saved: " + err.message; }
});
$("#s-reset").addEventListener("click", () => fillSettings(settings.defaults));
for (const [id, all] of [["#reindex-outdated", false], ["#reindex-all", true]])
  $(id).addEventListener("click", async () => {
    const r = await api("/api/admin/reindex", { method: "POST", json: { all } });
    $("#reindex-msg").textContent = `${r.queued} document(s) queued. Progress is shown on the Documents and Index status tabs.`;
    loadSettings();
  });

// ---------------------------------------------------------------- logs (admin): activity + warnings
// Activity (trace.py): recent calls like a browser's Network tab; click one for request, response and timing.
let logView = "activity", actCalls = [], actOpen = null, actPane = "summary";
function showLogView(view) {
  logView = view;
  for (const b of $$("#log-views button")) b.setAttribute("aria-selected", String(b.dataset.view === view));
  $("#view-activity").hidden = $("#act-actions").hidden = view !== "activity";
  $("#view-logs").hidden = $("#log-actions").hidden = view !== "logs";
  clearInterval(pollTimer);
  if (view === "logs") loadLogs();
  else { loadActivity(); pollTimer = setInterval(() => { if ($("#act-live").checked) loadActivity(); }, 3000); }
}
$$("#log-views button").forEach((b) => b.addEventListener("click", () => showLogView(b.dataset.view)));
const fmtMs = (ms) => ms >= 1000 ? `${(ms / 1000).toFixed(ms >= 10000 ? 0 : 1)} s` : `${Math.round(ms)} ms`;
const fmtTime = (iso) => new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const CALL_KIND = { http: "API", mcp: "AI assistant", llm: "Local model", index: "Indexing" };
function tokensText(t) {
  if (!t || t.prompt == null) return "";
  return `${t.prompt} → ${t.output ?? "?"}`;
}
async function loadActivity() {
  const r = await api(`/api/admin/activity?kind=${encodeURIComponent($("#act-kind").value)}`);
  actCalls = r.calls;
  renderActivity();
}
function renderActivity() {
  const q = $("#act-filter").value.trim().toLowerCase();
  const rows = actCalls.filter((c) => !q || `${c.title} ${c.user} ${c.status} ${c.detail}`.toLowerCase().includes(q));
  $("#act-rows").replaceChildren(...rows.map((c) => {
    const tr = el("tr", { className: "act-row" + (c.id === actOpen ? " selected" : "") },
      el("td", { className: "figures", textContent: fmtTime(c.time) }),
      el("td", {}, el("div", { className: "act-call" }, el("span", { className: "act-kind k-" + c.kind, textContent: CALL_KIND[c.kind] || c.kind }),
        el("span", { className: "act-name", textContent: c.title })),
        c.parent ? el("div", { className: "meta", textContent: `part of #${c.parent}` }) : ""),
      el("td", { textContent: c.user || (c.kind === "llm" || c.kind === "index" ? "background" : "—") }),
      el("td", {}, el("span", { className: "badge " + (c.status === "ok" ? "ok" : "st-failed"), textContent: c.status === "ok" ? "ok" : c.status })),
      el("td", { className: "num figures", textContent: fmtMs(c.ms) }),
      el("td", { className: "num figures", textContent: tokensText(c.tokens) }));
    tr.tabIndex = 0;
    tr.addEventListener("click", () => openCall(c.id));
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter") openCall(c.id); });
    return tr;
  }));
  $("#act-empty").hidden = rows.length > 0;
  $("#act-msg").textContent = `${rows.length} of ${actCalls.length} call(s)`;
}
let actCall = null;
async function openCall(id) {
  try { actCall = await api(`/api/admin/activity/${id}`); }
  catch (err) { $("#act-msg").textContent = err.message; return; }
  actOpen = id;
  $("#act-detail").hidden = false; $("#activity").classList.add("with-detail");
  $("#act-title").textContent = `#${actCall.id} ${actCall.title}`;
  showPane(actPane); renderActivity();
}
function pretty(x) {
  if (x == null) return "(nothing kept)";
  return typeof x === "string" ? x : JSON.stringify(x, null, 2);
}
function showPane(pane) {
  actPane = pane;
  for (const b of $$("#act-tabs button")) b.setAttribute("aria-selected", String(b.dataset.pane === pane));
  const c = actCall;
  if (pane !== "summary") {
    const text = pretty(c[pane]);
    const copy = el("button", { type: "button", className: "small", textContent: "Copy" });
    copy.addEventListener("click", () => navigator.clipboard.writeText(text).then(() => (copy.textContent = "Copied"), () => {}));
    $("#act-pane").replaceChildren(el("div", { className: "act-copy" }, copy), el("pre", { className: "act-body", textContent: text }));
    return;
  }
  const kv = [["Kind", CALL_KIND[c.kind] || c.kind], ["By", c.user || "background"], ["Status", c.status],
    ["Started", new Date(c.time).toLocaleString()], ["Took", fmtMs(c.ms)]];
  if (c.detail) kv.push(["Detail", c.detail]);
  if (c.tokens && c.tokens.prompt != null) kv.push(["Tokens", `${c.tokens.prompt} read, ${c.tokens.output} written`
    + (c.tokens.per_second ? ` (${c.tokens.per_second} tokens/s writing)` : "")]);
  const list = el("dl", { className: "kv act-kv" }, ...kv.flatMap(([k, v]) => [el("dt", { textContent: k }), el("dd", { textContent: v })]));
  const parts = [list];
  if (c.parent) {
    const a = el("button", { type: "button", className: "link", textContent: `#${c.parent}` });
    a.addEventListener("click", () => openCall(c.parent));
    parts.push(el("p", { className: "meta" }, "Made while serving ", a));
  }
  const kids = actCalls.filter((x) => x.parent === c.id);
  if (kids.length) parts.push(el("div", { className: "act-kids" }, el("h3", { textContent: "Calls made during this one" }),
    ...kids.map((k) => { const b = el("button", { type: "button", className: "link", textContent: `#${k.id} ${k.title} · ${fmtMs(k.ms)}` });
      b.addEventListener("click", () => openCall(k.id)); return el("div", {}, b); })));
  if (c.steps.length) {  // waterfall: each step as a bar, in the order the steps ran
    const total = c.steps.reduce((a, [, ms]) => a + ms, 0) || 1;
    let at = 0;
    parts.push(el("h3", { className: "act-h", textContent: "Where the time went" }), el("div", { className: "wf" },
      ...c.steps.map(([name, ms]) => {
        const bar = el("span", { className: "wf-bar" });
        bar.style.marginLeft = `${(at / total) * 100}%`; bar.style.width = `${Math.max(0.5, (ms / total) * 100)}%`;
        at += ms;
        return el("div", { className: "wf-row" }, el("span", { className: "wf-name", textContent: name.replaceAll("_", " ") }),
          el("span", { className: "wf-track" }, bar), el("span", { className: "wf-ms figures", textContent: fmtMs(ms) }));
      })));
  }
  $("#act-pane").replaceChildren(...parts);
}
$$("#act-tabs button").forEach((b) => b.addEventListener("click", () => showPane(b.dataset.pane)));
$("#act-close").addEventListener("click", () => { actOpen = null; $("#act-detail").hidden = true; $("#activity").classList.remove("with-detail"); renderActivity(); });
$("#act-kind").addEventListener("change", loadActivity);
$("#act-filter").addEventListener("input", renderActivity);
$("#act-refresh").addEventListener("click", loadActivity);
$("#act-clear").addEventListener("click", async () => {
  await api("/api/admin/activity", { method: "DELETE" });
  actOpen = null; $("#act-detail").hidden = true; $("#activity").classList.remove("with-detail"); loadActivity();
});

// ---------------------------------------------------------------- database (admin, read-only; dbview.py)
const fmtBytes = (b) => b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : b >= 1e6 ? `${(b / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1e3))} kB`;
const DB_PAGE = 50;
let dbTable = "", dbCols = [], dbRows = [], dbOffset = 0, dbTotal = 0, dbSort = "", dbDesc = false, dbPane = "rows";
async function loadDatabase() {
  let o;
  try { o = await api("/api/admin/db"); } catch (err) { $("#db-info").textContent = err.message; return; }
  $("#db-info").textContent = `PostgreSQL ${o.version} · ${o.mode} · database “${o.database}”, ${fmtBytes(o.bytes)} · extensions: `
    + o.extensions.map((e) => `${e.name} ${e.version}`).join(", ");
  $("#db-tables").replaceChildren(...o.tables.map((t) => {
    const b = el("button", { type: "button", className: "db-table" + (t.name === dbTable ? " selected" : "") },
      el("span", { className: "db-tname", textContent: t.name }),
      el("span", { className: "meta figures", textContent: t.kind === "view" ? "view"
        : `${t.estimated ? "~" : ""}${t.rows.toLocaleString()} rows · ${fmtBytes(t.bytes)}` }));
    b.addEventListener("click", () => openTable(t.name));
    return el("li", {}, b);
  }));
  $("#db-views button[data-view=sql]").disabled = !o.sql_available;
  $("#db-views button[data-view=sql]").title = o.sql_available ? "" : "Needs a read-only database role: set KS_READONLY_DATABASE_URL";
  if (!dbTable && o.tables.length) openTable(o.tables.some((t) => t.name === "documents") ? "documents" : o.tables[0].name);
  else if (dbTable) loadRows();
}
function openTable(name) {
  if (name !== dbTable) { dbOffset = 0; dbSort = ""; dbDesc = false; $("#db-contains").value = ""; $("#db-col").replaceChildren(); }
  dbTable = name;
  $$(".db-table").forEach((b) => b.classList.toggle("selected", b.querySelector(".db-tname").textContent === name));
  $("#db-name").textContent = name;
  $("#db-row").hidden = true;
  showDbPane(dbPane);
}
function showDbPane(pane) {
  dbPane = pane;
  for (const b of $$("#db-panes button")) b.setAttribute("aria-selected", String(b.dataset.pane === pane));
  $("#db-pane-rows").hidden = pane !== "rows";
  $("#db-pane-structure").hidden = pane !== "structure";
  if (pane === "rows") loadRows(); else loadStructure();
}
$$("#db-panes button").forEach((b) => b.addEventListener("click", () => showDbPane(b.dataset.pane)));
async function loadRows() {
  const q = new URLSearchParams({ offset: dbOffset, limit: DB_PAGE, sort: dbSort, desc: dbDesc });
  if ($("#db-col").value && $("#db-contains").value.trim()) { q.set("column", $("#db-col").value); q.set("contains", $("#db-contains").value.trim()); }
  let r;
  try { r = await api(`/api/admin/db/tables/${encodeURIComponent(dbTable)}/rows?${q}`); }
  catch (err) { $("#db-range").textContent = err.message; return; }
  dbCols = r.columns; dbRows = r.rows; dbTotal = r.total;
  if (!$("#db-col").options.length) $("#db-col").replaceChildren(...dbCols.map((c) => el("option", { value: c, textContent: c })));
  $("#db-cols").replaceChildren(...dbCols.map((c) => {
    const th = el("th", { scope: "col" }, el("button", { type: "button", className: "db-sort", textContent: c + (dbSort === c ? (dbDesc ? " ↓" : " ↑") : "") }));
    th.querySelector("button").addEventListener("click", () => { dbDesc = dbSort === c ? !dbDesc : false; dbSort = c; dbOffset = 0; loadRows(); });
    return th;
  }));
  $("#db-rows").replaceChildren(...r.rows.map((row, i) => {
    const tr = el("tr", { className: "act-row" }, ...row.map((v) => el("td", { className: v === null ? "muted" : "", textContent: v === null ? "null" : String(v) })));
    tr.tabIndex = 0;
    tr.addEventListener("click", () => openRow(i));
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter") openRow(i); });
    return tr;
  }));
  $("#db-empty").hidden = r.rows.length > 0;
  const end = dbOffset + r.rows.length;
  $("#db-range").textContent = r.rows.length ? `${(dbOffset + 1).toLocaleString()}–${end.toLocaleString()} of ${dbTotal == null ? "many" : dbTotal.toLocaleString()}` : "";
  $("#db-prev").disabled = dbOffset === 0;
  $("#db-next").disabled = dbTotal != null ? end >= dbTotal : r.rows.length < DB_PAGE;
  $("#db-meta").textContent = "";
}
async function openRow(i) {  // the same row again, uncut
  const q = new URLSearchParams({ offset: dbOffset + i, limit: 1, sort: dbSort, desc: dbDesc, full: true });
  if ($("#db-col").value && $("#db-contains").value.trim()) { q.set("column", $("#db-col").value); q.set("contains", $("#db-contains").value.trim()); }
  const r = await api(`/api/admin/db/tables/${encodeURIComponent(dbTable)}/rows?${q}`);
  const row = r.rows[0] || dbRows[i];
  $("#db-row-values").replaceChildren(...r.columns.flatMap((c, k) => [el("dt", { textContent: c }),
    el("dd", {}, el("pre", { className: "db-value" + (row[k] === null ? " muted" : ""), textContent: row[k] === null ? "null" : String(row[k]) }))]));
  $("#db-row").hidden = false;
}
$("#db-row-close").addEventListener("click", () => { $("#db-row").hidden = true; });
$("#db-filter").addEventListener("submit", (e) => { e.preventDefault(); dbOffset = 0; loadRows(); });
$("#db-prev").addEventListener("click", () => { dbOffset = Math.max(0, dbOffset - DB_PAGE); loadRows(); });
$("#db-next").addEventListener("click", () => { dbOffset += DB_PAGE; loadRows(); });
async function loadStructure() {
  const s = await api(`/api/admin/db/tables/${encodeURIComponent(dbTable)}`);
  const table = (head, rows) => el("table", { className: "data" }, el("thead", {}, el("tr", {}, ...head.map((h) => el("th", { textContent: h })))),
    el("tbody", {}, ...rows.map((r) => el("tr", {}, ...r.map((v) => el("td", { className: "mono small", textContent: v ?? "" }))))));
  const parts = [el("p", { className: "meta", textContent: `${s.kind}, ${fmtBytes(s.bytes)} with its indexes` }),
    el("h3", { className: "act-h", textContent: "Columns" }),
    table(["Name", "Type", "Required", "Default"], s.columns.map((c) => [c.name, c.type, c.not_null ? "yes" : "", c.generated ? "(computed)" : c.default]))];
  if (s.indexes.length) parts.push(el("h3", { className: "act-h", textContent: "Indexes" }), table(["Name", "Definition", "Size"], s.indexes.map((i) => [i.name, i.definition, fmtBytes(i.bytes)])));
  if (s.constraints.length) parts.push(el("h3", { className: "act-h", textContent: "Constraints" }), table(["Name", "Definition"], s.constraints.map((c) => [c.name, c.definition])));
  if (s.definition) parts.push(el("h3", { className: "act-h", textContent: "View definition" }), el("pre", { className: "act-body", textContent: s.definition }));
  $("#db-pane-structure").replaceChildren(...parts);
}
function showDbView(view) {
  for (const b of $$("#db-views button")) b.setAttribute("aria-selected", String(b.dataset.view === view));
  $("#db-view-tables").hidden = view !== "tables";
  $("#db-view-sql").hidden = view !== "sql";
}
$$("#db-views button").forEach((b) => b.addEventListener("click", () => showDbView(b.dataset.view)));
let sqlResult = null;
async function runSql() {
  const msg = $("#db-sql-msg");
  msg.className = "meta"; msg.textContent = "Running…";
  let r;
  try { r = await api("/api/admin/db/query", { method: "POST", json: { sql: $("#db-sql").value } }); }
  catch (err) {
    msg.className = "sql-error"; msg.textContent = err.message;
    sqlResult = null; $("#db-sql-wrap").hidden = $("#db-sql-copy").hidden = true; $("#db-sql-empty").hidden = true; return;
  }
  sqlResult = r;
  const numeric = r.columns.map((_, k) => r.rows.length > 0 && r.rows.every((row) => row[k] === null || typeof row[k] === "number"));
  $("#db-sql-cols").replaceChildren(...r.columns.map((c, k) => el("th", { scope: "col", className: numeric[k] ? "num" : "", textContent: c })));
  $("#db-sql-rows").replaceChildren(...r.rows.map((row) => el("tr", {}, ...row.map((v, k) => el("td", {
    className: (v === null ? "muted " : "") + (numeric[k] ? "num" : ""), textContent: v === null ? "null" : String(v),
    title: v !== null && String(v).length > 40 ? String(v) : "" })))));
  const has = r.columns.length > 0;
  $("#db-sql-wrap").hidden = !has; $("#db-sql-copy").hidden = !r.rows.length; $("#db-sql-empty").hidden = has;
  if (!has) $("#db-sql-empty").textContent = r.message || "Done.";
  msg.className = "meta";
  msg.textContent = has ? `${r.rows.length.toLocaleString()} row${r.rows.length === 1 ? "" : "s"}${r.more ? " (the first 500)" : ""} · ${fmtMs(r.ms)}` : "";
}
$("#db-sql-form").addEventListener("submit", (e) => { e.preventDefault(); runSql(); });
$("#db-sql").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); runSql(); } });
$$("#db-sql-examples .chip").forEach((b) => b.addEventListener("click", () => { $("#db-sql").value = b.dataset.sql; runSql(); }));
$("#db-sql-copy").addEventListener("click", () => {
  const cell = (v) => { const t = v === null ? "" : String(v); return /[",\n]/.test(t) ? `"${t.replaceAll('"', '""')}"` : t; };
  const csv = [sqlResult.columns, ...sqlResult.rows].map((row) => row.map(cell).join(",")).join("\n");
  navigator.clipboard.writeText(csv).then(() => { $("#db-sql-copy").textContent = "Copied"; setTimeout(() => ($("#db-sql-copy").textContent = "Copy as CSV"), 1500); }, () => {});
});
$("#db-list-toggle").addEventListener("click", () => {
  const hide = $("#db-view-tables").classList.toggle("tables-hidden");
  const b = $("#db-list-toggle");
  b.setAttribute("aria-expanded", String(!hide)); b.title = b.ariaLabel = hide ? "Show the table list" : "Hide the table list";
});
$("#db-refresh").addEventListener("click", loadDatabase);

let logText = "";
async function loadLogs() {
  const r = await api(`/api/admin/logs?level=${$("#log-level").value}`);
  const head = Object.entries(r.diagnostics).map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join("\n");
  const lines = r.records.map((x) => `${x.time} ${x.level} ${x.logger} ${x.message}${x.traceback ? "\n" + x.traceback.trimEnd() : ""}`);
  logText = `Ferret diagnostics (${new Date().toISOString()})\n${head}\n\n` +
    (lines.length ? lines.join("\n") : "(no log records at this level since the app started)");
  $("#log-text").textContent = logText;
  $("#log-msg").textContent = `${r.records.length} record(s)`;
}
$("#log-level").addEventListener("change", loadLogs);
$("#log-refresh").addEventListener("click", loadLogs);
$("#log-copy").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(logText); $("#log-msg").textContent = "Copied — paste it into your message."; }
  catch {  // clipboard blocked: select the text so Ctrl+C works
    const range = document.createRange(); range.selectNodeContents($("#log-text"));
    const sel = getSelection(); sel.removeAllRanges(); sel.addRange(range);
    $("#log-msg").textContent = "Text selected — press Ctrl+C to copy.";
  }
});
$("#log-download").addEventListener("click", () => {
  const a = el("a", { href: URL.createObjectURL(new Blob([logText], { type: "text/plain" })), download: "ferret-logs.txt" });
  document.body.append(a); a.click(); a.remove();
});

// ---------------------------------------------------------------- users & access (admin)
// ---------------------------------------------------------------- AI assistants (MCP)
// Copy-paste setup for each client, filled with this server's address and the token just created.
let mcpInfo = null, newToken = "", mcpClient = "claude-code";
const MCP_CLIENTS = {
  "claude-code": ["Claude Code", "Run this in a terminal, then restart Claude Code. Check with /mcp.",
    (i, t) => `claude mcp add --transport http ferret ${i.mcp_url} --header "Authorization: Bearer ${t}"`],
  "claude-desktop": ["Claude Desktop", "Claude Desktop → Settings → Developer → Edit Config: add this to claude_desktop_config.json and restart Claude. "
    + "It starts the ferret command on that computer, so Ferret must be installed there.",
    (i, t) => JSON.stringify({ mcpServers: { "ferret": { command: i.command, args: ["mcp"], env: { KS_URL: i.url, KS_TOKEN: t } } } }, null, 2)],
  cursor: ["Cursor", "Cursor → Settings → MCP → Add new server, or put this in ~/.cursor/mcp.json.",
    (i, t) => JSON.stringify({ mcpServers: { "ferret": { url: i.mcp_url, headers: { Authorization: `Bearer ${t}` } } } }, null, 2)],
  vscode: ["VS Code", "Put this in .vscode/mcp.json (one project) or run “MCP: Open User Configuration” (all projects).",
    (i, t) => JSON.stringify({ servers: { "ferret": { type: "http", url: i.mcp_url, headers: { Authorization: `Bearer ${t}` } } } }, null, 2)],
  other: ["Other", "Any MCP client that supports Streamable HTTP. The Claude skill in skills/ferret reads KS_URL and KS_TOKEN.",
    (i, t) => `MCP URL:  ${i.mcp_url}\nHeader:   Authorization: Bearer ${t}\n\nKS_URL=${i.url}\nKS_TOKEN=${t}`],
};
function showMcpConfig() {
  $("#mcp-clients").replaceChildren(...Object.entries(MCP_CLIENTS).map(([k, [label]]) => {
    const b = el("button", { type: "button", textContent: label });
    b.setAttribute("role", "tab");
    b.setAttribute("aria-selected", String(k === mcpClient));
    b.addEventListener("click", () => { mcpClient = k; showMcpConfig(); });
    return b;
  }));
  const [, where, make] = MCP_CLIENTS[mcpClient];
  $("#mcp-where").textContent = where;
  $("#mcp-config").textContent = mcpInfo ? make(mcpInfo, newToken || "<paste your token>") : "";
  $("#mcp-msg").textContent = newToken ? "" : "Create a token first, or paste one in place of <paste your token>.";
}
async function loadAssistants() {
  mcpInfo = await api("/api/tokens");
  showMcpConfig();
  $("#tok-rows").replaceChildren(...mcpInfo.tokens.map((t) => {
    const x = el("button", { className: "link", type: "button", textContent: "Revoke" });
    x.addEventListener("click", async () => {
      if (!confirm(`Revoke “${t.label}”? Assistants using it stop working.`)) return;
      await api(`/api/tokens/${t.id}`, { method: "DELETE" }); loadAssistants();
    });
    const day = (d) => new Date(d).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
    // the same token, with more or fewer rights; assistants see the new tool list when they reconnect
    const access = el("select", { ariaLabel: `Access of ${t.label}`, className: "compact" },
      el("option", { value: "read", textContent: "Read only", selected: t.read_only }),
      el("option", { value: "write", textContent: "Read and make changes", selected: !t.read_only }));
    access.addEventListener("change", async () => {
      try { await api(`/api/tokens/${t.id}`, { method: "PATCH", json: { allow_changes: access.value === "write" } }); }
      catch (err) { alert(err.message); }
      loadAssistants();
    });
    return el("tr", {}, el("td", {}, el("strong", { textContent: t.label })),
      el("td", {}, access), el("td", { textContent: day(t.created_at) }),
      el("td", { textContent: day(t.expires_at) }), el("td", { className: "actions-cell" }, x));
  }));
  $("#tok-empty").hidden = mcpInfo.tokens.length > 0;
}
$("#tok-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try {
    newToken = (await api("/api/tokens", { method: "POST", json: { label: f.get("label"), days: Number(f.get("days")), allow_changes: !!f.get("allow_changes") } })).token;
    $("#tok-value").textContent = `${newToken.slice(0, 6)}••••••••${newToken.slice(-4)}`;
    $("#tok-copied").textContent = ""; $("#tok-new").hidden = false; $("#tok-msg").textContent = "";
    e.target.reset(); loadAssistants();
  } catch (err) { $("#tok-msg").textContent = err.message; }
});
$("#tok-copy").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(newToken); $("#tok-copied").textContent = "Copied."; }
  catch { prompt("Copy the token (Ctrl+C):", newToken); }
});
$("#mcp-copy").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText($("#mcp-config").textContent); $("#mcp-msg").textContent = "Copied."; }
  catch { getSelection().selectAllChildren($("#mcp-config")); $("#mcp-msg").textContent = "Text selected — press Ctrl+C to copy."; }
});

// Users & access: three sub-tabs. Groups are created and deleted under Groups; everywhere else they are picked
// from the existing ones (a menu for a grant, ticks for a user's groups), so a typo never makes a new group.
let groupNames = [];
function adminSub(name) {
  $$("#admin-tabs .subtab").forEach((b) => b.setAttribute("aria-selected", b.dataset.sub === name));
  for (const x of ["collections", "users", "groups"]) $("#adm-" + x).hidden = x !== name;
  $("#admin-msg").textContent = "";
}
$$("#admin-tabs .subtab").forEach((b) => b.addEventListener("click", () => adminSub(b.dataset.sub)));
// A user's groups: a button showing the chosen names that opens a list of ticks.
function groupPicker(chosen, onChange) {
  const label = el("span");
  const ticks = groupNames.map((g) => el("label", { className: "check" }, el("input", { type: "checkbox", value: g, checked: chosen.includes(g) }), g));
  const value = () => ticks.map((t) => t.firstChild).filter((x) => x.checked).map((x) => x.value);
  const show = () => { label.textContent = value().join(", ") || "No groups"; };
  const box = el("details", { className: "picker" }, el("summary", {}, label),
    el("div", { className: "picker-pop" }, ...(ticks.length ? ticks : [el("span", { className: "muted small", textContent: "No groups yet: create them under Groups." })])));
  box.addEventListener("change", () => { show(); onChange?.(); });
  show();
  return { node: box, value };
}
document.addEventListener("click", (e) => { $$("details.picker[open]").forEach((d) => { if (!d.contains(e.target)) d.open = false; }); });
let newUserGroups = null;
async function loadAdmin() {
  const [cols, users, groups] = await Promise.all([api("/api/admin/collections"), api("/api/admin/users"), api("/api/admin/groups")]);
  groupNames = groups;
  newUserGroups = groupPicker([]);
  $("#new-user-groups").replaceChildren(newUserGroups.node);
  $("#col-rows").replaceChildren(...cols.map((c) => {
    const grants = el("td");
    for (const g of c.grants) {
      const x = el("button", { className: "icon", type: "button", title: `Remove access for ${g.group}` }, icon("close"));
      x.setAttribute("aria-label", `Remove access for ${g.group}`);
      x.addEventListener("click", async () => { await api(`/api/admin/collections/${c.id}/grants/${encodeURIComponent(g.group)}`, { method: "DELETE" }); loadAdmin(); refreshMe(); });
      grants.append(el("span", { className: "grant" }, `${g.group} (${g.can_write ? "read & write" : "read"})`, x));
    }
    if (!c.grants.length) grants.append(el("span", { className: "muted", textContent: "administrators only" }));
    const free = groups.filter((g) => !c.grants.some((x) => x.group === g));
    if (!free.length) return el("tr", {}, el("td", {}, el("strong", { textContent: c.name }), el("div", { className: "muted small", textContent: c.description })),
      el("td", { className: "num", textContent: c.documents }), grants,
      el("td", { className: "muted small", textContent: groups.length ? "Every group has access" : "Create a group first (Groups)" }));
    const grp = el("select", { ariaLabel: `Group to give access to ${c.name}` }, ...free.map((g) => el("option", { value: g, textContent: g })));
    const mode = el("select", { ariaLabel: "Access" }, el("option", { value: "read", textContent: "read" }), el("option", { value: "write", textContent: "read & write" }));
    const add = el("button", { className: "small", type: "button", textContent: "Give access" });
    add.addEventListener("click", async () => {
      try { await api(`/api/admin/collections/${c.id}/grants`, { method: "PUT", json: { group: grp.value, can_write: mode.value === "write" } }); loadAdmin(); refreshMe(); }
      catch (err) { $("#admin-msg").textContent = err.message; }
    });
    return el("tr", {}, el("td", {}, el("strong", { textContent: c.name }), el("div", { className: "muted small", textContent: c.description })),
      el("td", { className: "num", textContent: c.documents }), grants, el("td", {}, el("div", { className: "grant-form" }, grp, mode, add)));
  }));
  $("#user-rows").replaceChildren(...users.map((u) => {
    const save = el("button", { className: "small", type: "button", textContent: "Save", hidden: true });
    const pick = groupPicker(u.groups, () => { save.hidden = false; });
    save.addEventListener("click", async () => {
      try { await api(`/api/admin/users/${u.id}`, { method: "PATCH", json: { groups: pick.value() } }); $("#admin-msg").textContent = `Updated ${u.username}; they were signed out so the change applies at once.`; loadAdmin(); }
      catch (err) { $("#admin-msg").textContent = err.message; }
    });
    const toggle = el("button", { className: "link", type: "button", textContent: u.disabled ? "Enable" : "Disable" });
    toggle.addEventListener("click", async () => {
      try { await api(`/api/admin/users/${u.id}`, { method: "PATCH", json: { disabled: !u.disabled } }); loadAdmin(); }
      catch (err) { $("#admin-msg").textContent = err.message; }
    });
    return el("tr", {}, el("td", {}, el("strong", { textContent: u.username }), u.disabled ? el("span", { className: "badge", textContent: "disabled" }) : ""),
      el("td", {}, el("div", { className: "grant-form" }, pick.node, save)),
      el("td", { textContent: u.is_admin ? "Administrator" : "Member" }), el("td", { className: "actions-cell" }, toggle));
  }));
  $("#group-rows").replaceChildren(...groups.map((g) => {
    const members = users.filter((u) => u.groups.includes(g)).map((u) => u.username);
    const del = el("button", { className: "link danger", type: "button", textContent: "Delete" });
    del.addEventListener("click", async () => {
      if (!confirm(`Delete the group “${g}”?` + (members.length ? ` ${members.join(", ")} lose the access it gave.` : ""))) return;
      try { await api(`/api/admin/groups/${encodeURIComponent(g)}`, { method: "DELETE" }); loadAdmin(); refreshMe(); }
      catch (err) { $("#admin-msg").textContent = err.message; }
    });
    return el("tr", {}, el("td", {}, el("strong", { textContent: g })),
      el("td", { textContent: members.join(", ") || "—" }),
      el("td", { textContent: cols.flatMap((c) => c.grants.filter((x) => x.group === g).map((x) => `${c.name} (${x.can_write ? "read & write" : "read"})`)).join(", ") || "—" }),
      el("td", { className: "actions-cell" }, del));
  }));
  $("#groups-empty").hidden = groups.length > 0;
}
$("#col-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try { await api("/api/admin/collections", { method: "POST", json: { name: f.get("name"), description: f.get("description") } }); e.target.reset(); loadAdmin(); refreshMe(); }
  catch (err) { $("#admin-msg").textContent = err.message; }
});
$("#group-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try { await api("/api/admin/groups", { method: "POST", json: { name: new FormData(e.target).get("name") } }); e.target.reset(); $("#admin-msg").textContent = "Group created. Give it access under Collections and add users under Users."; loadAdmin(); }
  catch (err) { $("#admin-msg").textContent = err.message; }
});
$("#user-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try {
    await api("/api/admin/users", { method: "POST", json: { username: f.get("username"), password: f.get("password"), is_admin: !!f.get("is_admin"), groups: newUserGroups.value() } });
    e.target.reset(); $("#admin-msg").textContent = "User created."; loadAdmin();
  } catch (err) { $("#admin-msg").textContent = err.message; }
});

boot();
