// Knowledge Search UI. Plain ES module, no build step. All document text is inserted with
// textContent (never innerHTML) so indexed content cannot inject markup.
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const el = (tag, props = {}, ...kids) => {
  const n = Object.assign(document.createElement(tag), props);
  for (const k of kids) n.append(k);
  return n;
};
let me = null;
let pollTimer = null;
let topicFilter = null;  // Summaries tab: only the documents the local model gave this topic

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
  $("#timing").textContent = "";
  renderMe();
  searchHint();
}

async function searchHint() {
  const box = $("#search-hint");
  box.hidden = false;
  const st = await api("/api/status").catch(() => null);
  const c = st?.coverage;
  $("#hint-scope").textContent = c ? `${c.indexed.toLocaleString()} document${c.indexed === 1 ? "" : "s"} indexed in ${st.collections.length} collection${st.collections.length === 1 ? "" : "s"} you can read` + (c.pending ? ` · ${c.pending} still being indexed` : "") + (c.failed ? ` · ${c.failed} failed` : "") : "";
}

// Re-read the current user's collections/permissions without changing the visible tab.
async function refreshMe() { me = await api("/api/me"); renderMe(); }

function renderMe() {
  $("#who").textContent = me.username + (me.is_admin ? " (admin)" : "");
  $$(".admin-only").forEach((b) => { b.hidden = !me.is_admin; });
  const scope = $("#scope");
  scope.replaceChildren(el("legend", { textContent: "Collections" }));
  for (const c of me.collections) {
    const cb = el("input", { type: "checkbox", value: c.id, checked: true });
    scope.append(el("label", { className: "chip", title: c.description }, cb, " " + c.name));
  }
  if (!me.collections.length) scope.append(el("span", { className: "meta", textContent: me.is_admin ? "No collections yet — create one under Users & access." : "You have no access to any collection yet." }));
  const sel = $("#upload-collection");
  sel.replaceChildren(...me.collections.filter((c) => c.can_write).map((c) => el("option", { value: c.id, textContent: c.name })));
  $("#upload-form").hidden = sel.options.length === 0;
  $("#no-writable").hidden = sel.options.length > 0;
}

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
  for (const t of ["search", "documents", "summaries", "status", "settings", "admin", "logs"]) $("#tab-" + t).hidden = t !== name;
  clearInterval(pollTimer);
  if (name === "documents") { loadDocs(); pollTimer = setInterval(loadDocs, 3000); }
  if (name === "summaries") { loadSummaries(); pollTimer = setInterval(loadSummaries, 5000); }
  if (name === "status") loadStatus();
  if (name === "settings") loadSettings();
  if (name === "admin") loadAdmin();
  if (name === "logs") loadLogs();
}
$$(".tab").forEach((b) => b.addEventListener("click", () => { topicFilter = null; openTab(b.dataset.tab); }));

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
      if (/^[\s|:-]+$/.test(line)) continue;  // the |---|---| separator
      if (!table) { table = el("table"); out.push(el("div", { className: "table-wrap" }, table)); }
      table.append(el("tr", {}, ...line.trim().slice(1, -1).split("|").map((c) => el("td", {}, inline(c.trim())))));
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

function resultItem(r) {
  const loc = location(r.collection, r.heading_path, r.title, r.page, r.version_no);
  const badges = el("span", { className: "badges" });
  for (const b of r.match_basis) badges.append(el("span", { className: "badge ok", textContent: BASIS[b] || b }));
  if (r.signals.linked_from) badges.append(el("span", { className: "badge", textContent: "linked from " + r.signals.linked_from }));
  if (r.outdated_version) badges.append(el("span", { className: "badge warn", textContent: "newer version not yet indexed" }));
  if (r.metadata?.version || r.metadata?.effective_date)
    badges.append(el("span", { className: "badge", textContent: [r.metadata.version && `version ${r.metadata.version}`, r.metadata.effective_date && `effective ${r.metadata.effective_date}`].filter(Boolean).join(", ") }));
  const view = el("button", { className: "secondary small", textContent: "View passage" });
  view.addEventListener("click", () => openPassage(r.chunk_id));
  const related = el("div", { className: "r-related", hidden: true });
  relatedDocs(related, r.document_id, r.chunk_id);
  return el("li", { className: "result" },
    el("div", { className: "r-head" }, el("span", { className: "rank", textContent: r.rank }),
      el("a", { className: "r-title", href: r.source_url, target: "_blank", rel: "noopener", title: "Open the document" }, docTitle(r.title, r.clearer_title)), badges),
    el("div", { className: "meta", textContent: loc }),
    el("p", { className: "snippet" }, inline(r.snippet.replace(/\s*\n\s*/g, " "))),
    writtenSummary(r),
    related,
    el("div", { className: "r-actions" }, view, el("a", { href: r.source_url, target: "_blank", rel: "noopener", textContent: "Open document" })),
  );
}

// Title, summary and topics written by the local language model (enrich.py): labelled as such and never
// shown as evidence. A clearer title is only written for vague ones ("Overview", a file name).
function docTitle(title, clearer) {
  return clearer ? el("strong", { textContent: clearer, title: `Title written by the local model; the document's own title is “${title}”` })
    : el("strong", { textContent: title });
}
function writtenSummary(r) {
  if (!r.summary) return "";
  const topics = r.topics.map((t) => {
    const b = el("button", { className: "chip topic", type: "button", textContent: t, title: "Documents with this topic" });
    b.addEventListener("click", () => { closeDrawer(); topicFilter = t; openTab("summaries"); });
    return b;
  });
  return el("div", { className: "r-summary" },
    el("p", { className: "rel-row" }, el("span", { className: "rel-label", textContent: "Summary (local model)", title: "Written by the local language model from the document; not evidence" }), el("span", { textContent: r.summary })),
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

$("#search-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const ids = $$("#scope input:checked").map((c) => +c.value);
  const all = ids.length === me.collections.length;
  const out = $("#outcome");
  $("#search-hint").hidden = true;
  out.hidden = false; out.className = "outcome"; out.replaceChildren(el("span", { className: "spinner" }), " Searching…");
  const slow = setTimeout(() => { if (out.textContent.includes("Searching")) out.append(el("span", { className: "meta", textContent: " — still working (the first search after a start or a model change loads the model)" })); }, 3000);
  $("#results").replaceChildren(); $("#weak").hidden = true; $("#timing").textContent = "";
  linksCache.clear();
  if (!ids.length) { out.textContent = "Select at least one collection."; return; }
  let res;
  try { res = await api("/api/search", { method: "POST", json: { query: $("#q").value, collection_ids: all ? null : ids } }); }
  catch (err) { clearTimeout(slow); out.className = "outcome bad"; out.textContent = "Search failed: " + err.message; return; }
  clearTimeout(slow);
  out.className = "outcome " + res.outcome;
  out.replaceChildren(el("strong", { textContent: res.message }));
  if (res.reason_codes?.length) out.append(el("div", { className: "meta", textContent: "Reason codes: " + res.reason_codes.join(", ") }));
  if (res.coverage && res.coverage.complete === false)
    out.append(el("div", { className: "meta", textContent: `Index coverage in this scope: ${res.coverage.indexed}/${res.coverage.documents} documents current (${res.coverage.pending} pending, ${res.coverage.failed} failed, ${res.coverage.outdated} outdated).` }));
  const strong = res.results.filter((r) => r.supported), weak = res.results.filter((r) => !r.supported);
  $("#results").replaceChildren(...strong.map(resultItem));
  if (weak.length) {
    $("#weak").hidden = false; $("#weak").open = !strong.length;
    $("#weak summary").textContent = strong.length
      ? `${weak.length} more match${weak.length > 1 ? "es" : ""} (not confirmed as evidence)`
      : `Closest matches — not confirmed as evidence (${weak.length})`;
    $("#weak-results").replaceChildren(...weak.map(resultItem));
  }
  if (res.timings_ms) $("#timing").textContent = `${Math.round(res.timings_ms.total)} ms · ${res.index.embedding_model}${res.index.reranker ? " · reranker " + res.index.reranker : ""}`;
});

async function openPassage(id) {
  const p = await api(`/api/chunks/${id}`);
  $("#d-title").replaceChildren(docTitle(p.doc_title, p.clearer_title));
  $("#d-loc").textContent = location(p.collection, p.heading_path, p.doc_title, p.page, p.version_no);
  $("#d-body").replaceChildren(...p.context.map((c) => {
    const where = c.id !== p.id && section(c.heading_path || "", p.doc_title);
    return el("div", { className: c.id === p.id ? "hit" : "ctx" },
      where && where !== section(p.heading_path || "", p.doc_title) ? el("div", { className: "ctx-loc meta", textContent: where }) : "",
      ...prose(c.text, p.media_type));
  }));
  $("#d-summary").replaceChildren(writtenSummary(p));
  $("#d-open").href = `/api/documents/${p.document_id}/view` + (p.page ? `#page=${p.page}` : "");
  $("#d-download").href = `/api/documents/${p.document_id}/source?download=1&version=indexed`;
  $("#d-uri").replaceChildren(...(/^https?:\/\//.test(p.source_uri || "")
    ? [el("a", { href: p.source_uri, target: "_blank", rel: "noopener noreferrer", textContent: "Original page ↗", title: p.source_uri })] : []));
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
    if (chunkId) b.addEventListener("click", () => openPassage(chunkId));
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
  section("Mentions (found automatically)", (links.mentions || []).map((m) => item(m.title, `${m.how} · ${m.collection}`, m.chunk_id)));
  section("Related by meaning (found automatically)", (links.similar || []).map((m) => item(m.title, `similarity ${m.similarity.toFixed(2)} · ${m.collection}`, m.chunk_id)));
  section("Depends on this (blast radius, 2 hops)", imp.nodes.map((n) => item(n.title, `${n.depth === 1 ? "directly" : "indirectly"} · ${KIND[n.kind] || n.kind} · ${n.collection}`, n.chunk_id)));
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
// inner ring, second-hop documents on the outer ring next to the document that connects them.
const SVG = "http://www.w3.org/2000/svg";
const sv = (tag, attrs = {}, ...kids) => {
  const n = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  for (const k of kids) n.append(k);
  return n;
};
async function drawGraph(box, docId, depth, hops) {
  hops.querySelectorAll("button").forEach((b, i) => b.setAttribute("aria-pressed", String(i + 1 === depth)));
  const g = await api(`/api/documents/${docId}/graph?depth=${depth}`).catch(() => null);
  if (!g || g.nodes.length < 2) { box.replaceChildren(); return; }
  const cx = 0, cy = 0, r1 = 120, r2 = 250;
  const pos = new Map();
  const ring1 = g.nodes.filter((n) => n.depth === 1), ring2 = g.nodes.filter((n) => n.depth === 2);
  pos.set(g.nodes[0].id, [cx, cy]);
  const angle = new Map();
  ring1.forEach((n, i) => { const a = (2 * Math.PI * i) / ring1.length - Math.PI / 2; angle.set(n.id, a); pos.set(n.id, [cx + r1 * Math.cos(a), cy + r1 * Math.sin(a)]); });
  const parent = (n) => { for (const e of g.edges) { if (e.source === n.id && angle.has(e.target)) return e.target; if (e.target === n.id && angle.has(e.source)) return e.source; } return null; };
  const groups = new Map();
  for (const n of ring2) { const k = parent(n); groups.set(k, [...(groups.get(k) || []), n]); }
  const sector = (2 * Math.PI) / Math.max(ring1.length, 1);
  for (const [k, ns] of groups) ns.forEach((n, i) => {
    const base = k ? angle.get(k) : (2 * Math.PI * i) / ns.length;
    const a = base + (ns.length > 1 ? (i / (ns.length - 1) - 0.5) * sector * 0.8 : 0);
    angle.set(n.id, a);
    pos.set(n.id, [cx + r2 * Math.cos(a), cy + r2 * 0.8 * Math.sin(a)]);
  });
  // labels: outer ring points outward (nothing beyond it); centre and inner ring below the node (above at the top)
  const labelAt = (n) => {
    const [x, y] = pos.get(n.id);
    if (n.depth === 0) return [x, y + 24, "middle"];
    if (n.depth === 1) return [x, y + (Math.sin(angle.get(n.id)) < -0.5 ? -14 : 22), "middle"];
    const c = Math.cos(angle.get(n.id)), sn = Math.sin(angle.get(n.id));
    return c > 0.35 ? [x + 12, y + 4, "start"] : c < -0.35 ? [x - 12, y + 4, "end"] : [x, y + (sn < 0 ? -14 : 22), "middle"];
  };
  const xs = [], ys = [];
  for (const n of g.nodes) {
    const [x, y] = pos.get(n.id), [lx, ly, anchor] = labelAt(n), w = 125;
    xs.push(x, anchor === "start" ? lx + w : anchor === "end" ? lx - w : lx - w / 2, anchor === "middle" ? lx + w / 2 : lx);
    ys.push(y - 14, ly + 6);
  }
  const x0 = Math.min(...xs) - 8, y0 = Math.min(...ys) - 8, W = Math.max(...xs) - x0 + 8, H = Math.max(...ys) - y0 + 8;
  const svg = sv("svg", { viewBox: `${x0} ${y0} ${W} ${H}`, role: "img", "aria-label": `Link graph of ${g.document.title}` },
    sv("defs", {}, sv("marker", { id: "arrow", viewBox: "0 0 10 10", refX: "16", refY: "5", markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse" },
      sv("path", { d: "M0,0 L10,5 L0,10 z", class: "g-arrow" }))));
  const pairs = new Map();  // one line per source→target, styled by its strongest relation
  for (const e of g.edges) pairs.set(e.source + ">" + e.target, [...(pairs.get(e.source + ">" + e.target) || []), e]);
  const title = (id) => g.nodes.find((n) => n.id === id).title;
  for (const es of pairs.values()) {
    const a = pos.get(es[0].source), b = pos.get(es[0].target), kinds = es.map((e) => e.kind);
    if (!a || !b) continue;
    const cls = kinds.includes("depends_on") ? "dep" : kinds.every((k) => k === "links_to") ? "link"
      : kinds.every((k) => k === "mentions") ? "men" : kinds.every((k) => k === "similar") ? "sim" : "rel";
    svg.append(sv("line", { x1: a[0], y1: a[1], x2: b[0], y2: b[1], class: `g-edge g-${cls}`, ...(cls === "sim" ? {} : { "marker-end": "url(#arrow)" }) },
      sv("title", {}, `${title(es[0].source)} ${kinds.map((k) => KIND[k] || k).join(", ")} ${title(es[0].target)}`)));
  }
  for (const n of g.nodes) {
    const [x, y] = pos.get(n.id);
    const label = n.title.length > 20 ? n.title.slice(0, 19) + "…" : n.title;
    const [lx, ly, anchor] = labelAt(n);
    const node = sv("g", { class: "g-node" + (n.depth === 0 ? " g-root" : ""), tabindex: "0", role: "button", "aria-label": `Open ${n.title}` },
      sv("title", {}, `${n.title} · ${n.collection}`), sv("circle", { cx: x, cy: y, r: n.depth === 0 ? 10 : 7 }),
      sv("text", { x: lx, y: ly, "text-anchor": anchor }, label));
    if (n.depth && n.chunk_id) {
      node.addEventListener("click", () => openPassage(n.chunk_id));
      node.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); openPassage(n.chunk_id); } });
    }
    svg.append(node);
  }
  box.replaceChildren(svg, el("p", { className: "meta g-legend" },
    el("span", { className: "g-key g-dep" }), " depends on  ", el("span", { className: "g-key g-link" }), " links to  ",
    el("span", { className: "g-key g-rel" }), " related / part of / other  ", el("span", { className: "g-key g-men" }), " mentions  ",
    el("span", { className: "g-key g-sim" }), " related in meaning",
    g.truncated ? " · showing the nearest 60 documents" : ""));
}
function closeDrawer() { $("#drawer").hidden = $("#backdrop").hidden = true; }
$("#d-close").addEventListener("click", closeDrawer);
$("#backdrop").addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("#drawer").hidden) closeDrawer();
  else if (e.key === "/" && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName) && !$("#tab-search").hidden) {
    e.preventDefault(); $("#q").focus(); $("#q").select();
  }
});

// ---------------------------------------------------------------- documents
const dz = $("#dropzone"), fileInput = $("#file-input");
dz.addEventListener("click", () => fileInput.click());
dz.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
["dragenter", "dragover"].forEach((t) => dz.addEventListener(t, (e) => { e.preventDefault(); dz.classList.add("over"); }));
["dragleave", "drop"].forEach((t) => dz.addEventListener(t, (e) => { e.preventDefault(); dz.classList.remove("over"); }));
dz.addEventListener("drop", (e) => uploadFiles([...e.dataTransfer.files]));
fileInput.addEventListener("change", () => { uploadFiles([...fileInput.files]); fileInput.value = ""; });
const folderInput = $("#folder-input");
$("#folder-button").addEventListener("click", (e) => { e.stopPropagation(); folderInput.click(); });
folderInput.addEventListener("change", () => { uploadFiles([...folderInput.files]); folderInput.value = ""; });
const SUPPORTED = /\.(md|markdown|txt|html?|docx|pptx|pdf)$/i;

// One line per upload (not per file): counts while it runs; only rejected files are listed by name.
async function uploadFiles(files) {
  const list = $("#upload-list");
  const other = files.filter((f) => !SUPPORTED.test(f.name));
  files = files.filter((f) => SUPPORTED.test(f.name));
  const skipped = other.length ? ` · skipped ${other.length} of other types (${[...new Set(other.map((f) => (f.name.split(".").pop() || "").toLowerCase()))].join(", ")})` : "";
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

async function loadDocs() {
  const docs = await api("/api/documents");
  if (me.is_admin && docs.some((d) => d.status === "pending" || d.status === "processing")) {
    const st = await api("/api/status").catch(() => ({}));
    const busy = Object.entries(st.models || {}).filter(([, v]) => !v.startsWith("ready") && v !== "not loaded");
    $("#model-banner").hidden = !busy.length;
    $("#model-banner").textContent = busy.map(([k, v]) => `${k} — ${v}. Indexing continues automatically when the model is ready; large models can take several minutes to download.`).join(" ");
  } else $("#model-banner").hidden = true;
  $("#docs").replaceChildren(...docs.map((d) => {
    const actions = el("td");
    if (d.can_write && d.status === "failed") {
      const b = el("button", { className: "link", textContent: "Retry" });
      b.addEventListener("click", async () => { await api(`/api/documents/${d.id}/retry`, { method: "POST" }); loadDocs(); });
      actions.append(b);
    }
    actions.append(el("a", { href: `/api/documents/${d.id}/source?download=1&version=latest`, textContent: "Download", title: "The original file" }), " ");
    if (d.can_write) {
      const b = el("button", { className: "link danger", textContent: "Delete" });
      b.addEventListener("click", async () => { if (confirm(`Delete "${d.title}" and all its versions?`)) { await api(`/api/documents/${d.id}`, { method: "DELETE" }); loadDocs(); } });
      actions.append(" ", b);
    }
    const status = el("td", {}, el("span", { className: "badge st-" + d.status, textContent: d.status }));
    const again = d.adding_summary ? "adding its summary to the passages · " : "";
    if (d.status === "processing" && d.stage) {
      const pct = d.progress_total ? Math.round((100 * d.progress_done) / d.progress_total) : null;
      const label = again + (d.stage === "embedding" && d.progress_total ? `embedding ${d.progress_done}/${d.progress_total} chunks` : d.stage);
      const bar = el("div", { className: "progress" + (pct === null ? " indeterminate" : "") }, el("span", { style: pct === null ? "" : `width:${pct}%` }));
      status.append(el("div", { className: "meta", textContent: label }), bar);
    } else if (d.status === "pending") {
      status.append(el("div", { className: "meta", textContent: again + (d.queue_position ? `waiting — ${d.queue_position} ahead in queue` : "waiting to start") }));
    } else if (d.summary_stage) status.append(...summaryStage(d));
    if (d.needs_reindex) status.append(" ", el("span", { className: "badge warn", textContent: "outdated settings" }));
    if (d.error_code) status.append(el("div", { className: "meta error", textContent: `${d.error_code}: ${d.error_detail || ""}` }));
    const ver = d.indexed_version && d.indexed_version !== d.latest_version ? `v${d.latest_version} (indexed v${d.indexed_version})` : `v${d.latest_version}`;
    return el("tr", {},
      el("td", {}, sourceLink(d), el("div", { className: "meta", textContent: d.source_key })),
      el("td", { textContent: fileType(d.media_type) }), el("td", { textContent: d.collection }), status, el("td", { textContent: ver }), el("td", { textContent: d.chunks }),
      el("td", { textContent: new Date(d.updated_at).toLocaleString() }), actions);
  }));
}

const TYPES = { "application/pdf": "PDF", "text/markdown": "Markdown", "text/plain": "Text", "text/html": "HTML",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "Word",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation": "PowerPoint" };
const fileType = (mt) => TYPES[mt] || mt || "";
const sourceLink = (d) => el("a", { href: `/api/documents/${d.id}/view`, target: "_blank", rel: "noopener", textContent: d.clearer_title || d.title,
  title: d.clearer_title ? `Title written by the local model; the document's own title is “${d.title}”` : "" });
function summaryStage(d) {  // after indexing: the local model's summary (optional)
  const out = [el("div", { className: "meta" + (d.summary_stage === "failed" ? " error" : ""), textContent: {
    waiting: "summary queued (after indexing)", writing: "writing summary…",
    done: "summarised", failed: "summary could not be written" }[d.summary_stage] })];
  if (d.summary_stage === "writing") out.push(el("div", { className: "progress indeterminate" }, el("span")));
  return out;
}

// ---------------------------------------------------------------- summaries
// What the local model wrote for each document: progress first, then the documents being written or waiting,
// then the summaries. Same permissions as the Documents tab.
async function loadSummaries() {
  const [docs, st] = await Promise.all([api("/api/documents"), api("/api/status").catch(() => ({}))]);
  const line = st.index?.summaries_by_local_model || "";
  const indexed = docs.filter((d) => d.status === "indexed");
  const done = indexed.filter((d) => d.summary_stage === "done").length;
  const head = [el("p", { className: "meta", textContent: line })];
  if (indexed.length && indexed.some((d) => d.summary_stage)) {
    head.push(el("div", { className: "progress" }, el("span", { style: `width:${Math.round((100 * done) / indexed.length)}%` })));
  }
  const order = { writing: 0, failed: 1, waiting: 2, done: 3 };
  const topic = topicFilter?.toLowerCase();
  if (topic) {
    const all = el("button", { className: "link", type: "button", textContent: "Show all" });
    all.addEventListener("click", () => { topicFilter = null; loadSummaries(); });
    const find = el("button", { className: "link", type: "button", textContent: `Search the text for “${topicFilter}”` });
    find.addEventListener("click", () => { const q = topicFilter; topicFilter = null; openTab("search"); $("#q").value = q; $("#search-form").requestSubmit(); });
    const n = indexed.filter((d) => (d.topics || []).some((x) => x.toLowerCase() === topic)).length;
    head.unshift(el("div", { className: "topic-filter" }, el("strong", { textContent: `Topic “${topicFilter}”: ${n} document${n === 1 ? "" : "s"}` }),
      " (topics are written by the local model) · ", all, " · ", find));
  }
  const rows = indexed.filter((d) => (d.summary_stage || d.summary_text) && (!topic || (d.topics || []).some((x) => x.toLowerCase() === topic)))
    .sort((a, b) => (order[a.summary_stage] ?? 3) - (order[b.summary_stage] ?? 3) || a.title.localeCompare(b.title));
  $("#summaries").replaceChildren(...head, rows.length ? el("table", { className: "docs summaries" },
    el("thead", {}, el("tr", {}, ...["Document", "Type", "Summary and topics", "Status"].map((h) => el("th", { textContent: h })))),
    el("tbody", {}, ...rows.map((d) => el("tr", {},
      el("td", {}, sourceLink(d), el("div", { className: "meta", textContent: d.collection })),
      el("td", { textContent: fileType(d.media_type) }),
      el("td", {}, writtenSummary({ summary: d.summary_text, topics: d.topics }) || el("span", { className: "meta", textContent: "—" })),
      el("td", {}, ...(d.summary_stage ? summaryStage(d) : [])))))) : el("p", { className: "empty", textContent: "No summaries yet." }));
}

// ---------------------------------------------------------------- status
async function loadStatus() {
  const s = await api("/api/status");
  const c = s.coverage;
  const box = $("#status");
  box.replaceChildren(
    el("p", { className: c.complete ? "outcome evidence_found" : "outcome index_coverage_incomplete", textContent: c.complete ? "All documents in your scope are indexed with the current settings." : `Index coverage is incomplete in your scope (${c.pending} pending, ${c.failed} failed, ${c.outdated} outdated); searches cannot establish absence of information.` }),
    el("table", { className: "docs" },
      el("thead", {}, el("tr", {}, ...["Collection", "Documents", "Indexed", "Pending", "Failed"].map((h) => el("th", { textContent: h })))),
      el("tbody", {}, ...s.collections.map((r) => el("tr", {}, ...[r.name, r.documents, r.indexed, r.pending, r.failed].map((v) => el("td", { textContent: v })))))),
    el("h3", { textContent: "Active configuration" }),
    el("dl", { className: "kv" }, ...Object.entries(s.index).flatMap(([k, v]) => [el("dt", { textContent: k.replaceAll("_", " ") }), el("dd", { textContent: v })])),
  );
  if (me.is_admin) {
    const a = await api("/api/admin/status");
    box.append(el("h3", { textContent: "Job queue" }), el("dl", { className: "kv" },
      ...Object.entries(a.jobs).flatMap(([k, v]) => [el("dt", { textContent: k }), el("dd", { textContent: v })]),
      el("dt", { textContent: "oldest queued job" }), el("dd", { textContent: a.oldest_queued_job_seconds ? Math.round(a.oldest_queued_job_seconds) + " s" : "—" })));
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
  $("#s-enrich-on").checked = v.enrich_enabled;
  $("#s-g-high").value = v.gate_dense_high; $("#s-g-min").value = v.gate_dense_min; $("#s-g-cov").value = v.gate_lex_coverage;
  $("#s-g-all").value = v.gate_all_terms_min;
  $("#s-g-rr").value = v.gate_rerank_min; $("#s-g-mode").value = v.gate_mode; $("#s-g-lex").value = v.gate_lexical;
}
function showSettings(s) {
  settings = s;
  $("#s-model").replaceChildren(...s.options.embed_models.map((m) => el("option", { value: m.name, textContent: `${m.name} — ${m.dim}d, ~${m.size_mb} MB, ${m.license}` })));
  $("#s-rerank").replaceChildren(...s.options.rerankers.map((m) => el("option", { value: m.name, textContent: `${m.name} — ~${m.size_mb} MB, ${m.license}, ${m.note}` })));
  fillSettings(s.values);
  const st = Object.entries(s.model_status).map(([k, v]) => `${k}: ${v}`).join(" · ");
  $("#s-model-status").textContent = (st ? "Model status: " + st + ". " : "") +
    (s.thresholds_tuned ? "" : "Evidence thresholds have not been tuned for this model yet — results may be stricter or looser than intended; run the evaluation to tune them.");
  const ix = s.index;
  $("#reindex-info").textContent = `${ix.indexed}/${ix.documents} documents indexed with the current settings · ${ix.outdated} outdated · ${ix.pending} pending · ${ix.failed} failed`;
  const b = $("#settings-banner");
  b.hidden = !ix.outdated; b.className = "outcome index_coverage_incomplete";
  b.textContent = ix.outdated ? `${ix.outdated} document(s) were indexed with different settings. Reindex them so search uses the new settings everywhere.` : "";
}
async function loadSettings() { showSettings(await api("/api/admin/settings")); }
$("#s-overlap-on").addEventListener("change", (e) => { $("#s-overlap").disabled = !e.target.checked; if (e.target.checked && +$("#s-overlap").value === 0) $("#s-overlap").value = 150; });
$("#settings-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const v = {
    embed_model: $("#s-model").value, chunk_size: +$("#s-chunk").value,
    chunk_overlap: $("#s-overlap-on").checked ? +$("#s-overlap").value : 0, context_chars: +$("#s-ctx").value,
    rerank_enabled: $("#s-rerank-on").checked, rerank_model: $("#s-rerank").value, rerank_candidates: +$("#s-rerank-n").value,
    graph_expansion: $("#s-graph-on").checked, enrich_enabled: $("#s-enrich-on").checked,
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

// ---------------------------------------------------------------- logs (admin)
let logText = "";
async function loadLogs() {
  const r = await api(`/api/admin/logs?level=${$("#log-level").value}`);
  const head = Object.entries(r.diagnostics).map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join("\n");
  const lines = r.records.map((x) => `${x.time} ${x.level} ${x.logger} ${x.message}${x.traceback ? "\n" + x.traceback.trimEnd() : ""}`);
  logText = `Knowledge Search diagnostics (${new Date().toISOString()})\n${head}\n\n` +
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
  const a = el("a", { href: URL.createObjectURL(new Blob([logText], { type: "text/plain" })), download: "knowledge-search-logs.txt" });
  document.body.append(a); a.click(); a.remove();
});

// ---------------------------------------------------------------- users & access (admin)
async function loadAdmin() {
  const [cols, users] = await Promise.all([api("/api/admin/collections"), api("/api/admin/users")]);
  $("#col-rows").replaceChildren(...cols.map((c) => {
    const grants = el("td");
    for (const g of c.grants) {
      const x = el("button", { className: "link danger", textContent: "×", title: "Remove access" });
      x.addEventListener("click", async () => { await api(`/api/admin/collections/${c.id}/grants/${encodeURIComponent(g.group)}`, { method: "DELETE" }); loadAdmin(); refreshMe(); });
      grants.append(el("span", { className: "chip" }, `${g.group} (${g.can_write ? "read & write" : "read"}) `, x), " ");
    }
    if (!c.grants.length) grants.append(el("span", { className: "meta", textContent: "admins only" }));
    const grp = el("input", { placeholder: "group", size: 10, ariaLabel: "Group" });
    const w = el("input", { type: "checkbox" });
    const add = el("button", { className: "secondary", textContent: "Grant" });
    add.addEventListener("click", async () => {
      if (!grp.value.trim()) return;
      try { await api(`/api/admin/collections/${c.id}/grants`, { method: "PUT", json: { group: grp.value.trim(), can_write: w.checked } }); loadAdmin(); refreshMe(); }
      catch (err) { $("#admin-msg").textContent = err.message; }
    });
    return el("tr", {}, el("td", {}, el("strong", { textContent: c.name }), el("div", { className: "meta", textContent: c.description })),
      el("td", { textContent: c.documents }), grants, el("td", {}, grp, " ", el("label", { className: "check" }, w, " write"), " ", add));
  }));
  $("#user-rows").replaceChildren(...users.map((u) => {
    const groups = el("input", { value: u.groups.join(", "), ariaLabel: `Groups of ${u.username}` });
    const save = el("button", { className: "secondary", textContent: "Save groups" });
    save.addEventListener("click", async () => {
      try { await api(`/api/admin/users/${u.id}`, { method: "PATCH", json: { groups: groups.value.split(",").map((s) => s.trim()).filter(Boolean) } }); $("#admin-msg").textContent = `Updated ${u.username}.`; loadAdmin(); }
      catch (err) { $("#admin-msg").textContent = err.message; }
    });
    const toggle = el("button", { className: "link" + (u.disabled ? "" : " danger"), textContent: u.disabled ? "Enable" : "Disable" });
    toggle.addEventListener("click", async () => {
      try { await api(`/api/admin/users/${u.id}`, { method: "PATCH", json: { disabled: !u.disabled } }); loadAdmin(); }
      catch (err) { $("#admin-msg").textContent = err.message; }
    });
    return el("tr", {}, el("td", { textContent: u.username + (u.disabled ? " (disabled)" : "") }), el("td", {}, groups, " ", save),
      el("td", { textContent: u.is_admin ? "admin" : "user" }), el("td", {}, toggle));
  }));
}
$("#col-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try { await api("/api/admin/collections", { method: "POST", json: { name: f.get("name"), description: f.get("description") } }); e.target.reset(); loadAdmin(); refreshMe(); }
  catch (err) { $("#admin-msg").textContent = err.message; }
});
$("#user-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try {
    await api("/api/admin/users", { method: "POST", json: { username: f.get("username"), password: f.get("password"), is_admin: !!f.get("is_admin"), groups: (f.get("groups") || "").split(",").map((s) => s.trim()).filter(Boolean) } });
    e.target.reset(); $("#admin-msg").textContent = "User created."; loadAdmin();
  } catch (err) { $("#admin-msg").textContent = err.message; }
});

boot();
