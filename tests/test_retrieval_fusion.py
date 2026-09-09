"""混合检索融合测试：验证 Reciprocal Rank Fusion（RRF）的排序行为。

RRF 只用“排名”而非原始分数融合多路召回，故对量纲不一致（BM25 分数无界、
向量余弦∈[-1,1]）天然鲁棒。这里验证其核心性质。
"""

from __future__ import annotations

from src.knowledge.retrieval import reciprocal_rank_fusion


def test_rrf_prefers_items_ranked_high_in_multiple_channels():
    """在多个通道中都排在前列的项目应获得更高融合分。"""
    # 通道1 认为 A 最好，通道2 认为 A 也较好 → A 应排第一
    channel_a = [("A", 0.9), ("B", 0.8), ("C", 0.7)]
    channel_b = [("A", 0.6), ("C", 0.5), ("D", 0.4)]
    fused = reciprocal_rank_fusion([channel_a, channel_b])
    ids = [item[0] for item in fused]
    assert ids[0] == "A", f"两路都看好的 A 应第一，实际 {ids}"
    assert "B" in ids and "C" in ids and "D" in ids


def test_rrf_k_parameter_controls_boost():
    """更小的 k 会放大前排位置的收益，改变融合分差异。"""
    ranking = [("X", 1.0), ("Y", 0.5)]
    small_k = reciprocal_rank_fusion([ranking], k=1)
    large_k = reciprocal_rank_fusion([ranking], k=60)
    # 前排优势在 k 小时被放大：X/Y 分差应更大
    gap_small = small_k[0][1] - small_k[1][1]
    gap_large = large_k[0][1] - large_k[1][1]
    assert gap_small > gap_large


def test_rrf_deduplicates_by_identifier():
    """同一文档即使多路命中，也只保留一条（融合分累加）。"""
    dup = [("doc1", 0.9), ("doc1", 0.8)]
    fused = reciprocal_rank_fusion([dup, dup])
    assert len(fused) == 1
    assert fused[0][0] == "doc1"


def test_rrf_respects_weights():
    """带权重时，高权重通道的排序应主导融合结果。"""
    # 通道1 权重高且认为 A 第一；通道2 权重低认为 B 第一 → A 应仍领先
    ch1 = [("A", 0.9), ("B", 0.1)]
    ch2 = [("B", 0.9), ("A", 0.1)]
    fused_heavy_first = reciprocal_rank_fusion([ch1, ch2], weights=[2.0, 0.5])
    assert fused_heavy_first[0][0] == "A"
