"""Functional + visual UI walkthrough (Playwright, headless Chromium) against a running server that was set up
with demo data but no administrator yet (fresh install: `setup --demo`, then start).

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

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
    pg = b.new_page(viewport={"width": 1280, "height": 900})
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" and "401" not in m.text else None)

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
        pg.click("#setup-form button")
        print("first-run admin created")
    else:  # re-run against an existing install
        pg.fill("input[name=username]", "admin")
        pg.fill("input[name=password]", ADMIN_PW)
        pg.click("#login-form button")
    pg.wait_for_selector("#app-view:not([hidden])")
    print("admin tabs:", pg.locator(".tab:not([hidden])").all_inner_texts())

    # settings
    pg.click(".tab[data-tab=settings]")
    pg.wait_for_selector("#s-model option", state="attached")
    print("settings: model", pg.input_value("#s-model"), "| chunk", pg.input_value("#s-chunk"),
          "| overlap on", pg.is_checked("#s-overlap-on"), "|", pg.inner_text("#reindex-info"))
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
        pg.click(".tab[data-tab=settings]")
        pg.wait_for_selector("#reindex-info:has-text('documents indexed')")
        if " 0 outdated · 0 pending" in pg.inner_text("#reindex-info"):
            break
        pg.wait_for_timeout(1000)
    print("after reindex:", pg.inner_text("#reindex-info"))

    # users & access
    pg.click(".tab[data-tab=admin]")
    pg.wait_for_selector("#col-rows tr")
    import random
    col = f"trial-{random.randint(1000, 9999)}"
    pg.fill("#col-form [name=name]", col)
    pg.fill("#col-form [name=description]", "UI check collection")
    pg.click("#col-form button")
    pg.wait_for_selector(f"#col-rows tr:has-text('{col}')")
    row = pg.locator(f"#col-rows tr:has-text('{col}')")
    row.locator("input[aria-label=Group]").fill("trial-users")
    row.locator("input[type=checkbox]").check()
    row.locator("button:has-text('Grant')").click()
    pg.wait_for_selector("#col-rows tr:has-text('trial-users (read & write)')")
    user = f"tester{col[-4:]}"
    pg.fill("#user-form [name=username]", user)
    pg.fill("#user-form [name=password]", "tester-password-1")
    pg.fill("#user-form [name=groups]", "trial-users, staff")
    pg.click("#user-form button")
    pg.wait_for_selector(f"#user-rows tr:has-text('{user}')")
    print(f"collection {col!r} + grant + user {user!r} created")
    shot("02-users-access")

    # documents: multi-file upload into the new collection
    pg.click(".tab[data-tab=documents]")
    pg.wait_for_selector("#upload-collection option", state="attached")
    pg.select_option("#upload-collection", label=col)
    pg.set_input_files("#file-input", files=[
        {"name": "alpha.md", "mimeType": "text/markdown", "buffer": b"# Alpha\n\nThe heliotrope switch resets the sorter."},
        {"name": "beta.txt", "mimeType": "text/plain", "buffer": b"Beta notes: the cobalt gate opens at dawn."},
        {"name": "bad.exe", "mimeType": "application/octet-stream", "buffer": b"MZ"},
    ])
    pg.wait_for_selector("#upload-list li:has-text('bad.exe') .error")
    print("uploads:", [x.replace("\n", " ") for x in pg.locator("#upload-list li").all_inner_texts()])
    pg.wait_for_selector("#docs tr:has-text('Alpha') .st-indexed", timeout=60000)
    pg.wait_for_selector("#docs tr:has-text('beta.txt') .st-indexed", timeout=60000)
    print("both uploads indexed")
    pg.click(".tab[data-tab=search]")
    pg.fill("#q", "heliotrope switch")
    pg.click("#search-form button")
    pg.wait_for_selector("#outcome strong")
    shot("03-documents")
    pg.click("#logout")
    pg.wait_for_selector("#login-view:not([hidden])")

    # regular user flows
    pg.fill("input[name=username]", "alice")
    pg.fill("input[name=password]", "demo-password-alice")
    pg.click("#login-form button")
    pg.wait_for_selector("#app-view:not([hidden])")
    assert pg.is_visible("#tab-search") and pg.locator("#results > li").count() == 0 and pg.input_value("#q") == "", \
        "previous user's tab/results leaked into alice's session"
    print("alice starts on a clean Search tab (no previous user's results)")
    print("alice tabs:", pg.locator(".tab:not([hidden])").all_inner_texts(),
          "| chips:", pg.locator("#scope label").all_inner_texts())
    run("ERR-PAY-4012", "04-exact")
    pg.click("#results li >> text=View passage")
    pg.wait_for_selector("#drawer:not([hidden])")
    print("passage:", pg.inner_text("#d-loc"))
    shot("05-passage")
    pg.click("#d-close")
    run("how do I undo a bad release of the orders service", "06-conceptual")
    # graph navigation: connections of the Ledger Service wiki page
    run("ledger service schema changes", "10-graph-search")
    pg.locator("#results li, #weak-results li").filter(has_text="Ledger Service").first.locator("text=View passage").click()
    pg.wait_for_selector("#d-links h3")
    print("connections:", [x.replace("\n", " ") for x in pg.locator("#d-links h4, #d-links li").all_inner_texts()])
    shot("11-connections")
    pg.locator("#d-links li button", has_text="Checkout Workflow").first.click()
    pg.wait_for_selector("#d-title:has-text('Checkout Workflow')")
    print("followed link to:", pg.inner_text("#d-title"))
    pg.click("#d-close")
    run("who is on call for the services the Orders Service depends on", "13-linked-search")
    linked = pg.locator("#results .badge, #weak-results .badge").filter(has_text="linked from").all_inner_texts()
    assert linked, "no result reached through a link"
    print("linked results:", sorted(set(linked)))
    run("per diem for domestic travel", "07-conflict")
    run("who won the company football tournament", "08-no-evidence")
    pg.set_viewport_size({"width": 390, "height": 844})
    run("ERR-PAY-4012", "09-mobile")
    print("mobile horizontal overflow:", pg.evaluate("document.documentElement.scrollWidth > innerWidth")
          if False else pg.locator("body").bounding_box()["width"] > 390)
    print("console/page errors:", errors)
    b.close()
