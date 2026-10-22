"""Admin-editable runtime settings (stored in ``app_settings``, defaults from ``KS_*`` env) and a
per-process cache of loaded models.

Changing the embedding model or chunking changes the *index signature*; documents indexed with a
different signature are reported as outdated (coverage incomplete) until reindexed.
"""

import threading
from dataclasses import asdict, dataclass, fields

from psycopg import Connection
from psycopg.types.json import Jsonb

from .config import Settings, get_settings
from .embedder import MODELS, Embedder
from .parsing import PARSER_VERSION

RERANKERS = {
    # name: (licence, approx download MB, note)
    "Xenova/ms-marco-MiniLM-L-6-v2": ("Apache-2.0", 80, "fast, English"),
    "BAAI/bge-reranker-base": ("MIT", 1040, "stronger, English/Chinese, slower"),
    "BAAI/bge-reranker-v2-m3-int8": ("Apache-2.0", 570, "multilingual, slowest"),
}


@dataclass
class Runtime:
    embed_model: str
    chunk_size: int
    chunk_overlap: int  # 0 disables overlap
    rerank_enabled: bool
    rerank_model: str
    rerank_candidates: int
    # evidence gate (see search.gate_basis); thresholds are model-specific
    gate_dense_high: float
    gate_dense_min: float
    gate_lex_coverage: float
    gate_rerank_min: float
    gate_mode: str
    gate_lexical: str
    fusion_lexical_weight: float

    def signature(self) -> str:
        return f"{MODELS[self.embed_model].version_key}|{PARSER_VERSION}|chunk={self.chunk_size}/{self.chunk_overlap}"


_base: Settings | None = None


def configure(settings: Settings) -> None:
    """Use these base settings (instead of the environment) for defaults and model loading."""
    global _base
    _base = settings


def base() -> Settings:
    return _base or get_settings()


LIMITS = {"chunk_size": (200, 4000), "chunk_overlap": (0, 1000), "rerank_candidates": (5, 100),
          "fusion_lexical_weight": (0.0, 2.0)}


def defaults(model: str | None = None) -> Runtime:
    """Defaults from KS_* settings; cosine thresholds come from the model's tuned values when it has them."""
    s = base()
    model = model or s.embed_model
    spec = MODELS.get(model)
    high = spec.gate_high if spec and spec.gate_high is not None else s.gate_dense_high
    low = spec.gate_min if spec and spec.gate_min is not None else s.gate_dense_min
    lex = spec.gate_lex if spec and spec.gate_lex is not None else s.gate_lex_coverage
    return Runtime(
        embed_model=model, chunk_size=s.chunk_size, chunk_overlap=s.chunk_overlap,
        rerank_enabled=s.rerank_enabled, rerank_model=s.rerank_model, rerank_candidates=s.rerank_candidates,
        gate_dense_high=high, gate_dense_min=low,
        gate_lex_coverage=lex, gate_rerank_min=s.gate_rerank_min,
        gate_mode=s.gate_mode, gate_lexical=s.gate_lexical, fusion_lexical_weight=s.fusion_lexical_weight,
    )


def load(conn: Connection) -> Runtime:
    names = {f.name for f in fields(Runtime)}
    overrides = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM app_settings") if r["key"] in names}
    rt = defaults(overrides.get("embed_model"))
    for k, v in overrides.items():
        setattr(rt, k, v)
    return rt


def thresholds_tuned(model: str) -> bool:
    spec = MODELS.get(model)
    return bool(spec and spec.gate_high is not None)


def validate(changes: dict) -> dict:
    names = {f.name: f.type for f in fields(Runtime)}
    out = {}
    for k, v in changes.items():
        if k not in names:
            raise ValueError(f"unknown setting {k}")
        if k == "embed_model" and v not in MODELS:
            raise ValueError(f"unsupported embedding model {v}")
        if k == "rerank_model" and v not in RERANKERS:
            raise ValueError(f"unsupported reranker {v}")
        if k == "gate_mode" and v not in ("similarity", "rerank", "rerank_and_similarity"):
            raise ValueError("gate_mode must be similarity, rerank or rerank_and_similarity")
        if k == "gate_lexical" and v not in ("coverage", "idf"):
            raise ValueError("gate_lexical must be coverage or idf")
        if names[k] in (int, "int"):
            v = int(v)
        elif names[k] in (float, "float"):
            v = float(v)
        elif names[k] in (bool, "bool"):
            v = bool(v)
        if k in LIMITS and not LIMITS[k][0] <= v <= LIMITS[k][1]:
            raise ValueError(f"{k} must be between {LIMITS[k][0]} and {LIMITS[k][1]}")
        out[k] = v
    merged = {**asdict(defaults()), **out}
    if merged["chunk_overlap"] >= merged["chunk_size"]:
        raise ValueError("chunk_overlap must be smaller than chunk_size")
    return out


def save(conn: Connection, changes: dict, user_id: int | None) -> None:
    for k, v in changes.items():
        conn.execute(
            "INSERT INTO app_settings(key, value, updated_by) VALUES (%s, %s, %s) ON CONFLICT (key) DO UPDATE "
            "SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by, updated_at = now()",
            (k, Jsonb(v), user_id),
        )


# --- model cache ------------------------------------------------------------------------------

_lock = threading.Lock()
_embedders: dict[str, Embedder] = {}
_rerankers: dict = {}


def register_embedder(e: Embedder) -> None:
    _embedders[e.spec.name] = e


def embedder(name: str) -> Embedder:
    with _lock:
        if name not in _embedders:
            s = base()
            _embedders[name] = Embedder(name, str(s.model_cache), s.embed_threads, s.offline)
        return _embedders[name]


def model_states() -> dict[str, str]:
    """Load state of every model this process has touched (shown in the UI)."""
    out = {f"embedding: {k}": e.state for k, e in _embedders.items()}
    out.update({f"reranker: {k}": "ready" for k in _rerankers})
    return out


class RerankError(Exception):
    pass


def reranker(name: str):
    with _lock:
        if name not in _rerankers:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            s = base()
            try:
                _rerankers[name] = TextCrossEncoder(name, cache_dir=str(s.model_cache), local_files_only=s.offline)
            except Exception as e:  # download / load failure
                raise RerankError(f"cannot load reranker {name}: {e}") from e
        return _rerankers[name]


def rerank_scores(name: str, query: str, texts: list[str]) -> list[float]:
    try:
        return [float(x) for x in reranker(name).rerank(query, texts)]
    except RerankError:
        raise
    except Exception as e:
        raise RerankError(f"reranking failed: {e}") from e
