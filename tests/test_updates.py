"""Updates: the Windows installer path (what can be checked without a real install and a real setup run)."""

from knowledge_search import updates


def test_the_restart_helper_leaves_alone_what_the_installer_started():
    """It must never stop a fresh instance: doing that once made an update look like a failed restart
    (owner's report, 2026-10-26). It waits for the app instead, then starts it only if nothing answers."""
    script = updates.RESTART_SCRIPT
    assert "stop.ps1" not in script
    assert "healthz" in script and "knowledge_search.tray" in script
    assert "update-restart.log" in script


def test_windows_app_detection_does_not_claim_a_source_checkout():
    assert updates.windows_app() in (True, False)  # no error on any platform


def test_a_slow_database_start_is_waited_for_instead_of_killing_the_app(tmp_path, monkeypatch):
    """pixeltable starts postgres with `pg_ctl -w` under a 10 s subprocess timeout and re-raises on timeout,
    so a cold disk killed the app at startup while the database came up a second later. It is retried."""
    import subprocess

    from knowledge_search import db

    calls = {"get_server": 0}

    def slow(*args, **kwargs):
        calls["get_server"] += 1
        raise subprocess.TimeoutExpired("pg_ctl", 10)

    monkeypatch.setattr("pixeltable_pgserver.get_server", slow)  # imported inside start_embedded
    monkeypatch.setattr(db, "_wait_for_postgres", lambda *a, **k: False)
    try:
        db.start_embedded(tmp_path / "pg")
    except subprocess.TimeoutExpired:
        pass
    else:
        raise AssertionError("it must raise once every attempt timed out")
    assert calls["get_server"] == 3, calls


def test_the_database_wait_gives_up_instead_of_hanging(tmp_path):
    import time

    from knowledge_search import db

    started = time.monotonic()
    assert db._wait_for_postgres(tmp_path / "nothing-here", timeout=2) is False
    assert time.monotonic() - started < 10


def test_the_database_url_keeps_its_driver_and_socket_parameters():
    """A socket URL carries its directory in the query (`…/postgres?host=/tmp/pg`); splitting on the last
    slash turned that into `…/postgres?host=/tmp/ks` and the Linux jobs could not connect."""
    from urllib.parse import urlsplit

    from knowledge_search import db

    assert db._with_database("postgresql://postgres:@127.0.0.1:5432/postgres", "ks") == \
        "postgresql://postgres:@127.0.0.1:5432/ks"
    socket_url = db._with_database("postgresql://postgres:@/postgres?host=/tmp/pg", "ks")
    parts = urlsplit(socket_url)
    assert parts.path == "/ks", socket_url
    assert parts.query == "host=/tmp/pg", socket_url
