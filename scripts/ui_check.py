"""Functional + visual UI check (Playwright). Needs a running server with demo data (./setup.sh --demo --start).

    pip install playwright && playwright install chromium
    python scripts/ui_check.py docs/screenshots
"""
import os
import sys
from playwright.sync_api import sync_playwright
OUT = sys.argv[1]
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
    pg = b.new_page(viewport={"width": 1280, "height": 900})
    errors = []
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto("http://127.0.0.1:8000/")
    pg.fill("input[name=username]", "alice"); pg.fill("input[name=password]", "wrong-password")
    pg.click("button[type=submit]"); pg.wait_for_selector("#login-error:has-text('invalid')")
    pg.fill("input[name=password]", "demo-password-alice"); pg.click("button[type=submit]")
    pg.wait_for_selector("#app-view:not([hidden])")
    print("chips:", pg.locator("#scope label").all_inner_texts())
    pg.screenshot(path=f"{OUT}/01-empty.png")
    def run(q, shot):
        pg.fill("#q", q); pg.click("#search-form button")
        pg.wait_for_selector("#outcome strong")
        print(q, "->", pg.locator("#outcome strong").inner_text(), "| strong:", pg.locator("#results > li").count(),
              "| weak:", pg.locator("#weak-results > li").count())
        pg.screenshot(path=f"{OUT}/{shot}.png", full_page=True)
    run("ERR-PAY-4012", "02-exact")
    pg.click("#results li >> text=View passage"); pg.wait_for_selector("#drawer:not([hidden])")
    print("drawer:", pg.locator("#d-title").inner_text(), "|", pg.locator("#d-loc").inner_text(), "| open href:", pg.get_attribute("#d-open", "href"))
    pg.screenshot(path=f"{OUT}/03-passage.png")
    with pg.expect_popup() as pop: pg.click("#d-open")
    src = pop.value; src.wait_for_load_state(); print("source tab:", src.url, "| starts:", src.locator("body").inner_text()[:40].replace("\n"," "))
    src.close(); pg.click("#d-close")
    run("why was checkout unavailable in March 2025", "04-conceptual")
    run("who won the company football tournament", "05-no-evidence")
    run("daily meal allowance per diem for domestic business travel", "06-conflict")
    pg.click(".tab[data-tab=documents]"); pg.wait_for_selector("#docs tr")
    print("documents rows:", pg.locator("#docs tr").count(), "| upload form visible:", pg.is_visible("#upload-form"))
    pg.set_input_files("#upload-form input[type=file]", files=[{"name": "ui-note.md", "mimeType": "text/markdown", "buffer": b"# UI note\n\nThe heliotrope switch resets the sorter."}])
    pg.click("#upload-form button"); pg.wait_for_selector("#upload-msg:has-text('Queued')"); print("upload:", pg.inner_text("#upload-msg"))
    pg.wait_for_selector("#docs tr:has-text('UI note') .st-indexed", timeout=30000); print("indexed via worker: yes")
    pg.screenshot(path=f"{OUT}/07-documents.png", full_page=True)
    pg.once("dialog", lambda d: d.accept())
    pg.click("#docs tr:has-text('UI note') >> text=Delete"); pg.wait_for_selector("#docs tr:has-text('UI note')", state="detached"); print("delete: ok")
    pg.click(".tab[data-tab=status]"); pg.wait_for_selector("#status table")
    print("status:", pg.locator("#status .outcome").inner_text())
    pg.screenshot(path=f"{OUT}/08-status.png", full_page=True)
    pg.set_viewport_size({"width": 390, "height": 844}); pg.click(".tab[data-tab=search]"); run("ERR-PAY-4012", "09-mobile")
    print("horizontal overflow:", pg.evaluate("document.documentElement.scrollWidth > window.innerWidth"))
    pg.click("#logout"); pg.wait_for_selector("#login-view:not([hidden])"); print("logout: ok")
    print("console errors:", errors)
    b.close()
