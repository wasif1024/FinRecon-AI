"""Policy chunks in Qdrant. Every point carries tenant_id and every search filters on it (SRS §11)."""

import re
import uuid
from dataclasses import dataclass

from qdrant_client import QdrantClient, models

from finrecon_ai.policies.chunking import Chunk
from finrecon_ai.policies.embedding import Embedder

COLLECTION_PREFIX = "policy_chunks"
UPSERT_BATCH = 64


@dataclass(frozen=True)
class PolicyHit:
    chunk_id: str
    document_id: str
    title: str
    version: int
    section: str
    page: int | None
    text: str
    score: float


class PolicyStore:
    def __init__(self, client: QdrantClient, embedder: Embedder):
        self.client = client
        self.embedder = embedder
        # One collection per embedding model: vectors from different models must never be mixed.
        self.collection = f"{COLLECTION_PREFIX}__{re.sub(r'[^a-z0-9]+', '_', embedder.name.lower()).strip('_')}"

    def ensure_collection(self) -> None:
        if self.client.collection_exists(self.collection):
            return
        self.client.create_collection(
            self.collection,
            vectors_config=models.VectorParams(size=self.embedder.dimensions, distance=models.Distance.COSINE),
        )
        for field in ("tenant_id", "document_id"):
            self.client.create_payload_index(self.collection, field, models.PayloadSchemaType.KEYWORD)

    def index(self, *, tenant_id: uuid.UUID, document_id: uuid.UUID, title: str, version: int,
              chunks: list[Chunk]) -> list[str]:
        self.ensure_collection()
        ids: list[str] = []
        for start in range(0, len(chunks), UPSERT_BATCH):
            batch = chunks[start:start + UPSERT_BATCH]
            vectors = self.embedder.embed_documents([c.embedding_text for c in batch])
            points = []
            for chunk, vector in zip(batch, vectors, strict=True):
                point_id = str(uuid.uuid5(document_id, str(chunk.index)))  # stable: re-indexing overwrites
                ids.append(point_id)
                points.append(models.PointStruct(id=point_id, vector=vector, payload={
                    "tenant_id": str(tenant_id), "document_id": str(document_id), "title": title,
                    "version": version, "section": chunk.section, "page": chunk.page,
                    "chunk_index": chunk.index, "text": chunk.text,
                }))
            self.client.upsert(self.collection, points=points, wait=True)
        return ids

    def delete_document(self, document_id: uuid.UUID) -> None:
        if not self.client.collection_exists(self.collection):
            return
        self.client.delete(self.collection, points_selector=models.FilterSelector(filter=_match(
            "document_id", str(document_id))), wait=True)

    def search(self, *, tenant_id: uuid.UUID, query: str, limit: int = 5) -> list[PolicyHit]:
        if not self.client.collection_exists(self.collection):
            return []
        result = self.client.query_points(
            self.collection, query=self.embedder.embed_query(query), limit=limit, with_payload=True,
            query_filter=_match("tenant_id", str(tenant_id)),
        )
        return [PolicyHit(chunk_id=str(p.id), document_id=p.payload["document_id"], title=p.payload["title"],
                          version=p.payload["version"], section=p.payload["section"], page=p.payload["page"],
                          text=p.payload["text"], score=round(p.score, 4))
                for p in result.points]


def _match(field: str, value: str) -> models.Filter:
    return models.Filter(must=[models.FieldCondition(key=field, match=models.MatchValue(value=value))])


def default_store() -> PolicyStore:
    from finrecon_ai.config import get_settings
    from finrecon_ai.policies.embedding import FastEmbedder

    settings = get_settings()
    return PolicyStore(QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None),
                       FastEmbedder(settings.embedding_model))
