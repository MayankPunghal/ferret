"""Administrator → Database: the app's PostgreSQL tables, their structure and rows, read-only.

Browsing uses fixed queries (table and column names checked against the catalogue) in read-only
transactions with a time limit. The SQL box runs the administrator's own query as a separate role that is
not a superuser, can only read, and cannot see password hashes or session/token hashes. With the embedded
database the app creates that role itself (ks_reader); with an external server an administrator of that server
creates one and sets KS_READONLY_DATABASE_URL. Without it, the SQL box is off; browsing still works.
Shown values: secrets masked, vectors and long text shortened (the full value of one row on request).
"""

import datetime
import decimal
import json
import logging
import time
import uuid

import numpy as np
import psycopg
from fastapi import APIRouter, HTTPException
from psycopg import sql
from pydantic import BaseModel, Field

from . import auth, db
from .deps import Admin, Conn, state

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/db", tags=["admin"])
READER = "ks_reader"
SECRET = ("password", "token", "secret", "cookie")
CUT = 300  # characters shown per value in tables
FULL = 100_000  # characters shown for one row's full values
MAX_ROWS = 500  # rows returned by the SQL box


def _secret(column: str) -> bool:
    return any(s in column.lower() for s in SECRET)


def show(value, column: str = "", full: bool = False):
    """A value as the page shows it: JSON-safe, secrets masked, long values shortened."""
    if value is None:
        return None
    if _secret(column):
        return "•••"
    if isinstance(value, bool | int | float):
        return value
    if hasattr(value, "to_numpy"):  # pgvector's Vector / HalfVector
        value = value.to_numpy()
    if isinstance(value, np.ndarray):
        head = ", ".join(f"{x:.4f}" for x in value[:8])
        return f"[{head}{', …' if len(value) > 8 else ''}] ({len(value)} numbers)"
    if isinstance(value, bytes | memoryview):
        return f"({len(value)} bytes)"
    if isinstance(value, dict | list):
        value = json.dumps(value, ensure_ascii=False, default=str)
    elif isinstance(value, datetime.datetime | datetime.date | uuid.UUID | decimal.Decimal):
        value = str(value)
    text = str(value)
    limit = FULL if full else CUT
    return text if len(text) <= limit else text[:limit] + f" … ({len(text):,} characters)"


def _read_only(conn, seconds: int = 10) -> None:
    conn.execute("SET TRANSACTION READ ONLY")
    conn.execute(f"SET LOCAL statement_timeout = '{int(seconds)}s'")


def _tables(conn) -> dict[str, str]:
    """Tables, views and materialised views in the app's schema: name -> kind."""
    kinds = {"r": "table", "p": "table", "v": "view", "m": "view"}
    rows = conn.execute("SELECT c.relname, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm')").fetchall()
    return {r["relname"]: kinds[r["relkind"]] for r in rows}


def _table(conn, name: str) -> str:
    if name not in _tables(conn):
        raise HTTPException(404, "no such table")
    return name


def _columns(conn, table: str) -> list[dict]:
    return conn.execute(
        "SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type, a.attnotnull AS not_null, "
        "pg_get_expr(d.adbin, d.adrelid) AS default, a.attgenerated <> '' AS generated "
        "FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
        "WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum",
        (f'public."{table}"',)).fetchall()


@router.get("")
def overview(p: Admin, conn: Conn):
    """The server, its size and extensions, and every table with its row count and size."""
    with conn.transaction():
        _read_only(conn)
        info = conn.execute("SELECT current_setting('server_version') AS version, current_database() AS database, "
                            "pg_database_size(current_database()) AS bytes").fetchone()
        ext = conn.execute("SELECT extname AS name, extversion AS version FROM pg_extension ORDER BY 1").fetchall()
        rows = conn.execute(
            "SELECT c.relname AS name, c.relkind, c.reltuples::bigint AS estimate, pg_total_relation_size(c.oid) AS bytes, "
            "(SELECT count(*) FROM pg_index i WHERE i.indrelid = c.oid) AS indexes "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm') ORDER BY c.relname").fetchall()
        tables = []
        for r in rows:
            n = None
            if r["relkind"] in ("r", "p") and r["estimate"] < 200_000:  # exact when cheap; else the planner's estimate
                n = conn.execute(sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(r["name"]))).fetchone()["n"]
            tables.append({"name": r["name"], "kind": "table" if r["relkind"] in ("r", "p") else "view",
                           "rows": n if n is not None else max(r["estimate"], 0) if r["relkind"] in ("r", "p") else None,
                           "estimated": n is None and r["relkind"] in ("r", "p"), "bytes": r["bytes"],
                           "indexes": r["indexes"]})
    s = state.settings
    return {**info, "extensions": ext, "tables": tables,
            "mode": "external server" if s.database_url else f"embedded ({s.data_dir / 'pg'})",
            "sql_available": bool(_reader_url(conn))}


@router.get("/tables/{name}")
def structure(name: str, p: Admin, conn: Conn):
    """Columns, indexes, constraints and, for a view, its definition."""
    with conn.transaction():
        _read_only(conn)
        kind = _tables(conn).get(_table(conn, name))
        reg = (f'public."{name}"',)
        return {
            "name": name, "kind": kind, "columns": _columns(conn, name),
            "indexes": conn.execute(
                "SELECT i.relname AS name, pg_get_indexdef(i.oid) AS definition, pg_relation_size(i.oid) AS bytes "
                "FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid WHERE x.indrelid = %s::regclass ORDER BY 1",
                reg).fetchall(),
            "constraints": conn.execute(
                "SELECT conname AS name, pg_get_constraintdef(oid) AS definition FROM pg_constraint "
                "WHERE conrelid = %s::regclass ORDER BY contype, conname", reg).fetchall(),
            "definition": conn.execute("SELECT pg_get_viewdef(%s::regclass, true) AS d", reg).fetchone()["d"]
            if kind == "view" else None,
            "bytes": conn.execute("SELECT pg_total_relation_size(%s::regclass) AS b", reg).fetchone()["b"],
        }


@router.get("/tables/{name}/rows")
def rows(name: str, p: Admin, conn: Conn, offset: int = 0, limit: int = 50, sort: str = "", desc: bool = False,
         column: str = "", contains: str = "", full: bool = False):
    """One page of rows, optionally sorted and filtered (a column's text contains a value)."""
    with conn.transaction():
        _read_only(conn)
        cols = [c["name"] for c in _columns(conn, _table(conn, name))]
        if (sort and sort not in cols) or (column and column not in cols):
            raise HTTPException(400, "no such column")
        where, params = sql.SQL(""), []
        if column and contains:
            if _secret(column):
                raise HTTPException(400, "this column is hidden")
            where = sql.SQL(" WHERE {}::text ILIKE %s").format(sql.Identifier(column))
            params = [f"%{contains}%"]
        order = sql.SQL(" ORDER BY {} {} NULLS LAST").format(sql.Identifier(sort or cols[0]), sql.SQL("DESC" if desc else "ASC"))
        limit = max(1, min(limit, 200))
        q = sql.SQL("SELECT * FROM {}").format(sql.Identifier(name)) + where + order + sql.SQL(" LIMIT %s OFFSET %s")
        found = conn.execute(q, [*params, limit, max(0, offset)]).fetchall()
        try:
            with conn.transaction():
                total = conn.execute(sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(name)) + where,
                                     params).fetchone()["n"]
        except psycopg.errors.QueryCanceled:
            total = None  # too many to count within the time limit
    return {"columns": cols, "total": total, "offset": offset,
            "rows": [[show(r[c], c, full) for c in cols] for r in found]}


# --- SQL box ------------------------------------------------------------------------------------

def _reader_url(conn) -> str:
    """Where the SQL box connects: the configured read-only role, or the embedded server as ks_reader."""
    if state.settings.readonly_database_url:
        return state.settings.readonly_database_url
    if db._embedded is None:
        return ""
    params = conn.info.get_parameters()
    return psycopg.conninfo.make_conninfo(**{k: params[k] for k in ("host", "port", "dbname") if k in params}, user=READER)


def prepare_reader(conn) -> None:
    """Embedded database: (re)create the read-only role and grant it every table, minus secret columns."""
    if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (READER,)).fetchone():
        conn.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT").format(sql.Identifier(READER)))
    r = sql.Identifier(READER)
    conn.execute(sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(r))
    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(r))
    for table in _tables(conn):
        t = sql.Identifier(table)
        cols = [c["name"] for c in _columns(conn, table)]
        conn.execute(sql.SQL("REVOKE ALL ON {} FROM {}").format(t, r))
        if any(_secret(c) for c in cols):
            keep = [sql.Identifier(c) for c in cols if not _secret(c)]
            conn.execute(sql.SQL("GRANT SELECT ({}) ON {} TO {}").format(sql.SQL(", ").join(keep), t, r))
        else:
            conn.execute(sql.SQL("GRANT SELECT ON {} TO {}").format(t, r))
    conn.commit()


class Query(BaseModel):
    sql: str = Field(min_length=1, max_length=20_000)


@router.post("/query")
def query(body: Query, p: Admin, conn: Conn):
    """Run one read-only statement as the reader role: at most MAX_ROWS rows, 15 s."""
    url = _reader_url(conn)
    if not url:
        raise HTTPException(409, "The SQL box needs a read-only database role. With an external database, create one "
                                 "and set KS_READONLY_DATABASE_URL.")
    if not state.settings.readonly_database_url:
        prepare_reader(conn)
    auth.audit(conn, p.user_id, "db_query", None, sql=body.sql[:2000])
    conn.commit()
    t = time.perf_counter()
    try:
        with psycopg.connect(url, options="-c statement_timeout=15000 -c default_transaction_read_only=on") as c:
            c.read_only = True
            # prepare=True sends the text as one prepared statement: the server refuses a second statement
            cur = c.execute(body.sql, prepare=True)
            if cur.description is None:
                return {"columns": [], "rows": [], "more": False, "ms": round((time.perf_counter() - t) * 1000, 1),
                        "message": cur.statusmessage}
            cols = [d.name for d in cur.description]
            got = cur.fetchmany(MAX_ROWS + 1)
    except psycopg.Error as e:
        msg = (e.diag.message_primary if getattr(e, "diag", None) and e.diag.message_primary else str(e)).strip()
        raise HTTPException(400, msg[:1000]) from None
    return {"columns": cols, "rows": [[show(v, c) for v, c in zip(r, cols, strict=True)] for r in got[:MAX_ROWS]],
            "more": len(got) > MAX_ROWS, "ms": round((time.perf_counter() - t) * 1000, 1)}
