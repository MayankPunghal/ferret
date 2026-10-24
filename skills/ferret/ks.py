#!/usr/bin/env python3
"""Command-line client for a running Ferret server, for Claude (or a person) to use.

Standard library only. Prints JSON (or plain text for `doc --text`). Configuration:
  KS_URL       server address (default http://127.0.0.1:8000)
  KS_TOKEN     an API token, or
  KS_USER / KS_PASSWORD   credentials; the token they produce is cached in ~/.cache/knowledge-search/
               (owner-only permissions) until the server rejects it.

  python ks.py search "how do I roll back the orders service" [--collection 2] [--limit 10]
  python ks.py passage CHUNK_ID            the passage with the one before and after it
  python ks.py doc DOCUMENT_ID [--text]    the whole indexed document, passages in reading order
  python ks.py links DOCUMENT_ID           links to / from it, mentions, related in meaning
  python ks.py impact DOCUMENT_ID          what depends on it (2 hops)
  python ks.py docs [--collection 2] [--status failed]
  python ks.py status | me
  python ks.py upload PATH... --collection 2   files or folders (recursive; only supported types)
  python ks.py retry DOCUMENT_ID | delete DOCUMENT_ID --yes
  python ks.py reindex [--all]             administrators: re-index outdated (or all) documents
"""

import argparse
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

URL = os.environ.get("KS_URL", "http://127.0.0.1:8000").rstrip("/")
CACHE = Path.home() / ".cache" / "knowledge-search" / urllib.parse.quote(f"token-{os.environ.get('KS_USER')}@{URL}", safe="")
SUPPORTED = {".md", ".txt", ".html", ".htm", ".docx", ".pptx", ".pdf"}
# Upload response, listing and status fields that matter to a reader; the rest is noise for a model.
DOC_FIELDS = ("id", "title", "clearer_title", "source_key", "collection", "status", "error_code", "error_detail",
              "summary_stage", "summary", "topics", "chunks", "needs_reindex", "updated_at")


class Fail(Exception):
    pass


def _request(method, path, token=None, body=None, headers=None):
    h = dict(headers or {})
    if token:
        h["Authorization"] = f"Bearer {token}"
    if isinstance(body, (dict, list)):
        body, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    req = urllib.request.Request(URL + path, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            data = r.read()
            return r.status, json.loads(data) if data else None
    except urllib.error.HTTPError as e:
        data = e.read()
        try:
            return e.code, json.loads(data)
        except ValueError:
            return e.code, {"detail": data.decode(errors="replace")[:500]}
    except urllib.error.URLError as e:
        raise Fail(f"cannot reach {URL} ({e.reason}); is the app running? (start.bat / ./start.sh)") from e


def _login():
    user, pw = os.environ.get("KS_USER"), os.environ.get("KS_PASSWORD")
    if not (user and pw):
        raise Fail("set KS_TOKEN, or KS_USER and KS_PASSWORD (an account of the Ferret app)")
    code, out = _request("POST", "/api/auth/login", body={"username": user, "password": pw})
    if code != 200:
        raise Fail(f"login failed ({code}): {out.get('detail') if out else ''}")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(CACHE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(out["token"])
    return out["token"]


def call(method, path, body=None, headers=None):
    token = os.environ.get("KS_TOKEN") or (CACHE.read_text().strip() if CACHE.exists() else None) or _login()
    code, out = _request(method, path, token, body, headers)
    if code == 401 and not os.environ.get("KS_TOKEN"):  # cached token expired: sign in again once
        code, out = _request(method, path, _login(), body, headers)
    if code >= 400 and not (path == "/api/search" and isinstance(out, dict) and "outcome" in out):
        detail = out.get("detail") if isinstance(out, dict) else out
        raise Fail(f"{method} {path}: {code} {json.dumps(detail) if not isinstance(detail, str) else detail}")
    return out


def _absolute(u):
    return URL + u if u and u.startswith("/") else u


def search(a):
    body = {"query": a.query, "limit": a.limit}
    if a.collection:
        body["collection_ids"] = a.collection
    out = call("POST", "/api/search", body)
    keep = ("chunk_id", "document_id", "title", "clearer_title", "collection", "heading_path", "page", "supported",
            "match_basis", "text", "summary", "topics", "source_uri")
    results = []
    for r in out.get("results", []):
        x = {k: r[k] for k in keep if r.get(k) not in (None, "", [])}
        x["open"] = _absolute(r.get("source_url"))
        if r["signals"].get("linked_from"):
            x["linked_from"] = r["signals"]["linked_from"]
        meta = {k: r["metadata"][k] for k in ("series", "version", "effective_date") if r["metadata"].get(k)}
        if meta:  # versions of one policy: a "sources disagree" outcome compares these
            x["version"] = meta
        if r.get("outdated_version"):
            x["outdated_version"] = True
        results.append(x)
    return {k: out[k] for k in ("outcome", "message", "reason_codes") if k in out} | {"results": results}


def passage(a):
    out = call("GET", f"/api/chunks/{a.chunk_id}")
    out["open"] = _absolute(f"/api/documents/{out['document_id']}/view")
    return out


def doc(a):
    out = call("GET", f"/api/documents/{a.document_id}/text")
    out["open"] = _absolute(f"/api/documents/{a.document_id}/view")
    if not a.text:
        return out
    lines = [f"# {out['title']}  ({out['collection']}, {out['open']})"]
    for p in out["passages"]:
        where = (p["heading_path"] or "") + (f" (page {p['page']})" if p.get("page") else "")
        lines += [f"\n[chunk {p['chunk_id']}] {where}".rstrip(), p["text"]]
    return "\n".join(lines)


def docs(a):
    q = f"?collection_id={a.collection}" if a.collection else ""
    rows = call("GET", "/api/documents" + q)
    rows = [{k: r[k] for k in DOC_FIELDS if r.get(k) not in (None, "", [])} for r in rows
            if not a.status or r["status"] == a.status]
    return {"count": len(rows), "documents": rows}


def _files(paths):
    """(file, path in its folder or None): a folder brings its subfolders, without names starting with . or _"""
    for p in map(Path, paths):
        if p.is_dir():
            root = p.resolve()
            for f in sorted(root.rglob("*")):
                rel = f.relative_to(root).parts
                if f.is_file() and not any(x.startswith((".", "_")) for x in rel):
                    yield f, f"{root.name}/{'/'.join(rel)}"
        elif p.is_file():
            yield p, None
        else:
            raise Fail(f"no such file or folder: {p}")


def upload(a):
    if a.collection not in {c["id"] for c in call("GET", "/api/me")["collections"] if c["can_write"]}:
        raise Fail(f"this account cannot upload to collection {a.collection}; `ks.py me` lists the ones it can")
    counts, failed, skipped = {}, [], 0
    for f, path in _files(a.paths):
        if f.suffix.lower() not in SUPPORTED:
            skipped += 1
            continue
        boundary = uuid.uuid4().hex
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        body = b"".join([
            f'--{boundary}\r\nContent-Disposition: form-data; name="collection_id"\r\n\r\n{a.collection}\r\n'.encode(),
            # its path in the folder identifies it, so same-named files in subfolders stay apart
            f'--{boundary}\r\nContent-Disposition: form-data; name="path"\r\n\r\n{path}\r\n'.encode() if path else b"",
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{f.name}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n".encode(), f.read_bytes(), f"\r\n--{boundary}--\r\n".encode()])
        try:
            out = call("POST", "/api/documents", body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
            counts[out["outcome"]] = counts.get(out["outcome"], 0) + 1
        except Fail as e:
            failed.append({"file": str(f), "error": str(e)})
    return {"uploaded": counts, "rejected": failed, "skipped_other_types": skipped,
            "next": "indexing runs in the background; check with `ks.py status` or `ks.py docs --status failed`"}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--collection", type=int, action="append", help="collection id (repeatable)")
    s.add_argument("--limit", type=int, default=10)
    sub.add_parser("passage").add_argument("chunk_id", type=int)
    d = sub.add_parser("doc")
    d.add_argument("document_id")
    d.add_argument("--text", action="store_true", help="plain text instead of JSON")
    sub.add_parser("links").add_argument("document_id")
    sub.add_parser("impact").add_argument("document_id")
    ls = sub.add_parser("docs")
    ls.add_argument("--collection", type=int)
    ls.add_argument("--status", choices=["pending", "processing", "indexed", "failed"])
    sub.add_parser("status")
    sub.add_parser("me")
    u = sub.add_parser("upload")
    u.add_argument("paths", nargs="+")
    u.add_argument("--collection", type=int, required=True)
    sub.add_parser("retry").add_argument("document_id")
    rm = sub.add_parser("delete")
    rm.add_argument("document_id")
    rm.add_argument("--yes", action="store_true", help="required: deleting removes the file and its passages")
    sub.add_parser("reindex").add_argument("--all", action="store_true")
    a = ap.parse_args(argv)

    try:
        if a.cmd == "delete" and not a.yes:
            raise Fail("deleting is permanent; repeat with --yes once the user has confirmed")
        out = {
            "search": search, "passage": passage, "doc": doc, "docs": docs, "upload": upload,
            "links": lambda a: call("GET", f"/api/documents/{a.document_id}/links"),
            "impact": lambda a: call("GET", f"/api/documents/{a.document_id}/impact"),
            "status": lambda a: call("GET", "/api/status"),
            "me": lambda a: call("GET", "/api/me"),
            "retry": lambda a: call("POST", f"/api/documents/{a.document_id}/retry"),
            "delete": lambda a: call("DELETE", f"/api/documents/{a.document_id}"),
            "reindex": lambda a: call("POST", "/api/admin/reindex", {"all": a.all}),
        }[a.cmd](a)
    except Fail as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    text = out if isinstance(out, str) else json.dumps(out, indent=1, ensure_ascii=False, default=str)
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to a code page
    try:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
    except BrokenPipeError:  # output piped into `head`
        sys.stdout = None
    return 0


if __name__ == "__main__":
    sys.exit(main())
