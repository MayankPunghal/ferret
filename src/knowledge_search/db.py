"""PostgreSQL connection pool, embedded-server bootstrap and forward-only SQL migrations.

With ``KS_DATABASE_URL`` unset, an embedded PostgreSQL (+pgvector) from the pip package
``pixeltable-pgserver`` runs under ``KS_DATA_DIR/pg`` — no Docker or system install needed (laptop
profile). Servers point ``KS_DATABASE_URL`` at an external PostgreSQL with pgvector instead.
"""

from importlib import resources
from pathlib import Path

from pgvector.psycopg import register_vector
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_pool: ConnectionPool | None = None
_embedded = None
_embedded_pgdata: Path | None = None  # keeps the embedded server handle alive for this process


WINPTHREAD_SHA256 = "817e5b2636f532666e8f255e225af75e9d0c41b141512fde45264070a65b1c92"


def _ensure_windows_runtime(package_dir: Path | None = None, force: bool = False) -> None:
    """Windows only: put the MinGW runtime DLL that postgres.exe needs (but the wheel lacks) next to it.
    See vendor/win64/SOURCE.md. A DLL in the executable's folder wins over any copy on PATH."""
    import hashlib
    import os
    import shutil

    if os.name != "nt" and not force:
        return
    if package_dir is None:
        import pixeltable_pgserver

        package_dir = Path(pixeltable_pgserver.__file__).parent
    src = Path(__file__).parent / "vendor" / "win64" / "libwinpthread-1.dll"
    for bindir in package_dir.glob("pginstall*/bin"):
        dst = bindir / "libwinpthread-1.dll"
        if not dst.exists() or hashlib.sha256(dst.read_bytes()).hexdigest() != WINPTHREAD_SHA256:
            shutil.copyfile(src, dst)


def _discard_failed_init(pgdata: Path) -> None:
    """A data directory without PG_VERSION holds no database (initdb failed midway); move it aside so
    the next start can initialise cleanly instead of failing on a non-empty directory."""
    import time

    if pgdata.is_dir() and any(pgdata.iterdir()) and not (pgdata / "PG_VERSION").exists():
        pgdata.rename(pgdata.with_name(f"{pgdata.name}.failed-init-{int(time.time())}"))


def _wait_for_postgres(pgdata: Path, timeout: float = 60.0) -> bool:
    """True once the server whose postmaster.pid is in this folder answers on its port.

    pixeltable starts the server with `pg_ctl -w` under a 10 s subprocess timeout. On a cold disk `pg_ctl`
    itself can need longer than that, and pixeltable then re-raises and the app exits — while the server
    comes up a moment later (reproduced twice on the owner's machine, 2026-10-26; it is exactly what
    "the update did not restart Ferret" looked like). The server is fine: wait for it instead of dying.
    """
    import time

    import psycopg

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            port = int((pgdata / "postmaster.pid").read_text().splitlines()[3])
        except (OSError, IndexError, ValueError):  # not written yet, or not a pid file we understand
            time.sleep(1.0)
            continue
        try:
            with psycopg.connect(f"postgresql://postgres@127.0.0.1:{port}/postgres", connect_timeout=2) as c:
                c.execute("SELECT 1")
            return True
        except Exception:  # noqa: BLE001 - not listening yet
            time.sleep(1.0)
    return False


def _admin_url(pgdata: Path) -> str:
    """The admin URL of the server in this data folder, read from its pid file."""
    port = int((pgdata / "postmaster.pid").read_text().splitlines()[3])
    return f"postgresql://postgres@127.0.0.1:{port}/postgres"


def _stop_pg_ctl(pgdata: Path) -> None:
    """Stop a server that was attached to through its pid file (pixeltable could not hand us an object)."""
    import subprocess

    import pixeltable_pgserver

    for ctl in (Path(pixeltable_pgserver.__file__).parent).glob("pginstall*/bin/pg_ctl*"):
        subprocess.run([str(ctl), "stop", "-m", "fast", "-D", str(pgdata)], capture_output=True, check=False,
                       creationflags=0x08000000)
        return


def start_embedded(pgdata: Path, database: str = "ks", cleanup_mode: str = "stop") -> tuple[str, str]:
    """Start (or attach to) the embedded server; return (admin_url, database_url)."""
    import subprocess
    import time

    import psycopg
    from pixeltable_pgserver import get_server

    global _embedded, _embedded_pgdata
    _ensure_windows_runtime()
    _discard_failed_init(pgdata)
    pgdata.mkdir(parents=True, exist_ok=True)
    last: subprocess.SubprocessError | None = None
    for attempt in range(3):
        try:
            _embedded = get_server(pgdata.resolve(), cleanup_mode=cleanup_mode)
            break
        except subprocess.SubprocessError as error:  # pg_ctl timed out; the server may still be coming up
            last = error
            if not _wait_for_postgres(pgdata, timeout=60 if attempt == 0 else 30):
                time.sleep(2)
    else:
        assert last is not None
        raise last
    try:
        admin = _embedded.get_uri() if _embedded is not None else ""
    except Exception:  # noqa: BLE001 - pixeltable came back without postmaster info
        admin = ""
    if not admin:
        # the server answers but pixeltable's object is empty: use the pid file, and stop it ourselves later
        admin = _admin_url(pgdata)
        _embedded, _embedded_pgdata = None, pgdata
    with psycopg.connect(admin, autocommit=True) as c:
        if not c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,)).fetchone():
            c.execute(f'CREATE DATABASE "{database}"')
    return admin, admin.rsplit("/", 1)[0] + f"/{database}"


def resolve_url(settings) -> str:
    """The configured external database, or the embedded one (started on demand)."""
    if settings.database_url:
        return settings.database_url
    return start_embedded(Path(settings.data_dir) / "pg")[1]


def release_embedded() -> None:
    """This process no longer uses the embedded server; the last process to release it stops it. Called on
    app shutdown: the web server re-raises Ctrl+C / SIGTERM after shutting down, so exit hooks never run and
    the database would keep running after the app (unsafe to copy for a backup)."""
    if _embedded is not None:
        _embedded._cleanup()  # pixeltable_pgserver's own last-handle logic; idempotent with its exit hook
    elif _embedded_pgdata is not None:  # attached through the pid file: stop it the same way stop.ps1 would
        _stop_pg_ctl(_embedded_pgdata)


def _configure(conn: Connection) -> None:
    register_vector(conn)


def open_pool(database_url: str, max_size: int = 10) -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            database_url,
            min_size=1,
            max_size=max_size,
            kwargs={"row_factory": dict_row, "autocommit": False},
            configure=_configure,
            open=True,
        )
    return _pool


def pool() -> ConnectionPool:
    if _pool is None:
        raise RuntimeError("database pool not opened")
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def migrate(database_url: str) -> list[str]:
    """Apply pending migrations in filename order inside one transaction each."""
    import psycopg

    applied: list[str] = []
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(727274)")  # serialize concurrent starters
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
        files = sorted(
            f for f in resources.files("knowledge_search.migrations").iterdir() if f.name.endswith(".sql")
        )
        for f in files:
            if f.name in done:
                continue
            with conn.transaction():
                conn.execute(f.read_text())
                conn.execute("INSERT INTO schema_migrations(name) VALUES (%s)", (f.name,))
            applied.append(f.name)
    if "0005_document_vectors.sql" in applied:
        _backfill_document_vectors(database_url)
    return applied


def _backfill_document_vectors(database_url: str) -> None:
    """Document vector = mean of its passage vectors, computed here rather than with pgvector's avg() in SQL
    (which crashes the embedded PostgreSQL on Windows)."""
    import numpy as np
    import psycopg

    with psycopg.connect(database_url) as conn:
        register_vector(conn)
        docs = [r[0] for r in conn.execute(
            "SELECT DISTINCT document_id FROM chunks WHERE embedding IS NOT NULL").fetchall()]
        for d in docs:
            rows = conn.execute("SELECT embedding, embedding_model FROM chunks WHERE document_id = %s "
                                "AND embedding IS NOT NULL", (d,)).fetchall()
            conn.execute("UPDATE documents SET embedding = %s, embedding_model = %s WHERE id = %s",
                         (np.mean([r[0].to_numpy() for r in rows], axis=0), min(r[1] for r in rows), d))
