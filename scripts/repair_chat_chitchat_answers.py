"""就地修复历史会话里"检索原文冒充答案"的助手回复。

背景
----
在下面两个缺陷修好之前，凡是落到 ``knowledge_search`` 的短问题都会在 LLM
组稿失败（DeepSeek ``response_format=json_object`` 接口 400）后走模板兜底，
把知识库检索原文整段贴出来当答案：

1. 闲聊类问题（"我是谁"）被错误地路由到知识检索；
2. 模板兜底在检索为空时也不区分"命中"与"没命中"。

缺陷已在 ``src/agents/chat_agent.py`` 与 ``src/knowledge/retrieval.py`` 中修复，
但**已落库的历史消息不会自动更新**——演示时点开旧会话仍会看到截图里那种
"知识库检索结果：[1] 温度过高…"。本脚本按**新口径重跑受影响的那几轮问答**，
只覆盖这一条助手回复的正文，保留消息 id、所属会话与时间戳。

判定范围（严格收窄，避免误伤真实检索结果）
------------------------------------------
``role='assistant'`` 且同时满足：
- 正文以 ``知识库检索结果：`` 开头（模板兜底的标志前缀）；
- 归属的**用户提问**是闲聊/元问题（分类结果为 ``chitchat``）。

**不会**触碰任何真实工程查询的回答——那些回答同样可能以该前缀开头，
但它们是有依据的检索结果，属于正常输出。

用法::

    .venv/Scripts/python.exe scripts/repair_chat_chitchat_answers.py --dry-run
    .venv/Scripts/python.exe scripts/repair_chat_chitchat_answers.py
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agents.chat_agent import ChatAgent, classify_intent  # noqa: E402
from src.config import DATA_DIR  # noqa: E402

DB_PATH = Path(DATA_DIR) / "zhiwei.db"
PREFIX = "知识库检索结果："


def find_targets(connection: sqlite3.Connection) -> list[dict[str, object]]:
    """Return the assistant messages whose answer should be rebuilt.

    对每条以模板前缀开头的助手回复，回溯同一会话中它前面最近的一条用户提问；
    只有当该提问按新口径分类为 ``chitchat`` 时才算目标。
    """

    targets: list[dict[str, object]] = []
    rows = connection.execute(
        """SELECT id, thread_id, content FROM conversation_messages
           WHERE role='assistant' AND content LIKE ?
           ORDER BY id""",
        (PREFIX + "%",),
    ).fetchall()
    for message_id, thread_id, content in rows:
        question_row = connection.execute(
            """SELECT content FROM conversation_messages
               WHERE thread_id=? AND role='user' AND id<?
               ORDER BY id DESC LIMIT 1""",
            (thread_id, message_id),
        ).fetchone()
        if question_row is None:
            continue
        question = str(question_row[0])
        if classify_intent(question) != "chitchat":
            continue
        targets.append({
            "id": int(message_id), "thread_id": str(thread_id),
            "question": question, "old_len": len(str(content)),
        })
    return targets


def rebuild_answer(agent: ChatAgent, question: str) -> str:
    """Produce the answer the current code would give for this question."""

    state = {
        "thread_id": "repair", "question": question, "intent": "chitchat",
        "machine_id": None, "facts": [], "citations": [], "memory_context": "",
        "answer": "", "llm_used": False,
    }
    # 直接用图里的 dispatch 结果驱动模板渲染，行为与线上完全一致
    import asyncio

    dispatched = asyncio.run(agent._node_dispatch(state))
    state.update(dispatched)
    return ChatAgent._template_answer(state)  # type: ignore[arg-type]


def main() -> int:
    parser = argparse.ArgumentParser(description="修复闲聊问题被误答的历史记录")
    parser.add_argument("--dry-run", action="store_true", help="只列出待改记录，不写库")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"找不到数据库：{DB_PATH}")
        return 1

    connection = sqlite3.connect(DB_PATH)
    try:
        targets = find_targets(connection)
        if not targets:
            print("没有需要修复的记录——历史消息已与新口径一致。")
            return 0

        print(f"待修复 {len(targets)} 条助手回复：")
        agent = ChatAgent()
        for item in targets:
            print(f"  消息 #{item['id']}（会话 {item['thread_id']}）问题={item['question']!r} 原长={item['old_len']}")

        if args.dry_run:
            print("\n--dry-run：未写入数据库。")
            return 0

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = DB_PATH.with_suffix(f".db.bak-{stamp}")
        shutil.copy2(DB_PATH, backup)
        print(f"\n已备份整库：{backup}")

        updated = 0
        for item in targets:
            new_answer = rebuild_answer(agent, str(item["question"]))
            connection.execute(
                """UPDATE conversation_messages
                   SET content=?, citations_json='[]', intent='chitchat'
                   WHERE id=?""",
                (new_answer, item["id"]),
            )
            updated += 1
        connection.commit()
        print(f"已更新 {updated} 条记录（保留原消息 id / 会话 / 时间戳）。")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
