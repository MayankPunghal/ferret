// Knowledge Search UI. Plain ES module, no build step. All document text is inserted with
// textContent (never innerHTML) so indexed content cannot inject markup.
const $ = (s) => document.querySelector(s);
const el = (tag, props = {}, ...kids) => {
  const n = Object.assign(document.createElement(tag), props);
  for (const k of kids) n.append(k);
  return n;
};
let me = null;
let pollTimer = null;

async function api(path, opts = {}) {
  const headers = { "x-ks-csrf": "1", ...(opts.json ? { "content-type": "application/json" } : {}) };
  const r = await fetch(path, { ...opts, headers, body: opts.json ? JSON.stringify(opts.json) : opts.body });
  if (r.status === 401 && path !== "/api/auth/login") { showLogin(); throw new Error("signed out"); }
  const body = r.headers.get("content-type")?.includes("json") ? await r.json() : null;
  if (!r.ok && !(body && body.outcome)) {
    const d = body?.detail;
    throw new Error(typeof d === "string" ? d : d?.code ? `${d.code}: ${d.detail}` : `HTTP ${r.status}`);
  }
  return body;
}

function showLogin() { $("#app-view").hidden = true; $("#login-view").hidden = false; }

async function boot() {
  try { me = await api("/api/me"); } catch { return showLogin(); }
  $("#login-view").hidden = true; $("#app-view").hidden = false;
  $("#who").textContent = me.username + (me.is_admin ? " (admin)" : "");
  const scope = $("#scope");
  scope.replaceChildren(el("legend", { textContent: "Collections" }));
  for (const c of me.collections) {
    const cb = el("input", { type: "checkbox", value: c.id, checked: true });
    scope.append(el("label", { className: "chip", title: c.description }, cb, " " + c.name));
  }
  const sel = $("#upload-collection");
  sel.replaceChildren(...me.collections.filter((c) => c.can_write).map((c) => el("option", { value: c.id, textContent: c.name })));
  $("#upload-form").hidden = sel.options.length === 0;
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try {
    await api("/api/auth/login", { method: "POST", json: { username: f.get("username"), password: f.get("password") } });
    $("#login-error").textContent = ""; e.target.reset(); boot();
  } catch (err) { $("#login-error").textContent = err.message; }
});
$("#logout").addEventListener("click", async () => { await api("/api/auth/logout", { method: "POST" }).catch(() => {}); showLogin(); });

document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach((t) => t.removeAttribute("aria-current"));
  b.setAttribute("aria-current", "page");
  for (const t of ["search", "documents", "status"]) $("#tab-" + t).hidden = t !== b.dataset.tab;
  clearInterval(pollTimer);
  if (b.dataset.tab === "documents") { loadDocs(); pollTimer = setInterval(loadDocs, 3000); }
  if (b.dataset.tab === "status") loadStatus();
}));

// Snippets come back with \x02 / \x03 as highlight delimiters; render them as <mark> safely.
function highlighted(text) {
  const frag = document.createDocumentFragment();
  text.split(/(\x02[^\x03]*\x03)/).forEach((part) => {
    if (part.startsWith("\x02")) frag.append(el("mark", { textContent: part.slice(1, -1) }));
    else if (part) frag.append(part);
  });
  return frag;
}

function sourceHref(r) {
  return `/api/documents/${r.document_id}/source` + (r.page ? `#page=${r.page}` : "");
}

const BASIS = { exact_identifier: "exact identifier", strong_semantic: "strong semantic match", semantic_and_lexical_agreement: "semantic + keyword agreement" };

function resultItem(r) {
  const loc = [r.collection, r.heading_path, r.page ? `page ${r.page}` : "", `v${r.version_no}`].filter(Boolean).join(" · ");
  const badges = el("span", { className: "badges" });
  for (const b of r.match_basis) badges.append(el("span", { className: "badge ok", textContent: BASIS[b] || b }));
  if (r.outdated_version) badges.append(el("span", { className: "badge warn", textContent: "newer version not yet indexed" }));
  if (r.metadata?.version || r.metadata?.effective_date)
    badges.append(el("span", { className: "badge", textContent: [r.metadata.version && `version ${r.metadata.version}`, r.metadata.effective_date && `effective ${r.metadata.effective_date}`].filter(Boolean).join(", ") }));
  const view = el("button", { className: "link", textContent: "View passage" });
  view.addEventListener("click", () => openPassage(r.chunk_id));
  return el("li", { className: "result" },
    el("div", { className: "r-head" }, el("span", { className: "rank", textContent: r.rank }), el("strong", { textContent: r.title }), badges),
    el("div", { className: "meta", textContent: loc }),
    el("p", { className: "snippet" }, highlighted(r.snippet)),
    el("div", { className: "r-actions" }, view, el("a", { href: sourceHref(r), target: "_blank", rel: "noopener", textContent: "Open source" })),
  );
}

$("#search-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const ids = [...document.querySelectorAll("#scope input:checked")].map((c) => +c.value);
  const all = ids.length === me.collections.length;
  const out = $("#outcome");
  out.hidden = false; out.className = "outcome"; out.textContent = "Searching…";
  $("#results").replaceChildren(); $("#weak").hidden = true; $("#timing").textContent = "";
  if (!ids.length) { out.textContent = "Select at least one collection."; return; }
  let res;
  try { res = await api("/api/search", { method: "POST", json: { query: $("#q").value, collection_ids: all ? null : ids } }); }
  catch (err) { out.className = "outcome bad"; out.textContent = "Search failed: " + err.message; return; }
  out.className = "outcome " + res.outcome;
  out.replaceChildren(el("strong", { textContent: res.message }));
  if (res.reason_codes?.length) out.append(el("div", { className: "meta", textContent: "Reason codes: " + res.reason_codes.join(", ") }));
  if (res.coverage && !res.coverage.complete)
    out.append(el("div", { className: "meta", textContent: `Index coverage in this scope: ${res.coverage.indexed}/${res.coverage.documents} documents current (${res.coverage.pending} pending, ${res.coverage.failed} failed, ${res.coverage.outdated} outdated).` }));
  const strong = res.results.filter((r) => r.supported), weak = res.results.filter((r) => !r.supported);
  $("#results").replaceChildren(...strong.map(resultItem));
  if (weak.length) {
    $("#weak").hidden = false; $("#weak").open = false;
    $("#weak summary").textContent = `${weak.length} lower-relevance match${weak.length > 1 ? "es" : ""} (not adequate evidence)`;
    $("#weak-results").replaceChildren(...weak.map(resultItem));
  }
  if (res.timings_ms) $("#timing").textContent = `${Math.round(res.timings_ms.total)} ms · model ${res.index.embedding_model}`;
});

async function openPassage(id) {
  const p = await api(`/api/chunks/${id}`);
  $("#d-title").textContent = p.doc_title;
  $("#d-loc").textContent = [p.collection, p.heading_path, p.page ? `page ${p.page}` : "", `version ${p.version_no}`].filter(Boolean).join(" · ");
  $("#d-body").replaceChildren(...p.context.map((c) => el("p", { className: c.id === p.id ? "hit" : "ctx", textContent: c.text })));
  $("#d-open").href = `/api/documents/${p.document_id}/source` + (p.page ? `#page=${p.page}` : "");
  $("#d-uri").textContent = p.source_uri ? "Recorded location: " + p.source_uri : "";
  $("#drawer").hidden = false;
  $("#d-body .hit")?.scrollIntoView({ block: "center" });
}
$("#d-close").addEventListener("click", () => { $("#drawer").hidden = true; });

$("#upload-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = $("#upload-msg");
  msg.textContent = "Uploading…";
  try {
    const r = await api("/api/documents", { method: "POST", body: new FormData(e.target) });
    msg.textContent = r.outcome === "unchanged" ? "Unchanged — identical to the latest version, not reprocessed." : "Queued for indexing.";
    e.target.querySelector("input[type=file]").value = "";
    loadDocs();
  } catch (err) { msg.textContent = "Rejected: " + err.message; }
});

async function loadDocs() {
  const docs = await api("/api/documents");
  $("#docs").replaceChildren(...docs.map((d) => {
    const actions = el("td");
    if (d.can_write && d.status === "failed") {
      const b = el("button", { className: "link", textContent: "Retry" });
      b.addEventListener("click", async () => { await api(`/api/documents/${d.id}/retry`, { method: "POST" }); loadDocs(); });
      actions.append(b);
    }
    if (d.can_write) {
      const b = el("button", { className: "link danger", textContent: "Delete" });
      b.addEventListener("click", async () => { if (confirm(`Delete "${d.title}" and all its versions?`)) { await api(`/api/documents/${d.id}`, { method: "DELETE" }); loadDocs(); } });
      actions.append(" ", b);
    }
    const status = el("td", {}, el("span", { className: "badge st-" + d.status, textContent: d.status }));
    if (d.error_code) status.append(el("div", { className: "meta error", textContent: `${d.error_code}: ${d.error_detail || ""}` }));
    const ver = d.indexed_version && d.indexed_version !== d.latest_version ? `v${d.latest_version} (indexed v${d.indexed_version})` : `v${d.latest_version}`;
    return el("tr", {},
      el("td", {}, el("a", { href: `/api/documents/${d.id}/source`, target: "_blank", rel: "noopener", textContent: d.title }), el("div", { className: "meta", textContent: d.source_key })),
      el("td", { textContent: d.collection }), status, el("td", { textContent: ver }), el("td", { textContent: d.chunks }),
      el("td", { textContent: new Date(d.updated_at).toLocaleString() }), actions);
  }));
}

async function loadStatus() {
  const s = await api("/api/status");
  const c = s.coverage;
  const box = $("#status");
  box.replaceChildren(
    el("p", { className: c.complete ? "outcome evidence_found" : "outcome index_coverage_incomplete", textContent: c.complete ? "All documents in your scope are indexed with the active model." : "Index coverage is incomplete in your scope; searches cannot establish absence of information." }),
    el("table", { className: "docs" },
      el("thead", {}, el("tr", {}, ...["Collection", "Documents", "Indexed", "Pending", "Failed"].map((h) => el("th", { textContent: h })))),
      el("tbody", {}, ...s.collections.map((r) => el("tr", {}, ...[r.name, r.documents, r.indexed, r.pending, r.failed].map((v) => el("td", { textContent: v })))))),
    el("h3", { textContent: "Active index" }),
    el("dl", { className: "kv" }, ...Object.entries(s.index).flatMap(([k, v]) => [el("dt", { textContent: k }), el("dd", { textContent: v })])),
  );
  if (me.is_admin) {
    const a = await api("/api/admin/status");
    box.append(el("h3", { textContent: "Administration" }), el("pre", { className: "pre", textContent: JSON.stringify(a, null, 2) }));
  }
}

boot();
