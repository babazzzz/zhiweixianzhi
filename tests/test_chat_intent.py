"""聊天意图路由与检索相关性门槛测试。

锁住两条历史故障的不变量：

1. **闲聊问题不许走知识检索**。"我是谁"曾经被分类成 ``knowledge_search``，
   而中文按字切词让停用字"是"命中了 5 个无关 chunk，聊天窗口最终把
   维修手册原文当作答案吐出来。
2. **检索门槛不许误伤真实工程查询**。门槛的作用是拦掉"只有停用字命中"的
   伪召回，不能把"温度过高怎么处理"这类正常问题一起拦掉。
"""

from __future__ import annotations

from src.agents.chat_agent import ChatAgent, ChatState, classify_intent, extract_machine_id
from src.knowledge.retrieval import is_stopword, tokenize


# ---------------------------------------------------------------------------
# 意图分类
# ---------------------------------------------------------------------------

def test_chitchat_questions_route_to_chitchat():
    """问"你/系统"本身的问题必须识别为闲聊，不能落到知识检索。"""

    for question in ["我是谁", "你是谁", "你叫什么", "你好", "谢谢", "在吗"]:
        assert classify_intent(question) == "chitchat", f"{question!r} 应识别为闲聊"


def test_engineering_questions_not_misclassified_as_chitchat():
    """设备运维问题不能被闲聊关键词抢走——尤其"你好"里的"好"等字不得误触发。"""

    cases = {
        "CNC_078 的主轴温度高怎么处理？": "knowledge_search",
        "轴承磨损的维护 SOP 是什么？": "machine_sop",
        "CNC_025 最近修过什么？": "machine_records",
        "全厂有多少台设备": "general",
    }
    for question, expected in cases.items():
        assert classify_intent(question) == expected, f"{question!r} 应为 {expected}"


def test_unknown_question_defaults_to_knowledge_search():
    """规则认不出时落到知识检索——这是默认动作，再由 LLM 兜底纠偏。"""

    assert classify_intent("今天的天气怎么样") == "knowledge_search"


def test_extract_machine_id_normalizes_format():
    """设备号要从自然语言里抽出来并补零对齐。"""

    assert extract_machine_id("CNC_078 的主轴温度高怎么处理？") == "CNC_078"
    assert extract_machine_id("cnc-25 怎么样了") == "CNC_025"
    assert extract_machine_id("帮我看看CNC 7") == "CNC_007"
    assert extract_machine_id("主轴温度高怎么处理") is None


# ---------------------------------------------------------------------------
# 停用字与相关性门槛
# ---------------------------------------------------------------------------

def test_stopwords_recognized():
    """功能字与超短数字不携带检索信号。"""

    for token in ["是", "的", "在", "我", "你", "谁", "吗", "the", "of", "3"]:
        assert is_stopword(token), f"{token!r} 应判为停用字"
    for token in ["温", "度", "轴", "承", "cnc_078", "7014cdga", "amperage"]:
        assert not is_stopword(token), f"{token!r} 是实义词，不应判为停用字"


def test_tokenizer_splits_chinese_by_char():
    """中文按字切词——这正是停用字能造成伪召回的根源，行为本身要稳定。"""

    assert tokenize("我是谁") == ["我", "是", "谁"]
    # 英文按词、数字单独成词；型号里的斜杠是分隔符
    assert tokenize("SKF 7014CDGA/P4A") == ["skf", "7014", "cdga", "p4a"]


def test_template_answer_reports_miss_instead_of_dumping_raw_text():
    """检索为空时模板必须说"没找到"，不能贴检索原文。"""

    state: ChatState = {
        "thread_id": "T-TEST", "question": "我是谁", "intent": "knowledge_search",
        "machine_id": None,
        "facts": [{"type": "rag", "data": "", "empty": True}],
        "citations": [], "memory_context": "", "answer": "", "llm_used": False,
    }
    answer = ChatAgent._template_answer(state)
    assert "没有检索到" in answer
    assert "知识库检索结果" not in answer


def test_template_answer_renders_chitchat_self_description():
    """闲聊意图的模板输出是固定自述，且不得包含检索原文前缀。"""

    state: ChatState = {
        "thread_id": "T-TEST", "question": "我是谁", "intent": "chitchat",
        "machine_id": None, "facts": [{"type": "chitchat", "data": "我是智维先知的设备查询助手。"}],
        "citations": [], "memory_context": "", "answer": "", "llm_used": False,
    }
    answer = ChatAgent._template_answer(state)
    assert "智维先知" in answer
    assert "知识库检索结果" not in answer


def test_extract_answer_handles_json_envelope_and_fallback():
    """组稿出参固定是 JSON 信封，解析失败要能优雅退化而不是抛错。"""

    assert ChatAgent._extract_answer('{"answer": "这是回答"}') == "这是回答"
    assert ChatAgent._extract_answer('```json\n{"answer": "围栏包裹"}\n```') == "围栏包裹"
    assert ChatAgent._extract_answer('{"answer": "  "}') == ""
    assert ChatAgent._extract_answer("") == ""
    assert ChatAgent._extract_answer("裸文本回答") == "裸文本回答"
