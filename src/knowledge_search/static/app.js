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
  $("#drawer").hidden = true; $("#outcome").hidden = true; $("#weak").hidden = true; $("#q").value = "";
  for (const id of ["#results", "#weak-results", "#upload-list", "#docs", "#status", "#d-body"]) $(id).replaceChildren();
  $("#timing").textContent = "";
  renderMe();
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
  for (const t of ["search", "documents", "status", "settings", "admin"]) $("#tab-" + t).hidden = t !== name;
  clearInterval(pollTimer);
  if (name === "documents") { loadDocs(); pollTimer = setInterval(loadDocs, 3000); }
  if (name === "status") loadStatus();
  if (name === "settings") loadSettings();
  if (name === "admin") loadAdmin();
}
$$(".tab").forEach((b) => b.addEventListener("click", () => openTab(b.dataset.tab)));

// ---------------------------------------------------------------- search
// Snippets come back with \x02 / \x03 as highlight delimiters; render them as <mark> safely.
function highlighted(text) {
  const frag = document.createDocumentFragment();
  text.split(/(\x02[^\x03]*\x03)/).forEach((part) => {
    if (part.startsWith("\x02")) frag.append(el("mark", { textContent: part.slice(1, -1) }));
    else if (part) frag.append(part);
  });
  return frag;
}

const BASIS = {
  exact_identifier: "exact identifier", strong_semantic: "strong semantic match",
  semantic_and_lexical_agreement: "semantic + keyword agreement", reranker_judged_relevant: "reranker: relevant",
  reranker_and_similarity_agree: "reranker + similarity agree",
};

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
    el("div", { className: "r-actions" }, view, el("a", { href: r.source_url, target: "_blank", rel: "noopener", textContent: "Open source" })),
  );
}

$("#search-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const ids = $$("#scope input:checked").map((c) => +c.value);
  const all = ids.length === me.collections.length;
  const out = $("#outcome");
  out.hidden = false; out.className = "outcome"; out.replaceChildren(el("span", { className: "spinner" }), " Searching…");
  const slow = setTimeout(() => { if (out.textContent.includes("Searching")) out.append(el("span", { className: "meta", textContent: " — still working (the first search after a start or a model change loads the model)" })); }, 3000);
  $("#results").replaceChildren(); $("#weak").hidden = true; $("#timing").textContent = "";
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
    $("#weak summary").textContent = `${weak.length} lower-relevance match${weak.length > 1 ? "es" : ""} (not adequate evidence)`;
    $("#weak-results").replaceChildren(...weak.map(resultItem));
  }
  if (res.timings_ms) $("#timing").textContent = `${Math.round(res.timings_ms.total)} ms · ${res.index.embedding_model}${res.index.reranker ? " · reranker " + res.index.reranker : ""}`;
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

// ---------------------------------------------------------------- documents
const dz = $("#dropzone"), fileInput = $("#file-input");
dz.addEventListener("click", () => fileInput.click());
dz.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
["dragenter", "dragover"].forEach((t) => dz.addEventListener(t, (e) => { e.preventDefault(); dz.classList.add("over"); }));
["dragleave", "drop"].forEach((t) => dz.addEventListener(t, (e) => { e.preventDefault(); dz.classList.remove("over"); }));
dz.addEventListener("drop", (e) => uploadFiles([...e.dataTransfer.files]));
fileInput.addEventListener("change", () => { uploadFiles([...fileInput.files]); fileInput.value = ""; });

async function uploadFiles(files) {
  const list = $("#upload-list");
  for (const f of files) {
    const status = el("span", { className: "meta", textContent: "uploading…" });
    list.prepend(el("li", {}, el("strong", { textContent: f.name }), " ", status));
    const fd = new FormData();
    fd.append("file", f); fd.append("collection_id", $("#upload-collection").value);
    const uri = $("#upload-form [name=source_uri]").value; if (uri) fd.append("source_uri", uri);
    try {
      const r = await api("/api/documents", { method: "POST", body: fd });
      status.textContent = r.outcome === "unchanged" ? "unchanged (identical to the indexed version)" : "queued for indexing";
      status.className = "meta ok-text";
    } catch (err) { status.textContent = "rejected — " + err.message; status.className = "meta error"; }
  }
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
    if (d.can_write) {
      const b = el("button", { className: "link danger", textContent: "Delete" });
      b.addEventListener("click", async () => { if (confirm(`Delete "${d.title}" and all its versions?`)) { await api(`/api/documents/${d.id}`, { method: "DELETE" }); loadDocs(); } });
      actions.append(" ", b);
    }
    const status = el("td", {}, el("span", { className: "badge st-" + d.status, textContent: d.status }));
    if (d.status === "processing" && d.stage) {
      const pct = d.progress_total ? Math.round((100 * d.progress_done) / d.progress_total) : null;
      const label = d.stage === "embedding" && d.progress_total ? `embedding ${d.progress_done}/${d.progress_total} chunks` : d.stage;
      const bar = el("div", { className: "progress" + (pct === null ? " indeterminate" : "") }, el("span", { style: pct === null ? "" : `width:${pct}%` }));
      status.append(el("div", { className: "meta", textContent: label }), bar);
    } else if (d.status === "pending") {
      status.append(el("div", { className: "meta", textContent: d.queue_position ? `waiting — ${d.queue_position} ahead in queue` : "waiting to start" }));
    }
    if (d.needs_reindex) status.append(" ", el("span", { className: "badge warn", textContent: "outdated settings" }));
    if (d.error_code) status.append(el("div", { className: "meta error", textContent: `${d.error_code}: ${d.error_detail || ""}` }));
    const ver = d.indexed_version && d.indexed_version !== d.latest_version ? `v${d.latest_version} (indexed v${d.indexed_version})` : `v${d.latest_version}`;
    return el("tr", {},
      el("td", {}, el("a", { href: `/api/documents/${d.id}/source`, target: "_blank", rel: "noopener", textContent: d.title }), el("div", { className: "meta", textContent: d.source_key })),
      el("td", { textContent: d.collection }), status, el("td", { textContent: ver }), el("td", { textContent: d.chunks }),
      el("td", { textContent: new Date(d.updated_at).toLocaleString() }), actions);
  }));
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
  $("#s-rerank-on").checked = v.rerank_enabled; $("#s-rerank").value = v.rerank_model; $("#s-rerank-n").value = v.rerank_candidates;
  $("#s-g-high").value = v.gate_dense_high; $("#s-g-min").value = v.gate_dense_min; $("#s-g-cov").value = v.gate_lex_coverage;
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
    chunk_overlap: $("#s-overlap-on").checked ? +$("#s-overlap").value : 0,
    rerank_enabled: $("#s-rerank-on").checked, rerank_model: $("#s-rerank").value, rerank_candidates: +$("#s-rerank-n").value,
    gate_dense_high: +$("#s-g-high").value, gate_dense_min: +$("#s-g-min").value, gate_lex_coverage: +$("#s-g-cov").value,
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
