"""
==========================================
 智维先知 LangGraph版 — 主入口
==========================================
【使用方式】
python src/main.py --devices CNC_007 CNC_012 CNC_023

或者直接运行（使用默认设备）：
python src/main.py
"""

import sys
import os

# 确保项目根目录在Python路径中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.graph.build import build_analysis_graph, run_analysis


def print_report(report: str):
    """打印分析报告"""
    print("\n" + "=" * 60)
    print("📄 最终分析报告")
    print("=" * 60)
    print(report)


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="智维先知 LangGraph版 - CNC设备预测性维护系统")
    parser.add_argument("--devices", nargs="+", default=None,
                        help="要分析的设备ID，如 CNC_007 CNC_012")
    parser.add_argument("--thread", default=None,
                        help="会话ID（用于恢复）")

    args = parser.parse_args()

    # 默认设备
    devices = args.devices or ["CNC_007", "CNC_012"]

    print("=" * 60)
    print("  智维先知 — 基于LangGraph多智能体的预测性维护系统")
    print("=" * 60)

    # 构建并运行
    graph = build_analysis_graph()
    result = run_analysis(devices, graph, args.thread)

    # 输出报告
    report = result.get("final_report", "报告生成失败")
    print_report(report)

    # 输出摘要
    print("\n" + "=" * 60)
    print("📊 分析摘要")
    print("=" * 60)
    print(f"• 设备: {', '.join(result.get('device_ids', []))}")
    print(f"• 风险等级: {result.get('diagnosis_result', {}).get('risk_level', 'N/A')}")
    print(f"• 风险评分: {result.get('diagnosis_result', {}).get('risk_score', 0):.3f}")
    print(f"• 主要故障: {result.get('diagnosis_result', {}).get('primary_fault', {}).get('fault_name', '无')}")
    print(f"• 建议行动: {result.get('decision_result', {}).get('action', 'N/A')}")
    print(f"• 自验收状态: {result.get('verification_status', 'N/A')}")
    print(f"• 迭代次数: {result.get('iteration_count', 0)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
