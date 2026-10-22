"""Integration test fixtures: a real PostgreSQL+pgvector database and the real local embedding model.

No external services needed: an embedded PostgreSQL+pgvector is started in a temp dir (or set
KS_TEST_ADMIN_URL / KS_TEST_DATABASE_URL). The database ``ks_test`` is recreated on every run.
"""

import os
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from knowledge_search import auth, db
from knowledge_search.api import create_app, state
from knowledge_search.config import Settings
from knowledge_search.embedder import Embedder
from knowledge_search.ingest import Worker

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "fixtures" / "corpus"
# Default: a throwaway embedded PostgreSQL per test session. Set KS_TEST_ADMIN_URL (and
# KS_TEST_DATABASE_URL) to run against an external server instead, e.g. the docker compose db.
ADMIN_URL = os.environ.get("KS_TEST_ADMIN_URL")
TEST_URL = os.environ.get("KS_TEST_DATABASE_URL")
MODEL_CACHE = os.environ.get("KS_MODEL_CACHE", str(ROOT / "data" / "models"))
PASSWORD = "correct-horse-battery"


@pytest.fixture(scope="session")
def database(tmp_path_factory):
    global ADMIN_URL, TEST_URL
    if not ADMIN_URL:
        ADMIN_URL, _ = db.start_embedded(tmp_path_factory.mktemp("pg"), database="postgres", cleanup_mode="delete")
        TEST_URL = db._embedded.get_uri("ks_test")
    with psycopg.connect(ADMIN_URL, autocommit=True) as c:
        c.execute("DROP DATABASE IF EXISTS ks_test WITH (FORCE)")
        c.execute("CREATE DATABASE ks_test")
    db.migrate(TEST_URL)
    with psycopg.connect(TEST_URL) as c:
        from psycopg.rows import dict_row

        c.row_factory = dict_row
        for u, admin in [("alice", False), ("bob", False), ("hannah", False), ("sam", False), ("root", True)]:
            auth.create_user(c, u, PASSWORD, admin)
        for col, groups in {"engineering": ["staff", "hr"], "finance": ["staff", "hr"], "hr": ["hr"],
                            "platform-wiki": ["staff", "hr"]}.items():
            for g in groups:
                auth.grant(c, col, g)
        auth.grant(c, "engineering", "eng-writers", can_write=True)
        auth.grant(c, "scratch", "scratchers", can_write=True)
        for u, g in [("alice", "staff"), ("alice", "eng-writers"), ("bob", "staff"), ("hannah", "hr"),
                     ("sam", "scratchers")]:
            auth.add_user_to_group(c, u, g)
    return TEST_URL


@pytest.fixture(scope="session")
def embedder():
    e = Embedder("BAAI/bge-small-en-v1.5", MODEL_CACHE, offline=True)
    e.warm()
    return e


@pytest.fixture(scope="session")
def client(database, embedder, tmp_path_factory):
    # vector_search="memory": the laptop default (embedded DB); test_vectors also checks the pgvector path.
    settings = Settings(database_url=database, data_dir=tmp_path_factory.mktemp("data"), model_cache=Path(MODEL_CACHE),
                        vector_search="memory", embed_model="BAAI/bge-small-en-v1.5", auto_reindex=False,
                        inprocess_workers=0)
    with TestClient(create_app(settings, embedder, start_workers=False)) as c:
        yield c


@pytest.fixture(scope="session")
def drain(client):
    def _drain() -> int:
        s = state.settings
        return Worker(state.blobs, s.max_pdf_pages, s.max_extracted_chars).drain()

    return _drain


def token(client, user: str) -> dict:
    r = client.post("/api/auth/login", json={"username": user, "password": PASSWORD})
    assert r.status_code == 200, r.text
    client.cookies.clear()  # tests authenticate with bearer tokens unless testing cookies
    return {"Authorization": f"Bearer {r.json()['token']}"}


def collection_id(name: str) -> int:
    with db.pool().connection() as c:
        return c.execute("SELECT id FROM collections WHERE name = %s", (name,)).fetchone()["id"]


@pytest.fixture(scope="session")
def corpus(client, drain):
    """Upload the fictional fixture corpus through the API as root and index it."""
    h = token(client, "root")
    for col_dir in sorted(CORPUS.iterdir()):
        cid = collection_id(col_dir.name)
        for f in sorted(col_dir.iterdir()):
            r = client.post("/api/documents", headers=h, data={"collection_id": cid, "source_uri": f"fixture://{f.name}"},
                            files={"file": (f.name, f.read_bytes())})
            assert r.status_code == 202, r.text
    drain()
    with db.pool().connection() as c:
        bad = c.execute("SELECT title, status, error_code FROM documents WHERE status <> 'indexed'").fetchall()
    assert not bad, bad
    return True


def search(client, headers, query, **kw):
    r = client.post("/api/search", headers=headers, json={"query": query, **kw})
    assert r.status_code == 200, r.text
    return r.json()
