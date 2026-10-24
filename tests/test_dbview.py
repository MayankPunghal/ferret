"""Administrator → Database (dbview.py): browse tables read-only; the SQL box cannot write, escalate or see secrets."""

from .conftest import token


def test_browse_tables_structure_and_rows(client, corpus):
    root, bob = token(client, "root"), token(client, "bob")
    assert client.get("/api/admin/db", headers=bob).status_code == 404  # administrators only
    o = client.get("/api/admin/db", headers=root).json()
    names = {t["name"]: t for t in o["tables"]}
    assert {"documents", "chunks", "users", "sessions"} <= set(names) and names["documents"]["rows"] > 0
    assert any(e["name"] == "vector" for e in o["extensions"]) and o["sql_available"] and o["version"]
    s = client.get("/api/admin/db/tables/chunks", headers=root).json()
    assert "embedding" in {c["name"] for c in s["columns"]} and s["indexes"] and s["constraints"]
    r = client.get("/api/admin/db/tables/chunks/rows?limit=3", headers=root).json()
    emb = r["columns"].index("embedding")
    assert len(r["rows"]) == 3 and r["total"] > 3 and "numbers)" in r["rows"][0][emb]
    # secrets are masked in rows and cannot be filtered on
    u = client.get("/api/admin/db/tables/users/rows", headers=root).json()
    pw = u["columns"].index("password_hash")
    assert {row[pw] for row in u["rows"]} == {"•••"}
    assert client.get("/api/admin/db/tables/users/rows?column=password_hash&contains=a", headers=root).status_code == 400
    # filter and sort
    f = client.get("/api/admin/db/tables/users/rows?column=username&contains=bo&sort=username&desc=true", headers=root).json()
    assert [row[u["columns"].index("username")] for row in f["rows"]] == ["bob"] and f["total"] == 1
    assert client.get("/api/admin/db/tables/nope/rows", headers=root).status_code == 404
    assert client.get("/api/admin/db/tables/users/rows?sort=nope", headers=root).status_code == 400
    full = client.get("/api/admin/db/tables/chunks/rows?limit=1&full=true", headers=root).json()
    assert "…" not in str(full["rows"][0][full["columns"].index("text")])


def test_sql_box_reads_only_and_never_sees_secrets(client, corpus):
    root = token(client, "root")

    def q(text):
        return client.post("/api/admin/db/query", headers=root, json={"sql": text})

    ok = q("SELECT username FROM users ORDER BY username")
    assert ok.status_code == 200 and ["bob"] in ok.json()["rows"], ok.text
    assert q("SELECT count(*) FROM chunks").json()["rows"][0][0] > 0
    for bad in ("SELECT password_hash FROM users",            # secret column not granted
                "SELECT token_hash FROM sessions",
                "DELETE FROM documents",                       # read-only transaction, and no write grant
                "CREATE TABLE x (a int)",
                "SELECT 1; DELETE FROM documents",             # one statement only
                "SELECT pg_read_file('/etc/hostname')",        # not a superuser
                "SELECT set_config('role', 'postgres', false)",
                "COPY (SELECT 1) TO PROGRAM 'true'"):
        r = q(bad)
        assert r.status_code == 400, (bad, r.text)
    assert client.get("/api/admin/db/tables/documents/rows", headers=root).json()["total"] > 0  # nothing was deleted
    big = q("SELECT g FROM generate_series(1, 2000) g").json()
    assert len(big["rows"]) == 500 and big["more"]
    audit = q("SELECT action, detail->>'sql' FROM audit_events WHERE action = 'db_query' ORDER BY id DESC LIMIT 1").json()
    assert audit["rows"][0][0] == "db_query"
