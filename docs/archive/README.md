# docs/archive — 过时 / 去重文档归档

> 本目录只做**保留**，不做主动删除；如需恢复，移回原位置即可。运行期系统不读取本目录。

## 内容说明

### 1. 改造前的规划/架构文档（已被新实现取代）
- `企业级Agent建设规划.md`、`系统架构设计文档.md`、`模型选型与部署方案.md`
  —— 反映改造前的 6 技能 / 17 端点 / 6 表形态。当前架构以 `README.md`、
  `项目详细报告.md`、`ARCHITECTURE.md` 为准。

### 2. RAG 知识源的历史副本（与唯一源重复，已去重）
- `案例知识库.txt`、`维护手册.txt`、`设备成本与产量.txt`、`设备说明书.txt`
  —— 这 4 份与 **`data/knowledge/`** 下的唯一源 **md5 完全一致**，曾以双份存在于
  `docs/` 与 `data/knowledge/`。去重后统一以 **`data/knowledge/`** 为唯一源
  （`scripts/rag_tools.py` / `src/knowledge/index_builder.py` 只读该目录），
  此处仅作为历史快照保留。

> 知识库索引重建命令：
> ```bash
> .venv/Scripts/python.exe scripts/rag_tools.py build
> .venv/Scripts/python.exe scripts/rag_tools.py eval
> ```
