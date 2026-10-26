// site2md page: Website copies, Copy a website, Sign in, and one screen per copy (while it runs and after),
// with Overview, Pages and Log. Stand-alone, every request carries the one-time key from this page's address;
// inside Ferret (served under /crawler/) the administrator's own session is used instead.
"use strict";
const IN_KS = location.pathname.startsWith("/crawler");
const KEY = new URLSearchParams(location.search).get("k") || "";
const BASE = IN_KS ? "/api/admin/crawler/" : "/api/";
const AUTH = IN_KS ? { "x-ks-csrf": "1" } : { "X-Key": KEY };
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const el = (tag, props = {}, ...kids) => { const e = Object.assign(document.createElement(tag), props); e.append(...kids.filter((k) => k !== null && k !== undefined && k !== "")); return e; };
const btn = (label, fn, kind = "button") => { const b = el("button", { type: "button", className: kind, textContent: label }); b.addEventListener("click", fn); return b; };
const num = (n) => (n ?? 0).toLocaleString();
const plural = (n, one, many = one + "s") => `${num(n)} ${n === 1 ? one : many}`;
const ACTIVE = ["starting", "signin", "copying", "paused", "stopping"];
if (IN_KS) { document.body.classList.add("in-ks"); document.querySelector("#send h2").textContent = "Add to a collection"; }

async function api(path, body) {
  const r = await fetch(BASE + path, body === undefined ? { headers: AUTH }
    : { method: "POST", headers: { ...AUTH, "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || data.detail || `HTTP ${r.status}`);
  return data;
}
const q = (o) => new URLSearchParams(o).toString();
const fileHref = (name, file) => `${BASE}file?${q({ name, file, ...(IN_KS ? {} : { k: KEY }) })}`;

function show(screen) {
  $$("main > section").forEach((s) => { s.hidden = s.id !== `s-${screen}`; });
  window.scrollTo(0, 0);
}
$$("[data-go]").forEach((b) => b.addEventListener("click", () => loadHome()));
function ago(t) {
  if (!t) return "";
  const s = (Date.now() - t * 1000) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return new Date(t * 1000).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}
function minutes(s) { return s < 90 ? plural(s, "second") : s < 5400 ? plural(Math.round(s / 60), "minute") : `${(s / 3600).toFixed(1)} hours`; }
function clock(s) { const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, x = String(s % 60).padStart(2, "0"); return h ? `${h}:${String(m).padStart(2, "0")}:${x}` : `${m}:${x}`; }

// ---------------------------------------------------------------- Website copies
let landed = false;  // the first visit opens the copy that is running; later ones stay where you go
async function loadHome() {
  stopPolling();
  const d = await api("copies");
  const first = !landed;
  landed = true;
  if (first && d.run && ACTIVE.includes(d.run.state)) return openCopy(d.run.name);
  show("home");
  $("#open-copies").textContent = d.folder;
  $("#folder-path").value = d.folder;
  $("#empty").hidden = d.copies.length > 0;
  $("#copies").replaceChildren(...d.copies.map(copyRow));
}
function copyRow(c) {
  const cls = c.state === "done" && !c.failed ? "ok" : c.state === "done" ? "" : "warn";
  const status = c.state === "done" && c.failed ? `complete · ${num(c.failed)} failed` : c.status;
  const actions = el("div", { className: "row-actions" });
  if (c.left || c.updating) actions.append(btn(c.updating ? "Continue update" : "Continue", () => start({ name: c.name, mode: "continue" }), "primary"));
  actions.append(btn("Update", () => start({ name: c.name, mode: "update" })), btn("Open folder", () => api("open", { name: c.name })));
  const name = el("button", { type: "button", className: "name link-strong", textContent: c.name });
  name.addEventListener("click", () => openCopy(c.name));
  return el("li", {},
    el("div", {}, name, el("span", { className: `pill ${cls}`, textContent: status })),
    actions,
    el("div", { className: "sub" }, `${plural(c.pages, "page")}${c.files ? ` · ${plural(c.files, "attachment")}` : ""}${c.updated ? " · updated " + ago(c.updated) : ""} · `, el("span", { className: "mono", textContent: c.url })));
}
$("#open-copies").addEventListener("click", () => api("open", {}));
$("#new-copy").addEventListener("click", () => openNew());
$("#change-folder").addEventListener("click", () => { $("#folder-form").hidden = false; $("#folder-path").focus(); $("#folder-path").select(); });
$("#folder-cancel").addEventListener("click", () => { $("#folder-form").hidden = true; $("#folder-error").hidden = true; });
$("#folder-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try { await api("folder", { path: $("#folder-path").value }); $("#folder-form").hidden = true; $("#folder-error").hidden = true; loadHome(); }
  catch (x) { $("#folder-error").textContent = x.message; $("#folder-error").hidden = false; }
});

// ---------------------------------------------------------------- Copy a website
let checked = null, nameTouched = false, browserList = null;
async function openNew() {
  stopPolling();
  show("new");
  $("#new-form").reset(); $("#check").textContent = ""; $("#new-error").hidden = true;
  checked = null; nameTouched = false; $("#browser-row").hidden = true;
  $("#url").focus();
  if (!browserList) browserList = await api("browsers").catch(() => []);
  $("#channel").replaceChildren(...browserList.filter((b) => b.ready).map((b) => el("option", { value: b.channel, textContent: b.name })));
  $("#no-browser").hidden = $("#channel").options.length > 0;
}
$("#name").addEventListener("input", () => { nameTouched = true; });
let checkTimer = null;
$("#url").addEventListener("input", () => {
  clearTimeout(checkTimer);
  checked = null;
  const url = $("#url").value.trim();
  if (!url) { $("#check").textContent = ""; return; }
  checkTimer = setTimeout(() => checkUrl(url), 700);
});
async function checkUrl(url) {
  const line = $("#check");
  line.className = "check-line"; line.textContent = "Checking the address…";
  try {
    const c = await api("check", { url });
    if ($("#url").value.trim() !== url) return;
    checked = c;
    if (!nameTouched) $("#name").value = c.name;
    if (!c.ok) { line.className = "check-line bad"; line.textContent = c.note; }
    else if (c.signin) { line.className = "check-line signin"; line.textContent = "This site needs you to sign in. A browser window will open for that."; }
    else line.textContent = c.title ? `Found “${c.title}”.` : "Found the site.";
    syncBrowserRow();
  } catch (e) { line.className = "check-line bad"; line.textContent = e.message; }
}
const needsBrowser = () => !!(checked?.signin || $("#new-form [name=browser]").checked);
function syncBrowserRow() { $("#browser-row").hidden = !needsBrowser(); }
$("#new-form [name=browser]").addEventListener("change", syncBrowserRow);
$("#new-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target), err = $("#new-error");
  err.hidden = true;
  const url = f.get("url").trim();
  if (!url) { err.textContent = "Give the address of the site."; err.hidden = false; return; }
  if (!checked) await checkUrl(url);
  const lines = (s) => (s || "").split(/\n+/).map((x) => x.trim()).filter(Boolean);
  const options = {
    max_pages: Number(f.get("max_pages")) || 10000, prefixes: lines(f.get("prefixes")), follow: f.get("follow"),
    attachments: !!f.get("attachments"), browser: !!f.get("browser"), keep_html: !!f.get("keep_html"),
    ignore_robots: !!f.get("ignore_robots"), channel: needsBrowser() ? f.get("channel") || "" : "",
  };
  if (needsBrowser() && !options.channel) { err.textContent = "This needs a browser: install Google Chrome or Microsoft Edge."; err.hidden = false; return; }
  try { await start({ url: checked?.url || url, name: f.get("name").trim(), options, signin: !!checked?.signin, mode: "new" }, false); }
  catch (x) { err.textContent = x.message; err.hidden = false; }
});

// ---------------------------------------------------------------- One copy
let current = "", detail = null, tab = "overview", kind = "all", pollTimer = null, shown = 200;
function stopPolling() { clearTimeout(pollTimer); pollTimer = null; }
async function start(body, alertErrors = true) {
  try { const r = await api("start", body); openCopy(r.name); }
  catch (e) { if (alertErrors) alert(e.message); throw e; }
}
async function openCopy(name) {
  if (name !== current) { tab = "overview"; kind = "all"; $("#page-q").value = ""; $("#send").hidden = true; $("#send-state").textContent = ""; }
  current = name;
  await refresh();
}
async function refresh() {
  stopPolling();
  const d = detail = await api(`copy?${q({ name: current })}`);
  $$(".site-name").forEach((x) => { x.textContent = d.name; });
  if (d.state === "signin") { show("signin"); pollTimer = setTimeout(refresh, 1000); return; }
  if ($("#s-copy").hidden) show("copy");
  renderCopy(d);
  if (tab === "pages") loadPages();
  if (tab === "log") loadLog();
  if (d.live) pollTimer = setTimeout(refresh, tab === "overview" ? 1000 : 2000);
}
const STATE = {
  starting: () => "Starting…",
  copying: (d) => `Copying · ${plural(d.running, "page")} loading`,
  paused: () => "Paused. Pages being loaded finish; nothing new starts until you resume.",
  stopping: () => "Stopping and saving what was copied…",
  done: (d) => `Complete · ${minutes(d.elapsed)} of copying`,
  stopped: (d) => `Stopped · ${plural(d.counts.waiting, "page")} still to copy. Continue picks up where it stopped.`,
  "signin-expired": () => "Paused because the site started showing its sign-in page. Sign in again to continue.",
  failed: () => "Stopped with an error.",
  "not started": () => "Not started yet.",
};
const TILES = [
  ["saved", "Pages copied"], ["files", "Attachments"], ["waiting", "Waiting"], ["failed", "Failed"],
  ["skipped", "Skipped"], ["outside", "Links not followed"],
];
function renderCopy(d) {
  $("#copy-url").textContent = d.url;
  const st = $("#copy-state");
  st.replaceChildren(...(d.live ? [el("span", { className: "spinner small", ariaHidden: "true" })] : []), (STATE[d.state] || (() => d.state))(d));
  const notice = $("#copy-notice");
  notice.hidden = !(d.state === "failed" && d.error);
  notice.textContent = d.error || "";
  // actions
  const acts = [];
  if (d.live) {
    if (d.state === "copying") acts.push(btn("Pause", () => control("pause")));
    if (d.state === "paused") acts.push(btn("Resume", () => control("resume")));
    const stop = btn("Stop", () => control("stop"), "button danger"); stop.disabled = d.state === "stopping"; acts.push(stop);
  } else {
    if (d.counts.waiting && d.state !== "done") acts.push(btn(d.state === "signin-expired" ? "Sign in and continue" : "Continue", () => start({ name: d.name, mode: "continue", signin: d.state === "signin-expired" }), "primary"));
    acts.push(btn(IN_KS ? "Add to a collection" : "Send to Ferret", openSend, acts.length ? "button" : "primary"),
      btn("Update", () => start({ name: d.name, mode: "update" })), btn("Open folder", () => api("open", { name: d.name })));
    acts.push(btn("Delete", async () => {
      if (!confirm(`Delete the copy “${d.name}” (${plural(d.counts.saved, "page")}) from this computer? The website is not affected.`)) return;
      await api("delete", { name: d.name }); loadHome();
    }, "link danger"));
  }
  $("#copy-actions").replaceChildren(...acts);
  // tiles: each opens the matching pages
  const c = d.counts;
  $("#tiles").replaceChildren(...TILES.map(([k, label]) => {
    const t = el("button", { type: "button", className: `tile ${k}${c[k] && (k === "failed") ? " bad" : ""}${tab === "pages" && kind === k ? " on" : ""}` },
      el("span", { className: "tile-label", textContent: label }), el("span", { className: "tile-num", textContent: num(c[k]) }));
    t.addEventListener("click", () => showPages(k));
    return t;
  }), el("div", { className: "tile static" }, el("span", { className: "tile-label", textContent: d.live ? "Time · pages a minute" : "Time" }),
    el("span", { className: "tile-num", textContent: clock(d.elapsed) + (d.live && d.per_minute ? ` · ${d.per_minute}` : "") })));
  renderOverview(d);
}
async function control(action) {
  if (action === "stop" && !confirm("Stop copying? Everything copied so far is kept, and you can continue later.")) return;
  await api("control", { action }).catch((e) => alert(e.message));
  refresh();
}
function renderOverview(d) {
  $("#sections").replaceChildren(...d.sections.map((s) => {
    if (s.more) return el("li", { className: "more-sec", textContent: s.name });
    const found = s.saved + s.failed + s.skipped + s.waiting, lost = s.failed + s.skipped;
    const pct = (n) => `${found ? (100 * n) / found : 0}%`;
    const saved = el("span", { className: "saved" }), gone = el("span", { className: "lost" });
    saved.style.width = pct(s.saved); gone.style.width = pct(lost);
    return el("li", {}, el("span", { className: "sec", textContent: s.name, title: s.name }),
      el("span", { className: "num" }, `${num(s.saved)} / ${num(found)}`, lost ? el("span", { className: "gap", textContent: ` · ${num(lost)} not copied` }) : ""),
      el("div", { className: "bar", role: "img", ariaLabel: `${s.saved} of ${found} copied` }, saved, gone));
  }));
  if (!d.sections.length) $("#sections").replaceChildren(el("li", { className: "muted small", textContent: "Nothing found yet." }));
  $("#recent-block").hidden = !d.recent.length;
  $("#recent").replaceChildren(...d.recent.map((p) => el("li", {}, el("a", { href: fileHref(d.name, p.file), target: "_blank", rel: "noopener", textContent: p.file, title: p.url }))));
  $("#groups-block").hidden = !d.groups.length;
  $("#groups").replaceChildren(...d.groups.map((g) => {
    const see = el("button", { type: "button", className: "link-strong", textContent: g.reason });
    see.addEventListener("click", () => showPages(g.failed ? "failed" : "skipped", ""));
    const again = !d.live && (g.failed || g.reason === "needed sign-in") ? btn("Try again", () => api("try-again", { name: d.name, reason: g.reason }).then(() => openCopy(d.name)).catch((e) => alert(e.message))) : "";
    return el("li", {}, el("span", {}, see, el("span", { className: "muted", textContent: ` · ${num(g.count)}${g.failed ? " failed" : ""}` })), again,
      el("span", { className: "ex mono", textContent: g.example, title: g.note }));
  }));
  $("#linked-block").hidden = !d.linked.length;
  $("#linked").replaceChildren(...d.linked.slice(0, 30).map((p) => el("li", { textContent: p })),
    ...(d.linked.length > 30 ? [el("li", { className: "muted", textContent: `and ${d.linked.length - 30} more` })] : []));
  $("#outside-block").hidden = !d.outside.length;
  $("#outside").replaceChildren(...d.outside.map((g) => el("li", {},
    el("span", { className: "mono small", textContent: g.prefix }),
    d.live ? "" : btn("Copy these too", () => api("also-copy", { name: d.name, prefix: g.prefix }).then(() => openCopy(d.name)).catch((e) => alert(e.message))),
    el("span", { className: "ex", textContent: `${plural(g.count, "link")} · first on ${g.from}` }))));
}

// tabs
function setTab(t) {
  tab = t;
  $$(".tabbar .tab").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === t)));
  for (const p of ["overview", "pages", "log"]) $(`#p-${p}`).hidden = p !== t;
  if (detail) renderCopy(detail);
  if (t === "pages") loadPages();
  if (t === "log") loadLog();
}
$$(".tabbar .tab").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));

// Pages: everything the copy holds or left out, filtered by kind and text
const KINDS = [["all", "All"], ["saved", "Copied"], ["files", "Attachments"], ["waiting", "Waiting"], ["failed", "Failed"], ["skipped", "Skipped"], ["outside", "Links not followed"]];
function showPages(k, text) { kind = k; shown = 200; if (text !== undefined) $("#page-q").value = text; setTab("pages"); }
let pageTimer = null;
$("#page-q").addEventListener("input", () => { clearTimeout(pageTimer); pageTimer = setTimeout(() => { shown = 200; loadPages(); }, 250); });
const RESULT = {
  saved: (r) => [el("a", { href: fileHref(current, r.file), target: "_blank", rel: "noopener", className: "mono", textContent: r.file }), r.note ? el("span", { className: "muted", textContent: ` · ${r.note}` }) : ""],
  file: (r) => [el("a", { href: fileHref(current, r.file), target: "_blank", rel: "noopener", className: "mono", textContent: r.file })],
  failed: (r) => [el("span", { className: "pill bad", textContent: "failed" }), ` ${r.reason}`, el("div", { className: "muted small", textContent: r.note })],
  skipped: (r) => [el("span", { className: "pill", textContent: "skipped" }), ` ${r.reason}`, el("div", { className: "muted small", textContent: r.note })],
  waiting: () => [el("span", { className: "muted", textContent: "waiting" })],
  outside: (r) => [el("span", { className: "muted", textContent: r.note })],
};
async function loadPages() {
  const counts = detail?.counts || {};
  $("#kinds").replaceChildren(...KINDS.map(([k, label]) => {
    const n = k === "all" ? counts.saved + counts.files + counts.waiting + counts.failed + counts.skipped : counts[k];
    const b = el("button", { type: "button", className: `chip${kind === k ? " on" : ""}`, ariaPressed: String(kind === k) }, label, el("span", { className: "count", textContent: num(n) }));
    b.addEventListener("click", () => { kind = k; shown = 200; loadPages(); if (detail) renderCopy(detail); });
    return b;
  }));
  const rows = [];
  let total = 0;
  for (let offset = 0; offset < shown; offset += 200) {
    const d = await api(`pages?${q({ name: current, kind, q: $("#page-q").value, offset })}`);
    rows.push(...d.rows); total = d.total;
    if (d.rows.length < 200) break;
  }
  $("#page-rows").replaceChildren(...rows.map((r) => el("tr", {},
    el("td", { className: "addr" }, el("a", { href: r.url, target: "_blank", rel: "noopener noreferrer", className: "mono", textContent: r.url })),
    el("td", {}, ...(RESULT[r.kind] || (() => []))(r)))));
  if (!rows.length) $("#page-rows").replaceChildren(el("tr", {}, el("td", { colSpan: 2, className: "muted", textContent: "Nothing here." })));
  const more = $("#page-more");
  more.replaceChildren(
    // the same action the Overview offers per folder, for the whole "Links not followed" list
    kind === "outside" && total && !(detail && detail.live)
      ? btn(`Copy these ${plural(total, "link")} too`, () => api("also-copy-all", { name: current }).then(() => openCopy(current)).catch((e) => alert(e.message)), "primary")
      : "",
    total > rows.length ? `Showing ${num(rows.length)} of ${num(total)}. ` : "",
    total > rows.length ? btn("Show more", () => { shown += 200; loadPages(); }, "link") : "");
}

// Log: the crawler's own lines, newest at the bottom
async function loadLog() {
  const pre = $("#log"), atEnd = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 20;
  const d = await api(`log?${q({ name: current })}`);
  pre.textContent = d.lines.length ? d.lines.join("\n") : "No log yet. Copies made before this version have none.";
  if (atEnd) pre.scrollTop = pre.scrollHeight;
}

// ---------------------------------------------------------------- Send to Ferret
// Stand-alone: an address and an access token (never stored; the address is remembered in this browser).
// Inside Ferret: the administrator's own session, and the copy becomes a synced folder.
try { const s = localStorage.getItem("site2md-ks-server"); if (s) $("#ks-server").value = s; } catch { /* not kept */ }
const ksBody = () => ({ server: $("#ks-server").value.trim(), token: $("#ks-token").value.trim() });
const slug = (s) => s.toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 64);
async function ks(path, json) {
  const r = await fetch(path, json === undefined ? { headers: AUTH } : { method: "POST", headers: { ...AUTH, "Content-Type": "application/json" }, body: JSON.stringify(json) });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof d.detail === "string" ? d.detail : `HTTP ${r.status}`);
  return d;
}
function openSend() {
  $("#send").hidden = false;
  $("#send-state").textContent = "";
  if (IN_KS || $("#ks-token").value.trim()) loadCollections(); else $("#ks-token").focus();
}
$("#send-close").addEventListener("click", () => { $("#send").hidden = true; });
$("#ks-token").addEventListener("change", () => loadCollections());
$("#ks-server").addEventListener("change", () => { if ($("#ks-token").value.trim()) loadCollections(); });
async function loadCollections() {
  const st = $("#send-state");
  st.textContent = "Connecting…";
  try {
    let cols, admin;
    if (IN_KS) { const me = await ks("/api/me"); cols = me.collections.filter((c) => c.can_write); admin = me.is_admin; }
    else {
      const d = await api("ks/collections", ksBody()); cols = d.collections; admin = d.is_admin;
      try { localStorage.setItem("site2md-ks-server", ksBody().server); } catch { /* not kept */ }
    }
    $("#ks-col").replaceChildren(...cols.map((c) => el("option", { value: c.id, textContent: c.name })),
      ...(admin ? [el("option", { value: "new", textContent: "New collection…" })] : []));
    if (admin && !cols.length) $("#ks-col").value = "new";
    $("#ks-new").value = slug(current);
    $("#ks-col-row").hidden = $("#ks-send").hidden = !$("#ks-col").options.length;
    syncNewRow();
    st.textContent = $("#ks-col").options.length ? "" : "This account cannot add documents to any collection, or the token is read-only.";
  } catch (e) { st.textContent = e.message; }
}
function syncNewRow() { $("#ks-new-row").hidden = $("#ks-col").value !== "new"; }
$("#ks-col").addEventListener("change", syncNewRow);
$("#send-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const st = $("#send-state"), isNew = $("#ks-col").value === "new", newName = $("#ks-new").value.trim();
  if (isNew && !/^[a-z0-9_-]{2,64}$/.test(newName)) { st.textContent = "A collection name uses 2–64 lower-case letters, digits, - or _."; return; }
  const colName = isNew ? newName : $("#ks-col").selectedOptions[0].textContent;
  st.textContent = "Sending…";
  try {
    if (IN_KS) {
      const id = isNew ? (await ks("/api/admin/collections", { name: newName })).id : Number($("#ks-col").value);
      await ks("/api/admin/folders", { path: detail.folder, collection_id: id });
      st.replaceChildren(`Added to ${colName}. Ferret is indexing it now; after an Update here it takes in only what changed. `,
        el("button", { type: "button", className: "link", textContent: "Open Documents",
          onclick: () => window.parent.postMessage({ open: "documents" }, location.origin) }));
      return;
    }
    await api("send", { ...ksBody(), name: current, ...(isNew ? { new_collection: newName } : { collection_id: Number($("#ks-col").value) }) });
    const poll = async () => {
      const s = await api(`sent?${q({ name: current })}`);
      if (!s) return;
      if (s.state === "sending") { st.textContent = s.total ? `Sending ${num(s.done)} of ${num(s.total)}…` : "Sending…"; setTimeout(poll, 800); return; }
      if (s.state === "failed") { st.textContent = s.error; return; }
      st.replaceChildren(s.how === "synced folder" ? `Added to ${colName} as a synced folder: Ferret is indexing it now. `
        : `Sent to ${colName}: ${num(s.queued)} new or changed, ${num(s.unchanged)} unchanged${s.rejected.length ? `, ${s.rejected.length} refused` : ""}. `,
        el("a", { href: ksBody().server, target: "_blank", rel: "noopener", textContent: "Open Ferret" }));
    };
    poll();
  } catch (x) { st.textContent = x.message; }
});

loadHome().catch((e) => { document.body.textContent = e.message; });
