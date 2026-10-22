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

    # Empty = embedded PostgreSQL under data_dir/pg (laptop). Set for an external server, e.g.
    # postgresql://ks:secret@db:5432/ks (docker-compose server profile).
    database_url: str = ""
    data_dir: Path = Path("./data")
    model_cache: Path = Path("./data/models")
    embed_model: str = "BAAI/bge-small-en-v1.5"
    embed_threads: int = 0  # 0 = onnxruntime default
    offline: bool = False

    # Defaults for admin-editable runtime settings (Settings page / app_settings table override these).
    chunk_size: int = 900
    chunk_overlap: int = 150
    rerank_enabled: bool = False
    rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    rerank_candidates: int = 30
    gate_mode: str = "similarity"  # similarity | rerank | rerank_and_similarity (needs reranker)
    gate_lexical: str = "coverage"  # coverage | idf
    gate_rerank_min: float = 0.0

    max_upload_mb: int = 25
    max_pdf_pages: int = 2000
    max_extracted_chars: int = 5_000_000

    # Background ingestion workers inside the API process. 0 = run `knowledge-search worker`
    # as a separate process instead (recommended on a server to protect query latency).
    inprocess_workers: int = 1
    worker_poll_seconds: float = 1.0

    session_hours: int = 12
    cookie_secure: bool = False  # set true behind TLS

    # Evidence-gate thresholds (cosine similarity on L2-normalized vectors) for the default
    # model BAAI/bge-small-en-v1.5, chosen on the *val* split of eval/queries.jsonl as the highest
    # answer coverage with zero val false answers. They are model-specific: re-run
    # eval/run_eval.py after changing KS_EMBED_MODEL. See docs/EVALUATION.md.
    gate_dense_high: float = 0.88
    gate_dense_min: float = 0.76
    gate_lex_coverage: float = 0.34


@lru_cache
def get_settings() -> Settings:
    return Settings()
