"""Activity for the admin Logs page: the app's recent calls with their request, response, timing and, for the
local language model, tokens — like a browser's Network tab.

Recorded: every API call that changes something or searches (POST/PUT/PATCH/DELETE), any API call that fails
or takes over a second, MCP calls from AI assistants, the local model's calls and each document indexed.
Routine page polling (fast, successful GETs) is left out so it does not push real work out of the list.

Unlike the logs, this holds search text, passages and prompts. It is kept in memory only (the last
MAX_CALLS calls, gone on restart), shown to administrators only (who can read every collection), never
written to disk and never part of "Copy for support". Passwords, tokens and cookies are masked.
"""

import itertools
import json
import threading
import time
from collections import deque
from contextvars import ContextVar
from datetime import UTC, datetime

MAX_CALLS = 300
MAX_CHARS = 30_000  # per request or response body kept
SECRET = ("password", "token", "secret", "authorization", "cookie")

_calls: "deque[dict]" = deque(maxlen=MAX_CALLS)
_ids = itertools.count(1)
_lock = threading.Lock()
current: ContextVar[int | None] = ContextVar("trace_call", default=None)  # the call being served (for nesting)


def _mask(x):
    if isinstance(x, dict):
        return {k: "•••" if any(s in str(k).lower() for s in SECRET) and v else _mask(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_mask(v) for v in x]
    return x


def _keep(x):
    """A body as stored: secrets masked; longer than MAX_CHARS -> the first MAX_CHARS characters as text."""
    if x is None:
        return None
    x = _mask(x)
    text = x if isinstance(x, str) else json.dumps(x, ensure_ascii=False, default=str)
    if len(text) > MAX_CHARS:
        return text[:MAX_CHARS] + f"\n… ({len(text) - MAX_CHARS} more characters not kept)"
    return x


def reserve() -> int:
    """An id for a call that is still running, so the calls it makes can point at it (record(cid=...))."""
    with _lock:
        return next(_ids)


def record(kind: str, title: str, *, ms: float, user: str = "", status: str = "ok", request=None, response=None,
           steps: list[tuple[str, float]] | None = None, tokens: dict | None = None, detail: str = "",
           cid: int | None = None, parent: int | None = None) -> int:
    """Add one call. kind: http | mcp | llm | index. steps: (name, milliseconds) in the order they ran."""
    with _lock:
        cid = cid or next(_ids)
        _calls.append({
            "id": cid, "parent": parent if parent is not None else current.get(),
            "time": datetime.now(UTC).isoformat(timespec="milliseconds"), "kind": kind, "title": title, "user": user, "status": status, "ms": round(ms, 1),
            "detail": detail, "tokens": tokens, "steps": [[n, round(v, 1)] for n, v in steps or []],
            "request": _keep(request), "response": _keep(response),
        })
    return cid


def calls(kind: str = "", limit: int = MAX_CALLS) -> list[dict]:
    """Newest first, without the bodies (the list view)."""
    with _lock:
        rows = [c for c in reversed(_calls) if not kind or c["kind"] == kind][:limit]
    return [{k: v for k, v in c.items() if k not in ("request", "response")} for c in rows]


def get(cid: int) -> dict | None:
    with _lock:
        return next((dict(c) for c in _calls if c["id"] == cid), None)


def clear() -> None:
    with _lock:
        _calls.clear()


class Timer:
    """Steps of one piece of work: t.step("parse") after each part; t.steps for record()."""

    def __init__(self):
        self.start = self._last = time.perf_counter()
        self.steps: list[tuple[str, float]] = []

    def step(self, name: str) -> None:
        now = time.perf_counter()
        self.steps.append((name, (now - self._last) * 1000))
        self._last = now

    @property
    def ms(self) -> float:
        return (time.perf_counter() - self.start) * 1000
