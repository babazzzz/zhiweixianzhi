"""四层记忆架构。

避免记忆混乱的核心原则：**按时间尺度和用途分层，每层一个入口**。

1. 会话记忆（short_term）：当前聊天窗口内的多轮上下文。存 SQLite
   ``conversation_threads`` / ``conversation_messages``，满 12 条自动滚动摘要，
   原始消息保留供回看。
2. 情节记忆（episodic）：跨会话的"发生过什么"，由诊断/维修事件自然沉淀，
   复用 ``agent_activity`` 与 ``diagnostics``，不另建存储（避免重复记账）。
3. 设备画像（machine_profile）：某台设备的长期事实，如"3 号线 B2012 主轴
   轴承已更换两次"。存 ``long_term_memory``，kind=machine，subject=设备号。
4. 语义记忆（semantic）：通用知识与用户偏好，如"管理员倾向保守维修"。
   存 ``long_term_memory``，kind=preference/fact/lesson；知识库正文本身
   走 RAG 检索，不属于记忆模块。

对外只暴露 :class:`MemoryManager` 一个门面，防止上层误用内部存储。
"""

from .manager import MemoryManager, MEMORY_KINDS

__all__ = ["MemoryManager", "MEMORY_KINDS"]
