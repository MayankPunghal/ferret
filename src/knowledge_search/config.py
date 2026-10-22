"""Runtime configuration. Every setting comes from environment variables prefixed ``KS_``.

There are deliberately no generation / remote-inference settings: this release performs
no outbound AI calls. Model weights are fetched from Hugging Face only when not already in
``KS_MODEL_CACHE``; set ``KS_OFFLINE=1`` to forbid any download at runtime.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KS_", env_file=".env", extra="ignore")

    database_url: str = "postgresql://ks:devpass@127.0.0.1:55432/ks"
    data_dir: Path = Path("./data")
    model_cache: Path = Path("./data/models")
    embed_model: str = "BAAI/bge-small-en-v1.5"
    embed_threads: int = 0  # 0 = onnxruntime default
    offline: bool = False

    max_upload_mb: int = 25
    max_pdf_pages: int = 2000
    max_extracted_chars: int = 5_000_000

    # Background ingestion workers inside the API process. 0 = run `knowledge-search worker`
    # as a separate process instead (recommended on a server to protect query latency).
    inprocess_workers: int = 1
    worker_poll_seconds: float = 1.0

    session_hours: int = 12
    cookie_secure: bool = False  # set true behind TLS

    # Evidence-gate thresholds (cosine similarity on normalized vectors). Tuned on the
    # validation split of eval/queries.jsonl; see docs/EVALUATION.md before changing.
    gate_dense_high: float = 0.80
    gate_dense_min: float = 0.68
    gate_lex_coverage: float = 0.5


@lru_cache
def get_settings() -> Settings:
    return Settings()
