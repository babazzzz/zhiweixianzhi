"""Chroma-backed knowledge index with a deterministic lexical fallback."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping

try:
    import chromadb
except ImportError:  # pragma: no cover - optional in minimal deployments
    chromadb = None

from src.config import DATA_DIR
from src.llm.client import UnifiedLLMClient


class VectorStore:
    """Persist document chunks in Chroma when available and remain usable offline."""

    def __init__(self, persist_dir: Path | str | None = None, client: UnifiedLLMClient | None = None) -> None:
        self.persist_dir = Path(persist_dir or DATA_DIR / "chroma_db")
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self._client = client or UnifiedLLMClient()
        self._documents: dict[str, list[dict[str, object]]] = {}
        self._chroma = chromadb.PersistentClient(path=str(self.persist_dir)) if chromadb is not None else None

    async def add_documents(self, collection: str, docs: list[Mapping[str, object]]) -> None:
        """Add documents with source metadata and optional bge-m3 embeddings."""

        normalized = [{"id": str(doc["id"]), "text": str(doc["text"]), "metadata": dict(doc.get("metadata", {}))} for doc in docs]
        self._documents.setdefault(collection, []).extend(normalized)
        if self._chroma is None:
            return
        chroma_collection = self._chroma.get_or_create_collection(collection)
        ids = [str(doc["id"]) for doc in normalized]
        texts = [str(doc["text"]) for doc in normalized]
        embeddings: list[list[float]] | None = None
        try:
            embeddings = [await self._client.embed(text) for text in texts]
        except Exception:
            embeddings = None
        kwargs: dict[str, object] = {"ids": ids, "documents": texts, "metadatas": [self._safe_metadata(dict(doc["metadata"])) for doc in normalized]}
        if embeddings:
            kwargs["embeddings"] = embeddings
        chroma_collection.upsert(**kwargs)

    def search(self, collection: str, query: str, top_k: int = 5) -> list[dict[str, object]]:
        """Search a collection by semantic Chroma similarity or lexical overlap."""

        if self._chroma is not None:
            try:
                result = self._chroma.get_or_create_collection(collection).query(query_texts=[query], n_results=min(max(top_k, 1), 20))
                ids = result.get("ids", [[]])[0]
                texts = result.get("documents", [[]])[0]
                metadatas = result.get("metadatas", [[]])[0]
                return [{"id": ids[index], "text": texts[index], "metadata": metadatas[index] if index < len(metadatas) else {}} for index in range(len(ids))]
            except Exception:
                pass
        tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", query.lower()))
        ranked = sorted(self._documents.get(collection, []), key=lambda doc: len(tokens.intersection(set(str(doc["text"]).lower().split()))), reverse=True)
        return ranked[: min(max(top_k, 1), 20)]

    def search_similar_cases(self, fault_description: str, top_k: int = 3) -> list[dict[str, object]]:
        """Search the episodic case collection."""

        return self.search("case_library", fault_description, top_k)

    @staticmethod
    def _safe_metadata(metadata: Mapping[str, object]) -> dict[str, str | int | float | bool]:
        """Keep Chroma metadata JSON scalar-compatible."""

        return {key: value for key, value in metadata.items() if isinstance(value, (str, int, float, bool))}


def chunk_text(text: str, chunk_size: int = 420) -> list[str]:
    """Split source text at paragraph boundaries while retaining engineering context."""

    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", text) if item.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 1 > chunk_size and current:
            chunks.append(current)
            current = ""
        current = f"{current}\n{paragraph}".strip()
    if current:
        chunks.append(current)
    return chunks
