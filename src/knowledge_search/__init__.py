"""Knowledge Search: CPU-first, retrieval-only enterprise document search."""

import os

# Model downloads (Hugging Face): use plain HTTPS instead of the parallel "xet" transfer protocol, which
# failed on Windows with WinError 10054/10038 (connection reset / not a socket) on flaky connections, and
# allow slow networks more time. Must be set before huggingface_hub is imported; users can override.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "60")
os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "30")


def main() -> None:
    from .cli import main as cli_main

    cli_main()
