"""
==========================================
 RAG 知识库智能体 — rag.py
==========================================
★ RAG 核心技术学习示例 ★

【什么是 RAG？】
RAG = Retrieval-Augmented Generation（检索增强生成）

传统 LLM 的困境：
    训练数据截止于某个时间点，不知道你的私有数据，会"幻觉"

RAG 的解决方案：
    用户问 → 去知识库搜索相关资料 → 把资料和问题一起给LLM → LLM基于资料回答

比喻：
    传统LLM = 闭卷考试（全靠记忆）
    RAG = 开卷考试（可以查资料）

【我们这个项目中的 RAG】
故障案例知识库（原项目积累的7个标注案例 + 10种故障模式描述）
→ 关键词匹配检索
→ 传给 Diagnosi Agent 作为诊断参考

【为什么不使用向量数据库？】
对于小规模知识库（17个文本块），简单的关键词匹配比向量检索：
- 更快（不需调用嵌入模型）
- 更可靠（不会因嵌入模型问题而失败）
- 可解释性更强（能告诉你为什么匹配到某个案例）

大规模知识库（1000+文档）才需要向量数据库。
"""

from typing import Optional
from langchain_core.messages import HumanMessage
from ..state import AgentState

KNOWLEDGE_FILE = None  # 暂不依赖外部文件


def _get_knowledge_base() -> list[dict]:
    """
    获取知识库条目。

    每个条目包含：
    - id:     唯一标识
    - title:  标题
    - content: 正文内容
    - keywords: 关键词列表（用于匹配）
    """
    return [
        {"id": "C01", "title": "主轴轴承磨损", "keywords": ["轴承", "主轴", "温度上升", "电流上升", "转速下降", "振动"],
         "content": """案例1：CNC_007 主轴轴承磨损预兆
现象：温度从42℃逐步上升至56℃，电流从11.5A增至14.2A，伴随高频振动。
分析：温升速率0.8℃/h，电流上升同时转速下降，符合轴承磨损特征。
处理：更换主轴轴承后温度回归42℃，电流回归11.8A。提前预警时间：约48小时"""},

        {"id": "C02", "title": "冷却系统故障", "keywords": ["冷却", "温度骤升", "电流稳定", "散热"],
         "content": """案例2：CNC_012 冷却系统故障
现象：温度从40℃骤升至62℃，但电流稳定在10.5A左右无变化。
分析：温度急升但电流稳定——说明不是负载问题，而是散热失效。
处理：清洗冷却管路，更换冷却液，温度降至42℃。提前预警时间：约12小时"""},

        {"id": "C03", "title": "刀具磨损", "keywords": ["刀具", "刀片", "电流上升", "功率上升", "切削"],
         "content": """案例3：CNC_023 刀具磨损
现象：电流从12.3A逐渐升至15.8A，功率同步上升，温度轻微升高。
分析：电流持续上升但转速稳定，说明切削阻力增大——刀具变钝。
处理：更换刀片，电流回归12.5A。提前预警时间：约24小时"""},

        {"id": "C04", "title": "伺服电机过热", "keywords": ["电机", "过热", "温度", "电流波动", "电压下降"],
         "content": """案例4：CNC_045 伺服电机过热
现象：温度快速升至68℃，电流波动加剧（12-18A），电压下降3.5%。
分析：温度+电流+电压三者同时异常——电机内部故障。
处理：更换伺服电机，温度正常。提前预警时间：约6小时"""},

        {"id": "C05", "title": "电源模块老化", "keywords": ["电源", "电压波动", "电流波动", "老化"],
         "content": """案例5：CNC_067 电源模块老化
现象：电压从380V波动至365-392V，电流随之波动，但设备负载未变。
分析：电压波动范围(±3.6%)超出正常值(±1%)，且与负载无关——电源问题。
处理：更换电源模块，电压稳定在380V±0.8%。提前预警时间：约72小时"""},

        {"id": "C06", "title": "导轨润滑不足", "keywords": ["导轨", "润滑", "电流升高", "摩擦"],
         "content": """案例6：CNC_089 导轨润滑不足
现象：进给时电流异常升高(16.2A→19.5A)，停止后恢复正常，温度局部升高。
分析：仅在运动时电流升高——摩擦增大，润滑不足。
处理：补充润滑油，调整润滑周期从7天改为3天。提前预警时间：约36小时"""},

        {"id": "C07", "title": "传感器漂移", "keywords": ["传感器", "漂移", "数据跳变", "异常波动"],
         "content": """案例7：CNC_034 传感器漂移
现象：温度数据忽高忽低(38-52℃随机跳变)，但实际设备运行稳定。
分析：数据异常但无物理规律——传感器故障而非设备故障。
处理：校准温度传感器，数据恢复正常。提前预警时间：N/A（非设备故障）"""},

        {"id": "F01", "title": "故障模式-主轴轴承磨损(关键)", "keywords": ["F001", "轴承", "主轴", "关键"],
         "content": """故障模式F001：主轴轴承磨损 - 关键 | 系统：主轴系统 | 部件：轴承
检测参数：温度↑、电流↑、转速↓ | 特征：温度梯度上升，高频振动
建议措施：更换轴承，检查主轴对中"""},

        {"id": "F02", "title": "故障模式-切削刀具磨损(高)", "keywords": ["F002", "刀具", "刀片", "高"],
         "content": """故障模式F002：切削刀具磨损 - 高 | 系统：刀具系统 | 部件：刀片
检测参数：电流↑、功率↑ | 特征：电流持续上升，加工质量下降
建议措施：更换刀片，检查切削参数"""},

        {"id": "F03", "title": "故障模式-冷却系统故障(高)", "keywords": ["F003", "冷却", "散热", "高"],
         "content": """故障模式F003：冷却系统故障 - 高 | 系统：冷却系统 | 部件：冷却泵
检测参数：温度↑、电流稳定 | 特征：温度急升但电流平稳
建议措施：清洗冷却管路，检查冷却液位"""},

        {"id": "F04", "title": "故障模式-进给系统卡滞(高)", "keywords": ["F004", "进给", "卡滞", "丝杠"],
         "content": """故障模式F004：进给系统卡滞 - 高 | 系统：进给系统 | 部件：丝杠
检测参数：电流↑、转速↓、电压不变 | 特征：运动时电流升高静止时正常
建议措施：清洁丝杠，检查润滑"""},

        {"id": "F05", "title": "故障模式-导轨润滑不足(中)", "keywords": ["F005", "导轨", "润滑", "中"],
         "content": """故障模式F005：导轨润滑不足 - 中 | 系统：导轨系统 | 部件：导轨
检测参数：温度↑(局部)、电流↑(运动时) | 特征：仅运动时异常
建议措施：补充润滑油"""},

        {"id": "F06", "title": "故障模式-电机过热(关键)", "keywords": ["F006", "电机", "过热", "关键"],
         "content": """故障模式F006：电机过热 - 关键 | 系统：驱动系统 | 部件：伺服电机
检测参数：温度↑↑、电流↑↑、电压↓ | 特征：三重异常，温度加速上升
建议措施：停机冷却，检查电机绕组"""},

        {"id": "F07", "title": "故障模式-电源模块老化(中)", "keywords": ["F007", "电源", "老化"],
         "content": """故障模式F007：电源模块老化 - 中 | 系统：电气系统 | 部件：电源模块
检测参数：电压波动↑、电流波动↑ | 特征：与负载无关的同步波动
建议措施：检查电源模块，考虑更换"""},

        {"id": "F08", "title": "故障模式-传感器漂移(低)", "keywords": ["F008", "传感器", "漂移", "低"],
         "content": """故障模式F008：传感器漂移 - 低 | 系统：传感系统 | 部件：温度传感器
检测参数：温度异常波动 | 特征：无物理规律的数据跳变
建议措施：校准或更换传感器"""},

        {"id": "F09", "title": "故障模式-液压系统泄漏(高)", "keywords": ["F009", "液压", "泄漏", "高"],
         "content": """故障模式F009：液压系统泄漏 - 高 | 系统：液压系统 | 部件：液压管路
检测参数：电流↑、转速不稳定 | 特征：负载能力下降，动作迟缓
建议措施：检查液压管路，补充液压油"""},

        {"id": "F10", "title": "故障模式-传动皮带松弛(中)", "keywords": ["F010", "皮带", "松弛", "传动"],
         "content": """故障模式F010：传动皮带松弛 - 中 | 系统：传动系统 | 部件：皮带
检测参数：转速↓、电流波动 | 特征：转速不稳定，皮带打滑
建议措施：张紧或更换皮带"""},
    ]


def keyword_search(query: str, knowledge_base: list[dict], top_k: int = 3) -> list[dict]:
    """
    关键词匹配检索。

    【算法原理】
    1. 把查询拆成关键词（按空格/逗号/分号拆分）
    2. 对每个知识条目，计算匹配到的关键词数量
    3. 返回匹配度最高的 top_k 条

    虽然简单，但对小规模知识库非常有效！
    """
    # 构建查询关键词集
    import re
    query_keywords = set(re.split(r'[,\s;，；]+', query.lower()))
    query_keywords = {k for k in query_keywords if len(k) > 1}  # 去掉单字符

    if not query_keywords:
        return knowledge_base[:top_k]

    # 计算每个条目的匹配分数
    scored = []
    for item in knowledge_base:
        item_keywords = set(k.lower() for k in item["keywords"])
        # 匹配数 / 查询词数 —— 简单但有效的评分
        match_count = len(query_keywords & item_keywords)
        if match_count > 0:
            scored.append((match_count, item))

    # 按匹配度排序
    scored.sort(key=lambda x: x[0], reverse=True)

    return [item for _, item in scored[:top_k]]


def rag_agent(state: AgentState) -> dict:
    """
    RAG 知识库智能体：关键词检索 → 匹配案例 → 返回参考知识。

    【流程】
    1. 获取诊断信息（风险等级、故障类型、证据链）
    2. 构建查询关键词
    3. 从知识库中检索最相关案例
    4. 返回检索到的案例文本

    【输出】
    rag_context: 检索到的案例文本（Markdown格式）
    """
    diagnosis = state.get("diagnosis_result", {})
    device_id = diagnosis.get("device_id", state.get("current_device", "unknown"))
    primary_fault = diagnosis.get("primary_fault", {})
    evidence = diagnosis.get("evidence_chain", {})

    print(f"\n[RAG Agent] 为设备 {device_id} 检索相关案例...")

    # Step 1: 构建查询
    fault_name = primary_fault.get("fault_name", "")
    risk_level = diagnosis.get("risk_level", "")
    fault_id = primary_fault.get("fault_id", "")

    # 从证据链中提取关键信息
    high_evidence = [k for k, v in evidence.items() if v.get("status") in ("warning", "critical")]

    query_parts = [fault_name, fault_id, risk_level] + high_evidence
    query = " ".join(str(q) for q in query_parts if q)
    query += " " + device_id

    # Step 2: 检索
    knowledge_base = _get_knowledge_base()
    matches = keyword_search(query, knowledge_base, top_k=3)

    # Step 3: 组装上下文
    if matches:
        rag_context = "【检索到的相关故障案例】\n"
        for i, item in enumerate(matches, 1):
            rag_context += f"\n--- 案例{i}：{item['title']} ---\n{item['content']}\n"
        print(f"[RAG Agent] 匹配到 {len(matches)} 个相关案例")
    else:
        rag_context = "【未检索到直接匹配的案例，参考通用故障模式】\n"
        generic = [item for item in knowledge_base if item["id"].startswith("F")]
        for g in generic[:3]:
            rag_context += f"\n- {g['title']}: {g['content'][:80]}...\n"
        print("[RAG Agent] 使用通用故障模式作为参考")

    return {
        "rag_context": rag_context,
        "messages": [HumanMessage(content=f"📚 RAG: 为 {device_id} 检索到 {len(matches)} 个相关案例")],
    }
