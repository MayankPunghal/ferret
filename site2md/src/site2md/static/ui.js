// site2md page: five screens (Your copies, Copy a website, Sign in, Copying, Done) over a small local API.
// Every request carries the one-time key from this page's address.
"use strict";
const KEY = new URLSearchParams(location.search).get("k") || "";
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const el = (tag, props = {}, ...kids) => { const e = Object.assign(document.createElement(tag), props); e.append(...kids.filter((k) => k !== null && k !== undefined && k !== "")); return e; };
const num = (n) => (n ?? 0).toLocaleString();
const ACTIVE = ["starting", "signin", "copying", "paused", "stopping"];

async function api(path, body) {
  const r = await fetch(path, body === undefined ? { headers: { "X-Key": KEY } }
    : { method: "POST", headers: { "X-Key": KEY, "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

function show(screen) {
  $$("main > section").forEach((s) => { s.hidden = s.id !== `s-${screen}`; });
  window.scrollTo(0, 0);
}
$$("[data-go]").forEach((b) => b.addEventListener("click", () => loadHome()));

// ---------------------------------------------------------------- 1. Your copies
let copiesFolder = "";
async function loadHome() {
  const d = await api("/api/copies");
  copiesFolder = d.folder;
  $("#open-copies").title = d.folder;
  if (d.run && ACTIVE.includes(d.run.state)) return follow();
  show("home");
  $("#empty").hidden = d.copies.length > 0;
  $("#new-copy").hidden = d.copies.length === 0;
  $("#copies").replaceChildren(...d.copies.map(copyRow));
}
function copyRow(c) {
  const ok = c.status === "complete", cls = ok ? "ok" : /sign-in|stopped|could not/.test(c.status) ? "warn" : "";
  const when = c.updated ? new Date(c.updated * 1000).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";
  const btn = (label, fn, kind = "button") => { const b = el("button", { type: "button", className: kind, textContent: label }); b.addEventListener("click", fn); return b; };
  const actions = el("div", { className: "row-actions" });
  if (c.left || c.updating) actions.append(btn(c.updating ? "Continue update" : "Continue", () => start({ name: c.name, mode: "continue", signin: !!c.options?.options?.channel }), "primary"));
  actions.append(btn("Update", () => start({ name: c.name, mode: "update", signin: !!c.options?.options?.channel })),
    btn("Search it…", () => showDone({ name: c.name, url: c.url, state: "copy", pages: c.pages, files: c.files, groups: [] })),
    btn("Open folder", () => api("/api/open", { name: c.name })),
    btn("Delete", async () => {
      if (!confirm(`Delete the copy “${c.name}” (${num(c.pages)} pages) from this computer? The website is not affected.`)) return;
      await api("/api/delete", { name: c.name }); loadHome();
    }, "button danger"));
  return el("li", {},
    el("div", {}, el("span", { className: "name", textContent: c.name }), el("span", { className: `pill ${cls}`, textContent: c.status })),
    actions,
    el("div", { className: "sub" }, `${num(c.pages)} pages · ${num(c.files)} attachments${when ? " · updated " + when : ""} · `, el("span", { className: "mono", textContent: c.url })));
}
$("#open-copies").addEventListener("click", () => api("/api/open", {}));
for (const id of ["#new-copy", "#empty-new"]) $(id).addEventListener("click", () => openNew());

// ---------------------------------------------------------------- 2. Copy a website
let checked = null, nameTouched = false, browserList = null;
async function openNew() {
  show("new");
  $("#new-form").reset(); $("#check").textContent = ""; $("#new-error").hidden = true;
  checked = null; nameTouched = false; $("#browser-row").hidden = true;
  $("#url").focus();
  if (!browserList) browserList = await api("/api/browsers").catch(() => []);
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
    const c = await api("/api/check", { url });
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
    max_pages: Number(f.get("max_pages")) || 10000, prefixes: lines(f.get("prefixes")), hosts: lines(f.get("hosts")),
    attachments: !!f.get("attachments"), browser: !!f.get("browser"), keep_html: !!f.get("keep_html"),
    ignore_robots: !!f.get("ignore_robots"), channel: needsBrowser() ? f.get("channel") || "" : "",
  };
  if (needsBrowser() && !options.channel) { err.textContent = "This needs a browser: install Google Chrome or Microsoft Edge."; err.hidden = false; return; }
  try { await start({ url: checked?.url || url, name: f.get("name").trim(), options, signin: !!checked?.signin, mode: "new" }); }
  catch (x) { err.textContent = x.message; err.hidden = false; }
});

// ---------------------------------------------------------------- 3–4. Sign in, copying
async function start(body) {
  try { await api("/api/start", body); } catch (e) { alert(e.message); throw e; }
  follow();
}
let pollTimer = null;
async function follow() {
  clearTimeout(pollTimer);
  const r = await api("/api/run");
  if (!r) return loadHome();
  $$(".site-name").forEach((x) => { x.textContent = r.name; });
  if (r.state === "signin" || r.state === "starting") show(r.state === "signin" ? "signin" : "run");
  if (["copying", "paused", "stopping", "starting"].includes(r.state)) { show("run"); renderRun(r); }
  if (ACTIVE.includes(r.state)) { pollTimer = setTimeout(follow, 1000); return; }
  showDone(r);
}
function clock(s) { const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, x = String(s % 60).padStart(2, "0"); return h ? `${h}:${String(m).padStart(2, "0")}:${x}` : `${m}:${x}`; }
function renderRun(r) {
  $(".url-line").textContent = r.url;
  $("#run-state").textContent = { starting: "Starting…", copying: `Copying · ${num(r.running)} page${r.running === 1 ? "" : "s"} loading`, paused: "Paused. Pages being loaded finish; nothing new starts until you resume.", stopping: "Stopping and saving what was copied…" }[r.state] || "";
  $("#pause").hidden = r.state !== "copying"; $("#resume").hidden = r.state !== "paused";
  $("#stop").disabled = r.state === "stopping";
  for (const k of ["pages", "files", "queued", "skipped", "failed"]) $(`#c-${k}`).textContent = num(r[k]);
  $("#c-time").textContent = clock(r.elapsed); $("#c-rate").textContent = r.per_minute ?? "–";
  $("#sections").replaceChildren(...r.sections.map((s) => {
    if (s.more) return el("li", { className: "more-sec", textContent: s.name });
    const found = s.saved + s.failed + s.skipped + s.waiting, lost = s.failed + s.skipped;
    const pct = (n) => `${found ? (100 * n) / found : 0}%`;
    const saved = el("span", { className: "saved" }), gone = el("span", { className: "lost" });
    saved.style.width = pct(s.saved); gone.style.width = pct(lost);
    const bar = el("div", { className: "bar", role: "img", ariaLabel: `${s.saved} of ${found} saved` }, saved, gone);
    return el("li", {}, el("span", { className: "sec", textContent: s.name, title: s.name }),
      el("span", { className: "num" }, `${num(s.saved)} / ${num(found)}`, lost ? el("span", { className: "gap", textContent: ` · ${num(lost)} not copied` }) : ""), bar);
  }));
  $("#recent").replaceChildren(...r.recent.map((p) => el("li", {}, el("a", { href: `/file?k=${encodeURIComponent(KEY)}&name=${encodeURIComponent(r.name)}&file=${encodeURIComponent(p.file)}`, target: "_blank", rel: "noopener", textContent: p.file, title: p.url }))));
}
$$("[data-act]").forEach((b) => b.addEventListener("click", async () => {
  if (b.dataset.act === "stop" && !confirm("Stop copying? Everything saved so far is kept, and you can continue later.")) return;
  await api("/api/control", { action: b.dataset.act }).catch((e) => alert(e.message));
  follow();
}));

// ---------------------------------------------------------------- 5. Done
let doneName = "";
function minutes(s) { return s < 90 ? `${s} seconds` : s < 5400 ? `${Math.round(s / 60)} minutes` : `${(s / 3600).toFixed(1)} hours`; }
function showDone(r) {
  show("done");
  doneName = r.name;
  const title = { done: "Done", stopped: "Stopped", "signin-expired": "Your sign-in expired", failed: "The copy stopped with an error", copy: r.name }[r.state] || "Done";
  $("#done-title").textContent = title;
  const what = `${num(r.pages)} page${r.pages === 1 ? "" : "s"}${r.files ? ` and ${num(r.files)} attachment${r.files === 1 ? "" : "s"}` : ""}`;
  $("#done-line").textContent = r.state === "done" ? (r.resumed ? `Copied ${what} from ${r.name}; the part after continuing took ${minutes(r.elapsed)}.`
    : `Copied ${what} from ${r.name} in ${minutes(r.elapsed)}.`)
    : r.state === "copy" ? `${what}, from ${r.url}.` : `${what} saved so far from ${r.name}.`;
  const notice = $("#done-notice");
  notice.hidden = !["signin-expired", "failed", "stopped"].includes(r.state);
  notice.textContent = r.state === "signin-expired" ? "Pages started coming back as the sign-in page, so copying paused instead of recording them as failures. Sign in again and it continues where it stopped."
    : r.state === "failed" ? r.error : "Everything copied so far is kept. Continue picks up where it stopped.";
  const acts = $("#done-actions");
  const btn = (label, fn, kind = "button") => { const b = el("button", { type: "button", className: kind, textContent: label }); b.addEventListener("click", fn); return b; };
  acts.replaceChildren(
    ...(r.state === "signin-expired" ? [btn("Sign in again and continue", () => start({ name: r.name, mode: "continue", signin: true }), "primary")] : []),
    ...(r.state === "stopped" ? [btn("Continue", () => start({ name: r.name, mode: "continue" }), "primary")] : []),
    btn("Open folder", () => api("/api/open", { name: r.name })));
  const groups = r.groups || [];
  $("#not-copied-block").hidden = !groups.length;
  $("#not-copied").replaceChildren(...groups.map((g) => el("li", {},
    el("span", {}, el("strong", { textContent: g.reason }), ` · ${num(g.count)}`),
    g.failed ? btn("Try these again", () => api("/api/try-again", { name: r.name, reason: g.reason }).then(follow).catch((e) => alert(e.message))) : "",
    el("span", { className: "ex mono", textContent: g.example, title: g.note }))));
  $("#send-state").textContent = "";
}

// Knowledge Search: address remembered in this browser; the token is never stored.
try { const s = localStorage.getItem("site2md-ks-server"); if (s) $("#ks-server").value = s; } catch { /* not kept */ }
const ksBody = () => ({ server: $("#ks-server").value.trim(), token: $("#ks-token").value.trim() });
$("#ks-load").addEventListener("click", async () => {
  const st = $("#send-state");
  st.textContent = "Connecting…";
  try {
    const d = await api("/api/ks/collections", ksBody());
    try { localStorage.setItem("site2md-ks-server", $("#ks-server").value.trim()); } catch { /* not kept */ }
    $("#ks-col").replaceChildren(...d.collections.map((c) => el("option", { value: c.id, textContent: c.name })));
    $("#ks-col-row").hidden = $("#ks-send").hidden = !d.collections.length;
    st.textContent = d.collections.length ? `Signed in as ${d.user}.` : `${d.user} cannot add documents to any collection, or the token is read-only.`;
  } catch (e) { st.textContent = e.message; }
});
$("#send-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const st = $("#send-state");
  try {
    await api("/api/send", { ...ksBody(), name: doneName, collection_id: Number($("#ks-col").value) });
    const poll = async () => {
      const s = await api(`/api/sent?name=${encodeURIComponent(doneName)}`);
      if (!s) return;
      if (s.state === "sending") { st.textContent = s.total ? `Sending ${num(s.done)} of ${num(s.total)}…` : "Sending…"; setTimeout(poll, 800); return; }
      if (s.state === "failed") { st.textContent = s.error; return; }
      const ks = $("#ks-server").value.trim().replace(/\/$/, "");
      st.replaceChildren(s.how === "synced folder" ? "Added as a synced folder: Knowledge Search is indexing it now. "
        : `Sent: ${num(s.queued)} new or changed, ${num(s.unchanged)} unchanged${s.rejected.length ? `, ${s.rejected.length} refused` : ""}. `,
        el("a", { href: ks, target: "_blank", rel: "noopener", textContent: "Open Knowledge Search" }));
    };
    poll();
  } catch (x) { st.textContent = x.message; }
});

loadHome().catch((e) => { document.body.textContent = e.message; });
