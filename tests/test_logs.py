"""Admin Logs page backend: captured errors with tracebacks, diagnostics, privacy, Windows noise filter."""

import logging

import knowledge_search.api as api
from knowledge_search.logs import WindowsDisconnectNoise

from .conftest import token


def test_logs_capture_errors_without_query_text(client, corpus, monkeypatch):
    alice, root = token(client, "alice"), token(client, "root")
    assert client.get("/api/admin/logs", headers=alice).status_code == 404

    def boom(*a, **k):
        raise RuntimeError("simulated failure for the logs test")

    monkeypatch.setattr(api, "search", boom)
    secret_query = "confidential quarterly numbers zebra-42"
    assert client.post("/api/search", headers=alice, json={"query": secret_query}).status_code == 503
    r = client.get("/api/admin/logs?level=ERROR", headers=root).json()
    rec = [x for x in r["records"] if "search failed" in x["message"]][-1]
    assert "RuntimeError: simulated failure for the logs test" in rec["traceback"]
    assert secret_query not in str(r)  # query text is never logged
    d = r["diagnostics"]
    assert d["python"] and d["os"] and d["embedding_model"] and d["database"] in ("embedded", "external")


def test_windows_disconnect_noise_is_filtered_but_real_errors_are_not():
    f = WindowsDisconnectNoise()

    def rec(name, msg, exc):
        try:
            raise exc
        except Exception as e:  # noqa: BLE001
            return logging.LogRecord(name, logging.ERROR, __file__, 1, msg, None, (type(e), e, e.__traceback__))

    noise = rec("asyncio", "Exception in callback _ProactorBasePipeTransport._call_connection_lost()",
                ConnectionResetError(10054, "An existing connection was forcibly closed by the remote host"))
    assert f.filter(noise) is False
    assert f.filter(rec("asyncio", "Task exception was never retrieved", RuntimeError("x"))) is True
    assert f.filter(rec("knowledge_search.api", "search failed", ConnectionResetError())) is True
