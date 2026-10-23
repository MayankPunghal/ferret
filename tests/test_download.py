"""Model-download retry behaviour (network failures on Windows aborted setup)."""

import pytest

from knowledge_search import download


def test_retries_until_success(monkeypatch):
    monkeypatch.setattr(download, "DELAYS", (0, 0, 0))
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionResetError("[WinError 10054] An existing connection was forcibly closed")
        return "model"

    assert download.with_retries(flaky, "test model") == "model" and len(calls) == 3


def test_gives_up_after_last_attempt_and_never_retries_offline(monkeypatch):
    monkeypatch.setattr(download, "DELAYS", (0, 0))
    calls = []

    def broken():
        calls.append(1)
        raise ValueError("Could not load model from any source")

    with pytest.raises(ValueError):
        download.with_retries(broken, "test model")
    assert len(calls) == 3
    calls.clear()
    with pytest.raises(ValueError):
        download.with_retries(broken, "test model", offline=True)
    assert len(calls) == 1


def test_xet_transfer_disabled_by_default():
    import os

    import knowledge_search  # noqa: F401  (sets the defaults on import)

    assert os.environ["HF_HUB_DISABLE_XET"] == "1"


def test_non_network_errors_are_not_retried(monkeypatch):
    monkeypatch.setattr(download, "DELAYS", (0, 0))
    calls = []

    def unsupported():
        calls.append(1)
        raise ValueError("Model x/y is not supported in TextCrossEncoder")

    with pytest.raises(ValueError):
        download.with_retries(unsupported, "test model")
    assert len(calls) == 1
