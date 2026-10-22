"""PostgreSQL connection pool and forward-only SQL migrations."""

from importlib import resources

from pgvector.psycopg import register_vector
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_pool: ConnectionPool | None = None


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
    return applied
