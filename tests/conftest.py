"""共享 pytest 配置：保证 `src` 可被导入，并为测试提供内存数据库 fixture。

测试策略：所有用例尽量跑在**内存 SQLite**（tmp_path 文件库）上，不触碰
``data/zhiwei.db`` 生产数据；RAG 的 bge-m3 稠密检索、Ollama/DeepSeek 网络调用
一律不触发——只测纯算法与确定性路径，保证快且可复现。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 把项目根加入 sys.path，使 `import src.*` 在任意 cwd 下都可用
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def memory_db(tmp_path):
    """一个全新的、已建好全部 schema 的 SQLite 测试库（不碰生产 zhiwei.db）。"""
    from src.database.connection import Database

    db = Database(database_url=f"sqlite:///{tmp_path / 'test.db'}")
    db.initialize_schema()
    return db
