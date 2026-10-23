"""Retry wrapper for model loading. fastembed retries a download only briefly; on unstable networks a
connection reset aborts setup. Already-downloaded files stay in the cache, so a retry resumes cheaply."""

import logging
import time

log = logging.getLogger(__name__)

DELAYS = (5, 15, 30, 60)  # seconds between attempts (5 attempts in total)


def _transient(e: Exception) -> bool:
    """Network-type failures. fastembed reports failed downloads as ValueError('... from any source')."""
    text = str(e)
    return isinstance(e, (OSError, TimeoutError)) or "from any source" in text or "onnection" in text


def with_retries(load, what: str, offline: bool = False):
    """Call `load()`; retry network-type failures with backoff (never offline: a missing file won't appear)."""
    for attempt, delay in enumerate((*DELAYS, None), start=1):
        try:
            return load()
        except Exception as e:  # noqa: BLE001 - network/download errors come in many types
            if offline or delay is None or not _transient(e):
                raise
            log.warning("could not load %s (attempt %d/%d: %s); retrying in %ds — downloaded files are kept",
                        what, attempt, len(DELAYS) + 1, str(e).splitlines()[0][:160], delay)
            time.sleep(delay)
