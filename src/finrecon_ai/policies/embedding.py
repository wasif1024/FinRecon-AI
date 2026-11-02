"""Text embeddings. Local fastembed model by default: no second AI provider, no extra API key."""

import hashlib
import math
import re
from functools import cached_property
from typing import Protocol


class Embedder(Protocol):
    name: str
    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class FastEmbedder:
    """BAAI/bge-small-en-v1.5 by default (384 dims). Downloads the model (~70 MB) on first use."""

    def __init__(self, model_name: str):
        self.name = model_name

    @cached_property
    def _model(self):
        from fastembed import TextEmbedding

        return TextEmbedding(self.name)

    @cached_property
    def dimensions(self) -> int:  # type: ignore[override]
        from fastembed import TextEmbedding

        return TextEmbedding.get_embedding_size(self.name)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self._model.passage_embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        return next(iter(self._model.query_embed(text))).tolist()


class HashingEmbedder:
    """Deterministic bag-of-words vectors for tests: lexical overlap gives similarity, no model download."""

    def __init__(self, dimensions: int = 256):
        self.name = f"hashing-{dimensions}"
        self.dimensions = dimensions

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            if len(word) > 2:
                vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dimensions] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]
