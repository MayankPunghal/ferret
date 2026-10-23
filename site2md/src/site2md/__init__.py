"""site2md: save a website or intranet as Markdown files, one per page (see crawl.py)."""

import argparse
from pathlib import Path

__version__ = "0.4.0"


def main(argv: list[str] | None = None) -> None:
    from .crawl import BrowserFetcher, Crawler

    ap = argparse.ArgumentParser(prog="site2md", description="Save a website as Markdown files, one per page.")
    ap.add_argument("url", help="start page")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--max-pages", type=int, default=10_000)
    ap.add_argument("--prefix", action="append", help="only URLs starting with this (repeatable; default: the start URL's folder)")
    ap.add_argument("--allow-host", action="append", help="also follow links anywhere on this host (repeatable)")
    ap.add_argument("--header", action="append", default=[], help="extra request header, e.g. 'Authorization: Bearer ...'")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--delay", type=float, default=0.2, help="seconds each worker waits between requests")
    ap.add_argument("--ignore-robots", action="store_true", help="ignore robots.txt (only for your own sites)")
    ap.add_argument("--no-files", action="store_true", help="do not save linked PDF/Word/text files")
    ap.add_argument("--resume", action="store_true", help="continue an interrupted crawl in --out")
    b = ap.add_argument_group("browser (sites behind a login or built with JavaScript; needs: pip install playwright)")
    b.add_argument("--browser", action="store_true", help="fetch pages with a real browser")
    b.add_argument("--login", action="store_true", help="open a browser window to sign in first (saved for next time)")
    b.add_argument("--channel", default="chromium", help="chrome or msedge (the installed browser), or firefox / "
                   "chromium (Playwright's own, once: playwright install firefox|chromium); default chromium")
    b.add_argument("--profile", default="", help="where the browser keeps the sign-in "
                   "(default: ~/.site2md/profile-<channel>, one per browser)")
    b.add_argument("--show-browser", action="store_true", help="show the browser window while crawling")
    b.add_argument("--browser-path", default="", help="path to a Chromium/Chrome executable")
    ap.add_argument("--save-html", action="store_true", help="also keep the HTML each page was converted from, "
                    "in <out>-html (to check what a page held)")
    a = ap.parse_args(argv)
    fetcher = None
    a.profile = a.profile or str(Path.home() / ".site2md" / f"profile-{a.channel}")
    if a.browser or a.login:
        Path(a.profile).mkdir(parents=True, exist_ok=True)
        if a.login:
            login = BrowserFetcher(Path(a.profile), a.channel, headless=False, executable=a.browser_path)
            login.login(a.url)
            login.close()
        fetcher = BrowserFetcher(Path(a.profile), a.channel, headless=not a.show_browser, executable=a.browser_path)
    headers = dict(h.split(":", 1) for h in a.header)
    try:
        Crawler(a.url, Path(a.out), prefixes=a.prefix, allow_hosts=a.allow_host, max_pages=a.max_pages,
                workers=a.workers, delay=a.delay, headers={k.strip(): v.strip() for k, v in headers.items()},
                robots=not a.ignore_robots, files=not a.no_files, browser=fetcher,
                save_html=Path(str(Path(a.out)) + "-html") if a.save_html else None).run(resume=a.resume)
    finally:
        if fetcher:
            fetcher.close()
