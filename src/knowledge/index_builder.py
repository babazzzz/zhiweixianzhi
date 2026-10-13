"""Build and load the hybrid knowledge index from files + database.

知识来源（四类，统一切成 chunk 后进入同一个索引）
------------------------------------------------
===============  ======================================================
``data/knowledge/*.txt``  维护手册 / 设备说明书 / 案例知识库 / 成本产量（人写的工程文档）
``fmeca_rules``  结构化 FMECA 规则表：机理 / 检测 / SOP / 备件 / 工时
``case_library`` 历史故障案例表：现象 / 根因 / 处置 / 风险链
``machines``     设备主数据：型号 / 产线 / 产量 / 成本（用于"这台设备什么来头"类问题）
===============  ======================================================

索引落盘在 ``data/index/``：

* ``bm25.pkl``  —— 稀疏索引（必须，离线可用）
* ``chunks.json``—— chunk 全量快照（用于前端"知识库"页浏览与调试）
* Chroma 目录    —— 稠密索引（可选，Ollama 可用时才有）

重建策略：``ensure_index()`` 先尝试加载已有索引；索引缺失或源文件发生变化
（按内容哈希判断）时才重建，避免每次启动都重新嵌入一遍。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from src.config import DATA_DIR
from src.database.connection import Database
from src.knowledge.chunking import DocumentChunk, chunk_document
from src.knowledge.retrieval import BM25Index, DenseIndex, HybridRetriever
from src.observability import get_logger

LOGGER = get_logger("rag")

INDEX_DIR = DATA_DIR / "index"
BM25_PATH = INDEX_DIR / "bm25.pkl"
CHUNKS_PATH = INDEX_DIR / "chunks.json"
FINGERPRINT_PATH = INDEX_DIR / "fingerprint.json"

#: 文档类型 → (文件名, doc_type)。顺序即索引顺序，便于调试时定位。
DOCUMENT_SOURCES: tuple[tuple[str, str], ...] = (
    ("维护手册.txt", "manual"),
    ("设备说明书.txt", "spec"),
    ("故障征兆判读指引.txt", "guide"),
    ("案例知识库.txt", "case"),
    ("设备成本与产量.txt", "cost"),
)


class KnowledgeIndex:
    """Owns the built index and exposes a ready-to-use retriever."""

    def __init__(self, database: Database | None = None, index_dir: Path | None = None) -> None:
        self._database = database
        self._index_dir = Path(index_dir or INDEX_DIR)
        self.bm25 = BM25Index()
        self.dense = DenseIndex(persist_dir=DATA_DIR / "chroma_db")
        self.retriever = HybridRetriever(bm25=self.bm25, dense=self.dense)
        self._chunks: list[DocumentChunk] = []
        self._last_build: dict[str, Any] = {}

    @property
    def chunks(self) -> list[DocumentChunk]:
        """Return the currently indexed chunks."""

        return list(self._chunks)

    @property
    def database(self) -> Database:
        """Lazily create the repository so building without a DB stays possible."""

        if self._database is None:
            self._database = Database()
        return self._database

    # ------------------------------------------------------------------- building

    def collect_chunks(self) -> list[DocumentChunk]:
        """Assemble chunks from every configured knowledge source."""

        chunks: list[DocumentChunk] = []
        for filename, doc_type in DOCUMENT_SOURCES:
            for directory in (DATA_DIR / "knowledge", Path("docs")):
                path = directory / filename
                if not path.exists():
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                chunks.extend(chunk_document(text, source=filename, doc_type=doc_type))
                break
        chunks.extend(self._chunks_from_fmeca())
        chunks.extend(self._chunks_from_cases())
        chunks.extend(self._chunks_from_machines())
        # 统一重排 id，避免不同来源的 chunk_index 冲突
        for index, chunk in enumerate(chunks):
            object.__setattr__(chunk, "chunk_index", index)
        return chunks

    def _chunks_from_fmeca(self) -> list[DocumentChunk]:
        """Turn each FMECA rule into one self-contained chunk."""

        chunks: list[DocumentChunk] = []
        for rule in self.database.list_fmeca():
            steps = rule.get("sop_steps") or []
            sop_text = "\n".join(f"{i}. {step}" for i, step in enumerate(steps, start=1)) or "（规则库未提供 SOP）"
            parts = rule.get("required_parts") or []
            text = "\n".join([
                f"【FMECA规则库 > F-{int(rule['fault_code']):02d} {rule['fault_name']}】",
                f"系统：{rule.get('system_name', '—')}｜部件：{rule.get('component', '—')}｜严重度：{rule.get('severity', '—')}",
                f"典型信号：{'、'.join(rule.get('evidence_signals', [])) or '—'}",
                f"故障机理：{rule.get('physical_mechanism', '—')}",
                f"检测方法：{rule.get('detection_method', '—')}",
                f"标准维修步骤：\n{sop_text}",
                f"所需备件：{'、'.join(parts) or '按现场检查结果确定'}",
                f"预估工时：{rule.get('estimated_hours', '—')} 小时｜建议工程师级别：{rule.get('engineer_level', '—')}",
            ])
            chunks.append(DocumentChunk(
                id=f"fmeca::{rule['fault_code']}", text=text, source="fmeca_rules", doc_type="fmeca",
                section_path=["FMECA规则库", f"F-{int(rule['fault_code']):02d} {rule['fault_name']}"],
                fault_code=int(rule["fault_code"]), chunk_index=len(chunks), char_count=len(text),
            ))
        return chunks

    def _chunks_from_cases(self) -> list[DocumentChunk]:
        """Turn each historical case into one chunk (phenomenon → cause → fix)."""

        chunks: list[DocumentChunk] = []
        try:
            rows = self.database.execute("SELECT * FROM case_library ORDER BY created_at DESC")
        except Exception:  # noqa: BLE001 - 表不存在时跳过，不阻塞索引构建
            return chunks
        for row in rows:
            text = "\n".join([
                f"【历史案例库 > {row['id']}（{row['machine_id']}）】",
                f"故障码：{row['fault_code']}",
                f"现象：{row['description']}",
                f"根因：{row['root_cause']}",
                f"处置：{row['resolution']}",
                f"风险链：{row['risk_path']}",
                f"当时剩余寿命：{row['rul_hours'] if row['rul_hours'] is not None else '未记录'} 小时",
            ])
            chunks.append(DocumentChunk(
                id=f"case::{row['id']}", text=text, source="case_library", doc_type="case",
                section_path=["历史案例库", str(row["id"])], fault_code=int(row["fault_code"]),
                chunk_index=len(chunks), char_count=len(text),
            ))
        return chunks

    def _chunks_from_machines(self) -> list[DocumentChunk]:
        """Summarize machine master data by production line.

        100 台设备逐条入库会把索引撑大且几乎不产生有效召回，因此按**产线聚合**：
        既保留"某条产线有哪些设备、产能多少"这类可回答的问题，又控制索引规模。
        """

        chunks: list[DocumentChunk] = []
        machines = self.database.list_machines()
        lines: dict[str, list[dict[str, object]]] = {}
        for machine in machines:
            lines.setdefault(str(machine.get("production_line", "未分配")), []).append(machine)
        for line, items in sorted(lines.items()):
            total_output = sum(int(item.get("daily_output", 0) or 0) for item in items)
            models = sorted({str(item.get("model", "")) for item in items})
            text = "\n".join([
                f"【设备主数据 > {line}】",
                f"设备数量：{len(items)} 台，编号 {items[0]['id']} ~ {items[-1]['id']}",
                f"型号：{'、'.join(models)}",
                f"日产合计：{total_output:,} 件",
                "用途：工业数控加工中心，用于产线高精度金属零件加工（铣削/钻孔/攻丝/镗孔）。",
            ])
            chunks.append(DocumentChunk(
                id=f"machine::{line}", text=text, source="machines", doc_type="machine",
                section_path=["设备主数据", line], fault_code=None,
                chunk_index=len(chunks), char_count=len(text),
            ))
        return chunks

    # --------------------------------------------------------------- persistence

    async def build(self, *, embed: bool = True) -> dict[str, Any]:
        """(Re)build the index from scratch and persist it."""

        chunks = self.collect_chunks()
        self._chunks = chunks
        self.bm25.build(chunks)

        self._index_dir.mkdir(parents=True, exist_ok=True)
        self.bm25.save(self._index_dir / "bm25.pkl")
        CHUNKS_PATH.parent.mkdir(parents=True, exist_ok=True)
        CHUNKS_PATH.write_text(
            json.dumps([chunk.as_record() for chunk in chunks], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        embedded = 0
        if embed:
            try:
                embedded = await self.dense.build(chunks)
            except Exception as error:  # noqa: BLE001 - 稠密通道失败不应影响稀疏索引
                LOGGER.warning(f"dense index build skipped: {type(error).__name__}: {error}")

        FINGERPRINT_PATH.write_text(json.dumps(self._fingerprint(chunks), ensure_ascii=False, indent=2), encoding="utf-8")
        self._last_build = {
            "chunks": len(chunks),
            "by_doc_type": _count_by_type(chunks),
            "embedded": embedded,
            "dense_enabled": self.dense.available,
            "sources": sorted({chunk.source for chunk in chunks}),
        }
        LOGGER.info(f"knowledge index built: {self._last_build}")
        return self._last_build

    async def ensure(self, *, embed: bool = True, force: bool = False) -> dict[str, Any]:
        """Load the persisted index, rebuilding only when it is missing or stale."""

        if not force and self._load_from_disk():
            self._last_build = {"loaded": True, "chunks": len(self._chunks), **_count_by_type(self._chunks)}
            LOGGER.info(f"knowledge index loaded from disk: {len(self._chunks)} chunks")
            return {**self._last_build, "dense_enabled": self.dense.available}
        return await self.build(embed=embed)

    def _load_from_disk(self) -> bool:
        """Load BM25 + chunk snapshot, validating against the source fingerprint."""

        if not (self._index_dir / "bm25.pkl").exists() or not CHUNKS_PATH.exists():
            return False
        try:
            if FINGERPRINT_PATH.exists():
                stored = json.loads(FINGERPRINT_PATH.read_text(encoding="utf-8"))
                if stored != self._fingerprint(self._peek_chunks()):
                    return False
        except Exception:  # noqa: BLE001 - 指纹校验失败就重建
            return False
        if not self.bm25.load(self._index_dir / "bm25.pkl"):
            return False
        self._chunks = self._peek_chunks()
        return bool(self._chunks)

    def _peek_chunks(self) -> list[DocumentChunk]:
        """Rehydrate chunk objects from the JSON snapshot without recomputing."""

        records = json.loads(CHUNKS_PATH.read_text(encoding="utf-8"))
        return [
            DocumentChunk(
                id=str(record["id"]), text=str(record["text"]), source=str(record["source"]),
                doc_type=str(record["doc_type"]), section_path=list(record.get("section_path", [])),
                fault_code=record.get("fault_code"), chunk_index=int(record.get("chunk_index", 0)),
                char_count=int(record.get("char_count", len(str(record["text"])))),
            )
            for record in records
        ]

    @staticmethod
    def _fingerprint(chunks: list[DocumentChunk]) -> dict[str, Any]:
        """Content hash used to detect whether a rebuild is necessary."""

        digest = hashlib.sha256("\n".join(f"{chunk.id}|{chunk.text}" for chunk in chunks).encode("utf-8")).hexdigest()
        return {"sha256": digest, "count": len(chunks)}

    def stats(self) -> dict[str, Any]:
        """Return index stats merged with the last build report."""

        return {**self.retriever.stats(), "last_build": self._last_build}


def _count_by_type(chunks: list[DocumentChunk]) -> dict[str, int]:
    """Count chunks per document type."""

    counts: dict[str, int] = {}
    for chunk in chunks:
        counts[chunk.doc_type] = counts.get(chunk.doc_type, 0) + 1
    return counts


async def rebuild_index(database: Database | None = None, *, embed: bool = True) -> dict[str, Any]:
    """Convenience entry point for scripts and the admin API."""

    index = KnowledgeIndex(database)
    return await index.build(embed=embed)


if __name__ == "__main__":
    print(asyncio.run(rebuild_index()))
