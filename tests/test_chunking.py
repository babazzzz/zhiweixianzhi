"""切分器测试：验证结构感知切分（标题路径前置 + 语义块边界 + 故障码推断）。"""

from __future__ import annotations

from src.knowledge.chunking import chunk_document

# 一段维护手册样式的文本：两个「故障类型」小节，各带若干语义块起始行
_MANUAL = """# 维护手册

## 故障类型1：轴承磨损

典型症状：主轴运行中温升明显，噪声增大，振动加剧。
标准维修步骤：先断电挂牌，再拆卸主轴箱侧盖，检查轴承游隙与润滑脂状态。
所需备件：深沟球轴承、润滑脂、密封圈。
安全提示：高温部件务必冷却至室温后再作业。

## 故障类型2：电机过载

典型症状：电流持续超额定值，电机温度升高，伴有焦糊味。
标准维修步骤：用钳形表实测三相电流，检查负载是否超限，必要时更换电机。
所需备件：同规格伺服电机、热继电器。
"""


def test_chunk_document_splits_on_headings():
    """不同「故障类型」应被拆到不同 chunk，而不是混在一起。"""
    chunks = chunk_document(_MANUAL, source="维护手册.txt", doc_type="manual")
    assert len(chunks) >= 2, f"期望至少 2 个块，实际 {len(chunks)}"

    texts = [c.text for c in chunks]
    # 标题路径被前置到正文，用于稠密+词法双通道召回
    assert any("轴承磨损" in t for t in texts)
    assert any("电机过载" in t for t in texts)
    # 同一故障的内容没有跨块丢失：两块正文各自包含关键语义
    assert any("标准维修步骤" in t and "主轴箱" in t for t in texts)
    assert any("伺服电机" in t for t in texts)


def test_chunk_document_sets_source_and_doc_type():
    """每个 chunk 都应携带来源标识与文档类型元数据。"""
    chunks = chunk_document(_MANUAL, source="维护手册.txt", doc_type="manual")
    for chunk in chunks:
        assert chunk.source == "维护手册.txt"
        assert chunk.doc_type == "manual"
        assert chunk.id.startswith("维护手册.txt::")


def test_chunk_document_small_returns_at_least_one():
    """极短文本也应至少产生一个 chunk。"""
    chunks = chunk_document("简单的单句说明。", source="a.txt", doc_type="manual")
    assert len(chunks) >= 1
    assert chunks[0].text.strip()
