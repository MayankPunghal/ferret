"""Functional + visual UI walkthrough (Playwright, headless Chromium) against a running server that was set up
with demo data but no administrator yet (fresh data dir: `ferret serve`, then `ferret demo`).

    pip install playwright && playwright install chromium     (or set CHROMIUM_PATH to an existing Chromium)
    python scripts/ui_check.py docs/screenshots [http://127.0.0.1:8000]

Prints one line per check; exits non-zero on the first failure.
"""

import os
import sys

from playwright.sync_api import sync_playwright

OUT = sys.argv[1]
URL = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8000"
ADMIN_PW = "ui-check-admin-password"
QUOTAS = b"""<html><head><title>Transform quotas</title></head><body><main><h1>Transform quotas</h1>
<table><thead><tr><th>Workload</th><th>Dimension</th><th>Quota</th><th>Adjustable</th></tr></thead><tbody>
<tr><td rowspan="2">Mainframe</td><td>Lines of code for each job</td><td>30,000,000</td><td>No</td></tr>
<tr><td>Jobs for each account</td><td>10</td><td>Yes</td></tr>
<tr><td>Windows</td><td>Servers for each job</td><td>500</td><td>Yes</td></tr></tbody></table></main></body></html>"""

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
    pg = b.new_page(viewport={"width": 1280, "height": 900})
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append(f"{m.text} ({m.location.get('url')})") if m.type == "error"
          and "401" not in m.text and "422" not in m.text
          and "/api/admin/db/query" not in m.location.get("url", "") else None)  # the refused DELETE below

    def menu(selector):
        """Click a sidebar item, or one in the account menu (Settings, About, Sign out), opening the menu first."""
        if not pg.locator(selector).is_visible():
            pg.click("#account-button")
        pg.click(selector)

    def shot(name):
        pg.screenshot(path=f"{OUT}/{name}.png", full_page=True)

    def run(q, name):
        pg.fill("#q", q)
        pg.click("#search-form button")
        pg.wait_for_selector("#outcome strong")
        print(f"search {q!r}: {pg.locator('#outcome strong').inner_text()} | evidence={pg.locator('#results > li').count()}"
              f" weaker={pg.locator('#weak-results > li').count()}")
        shot(name)

    # first run: create the administrator in the browser
    pg.goto(URL)
    pg.wait_for_selector("#setup-view:not([hidden]), #login-view:not([hidden])")
    if pg.is_visible("#setup-view"):
        shot("00-first-run")
        pg.fill("#setup-form [name=password]", ADMIN_PW)
        pg.fill("#setup-form [name=password2]", ADMIN_PW)
        pg.check("#setup-form [name=terms]")
        pg.click("#setup-form button")
        print("first-run admin created")
    else:  # re-run against an existing install
        pg.fill("input[name=username]", "admin")
        pg.fill("input[name=password]", ADMIN_PW)
        pg.click("#login-form button")
    pg.wait_for_selector("#app-view:not([hidden])")
    print("admin tabs:", pg.locator(".tab:not([hidden])").all_inner_texts())

    # settings
    menu(".tab[data-tab=settings]")
    pg.wait_for_selector("#s-model option", state="attached")
    print("settings: model", pg.input_value("#s-model"), "| chunk", pg.input_value("#s-chunk"),
          "| overlap on", pg.is_checked("#s-overlap-on"), "|", pg.inner_text("#reindex-info"))
    print("summaries: length", pg.input_value("#s-sumlen"), "| line from", pg.input_value("#s-ctx-src"),
          "| device", pg.input_value("#s-llm-dev"), "|", pg.inner_text("#s-llm-dev-note")[:60])
    shot("01-settings")
    pg.fill("#s-chunk", "700" if pg.input_value("#s-chunk") != "700" else "900")
    pg.click("#settings-form button[type=submit]")
    pg.wait_for_selector("#settings-msg:has-text('Saved')")
    pg.wait_for_selector("#settings-banner:not([hidden])")
    print("settings saved; banner:", pg.inner_text("#settings-banner")[:90])
    pg.click("#reindex-outdated")
    pg.wait_for_selector("#reindex-msg:has-text('queued')")
    print("reindex:", pg.inner_text("#reindex-msg"))
    pg.wait_for_function("1", timeout=1000) if False else None
    for _ in range(60):
        menu(".tab[data-tab=settings]")
        pg.wait_for_selector("#reindex-info:has-text('documents indexed')")
        if " 0 outdated · 0 pending" in pg.inner_text("#reindex-info"):
            break
        pg.wait_for_timeout(1000)
    print("after reindex:", pg.inner_text("#reindex-info"))

    # users & access: a group (Groups), access for it to a new collection (Collections), a user in it (Users)
    menu(".tab[data-tab=admin]")
    pg.wait_for_selector("#col-rows tr")
    import random
    col = f"trial-{random.randint(1000, 9999)}"
    grp = f"trial-users-{col[-4:]}"
    pg.click("#admin-tabs .subtab[data-sub=groups]")
    pg.fill("#group-form [name=name]", grp)
    pg.click("#group-form button")
    pg.wait_for_selector(f"#group-rows tr:has-text('{grp}')")
    pg.click("#admin-tabs .subtab[data-sub=collections]")
    pg.fill("#col-form [name=name]", col)
    pg.fill("#col-form [name=description]", "UI check collection")
    pg.click("#col-form button")
    pg.wait_for_selector(f"#col-rows tr:has-text('{col}')")
    row = pg.locator(f"#col-rows tr:has-text('{col}')")
    row.locator("select").nth(0).select_option(grp)
    row.locator("select").nth(1).select_option("write")
    row.locator("button:has-text('Give access')").click()
    pg.wait_for_selector(f"#col-rows tr:has-text('{grp} (read & write)')")
    pg.click("#admin-tabs .subtab[data-sub=users]")
    user = f"tester{col[-4:]}"
    pg.fill("#user-form [name=username]", user)
    pg.fill("#user-form [name=password]", "tester-password-1")
    pg.click("#new-user-groups summary")
    pg.check(f"#new-user-groups input[value='{grp}']")
    pg.click("#user-form button")
    pg.wait_for_selector(f"#user-rows tr:has-text('{user}'):has-text('{grp}')")
    print(f"group {grp!r}, collection {col!r} + access, user {user!r} in the group created")
    shot("02-users-access")

    # documents: multi-file upload into the new collection
    menu(".tab[data-tab=documents]")
    pg.wait_for_selector("#upload-collection option", state="attached")
    pg.select_option("#upload-collection", label=col)
    pg.set_input_files("#file-input", files=[
        {"name": "alpha.md", "mimeType": "text/markdown", "buffer": b"# Alpha\n\nThe heliotrope switch resets the sorter."},
        {"name": "beta.txt", "mimeType": "text/plain", "buffer": b"Beta notes: the cobalt gate opens at dawn."},
        {"name": "quotas.html", "mimeType": "text/html", "buffer": QUOTAS},
        {"name": "empty.md", "mimeType": "text/markdown", "buffer": b""},  # rejected at upload
        {"name": "bad.exe", "mimeType": "application/octet-stream", "buffer": b"MZ"},
    ])
    # one line for the whole upload; only the rejected file is named; other types are not uploaded
    pg.wait_for_selector("#upload-list li:has-text('Uploaded 4 file(s): 3 queued for indexing, 1 rejected')")
    assert "skipped 1 of other types (exe)" in pg.inner_text("#upload-list") and pg.locator(".upload-errors li").count() == 1
    print("uploads:", pg.inner_text("#upload-list").replace("\n", " | "))
    pg.wait_for_selector("#docs tr:has-text('Alpha') .st-indexed", timeout=60000)
    pg.wait_for_selector("#docs tr:has-text('beta.txt') .st-indexed", timeout=60000)
    pg.wait_for_selector("#docs tr:has-text('quotas.html') .st-indexed", timeout=60000)
    print("both uploads indexed; stages:", sorted(set(pg.locator("#docs td:nth-child(4) .meta").all_inner_texts())))
    shot("03-documents")
    menu(".tab[data-tab=search]")
    pg.fill("#q", "heliotrope switch")
    pg.click("#search-form button")
    pg.wait_for_selector("#outcome strong")

    # index status: document summaries by the optional local language model (waits for them when installed)
    def summaries_line():
        menu(".tab[data-tab=status]")
        pg.wait_for_selector("#status dl")
        return pg.locator("#status dt:has-text('summaries') + dd").inner_text()
    import re
    line = summaries_line()
    for _ in range(120):
        m = re.match(r".*: (\d+) of (\d+) documents", line)
        if not m or m.group(1) == m.group(2):
            break
        pg.wait_for_timeout(5000)
        line = summaries_line()
    print("summaries:", line)
    shot("16-status")
    menu(".tab[data-tab=summaries]")
    pg.wait_for_selector("#summaries table, #summaries .empty")
    print("summaries tab:", pg.locator("#summaries tbody tr").count(), "rows | first:",
          pg.locator("#summaries tbody tr").first.inner_text().replace("\n", " | ")[:160]
          if pg.locator("#summaries tbody tr").count() else "-")
    shot("17-summaries")
    # readable view of a Markdown document (rendered, not raw text)
    menu(".tab[data-tab=documents]")
    pg.wait_for_selector("#docs tr")
    with pg.context.expect_page() as viewer:
        pg.locator("#docs tr:has-text('ledger-service.md') td:first-child a").click()
    v = viewer.value
    v.wait_for_load_state()
    assert v.locator("main h1").count() == 1 and "# " not in v.locator("main").inner_text()[:2], "Markdown not rendered"
    print("viewer:", v.title(), "| h1:", v.locator("main h1").inner_text(), "| download link:",
          v.locator("header a").inner_text())
    v.screenshot(path=f"{OUT}/18-viewer.png")
    v.close()
    # a web page's table with a merged cell: a table with a header row in the passage, the merged cell repeated
    run("lines of code for each mainframe job", "25-table-search")
    pg.locator("#results li.result, #weak-results li.result").filter(has_text="Mainframe").first.locator("text=View passage").click()
    pg.wait_for_selector("#drawer:not([hidden]) #d-body table th")
    heads = pg.locator("#d-body th").all_inner_texts()
    assert heads == ["Workload", "Dimension", "Quota", "Adjustable"], heads
    assert pg.locator("#d-body td", has_text="Mainframe").count() == 2, "merged cell not repeated"
    print("table passage: header", heads, "| rows", pg.locator("#d-body tr").count() - 1)
    shot("25-table")
    pg.keyboard.press("Escape")
    pg.wait_for_selector("#drawer", state="hidden")
    menu("#logout")
    pg.wait_for_selector("#login-view:not([hidden])")

    # regular user flows
    pg.fill("input[name=username]", "alice")
    pg.fill("input[name=password]", "demo-password-alice")
    pg.click("#login-form button")
    pg.wait_for_selector("#app-view:not([hidden])")
    assert pg.is_visible("#tab-search") and pg.locator("#results > li").count() == 0 and pg.input_value("#q") == "", \
        "previous user's tab/results leaked into alice's session"
    print("alice starts on a clean Search tab (no previous user's results)")
    assert pg.is_visible("#search-hint") and "documents indexed" in pg.inner_text("#hint-scope"), "no search hint"
    print("search hint:", pg.inner_text("#hint-scope"))
    shot("00-search-start")
    print("alice tabs:", pg.locator(".tab:not([hidden])").all_inner_texts(),
          "| chips:", pg.locator("#scope label").all_text_contents())
    run("ERR-PAY-4012", "04-exact")
    pg.click("#results li >> text=View passage")
    pg.wait_for_selector("#drawer:not([hidden])")
    print("passage:", pg.inner_text("#d-loc"))
    assert pg.locator("#d-body code").count() and "`" not in pg.inner_text("#d-body"), "passage Markdown not rendered"
    shot("05-passage")
    pg.keyboard.press("Escape")
    pg.wait_for_selector("#drawer", state="hidden")
    print("passage rendered (code spans), Esc closes it")
    run("how do I undo a bad release of the orders service", "06-conceptual")
    # graph navigation: connections of the Ledger Service wiki page
    run("ledger service schema changes", "10-graph-search")
    ledger = pg.locator("#results li.result, #weak-results li.result").filter(has_text="Ledger Service").first
    ledger.locator(".r-related .rel-row").first.wait_for()
    print("related with the result:", [x.replace("\n", " ") for x in ledger.locator(".r-related .rel-row").all_inner_texts()])
    ledger.screenshot(path=f"{OUT}/15-related.png")
    summary = ledger.locator(".r-summary")  # only when the optional local language model is installed
    print("summary with the result:", summary.inner_text().replace("\n", " | ") if summary.count()
          else "none (no local language model installed)")
    if summary.count():  # a topic lists the documents with that topic (Summaries tab), not a text search
        topic = ledger.locator(".chip.topic").first
        name = topic.inner_text()
        topic.click()
        pg.wait_for_selector("#tab-summaries:not([hidden]) .topic-filter")
        rows = pg.locator("#summaries tbody tr")
        assert rows.count() >= 1 and all(name.lower() in r.lower() for r in rows.all_inner_texts()), "topic filter"
        print("topic:", pg.inner_text(".topic-filter").replace("\n", " "), "| rows", rows.count())
        shot("19-topic")
        menu(".tab[data-tab=search]")
    ledger.locator("text=View passage").click()
    pg.wait_for_selector("#d-links h3")
    print("connections:", [x.replace("\n", " ") for x in pg.locator("#d-links h4, #d-links li").all_inner_texts()])
    pg.click("#d-links .graph-box summary")
    pg.wait_for_selector("#d-links .graph svg .g-node")
    print("graph (1 hop): nodes", pg.locator("#d-links .g-node").count(), "edges", pg.locator("#d-links .g-edge").count())
    shot("11-connections")
    pg.click("#d-links .graph-hops button:has-text('2 hops')")
    pg.wait_for_selector("#d-links .graph-hops button[aria-pressed=true]:has-text('2 hops')")
    pg.wait_for_timeout(300)
    print("graph (2 hops): nodes", pg.locator("#d-links .g-node").count(), "edges", pg.locator("#d-links .g-edge").count())
    pg.locator("#d-links .graph").screenshot(path=f"{OUT}/14-graph.png")
    pg.locator("#d-links li button", has_text="Checkout Workflow").first.click()
    pg.wait_for_selector("#d-title:has-text('Checkout Workflow')")
    print("followed link to:", pg.inner_text("#d-title"))
    pg.click("#d-close")
    run("who is on call for the services the Orders Service depends on", "13-linked-search")
    linked = pg.locator("#results .badge, #weak-results .badge").filter(has_text="linked from").all_inner_texts()
    assert linked, "no result reached through a link"
    print("linked results:", sorted(set(linked)))
    # AI assistants: create a token, the setup snippet carries it, an MCP call with it works, revoke it
    menu(".tab[data-tab=assistants]")
    pg.fill("#tok-form input[name=label]", "ui-check assistant")
    pg.click("#tok-form button")
    pg.wait_for_selector("#tok-new:not([hidden])")
    shown = pg.inner_text("#tok-value").strip()
    assert "••••" in shown, "the token is shown shortened"
    pg.context.grant_permissions(["clipboard-read", "clipboard-write"])
    pg.click("#tok-copy")
    tok = pg.evaluate("navigator.clipboard.readText()")
    assert tok.startswith(shown[:6]) and tok.endswith(shown[-4:]) and len(tok) > len(shown) - 8, "Copy copies the whole token"
    assert tok in pg.inner_text("#mcp-config") and "/mcp" in pg.inner_text("#mcp-config"), "setup snippet"
    shot("20-assistants")
    for c in ("Claude Desktop", "Cursor", "VS Code"):
        pg.click(f"#mcp-clients button:has-text('{c}')")
        assert tok in pg.inner_text("#mcp-config"), c
    r = pg.request.post(URL + "/mcp", headers={"authorization": f"Bearer {tok}"},
                        data={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": "search", "arguments": {"query": "ERR-PAY-4012"}}})
    print("assistants: token in snippets for every client; MCP search with it:", r.status,
          '"evidence_found"' in r.json()["result"]["content"][0]["text"])
    pg.once("dialog", lambda d: d.accept())
    pg.click("#tok-rows tr:has-text('ui-check assistant') button:has-text('Revoke')")
    pg.wait_for_selector("#tok-rows tr:has-text('ui-check assistant')", state="detached")
    gone = pg.request.post(URL + "/mcp", headers={"authorization": f"Bearer {tok}"},
                           data={"jsonrpc": "2.0", "id": 1, "method": "initialize"}).status
    assert gone == 401, gone
    print("revoked token refused:", gone)
    # Logs → Activity (administrators): the calls above with request, response and timing, like a browser's Network tab
    def sign_in(user, pw):
        menu("#logout")
        pg.wait_for_selector("#login-view:not([hidden])")
        pg.fill("input[name=username]", user)
        pg.fill("input[name=password]", pw)
        pg.click("#login-form button")
        pg.wait_for_selector("#app-view:not([hidden])")

    sign_in("admin", ADMIN_PW)
    menu(".tab[data-tab=logs]")
    pg.wait_for_selector("#act-rows tr")
    kinds = set(pg.locator("#act-rows .act-kind").all_inner_texts())
    assert {"API", "AI assistant", "Indexing"} <= kinds, kinds
    pg.click("#act-rows tr:has-text('Search “ledger service schema changes”')")
    pg.wait_for_selector("#act-detail:not([hidden]) .wf-row")
    steps = pg.locator("#act-pane .wf-name").all_inner_texts()
    shot("21-activity")
    pg.click("#act-tabs button[data-pane=request]")
    assert "ledger service schema changes" in pg.inner_text("#act-pane pre")
    pg.click("#act-tabs button[data-pane=summary]")
    print("activity: kinds", sorted(kinds), "| search steps", steps)
    pg.click("#log-views button[data-view=logs]")
    pg.wait_for_selector("#log-text:not(:empty)")
    pg.click("#log-views button[data-view=activity]")
    # Database (administrators): tables, rows with secrets hidden, structure, read-only SQL
    menu(".tab[data-tab=database]")
    pg.wait_for_selector("#db-rows tr")
    tables = pg.locator(".db-tname").all_inner_texts()
    assert {"documents", "chunks", "users"} <= set(tables), tables
    pg.click(".db-table:has-text('chunks')")
    pg.wait_for_selector("#db-name:has-text('chunks')")
    pg.wait_for_selector("#db-range:has-text(' of ')")
    pg.click("#db-rows tr >> nth=0")
    pg.wait_for_selector("#db-row:not([hidden]) .db-value")
    shot("22-database")
    pg.click("#db-row-close")
    pg.click(".db-table:has-text('users')")
    pg.wait_for_selector("#db-name:has-text('users')")
    pg.wait_for_selector("#db-cols th:has-text('password_hash')")
    col = [t.strip() for t in pg.locator("#db-cols th").all_inner_texts()].index("password_hash")
    hashes = set(pg.locator(f"#db-rows tr td:nth-child({col + 1})").all_inner_texts())
    assert hashes == {"•••"}, hashes
    pg.click("#db-panes button[data-pane=structure]")
    pg.wait_for_selector("#db-pane-structure table")
    pg.click("#db-views button[data-view=sql]")
    pg.click("#db-sql-form button[type=submit]")
    pg.wait_for_selector("#db-sql-rows tr")
    msg = pg.inner_text("#db-sql-msg")
    shot("23-database-sql")
    pg.fill("#db-sql", "DELETE FROM documents")
    pg.click("#db-sql-form button[type=submit]")
    pg.wait_for_selector("#db-sql-msg:has-text('read-only')")
    # the sidebar folds to icons (remembered), and back
    pg.click("#nav-toggle")
    assert pg.locator("#sidebar").bounding_box()["width"] < 80
    shot("24-nav-collapsed")
    pg.reload()
    pg.wait_for_selector("#app-view.nav-collapsed:not([hidden])")
    pg.click("#nav-toggle")
    assert pg.locator("#sidebar").bounding_box()["width"] > 200
    menu(".tab[data-tab=database]")
    print("database: tables", len(tables), "| password hashes shown as", hashes, "| SQL:", msg, "| DELETE:", pg.inner_text("#db-sql-msg"))
    # Ask: the conversation window renders; on an instance with no local model it says where to get one
    menu(".tab[data-tab=ask]")
    pg.wait_for_selector("#tab-ask:not([hidden]) #ask-form")
    pg.wait_for_selector("#tab-ask:not([hidden]) #ask-log .ask-msg")
    assert pg.locator("#ask-log .ask-msg").count() >= 1, "Ask greets the user"
    pg.wait_for_selector("#tab-ask:not([hidden]) #ask-convs li")
    assert pg.locator("#ask-convs li").count() >= 1, "the conversation list renders"
    assert pg.is_visible("#ask-note"), "Ask explains that no local model is downloaded"
    print("ask: note =", pg.inner_text("#ask-note")[:90])
    sign_in("alice", "demo-password-alice")
    run("per diem for domestic travel", "07-conflict")
    run("who won the company football tournament", "08-no-evidence")
    pg.set_viewport_size({"width": 390, "height": 844})
    run("ERR-PAY-4012", "09-mobile")
    print("mobile horizontal overflow:", pg.evaluate("document.documentElement.scrollWidth > innerWidth")
          if False else pg.locator("body").bounding_box()["width"] > 390)
    print("console/page errors:", errors)
    b.close()
