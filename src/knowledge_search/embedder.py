"""Local embedding adapter (fastembed / ONNX Runtime, CPU by default).

The model registry pins exactly how each model is used (query/document prefixes, dimension,
normalization) so the behaviour is versioned and testable. Vectors from different models are
never mixed: chunks store ``embedding_model`` and search filters on the active model.
"""

import threading
from dataclasses import dataclass

import numpy as np

BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


@dataclass(frozen=True)
class ModelSpec:
    name: str
    dim: int
    query_prefix: str = ""
    doc_prefix: str = ""
    license: str = ""

    @property
    def version_key(self) -> str:
        # Identifies the vector space: model + prefix behaviour + normalization.
        return f"{self.name}|q={bool(self.query_prefix)}|d={bool(self.doc_prefix)}|l2"


MODELS = {
    "BAAI/bge-small-en-v1.5": ModelSpec("BAAI/bge-small-en-v1.5", 384, BGE_QUERY_PREFIX, "", "MIT"),
    "snowflake/snowflake-arctic-embed-s": ModelSpec(
        "snowflake/snowflake-arctic-embed-s", 384, BGE_QUERY_PREFIX, "", "Apache-2.0"
    ),
    "ibm-granite/granite-embedding-small-english-r2": ModelSpec(
        "ibm-granite/granite-embedding-small-english-r2", 384, "", "", "Apache-2.0"
    ),
}


class EmbeddingError(Exception):
    pass


class Embedder:
    def __init__(self, model: str, cache_dir: str, threads: int = 0, offline: bool = False):
        if model not in MODELS:
            raise EmbeddingError(f"model {model!r} is not in the supported registry")
        self.spec = MODELS[model]
        self._cache_dir = cache_dir
        self._threads = threads or None
        self._offline = offline
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from fastembed import TextEmbedding

                    try:
                        self._model = TextEmbedding(
                            self.spec.name,
                            cache_dir=self._cache_dir,
                            threads=self._threads,
                            local_files_only=self._offline,
                        )
                    except Exception as e:  # download / load failure
                        raise EmbeddingError(f"cannot load model {self.spec.name}: {e}") from e
        return self._model

    def _embed(self, texts: list[str]) -> np.ndarray:
        try:
            vecs = np.array(list(self._load().embed(texts, batch_size=32)), dtype=np.float32)
        except EmbeddingError:
            raise
        except Exception as e:
            raise EmbeddingError(f"embedding failed: {e}") from e
        if vecs.shape != (len(texts), self.spec.dim):
            raise EmbeddingError(f"unexpected embedding shape {vecs.shape}")
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vecs / norms

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return self._embed([self.spec.doc_prefix + t for t in texts])

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed([self.spec.query_prefix + text])[0]

    def warm(self) -> None:
        self.embed_query("warm up")
