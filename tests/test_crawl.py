"""Website crawler: follows every link (content, nav, footer, sitemap), one Markdown file per page, links
rewritten to local files, never follows logout/robots-disallowed links, saves linked PDFs, dedupes."""

import io
import json
import threading
import urllib.error
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from site2md.crawl import EXPORT, FOLDER_VIEW, Crawler, file_name_for, normalize
from site2md.html import html_to_markdown

from knowledge_search.parsing import chunk_document, parse_document

NAV = ('<header><a href="/">Home</a> <a href="/about">About us</a></header>'
       '<nav><a href="/docs/a.html">Runbook A</a> <a href="/docs/b/">Service B</a> <a href="/logout">Log out</a>'
       ' <a href="/private/secret">Private</a></nav>')
FOOT = '<footer><a href="/files/manual.pdf">Manual</a> <a href="https://elsewhere.example/">Elsewhere</a></footer>'
PAGES = {
    "/": f"<html><head><title>Intranet Home | Acme Wiki</title></head><body>{NAV}<main><h1>Welcome</h1>"
         "<p>Start here.</p></main>{FOOT}</body></html>",
    "/docs/a.html": f"<html><head><title>Runbook A | Acme Wiki</title></head><body>{NAV}<main><h1>Runbook A</h1><p>Restart "
                    "the service as described in <a href='../docs/b/#restart'>Service B</a>.</p></main>"
                    f"{FOOT}</body></html>",
    "/docs/b/": f"<html><head><title>Service B | Acme Wiki</title></head><body>{NAV}<main><h1>Service B</h1>"
                "<h2 id='restart'>Restart</h2><p>Run the restart job.</p>"
                "<a href='/docs/a.html?utm_source=mail'>back to A</a></main></body></html>",
    "/about": f"<html><head><title>About</title></head><body>{NAV}<main><h1>About</h1><p>Team page.</p></main></body></html>",
    "/about/": "<html><body><main><h1>About</h1><p>Team page.</p></main></body></html>",
    "/orphan": "<html><head><title>Orphan</title></head><body><main><h1>Orphan</h1><p>Only in the sitemap. <a href='/about/'>Team</a></p></main></body></html>",
    "/private/secret": "<html><body><main><p>Disallowed.</p></main></body></html>",
    "/logout": "<html><body>bye</body></html>",
    "/hub": f"<html><head><title>HR Corner</title></head><body>{NAV}<main><h1>HR Corner</h1><h2>Policies</h2>"
            "<iframe src='https://calendar.google.com/calendar/embed?src=x'></iframe></main></body></html>",
}


def serve():
    hits = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            path = self.path.split("?")[0]
            host = f"http://127.0.0.1:{self.server.server_port}"
            if path == "/robots.txt":
                body, ctype = b"User-agent: *\nDisallow: /private/\n", "text/plain"
            elif path == "/sitemap.xml":
                body, ctype = f"<urlset><url><loc>{host}/orphan</loc></url></urlset>".encode(), "application/xml"
            elif path == "/files/manual.pdf":
                body, ctype = b"%PDF-1.4 fake", "application/pdf"
            elif path in PAGES:
                body, ctype = PAGES[path].encode(), "text/html; charset=utf-8"
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, hits


def test_normalize_drops_fragments_tracking_and_default_ports():
    assert normalize("HTTP://Wiki.Example:80/a/b.html?utm_source=x&b=2&a=1#top") == "http://wiki.example/a/b.html?a=1&b=2"


def test_crawl_follows_every_link_and_writes_one_markdown_per_page(tmp_path):
    srv, hits = serve()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        m = Crawler(base + "/", tmp_path, delay=0, log=lambda *_: None).run()
    finally:
        srv.shutdown()
    pages = {u.removeprefix(base): v["file"] for u, v in m["pages"].items()}
    # nav, header, footer, content and sitemap links are all followed; the same page under two URLs is saved once
    assert set(pages) == {"/", "/docs/a.html", "/docs/b/", "/about", "/orphan"}
    assert "/about/" in {u.removeprefix(base) for u in m["skipped"]}
    assert m["files"] == {base + "/files/manual.pdf": "files-manual-pdf.pdf"}
    assert (tmp_path / "files-manual-pdf.pdf").read_bytes().startswith(b"%PDF")
    # never: logout, robots-disallowed, other hosts
    assert "/logout" not in hits and "/private/secret" not in hits
    assert not any("elsewhere" in u for u in m["pages"])
    a = (tmp_path / pages["/docs/a.html"]).read_text()
    assert f'source_url: "{base}/docs/a.html"' in a and 'title: "Runbook A"' in a
    assert "(docs-b.md#restart)" in a  # link to a crawled page -> local file (graph edge after ingest)
    assert "Acme Wiki" not in a  # site-wide title suffix removed
    assert "Log out" not in a  # menus are not part of the page text
    chunks = chunk_document(parse_document(a.encode(), "docs-a.md"), 900, 0)
    assert [(ln.target, ln.anchor) for c in chunks for ln in c.links] == [("docs-b.md", "restart")]
    assert json.loads((tmp_path / "manifest.json").read_text())["queue"] == []


def test_crawl_resume_and_page_limit(tmp_path):
    srv, _ = serve()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        first = Crawler(base + "/", tmp_path, delay=0, max_pages=2, workers=1, log=lambda *_: None).run()
        assert len(first["pages"]) + len(first["files"]) == 2 and first["queue"]
        done = Crawler(base + "/", tmp_path, delay=0, log=lambda *_: None).run(resume=True)
    finally:
        srv.shutdown()
    assert len(done["pages"]) == 5 and not done["queue"]


def test_stopping_with_ctrl_c_keeps_the_crawl_and_resume_finishes_it(tmp_path):
    srv, _ = serve()
    base = f"http://127.0.0.1:{srv.server_port}"

    class Stopped(Crawler):
        def fetch(self, url):
            if self._count() == 2:
                raise KeyboardInterrupt
            return super().fetch(url)

    try:
        first = Stopped(base + "/", tmp_path, delay=0, workers=1, log=lambda *_: None).run()
        assert len(first["pages"]) + len(first["files"]) == 2 and first["queue"]
        assert json.loads((tmp_path / "manifest.json").read_text())["queue"]
        done = Crawler(base + "/", tmp_path, delay=0, log=lambda *_: None).run(resume=True)
    finally:
        srv.shutdown()
    assert len(done["pages"]) == 5 and not done["queue"]


def _docx(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/'
                   f'2006/main"><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')
    return buf.getvalue()


def _virus_scan(file_id: str, name: str) -> bytes:
    """Drive's warning page for files over 100 MB, as served."""
    return ("<!DOCTYPE html><html><head><title>Google Drive - Virus scan warning</title></head><body>"
            "<div class='uc-main'><div id='uc-text'><p class='uc-warning-caption'>Google Drive can't scan this file "
            f"for viruses.</p><p class='uc-warning-subcaption'><span class='uc-name-size'><a href='/open?id={file_id}'>"
            f"{name}</a> (153M)</span> is too large for Google to scan for viruses. Would you still like to download "
            "this file?</p><form id='download-form' action='https://drive.usercontent.google.com/download' "
            "method='get'><input type='submit' id='uc-download-link' value='Download anyway'/>"
            f"<input type='hidden' name='id' value='{file_id}'><input type='hidden' name='export' value='download'>"
            "<input type='hidden' name='confirm' value='t'><input type='hidden' name='uuid' value='9f1c'></form>"
            "</div></div></body></html>").encode()


def _confirmed(file_id: str) -> str:
    return f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t&uuid=9f1c"


def test_embedded_google_drive_documents_are_saved_and_linked(tmp_path):
    pdf_id, doc_id, video_id, deck_id, big_id, manual_id = ("1" + c * 30 for c in "abcdef")
    hr = ("<html><head><title>HR Corner</title></head><body><main><h1>HR Corner</h1><p>Policies.</p>"
          f"<a href='/hr/pip'>PIP policy</a><div data-url='https://docs.google.com/document/d/{doc_id}/edit'></div>"
          f"<iframe src='https://drive.google.com/file/d/{video_id}/preview'></iframe>"
          f"<iframe src='https://drive.google.com/file/d/{deck_id}/preview'></iframe>"
          f"<iframe src='https://drive.google.com/file/d/{big_id}/preview'></iframe>"
          f"<iframe src='https://drive.google.com/file/d/{manual_id}/preview'></iframe></main></body></html>")
    pip = ("<html><head><title>PIP Policy</title></head><body><main><h1>PIP Policy</h1>"
           f'<iframe src="https://drive.google.com/file/d/{pdf_id}/preview"></iframe></main></body></html>')
    pdf = (Path(__file__).parent.parent / "fixtures/corpus/engineering/incident-2025-031-postmortem.pdf").read_bytes()
    docx = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    served = {"https://intranet.example/hr": (hr.encode(), "text/html", ""),
              "https://intranet.example/hr/pip": (pip.encode(), "text/html", ""),
              EXPORT["file"].format(pdf_id): (pdf, "application/pdf", "SF PIP Policy.docx.pdf"),
              EXPORT["document"].format(doc_id): (_docx("Twenty days of paid leave."), docx, "Leave Policy.docx"),
              EXPORT["file"].format(video_id): (b"\0" * 10, "application/octet-stream", "Self Review.mov"),
              EXPORT["file"].format(deck_id): (b"PK pptx", "application/octet-stream", "Induction on LTA.pptx"),
              EXPORT["file"].format(big_id): (_virus_scan(big_id, "Show n Tell June.mp4"), "text/html", ""),
              EXPORT["file"].format(manual_id): (_virus_scan(manual_id, "HRIS Manual.pdf"), "text/html", ""),
              _confirmed(big_id): (b"\0" * 10, "video/mp4", "Show n Tell June.mp4"),
              _confirmed(manual_id): (pdf + b"\n%manual", "application/pdf", "HRIS Manual.pdf")}
    downloaded = []

    class Intranet(Crawler):
        def _head(self, url):
            data, ctype, filename = served[url]
            return ctype, len(data), filename

        def _get(self, url, page=False):
            if url not in served:
                raise urllib.error.HTTPError(url, 404, "not found", None, None)
            downloaded.append(url)
            data, ctype, filename = served[url]
            return data, ctype, url, filename

    m = Intranet("https://intranet.example/hr", tmp_path, delay=0, robots=False, log=lambda *_: None).run()
    # documents are saved as they are (the indexer reads PDF and Word itself), named after the file
    assert m["files"] == {EXPORT["file"].format(pdf_id): "SF PIP Policy.pdf",
                          EXPORT["document"].format(doc_id): "Leave Policy.docx",
                          EXPORT["file"].format(deck_id): "Induction on LTA.pptx",
                          EXPORT["file"].format(manual_id): "HRIS Manual.pdf"}
    assert (tmp_path / "SF PIP Policy.pdf").read_bytes() == pdf
    # videos uploaded to Drive are recognised by name before downloading and skipped
    assert EXPORT["file"].format(video_id) not in downloaded
    assert m["skipped"][EXPORT["file"].format(video_id)] == ("embedded MOV is not a PDF, Word or PowerPoint file: "
                                                             "Self Review.mov")
    # files over 100 MB (Drive's "can't scan for viruses" page): "Download anyway" for PDF, Word and PowerPoint
    assert _confirmed(manual_id) in downloaded and _confirmed(big_id) not in downloaded
    assert m["skipped"][EXPORT["file"].format(big_id)] == ("embedded MP4 is not a PDF, Word or PowerPoint file: "
                                                           "Show n Tell June.mp4")
    # every page keeps its own text and links its documents, so search finds both and they are connected
    assert sorted(p.name for p in tmp_path.glob("*.md")) == ["hr-pip.md", "hr.md"]
    pip_md = (tmp_path / "hr-pip.md").read_text()
    assert "- [SF PIP Policy](SF%20PIP%20Policy.pdf)" in pip_md
    md = (tmp_path / "hr.md").read_text()
    assert "[PIP policy](hr-pip.md)" in md and "- [Leave Policy](Leave%20Policy.docx)" in md
    # the indexer resolves the link to the document's file name
    links = [ln.target for c in chunk_document(parse_document(pip_md.encode(), "hr-pip.md"), 900, 0) for ln in c.links]
    assert links == ["sf pip policy.pdf"]


def test_files_in_embedded_drive_folders_are_saved_and_other_embeds_listed(tmp_path):
    folder, sub, form_id, sheet_id, sheet_file = ("2" + c * 30 for c in "abcde")
    page = ("<html><head><title>HR Forms</title></head><body><main><h1>HR Forms</h1>"
            f"<iframe src='https://drive.google.com/embeddedfolderview?usp=sharing&amp;id={folder}#list'></iframe>"
            "<iframe src='https://calendar.google.com/calendar/embed?src=holidays'></iframe>"
            f"<iframe src='https://docs.google.com/spreadsheets/d/{sheet_id}/htmlembed'></iframe></main></body></html>")
    # Drive's folder view: the files as links, subfolders as folder links
    listing = (f"<a href='https://drive.google.com/file/d/{form_id}/view?usp=drive_web'>Form 11.pdf</a>"
               f"<a href='https://drive.google.com/file/d/{sheet_file}/view?usp=drive_web'>Rota.xlsx UPDATED</a>"
               f"<a href='https://drive.google.com/drive/folders/{sub}'>2026</a>")
    sub_listing = f"<a href='https://docs.google.com/presentation/d/{form_id}x/edit?usp=drive_web'>Holidays</a>"
    pdf = (Path(__file__).parent.parent / "fixtures/corpus/engineering/incident-2025-031-postmortem.pdf").read_bytes()
    served = {"https://intranet.example/forms": (page.encode(), "text/html", ""),
              FOLDER_VIEW.format(folder): (listing.encode(), "text/html; charset=utf-8", ""),
              FOLDER_VIEW.format(sub): (listing.encode() + sub_listing.encode(), "text/html", ""),  # links back
              EXPORT["file"].format(form_id): (pdf, "application/pdf", "Form 11.pdf"),
              EXPORT["file"].format(sheet_file): (b"PK", "application/octet-stream", "Rota.xlsx UPDATED"),
              EXPORT["presentation"].format(form_id + "x"): (pdf + b"\n%slides", "application/pdf",
                                                              "Holidays 2026.pdf")}

    class Intranet(Crawler):
        def _head(self, url):
            data, ctype, filename = served[url]
            return ctype, len(data), filename

        def _get(self, url, page=False):
            data, ctype, filename = served[url]
            return data, ctype, url, filename

    m = Intranet("https://intranet.example/forms", tmp_path, delay=0, robots=False, log=lambda *_: None).run()
    # the files in the folder and its subfolder are saved (Google Slides as PDF) and linked from the page
    assert sorted(m["files"].values()) == ["Form 11.pdf", "Holidays 2026.pdf"]
    md = (tmp_path / "forms.md").read_text()
    assert "- [Form 11](Form%2011.pdf)" in md and "- [Holidays 2026](Holidays%202026.pdf)" in md
    assert m["skipped"][EXPORT["file"].format(sheet_file)] == ("embedded XLSX is not a PDF, Word or PowerPoint file: "
                                                               "Rota.xlsx UPDATED")
    # what else the page embeds (calendars, sheets) is not saved, but listed, so a page with little text is explained
    assert m["skipped"]["https://calendar.google.com/calendar/embed?src=holidays"] == (
        "embedded calendar.google.com/calendar not saved (on https://intranet.example/forms)")
    assert m["skipped"][f"https://docs.google.com/spreadsheets/d/{sheet_id}/htmlembed"].startswith(
        "embedded docs.google.com/spreadsheets not saved")


def test_page_text_outside_the_main_element_is_kept():
    # several main areas are all kept
    _, md = html_to_markdown("<body><nav>Menu</nav><main><h1>Sick Leave</h1></main><article><p>Fifteen days "
                             "a year.</p></article></body>")
    assert "Sick Leave" in md and "Fifteen days" in md and "Menu" not in md
    # a "main" element holding only the title banner (site builders): the page text is not lost
    body = "Every employee is eligible for fifteen days of medical leave a year. " * 2
    _, md = html_to_markdown(f"<body><div role='main'><h1>Medical Leave</h1></div><section><p>{body}</p>"
                             "</section></body>")
    assert "fifteen days of medical leave" in md
    # Google Sites: the banner in "main", the sessions in sections after it; the site footer is not content
    sessions = "".join(f"<section><h2>Team {n} - June 2023</h2><p>Here's the presentation by team {n}</p></section>"
                       for n in range(3))
    _, md = html_to_markdown("<body><nav><a href='/'>Home</a></nav><div role='main'><p>We want to hear what you "
                             f"have been doing!</p></div>{sessions}<footer>Report abuse. Page details.</footer></body>")
    assert "## Team 2 - June 2023" in md and "Report abuse" not in md
    # a docs page: the text outside <main> is only a footer note, so <main> alone is kept
    _, md = html_to_markdown(f"<body><main><h1>Runbook</h1><p>{body}</p></main><div>Built with MkDocs</div></body>")
    assert "Built with MkDocs" not in md


def test_document_names_stay_readable_and_safe():
    used = set()
    assert file_name_for("Leave Policy (1)", used) == "Leave Policy (1)"
    assert file_name_for("leave policy (1)", used) == "leave policy (1) (2)"  # same name ignoring case
    assert file_name_for('Q3: plan? "v2" / final', used) == "Q3- plan- -v2- - final"
    assert file_name_for("CON", used) == "CON_"


def test_a_page_with_only_a_title_is_not_written(tmp_path):
    """Google Sites hub pages that only embed (unsaved) files would be empty documents that cannot be indexed."""
    srv, _ = serve()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        m = Crawler(base + "/hub", tmp_path, delay=0, max_pages=1, workers=1, log=lambda *_: None).run()
    finally:
        srv.shutdown()
    assert base + "/hub" not in m["pages"] and "no text of its own" in m["skipped"][base + "/hub"]
    assert not (tmp_path / "hub.md").exists()
