"""Folders: uploads keyed by their path in a folder, and folders on disk synced by path (folders.py)."""

import time

from knowledge_search import db

from .conftest import collection_id, token


def _keys(cid: int) -> set[str]:
    with db.pool().connection() as c:
        return {r["source_key"] for r in c.execute("SELECT source_key FROM documents WHERE collection_id = %s", (cid,))}


def test_upload_by_path_keeps_same_named_files_apart(client, corpus):
    h, cid = token(client, "root"), collection_id("scratch")

    def up(name, text, path=None):
        data = {"collection_id": cid, **({"path": path} if path else {})}
        r = client.post("/api/documents", headers=h, data=data, files={"file": (name, text.encode())})
        assert r.status_code == 202, r.text
        return r.json()

    a = up("index.md", "# Modules\nbilling", "kit/docs/modules/index.md")
    b = up("index.md", "# Workflows\nrefunds", "kit/docs/workflows/index.md")
    assert a["document_id"] != b["document_id"]
    assert up("index.md", "# Modules\nbilling", "kit/docs/modules/index.md")["outcome"] == "unchanged"
    # a file uploaded earlier by its bare name is the same document when its folder is uploaded
    old = up("guide.md", "# Guide\nv1")
    new = up("guide.md", "# Guide\nv2", "/kit/guide.md")
    assert new["document_id"] == old["document_id"] and new["outcome"] == "queued"
    assert {"kit/docs/modules/index.md", "kit/docs/workflows/index.md", "kit/guide.md"} <= _keys(cid)
    assert "guide.md" not in _keys(cid)
    bad = client.post("/api/documents", headers=h, data={"collection_id": cid, "path": "kit/../../etc/x.md"},
                      files={"file": ("x.md", b"# x")})
    assert bad.status_code == 422


def _wait(client, h, fid):
    for _ in range(100):
        f = next(x for x in client.get("/api/admin/folders", headers=h).json() if x["id"] == fid)
        if not f["syncing"] and f["result"]:
            return f
        time.sleep(0.1)
    raise AssertionError("sync did not finish")


def test_folder_source_syncs_only_the_difference(client, corpus, tmp_path):
    h, cid = token(client, "root"), collection_id("platform-wiki")
    kit = tmp_path / "repo-kit"
    for rel, text in {"AGENTS.md": "# Agents", "docs/modules/index.md": "# Modules", "docs/workflows/index.md": "# Flows",
                      "docs/llms.txt": "one line per page", "docs/_tools/lookup.md": "# tool",
                      ".claude/skills/x/SKILL.md": "# skill", "docs/_notes/n.md": "# notes", "docs/agent/e.jsonl": "{}",
                      "scripts/run.py": "print()", "site.yml": "a: 1", "logo.png": "png"}.items():
        (kit / rel).parent.mkdir(parents=True, exist_ok=True)
        (kit / rel).write_text(text)

    assert client.post("/api/admin/folders", headers=token(client, "alice"),
                       json={"path": str(kit), "collection_id": cid}).status_code == 404  # administrators only
    assert client.post("/api/admin/folders", headers=h, json={"path": "repo-kit", "collection_id": cid}).status_code == 400
    assert client.post("/api/admin/folders", headers=h,
                       json={"path": str(tmp_path / "nope"), "collection_id": cid}).status_code == 400
    r = client.post("/api/admin/folders", headers=h, json={"path": str(kit), "collection_id": cid, "types": ["markdown"]})
    assert r.status_code == 202, r.text
    f = _wait(client, h, r.json()["id"])["result"]
    assert (f["files"], f["queued"], f["removed"]) == (3, 3, 0), f
    assert f["skipped"]["folders starting with . or _"] == 3 and f["skipped"][".txt"] == 1 and f["skipped"][".py"] == 1
    assert {k for k in _keys(cid) if k.startswith("repo-kit/")} == {
        "repo-kit/AGENTS.md", "repo-kit/docs/modules/index.md", "repo-kit/docs/workflows/index.md"}

    # change one file, add one, delete one: only those change
    (kit / "docs/modules/index.md").write_text("# Modules\nnow with billing")
    (kit / "docs/new.md").write_text("# New page")
    (kit / "AGENTS.md").unlink()
    fid = r.json()["id"]
    client.post(f"/api/admin/folders/{fid}/sync", headers=h)
    f = _wait(client, h, fid)["result"]
    assert (f["files"], f["queued"], f["unchanged"], f["removed"]) == (3, 2, 1, 1), f
    assert "repo-kit/AGENTS.md" not in _keys(cid) and "repo-kit/docs/new.md" in _keys(cid)

    # stop syncing and remove its documents from the index; the files stay
    gone = client.delete(f"/api/admin/folders/{fid}?documents=true", headers=h).json()
    assert gone["documents_removed"] == 3 and not any(k.startswith("repo-kit/") for k in _keys(cid))
    assert (kit / "docs/new.md").exists()
