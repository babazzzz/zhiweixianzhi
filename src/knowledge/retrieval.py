"""Hybrid retrieval: BM25 (sparse) + embeddings (dense) fused with RRF.

三条检索通道与它们的分工
------------------------
===================  =========================================================
BM25（稀疏）          精确术语命中。"SKF 7014CDGA/P4A"、故障码、"SKF 7014" 这类
                     专有名词，向量模型往往把它们和随机型号混在一起，BM25 不会。
Embedding（稠密）     语义相似。用户说"主轴发热"，文档写"温度升高"，靠向量才能连上。
RRF 融合              两条通道各自排名，用 Reciprocal Rank Fusion 合并。
                     为什么不用分数加权：BM25 分数无上界、向量分数在 [-1,1]，
                     两者量纲不可比，归一化再加权是拍脑袋；RRF 只用**排名**，
                     天然无需校准，且对单通道失效更鲁棒。
===================  =========================================================

向量通道使用本地 Ollama 的 bge-m3 嵌入；Ollama 未启动时自动降级为**纯 BM25**，
不会让整个检索链路挂掉——这是有意为之：知识库检索是诊断的前置依赖，
它挂了比它不准更糟。
"""

from __future__ import annotations

import math
import pickle
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from src.config import DATA_DIR
from src.knowledge.chunking import DocumentChunk, expand_query
from src.observability import get_logger

LOGGER = get_logger("rag")

#: 中文按字 + 英文/数字按词的分词器。对工程文档而言，「字级」召回率高于「词级」，
#: 代价是精度略低——这正是 BM25 的 IDF 与后面的融合排序要解决的问题。
_TOKEN_PATTERN = re.compile(r"[a-zA-Z_][a-zA-Z0-9_\-]*|\d+(?:\.\d+)?|[\u4e00-\u9fff]")

#: BM25 调参（中文短文本经验值）
BM25_K1 = 1.5
BM25_B = 0.75
#: RRF 平滑常数：k 越大，头部排名的优势被稀释
RRF_K = 60

#: 中文停用字表：这些字在语料里出现频率可能并不高（"是"10%、"在"6%），
#: 所以**不能**用文档频率判定——它们的信息量本身为零，只要命中的全是这类字，
#: 就说明检索没有找到任何实质证据。中文按字切词时这点尤其致命：
#: "我是谁" → 我/是/谁，其中只有"是"命中了 5 个 chunk，于是 5 条无关手册条文
#: 被当作"知识库检索结果"拼进了聊天回答。
_STOPWORD_CHARS: Final[frozenset[str]] = frozenset(
    "的 了 是 在 有 和 与 或 就 都 而 及 其 这 那 之 也 还 被 把 为 以 于 对 从 到 会 能 可 要 不 没 无 我 你 他 她 它 们 谁 什 么 怎 样 哪 吗 呢 吧 啊 呀 哦 嗯 好 请 谢 再 见 个 些 上 下 里 中 后 前 时 分 秒 高 低 大 小 多 少 一 二 三 四 五 六 七 八 九 十".split()
)
#: 英文/数字停用词（型号与数字编号不在此列，它们是强信号）
_STOPWORD_WORDS: Final[frozenset[str]] = frozenset(
    "the a an of to in on for and or is are was were be been with by at as it this that what how why when where which who".split()
)


def is_stopword(token: str) -> bool:
    """Report whether one token carries no retrieval signal on its own."""

    if token in _STOPWORD_CHARS:
        return True
    if token in _STOPWORD_WORDS:
        return True
    # 单个纯数字（如"3"）在工程文档里遍地都是，不构成证据
    return token.isdigit() and len(token) <= 2


#: 信息量门槛豁免长度：查询本身 ≥ 该字符数时不做门槛判定。
#: 长问题即使只命中常见词，也往往是真在问知识库里的东西，误杀代价高于放过。
MIN_QUERY_CHARS_FOR_GATE = 10


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """One hit with its rank from each channel and the fused score."""

    chunk: DocumentChunk
    score: float
    bm25_rank: int | None = None
    dense_rank: int | None = None
    #: 命中原因，用于在前端解释"为什么召回这条"
    matched_terms: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation for the API and the chat citations."""

        return {
            "id": self.chunk.id, "text": self.chunk.text, "source": self.chunk.source,
            "doc_type": self.chunk.doc_type, "section_path": list(self.chunk.section_path),
            "fault_code": self.chunk.fault_code, "score": round(self.score, 6),
            "bm25_rank": self.bm25_rank, "dense_rank": self.dense_rank,
            "matched_terms": list(self.matched_terms),
        }


def tokenize(text: str) -> list[str]:
    """Tokenize mixed Chinese/English engineering text.

    英文/型号统一小写（``SKF 7014CDGA/P4A`` → ``skf``/``7014cdga``），
    中文按字切（``温度升高`` → ``温``/``度``/``升``/``高``）。
    """

    return [token.lower() for token in _TOKEN_PATTERN.findall(text)]


class BM25Index:
    """Classic Okapi BM25 over tokenized chunks, persistable with pickle."""

    def __init__(self) -> None:
        self._chunks: list[DocumentChunk] = []
        self._term_freqs: list[Counter[str]] = []
        self._doc_freqs: Counter[str] = Counter()
        self._doc_lengths: list[int] = []
        self._avg_length = 0.0

    def build(self, chunks: Sequence[DocumentChunk]) -> None:
        """(Re)build the inverted statistics from a chunk list."""

        self._chunks = list(chunks)
        self._term_freqs = []
        self._doc_freqs = Counter()
        for chunk in self._chunks:
            tokens = tokenize(chunk.text)
            self._term_freqs.append(Counter(tokens))
            self._doc_freqs.update(tokens)
        self._doc_lengths = [sum(counter.values()) for counter in self._term_freqs]
        total = sum(self._doc_lengths)
        self._avg_length = total / len(self._doc_lengths) if self._doc_lengths else 0.0

    def search(self, query: str, top_k: int = 8, fault_code: int | None = None) -> list[tuple[int, float, tuple[str, ...]]]:
        """Return ``(chunk_index, score, matched_terms)`` ordered by relevance."""

        if not self._chunks:
            return []
        query_tokens = [token for token in tokenize(query) if token.strip()]
        if not query_tokens:
            return []
        total_docs = len(self._chunks)
        scored: list[tuple[int, float, tuple[str, ...]]] = []
        for index, frequencies in enumerate(self._term_freqs):
            chunk = self._chunks[index]
            if fault_code is not None and chunk.fault_code is not None and chunk.fault_code != fault_code:
                continue
            length = self._doc_lengths[index] or 1
            score = 0.0
            matched: list[str] = []
            for token in query_tokens:
                tf = frequencies.get(token, 0)
                if tf == 0:
                    continue
                df = self._doc_freqs.get(token, 0)
                # IDF 可能为负（词太常见），截断到 0 避免"越常见越相关"
                idf = max(0.0, math.log(1.0 + (total_docs - df + 0.5) / (df + 0.5)))
                denominator = tf + BM25_K1 * (1.0 - BM25_B + BM25_B * length / max(self._avg_length, 1e-6))
                score += idf * (tf * (BM25_K1 + 1.0)) / max(denominator, 1e-6)
                matched.append(token)
            if score > 0:
                scored.append((index, score, tuple(sorted(set(matched)))))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:top_k]

    def chunks(self) -> list[DocumentChunk]:
        """Return the indexed chunks in insertion order."""

        return list(self._chunks)

    def save(self, path: Path) -> None:
        """Persist the index so the server does not rebuild it on every boot."""

        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            pickle.dump({
                "chunks": [chunk.as_record() for chunk in self._chunks],
                "doc_freqs": dict(self._doc_freqs),
                "doc_lengths": self._doc_lengths,
                "avg_length": self._avg_length,
            }, handle)

    def load(self, path: Path) -> bool:
        """Load a persisted index, returning False when absent or corrupt."""

        if not path.exists():
            return False
        try:
            with path.open("rb") as handle:
                payload = pickle.load(handle)
            self._chunks = [
                DocumentChunk(
                    id=str(record["id"]), text=str(record["text"]), source=str(record["source"]),
                    doc_type=str(record["doc_type"]), section_path=list(record["section_path"]),
                    fault_code=record.get("fault_code"), chunk_index=int(record.get("chunk_index", 0)),
                    char_count=int(record.get("char_count", 0)),
                )
                for record in payload["chunks"]
            ]
            self._doc_freqs = Counter(payload["doc_freqs"])
            self._doc_lengths = list(payload["doc_lengths"])
            self._avg_length = float(payload["avg_length"])
            self._term_freqs = [Counter(tokenize(chunk.text)) for chunk in self._chunks]
            return True
        except Exception as error:  # noqa: BLE001 - 索引损坏时宁可重建也不要让服务起不来
            LOGGER.warning(f"bm25 index load failed, will rebuild: {type(error).__name__}: {error}")
            return False


class DenseIndex:
    """Embedding-backed semantic index with graceful degradation."""

    def __init__(self, collection_name: str = "zhiwei_knowledge", persist_dir: Path | None = None) -> None:
        self._collection_name = collection_name
        self._persist_dir = Path(persist_dir or DATA_DIR / "chroma_db")
        self._client: Any = None
        self._collection: Any = None
        self._llm: Any = None
        self._available: bool | None = None

    @property
    def available(self) -> bool:
        """Report whether the dense channel can currently serve queries."""

        if self._available is None:
            self._available = self._bootstrap()
        return self._available

    def _bootstrap(self) -> bool:
        """Lazily import chromadb and connect, returning False on any failure."""

        try:
            import chromadb  # noqa: PLC0415 - 可选依赖，避免拖慢启动
        except ImportError:
            LOGGER.info("chromadb 未安装，稠密检索通道关闭，仅使用 BM25")
            return False
        try:
            self._persist_dir.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(self._persist_dir))
            self._collection = self._client.get_or_create_collection(self._collection_name)
            return True
        except Exception as error:  # noqa: BLE001
            LOGGER.warning(f"chroma 初始化失败，稠密检索通道关闭: {type(error).__name__}: {error}")
            return False

    async def build(self, chunks: Sequence[DocumentChunk]) -> int:
        """Embed and upsert every chunk, returning how many were embedded."""

        if not self.available:
            return 0
        from src.llm.client import UnifiedLLMClient  # noqa: PLC0415

        if self._llm is None:
            self._llm = UnifiedLLMClient(timeout_seconds=30.0)
        embedded = 0
        batch_size = 32
        for start in range(0, len(chunks), batch_size):
            batch = list(chunks[start:start + batch_size])
            vectors: list[list[float]] = []
            for chunk in batch:
                try:
                    vectors.append(await self._llm.embed(chunk.text))
                except Exception as error:  # noqa: BLE001 - 单条失败不能中断整批
                    LOGGER.warning(f"embed failed for {chunk.id}: {type(error).__name__}: {error}")
                    vectors.append([])
            valid = [
                (chunk, vector) for chunk, vector in zip(batch, vectors) if vector
            ]
            if not valid:
                continue
            self._collection.upsert(
                ids=[chunk.id for chunk, _ in valid],
                documents=[chunk.text for chunk, _ in valid],
                embeddings=[vector for _, vector in valid],
                metadatas=[{key: value for key, value in chunk.metadata().items() if not isinstance(value, list)} for chunk, _ in valid],
            )
            embedded += len(valid)
        return embedded

    async def search(self, query: str, top_k: int = 8, fault_code: int | None = None) -> list[tuple[str, float]]:
        """Return ``(chunk_id, similarity)`` pairs, empty when unavailable."""

        if not self.available:
            return []
        from src.llm.client import UnifiedLLMClient  # noqa: PLC0415

        if self._llm is None:
            self._llm = UnifiedLLMClient(timeout_seconds=30.0)
        try:
            vector = await self._llm.embed(query)
        except Exception as error:  # noqa: BLE001
            LOGGER.warning(f"query embed failed, dense channel skipped: {type(error).__name__}: {error}")
            return []
        try:
            where = {"fault_code": fault_code} if fault_code is not None else None
            result = self._collection.query(
                query_embeddings=[vector], n_results=top_k,
                **({"where": where} if where else {}),
            )
        except Exception as error:  # noqa: BLE001
            LOGGER.warning(f"chroma query failed: {type(error).__name__}: {error}")
            return []
        ids = result.get("ids", [[]])[0]
        distances = result.get("distances", [[]])[0]
        hits: list[tuple[str, float]] = []
        for chunk_id, distance in zip(ids, distances):
            # chroma 默认余弦距离，越小越近 → 转成"越大越相似"
            hits.append((str(chunk_id), 1.0 - float(distance)))
        return hits


@dataclass(slots=True)
class HybridRetriever:
    """Fuse BM25 and dense retrieval with Reciprocal Rank Fusion."""

    bm25: BM25Index = field(default_factory=BM25Index)
    dense: DenseIndex = field(default_factory=DenseIndex)
    #: 融合权重：BM25 略高于向量，因为工程术语的精确命中在本场景更关键
    bm25_weight: float = 1.0
    dense_weight: float = 0.9

    async def search(
        self,
        query: str,
        top_k: int = 6,
        fault_code: int | None = None,
        doc_types: Sequence[str] | None = None,
    ) -> list[RetrievedChunk]:
        """Retrieve the most relevant chunks using both channels."""

        candidate_pool = max(top_k * 3, 12)
        sparse_hits = self.bm25.search(expand_query(query), top_k=candidate_pool, fault_code=fault_code)
        dense_hits = await self.dense.search(query, top_k=candidate_pool, fault_code=fault_code)

        indexed_chunks = self.bm25.chunks()
        chunks_by_id = {chunk.id: chunk for chunk in indexed_chunks}

        fused: dict[str, float] = {}
        sparse_ranks: dict[str, int] = {}
        matched_terms: dict[str, tuple[str, ...]] = {}
        for rank, (index, _score, terms) in enumerate(sparse_hits, start=1):
            chunk_id = indexed_chunks[index].id
            fused[chunk_id] = fused.get(chunk_id, 0.0) + self.bm25_weight / (RRF_K + rank)
            sparse_ranks[chunk_id] = rank
            matched_terms[chunk_id] = terms

        dense_ranks: dict[str, int] = {}
        for rank, (chunk_id, _similarity) in enumerate(dense_hits, start=1):
            dense_ranks[chunk_id] = rank
            fused[chunk_id] = fused.get(chunk_id, 0.0) + self.dense_weight / (RRF_K + rank)

        results: list[RetrievedChunk] = []
        for chunk_id, score in fused.items():
            chunk = chunks_by_id.get(chunk_id)
            if chunk is None:
                continue
            if doc_types and chunk.doc_type not in doc_types:
                continue
            results.append(RetrievedChunk(
                chunk=chunk, score=score,
                bm25_rank=sparse_ranks.get(chunk_id), dense_rank=dense_ranks.get(chunk_id),
                matched_terms=matched_terms.get(chunk_id, ()),
            ))
        # 同分时优先保留 BM25 命中术语更多的条目（精确术语优先）
        results.sort(key=lambda item: (item.score, len(item.matched_terms)), reverse=True)
        hits = results[:top_k]
        LOGGER.info(f"rag search: query={query!r} sparse={len(sparse_hits)} dense={len(dense_hits)} fused={len(results)}")
        return self._apply_relevance_gate(query, hits, len(indexed_chunks))

    def _apply_relevance_gate(
        self,
        query: str,
        hits: list[RetrievedChunk],
        total_chunks: int,
    ) -> list[RetrievedChunk]:
        """Discard hits whose only BM25 evidence is a stopword.

        BM25 的 IDF 在词太常见时被截断到 0，但**分数仍为正**，于是"是"这类
        功能字能把完全无关的 chunk 送进 top_k。这里以"查询里是否存在任一
        实义词元命中"作为相关性判据：没有证据就返回空列表，让上层（聊天窗口）
        明确说出"没检索到"，而不是把无关原文当作答案拼出去。

        门槛只在"查询较短（< MIN_QUERY_CHARS_FOR_GATE）且 BM25 通道有输出"时
        生效——纯向量命中的结果不动，长问题也不动，避免误伤正常召回。
        """

        if not hits or len(query.strip()) >= MIN_QUERY_CHARS_FOR_GATE or total_chunks <= 0:
            return hits
        informative = {term for term in tokenize(query) if term.strip() and not is_stopword(term)}
        if not informative:
            # 查询整句由停用字构成（"我是谁""谢谢""在吗"）——语料里不存在能支撑
            # 回答它的内容，直接判为未命中。
            LOGGER.info(f"rag relevance gate: query={query!r} 全部为停用字，判定未命中")
            return []
        matched_informative = [
            hit for hit in hits if informative.intersection(hit.matched_terms)
        ]
        if not matched_informative:
            LOGGER.info(f"rag relevance gate: query={query!r} 无实义词元命中（候选 {len(hits)} 条已丢弃）")
        return matched_informative

    def stats(self) -> dict[str, Any]:
        """Return index statistics for the knowledge page and health checks."""

        chunks = self.bm25.chunks()
        by_type: Counter[str] = Counter(chunk.doc_type for chunk in chunks)
        return {
            "chunks": len(chunks),
            "by_doc_type": dict(by_type),
            "sources": sorted({chunk.source for chunk in chunks}),
            "dense_enabled": self.dense.available,
            "avg_chunk_chars": round(sum(chunk.char_count for chunk in chunks) / len(chunks), 1) if chunks else 0,
        }


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[tuple[str, float]]],
    *,
    k: int = RRF_K,
    weights: Sequence[float] | None = None,
) -> list[tuple[str, float]]:
    """Fuse several ranked lists with RRF — exported for tests and reuse."""

    fused: dict[str, float] = {}
    for channel_index, ranking in enumerate(rankings):
        weight = weights[channel_index] if weights and channel_index < len(weights) else 1.0
        for rank, (identifier, _score) in enumerate(ranking, start=1):
            fused[identifier] = fused.get(identifier, 0.0) + weight / (k + rank)
    return sorted(fused.items(), key=lambda item: item[1], reverse=True)


def format_context(hits: Sequence[RetrievedChunk], *, max_chars: int = 3200) -> str:
    """Render retrieved chunks as a numbered, citable context block for the LLM."""

    blocks: list[str] = []
    used = 0
    for index, hit in enumerate(hits, start=1):
        block = f"[{index}] 来源：{hit.chunk.source}｜{hit.chunk.breadcrumb}\n{hit.chunk.text}"
        if used + len(block) > max_chars and blocks:
            break
        blocks.append(block)
        used += len(block)
    return "\n\n".join(blocks)


def citations_from(hits: Sequence[RetrievedChunk]) -> list[dict[str, Any]]:
    """Build the citation list attached to every RAG-grounded answer."""

    return [
        {
            "index": index,
            "source": hit.chunk.source,
            "doc_type": hit.chunk.doc_type,
            "section_path": hit.chunk.breadcrumb,
            "score": round(hit.score, 6),
            "excerpt": hit.chunk.text[:160],
        }
        for index, hit in enumerate(hits, start=1)
    ]


def chunks_to_records(chunks: Iterable[DocumentChunk]) -> list[dict[str, object]]:
    """Serialize chunks for persistence or API responses."""

    return [chunk.as_record() for chunk in chunks]
