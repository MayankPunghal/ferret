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
