"""Embedded-database helpers (pure unit tests; the integration tests exercise the real server)."""

import hashlib

from knowledge_search import db


def test_windows_runtime_dll_is_placed_next_to_postgres(tmp_path):
    bindir = tmp_path / "pginstall18" / "bin"
    bindir.mkdir(parents=True)
    (bindir / "libwinpthread-1.dll").write_bytes(b"wrong 32-bit copy")
    db._ensure_windows_runtime(tmp_path, force=True)
    data = (bindir / "libwinpthread-1.dll").read_bytes()
    assert hashlib.sha256(data).hexdigest() == db.WINPTHREAD_SHA256
    assert data[:2] == b"MZ"


def test_failed_init_directory_is_moved_aside(tmp_path):
    pg = tmp_path / "pg"
    pg.mkdir()
    (pg / "half-written").write_text("x")
    db._discard_failed_init(pg)
    assert not pg.exists() and len(list(tmp_path.glob("pg.failed-init-*"))) == 1
    ok = tmp_path / "ok"
    ok.mkdir()
    (ok / "PG_VERSION").write_text("18")
    db._discard_failed_init(ok)
    assert (ok / "PG_VERSION").exists()  # a real database is never touched
