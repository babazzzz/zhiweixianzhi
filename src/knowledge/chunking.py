"""Structure-aware document chunking for the 智维先知 knowledge base.

为什么不用「固定长度滑窗」
-------------------------
本项目知识库是**强结构**的工程文档：维护手册按「故障类型 N」分节，每节内部是
「典型症状 / 标准维修步骤 / 所需备件 / 工时」；设备说明书按「一、二、三、」分章，
每章下是编号条目。固定长度滑窗会：

* 把一个 SOP 步骤从中间劈开 → 召回的片段不可执行；
* 把「症状」和「处置」切到两个 chunk → 检索到一半答案；
* 丢失章节上下文 → 「步骤 3」脱离「故障类型 1」后毫无意义。

因此这里做**结构优先 + 长度兜底**的两级切分：

1. **结构级**：按标题层级把文档切成 section，记录标题路径（breadcrumb）；
2. **语义级**：在 section 内按「语义块」再切——SOP 步骤列表、症状列表、参数条目
   各自是原子单元，不跨块拼接；
3. **长度兜底**：超长块按句子滑窗切分，重叠 ``overlap_chars`` 个字符，
   并把标题路径**前置**到每个 chunk 的正文里。

把标题路径写进正文是提升召回率最有效的手段之一：向量模型看到的是
「维护手册 > 故障类型1：轴承磨损 > 标准维修步骤」而不是孤立的「3. 拆卸主轴箱侧盖」，
语义匹配和 BM25 词法匹配都因此受益。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

# 标题识别：markdown #、中文数字编号（一、二、三）、「### 故障类型N：xxx」
_HEADING_PATTERNS: tuple[tuple[int, re.Pattern[str]], ...] = (
    (1, re.compile(r"^\s*#\s+(?P<title>.+?)\s*$")),
    (2, re.compile(r"^\s*##\s+(?P<title>.+?)\s*$")),
    (3, re.compile(r"^\s*###\s+(?P<title>.+?)\s*$")),
    (2, re.compile(r"^\s*[一二三四五六七八九十]+、\s*(?P<title>.+?)\s*$")),
    (3, re.compile(r"^\s*故障类型\s*(?P<code>\d+)\s*[：:]\s*(?P<title>.+?)\s*$")),
    (3, re.compile(r"^\s*【\s*(?P<title>[^】]+?)\s*】\s*$")),
)

#: 语义块起始标记：一行以这些词开头时，开启一个新的语义块
_BLOCK_STARTERS: tuple[str, ...] = (
    "典型症状", "标准维修步骤", "所需备件", "预估维修工时", "建议工程师级别",
    "故障机理", "检测方法", "处理", "分析", "现象", "注意事项", "安全提示",
)

#: 故障名 → 故障码，用于给 chunk 打 ``fault_code`` 元数据，支持按故障码过滤检索
FAULT_CODE_HINTS: dict[str, int] = {
    "轴承磨损": 1, "电机过载": 2, "温度过高": 3, "电压不稳": 4, "电流异常": 5,
    "转子故障": 6, "电源模块故障": 7, "散热系统故障": 8, "复合故障": 9,
    "bearing_wear": 1, "motor_overload": 2, "over_temperature": 3, "voltage_instability": 4,
    "current_abnormality": 5, "rotor_fault": 6, "power_module_failure": 7,
    "cooling_system_failure": 8,
}

#: 领域同义词：给 chunk 的文本与查询做**双向**扩展，提升召回率
#: （例如用户问「发热」，文档写的是「温度升高」）
SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("温度", "温升", "发热", "过热", "高温", "temperature"),
    ("电流", "安培", "安", "amperage", "current"),
    ("电压", "volt", "voltage", "伏"),
    ("转速", "rpm", "speed", "转每分钟"),
    ("振动", "抖动", "vibration", "震"),
    ("轴承", "bearing", "滚珠"),
    ("散热", "冷却", "风扇", "cooling", "滤网"),
    ("润滑", "润滑脂", "润滑油", "lubrication"),
    ("刀具", "刀片", "切削", "tool"),
    ("电源模块", "稳压", "power module", "电源"),
    ("电机", "马达", "伺服", "motor"),
    ("编码器", "码盘", "encoder"),
    ("停机", "急停", "断电", "downtime"),
    ("维修", "检修", "维护", "保养", "maintenance"),
    ("产量", "日产量", "日产", "产能", "产出", "units produced", "output"),
    ("成本", "单位生产成本", "成本高", "unit cost"),
    ("故障", "异常", "失效", "fault", "failure"),
)

_SYNONYM_INDEX: dict[str, tuple[str, ...]] = {
    term: group for group in SYNONYM_GROUPS for term in group
}


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    """One retrievable knowledge unit with full provenance."""

    id: str
    text: str
    source: str
    #: 文档类型：manual（维护手册）/ spec（设备说明书）/ guide（征兆判读指引）/
    #: case（案例库）/ fmeca（规则库）/ cost（成本产量）
    doc_type: str
    #: 标题路径，如 ["维护手册", "故障类型1：轴承磨损", "标准维修步骤"]
    section_path: list[str] = field(default_factory=list)
    #: 从标题或正文推断出的故障码，用于按故障过滤检索
    fault_code: int | None = None
    chunk_index: int = 0
    char_count: int = 0

    @property
    def breadcrumb(self) -> str:
        """Return the human-readable section path."""

        return " > ".join(self.section_path)

    def metadata(self) -> dict[str, str | int | None]:
        """Return Chroma-safe scalar metadata."""

        return {
            "source": self.source,
            "doc_type": self.doc_type,
            "section_path": self.breadcrumb,
            "fault_code": self.fault_code if self.fault_code is not None else -1,
            "chunk_index": self.chunk_index,
        }

    def as_record(self) -> dict[str, object]:
        """Return the JSON shape persisted in the chunk index."""

        return {
            "id": self.id, "text": self.text, "source": self.source, "doc_type": self.doc_type,
            "section_path": list(self.section_path), "fault_code": self.fault_code,
            "chunk_index": self.chunk_index, "char_count": self.char_count,
        }


def chunk_document(
    text: str,
    *,
    source: str,
    doc_type: str,
    max_chars: int = 480,
    overlap_chars: int = 80,
    min_chars: int = 24,
) -> list[DocumentChunk]:
    """Split one document into structure-aware chunks.

    Args:
        text: 原始文档全文。
        source: 文档来源标识（文件名或 "fmeca_rules" 之类的逻辑名），写入元数据用于引用。
        doc_type: manual / spec / case / fmeca / cost。
        max_chars: 单个 chunk 的字符上限，超过则按句子滑窗再切。
        overlap_chars: 滑窗重叠字符数，避免把一句完整的话切断。
        min_chars: 过短的碎片会被合并到前一个 chunk，避免产生无意义的噪声块。

    Returns:
        按文档顺序排列的 chunk 列表，每个 chunk 的正文已前置标题路径。
    """

    root_title = _first_title(text) or source
    sections = _split_sections(text, root_title)
    chunks: list[DocumentChunk] = []
    for section in sections:
        path = section.path
        fault_code = _infer_fault_code(path, section.body)
        for block in _split_blocks(section.body):
            for piece in _split_long(block, max_chars, overlap_chars):
                piece = piece.strip()
                if not piece:
                    continue
                prefix = " > ".join(path)
                full_text = f"【{prefix}】\n{piece}" if prefix else piece
                chunks.append(DocumentChunk(
                    id="", text=full_text, source=source, doc_type=doc_type,
                    section_path=list(path), fault_code=fault_code,
                    chunk_index=len(chunks), char_count=len(full_text),
                ))
    chunks = _merge_tiny(chunks, min_chars)
    for index, chunk in enumerate(chunks):
        object.__setattr__(chunk, "id", f"{source}::{index:04d}")
        object.__setattr__(chunk, "chunk_index", index)
    return chunks


@dataclass(frozen=True, slots=True)
class _Section:
    """Internal representation of one heading-delimited section."""

    path: list[str]
    body: str


def _first_title(text: str) -> str:
    """Return the first meaningful line as the document root title."""

    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("---"):
            return line
    return ""


def _split_sections(text: str, root_title: str) -> list[_Section]:
    """Split the document by heading levels, tracking the current heading stack."""

    stack: list[tuple[int, str]] = [(1, root_title)]
    sections: list[_Section] = []
    buffer: list[str] = []

    def _flush() -> None:
        body = "\n".join(buffer).strip()
        buffer.clear()
        if body:
            sections.append(_Section(path=[title for _, title in stack], body=body))

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped in ("---", "***", "___"):
            continue
        heading = _match_heading(stripped)
        if heading is not None:
            level, title = heading
            _flush()
            while len(stack) > 1 and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            continue
        buffer.append(raw_line)
    _flush()
    return sections


def _match_heading(line: str) -> tuple[int, str] | None:
    """Return ``(level, title)`` when the line is a heading, otherwise ``None``."""

    if not line:
        return None
    for level, pattern in _HEADING_PATTERNS:
        match = pattern.match(line)
        if match is None:
            continue
        title = match.group("title").strip()
        if not title:
            return None
        return level, title
    return None


def _split_blocks(body: str) -> list[str]:
    """Split a section body into semantic blocks (SOP lists, symptom lists, ...)."""

    lines = body.splitlines()
    blocks: list[str] = []
    current: list[str] = []
    for line in lines:
        stripped = line.strip()
        starts_block = any(stripped.startswith(prefix) for prefix in _BLOCK_STARTERS)
        # 「1. xxx」这类编号条目在顶级缩进（无前导空格）时也视为新块起点
        starts_numbered = bool(re.match(r"^\d+[.、)]\s", stripped))
        if (starts_block or starts_numbered) and current:
            blocks.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append("\n".join(current).strip())
    return [block for block in blocks if block]


def _split_long(block: str, max_chars: int, overlap_chars: int) -> list[str]:
    """Split an oversized block on sentence boundaries with character overlap."""

    if len(block) <= max_chars:
        return [block]
    sentences = re.split(r"(?<=[。！？；\n])", block)
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        if len(current) + len(sentence) <= max_chars:
            current += sentence
            continue
        if current:
            pieces.append(current)
            # 保留尾部 overlap，保证跨块的句子仍能被完整召回一次
            tail = current[-overlap_chars:] if overlap_chars > 0 else ""
            current = tail + sentence
        else:
            current = sentence
    if current.strip():
        pieces.append(current)
    return [piece.strip() for piece in pieces if piece.strip()]


def _merge_tiny(chunks: list[DocumentChunk], min_chars: int) -> list[DocumentChunk]:
    """Fold sub-minimum fragments into their predecessor to avoid noise chunks."""

    if not chunks:
        return []
    merged: list[DocumentChunk] = []
    for chunk in chunks:
        body = chunk.text.rsplit("\n", 1)[-1]
        if merged and len(body) < min_chars:
            previous = merged[-1]
            combined = f"{previous.text}\n{body}"
            merged[-1] = DocumentChunk(
                id=previous.id, text=combined, source=previous.source, doc_type=previous.doc_type,
                section_path=previous.section_path, fault_code=previous.fault_code or chunk.fault_code,
                chunk_index=previous.chunk_index, char_count=len(combined),
            )
        else:
            merged.append(chunk)
    return merged


def _infer_fault_code(path: Iterable[str], body: str) -> int | None:
    """Infer the fault code from the heading path, falling back to the body."""

    for label in path:
        for name, code in FAULT_CODE_HINTS.items():
            if name in label:
                return code
    for name, code in FAULT_CODE_HINTS.items():
        if name in body[:200]:
            return code
    return None


def expand_query(query: str, *, limit_per_term: int = 3) -> str:
    """Expand a user query with domain synonyms to improve recall.

    例：``"主轴发热怎么办"`` → ``"主轴发热怎么办 温度升高 温升 高温 过热 temperature"``。
    扩展词只用于**稀疏检索**（BM25）侧的召回；稠密检索仍用原始 query，避免语义被稀释。
    """

    extra: list[str] = []
    seen = {query}
    for term, group in _SYNONYM_INDEX.items():
        if term in query:
            for candidate in group:
                if candidate != term and candidate not in seen:
                    seen.add(candidate)
                    extra.append(candidate)
                    if len(extra) >= limit_per_term * len(group):
                        break
    return " ".join([query, *extra]) if extra else query
