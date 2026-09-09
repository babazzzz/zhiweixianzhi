# 智维先知 · 智能设备预测性维护系统

> **苗圃杯参赛项目** · 全栈演示版（供评委本地运行）

---

## 项目简介

**智维先知** 是基于「双模型融合 + 工业智能体（Coze）」架构构建的 CNC 设备预测性维护系统，覆盖 15 台 CNC 机床（CNC_007 ～ CNC_053）、3 条产线的全链路健康监控与故障预测。

核心能力：

| 能力 | 说明 |
|------|------|
| 实时风险评估 | 真实 CSV 数据 + 全设备分位数基线，本地即可出分 |
| Coze 工作流分析 | WF_analyze（故障预测）→ WF3（工单生成）→ 绘图工作流（6 张 AI 图表）三级闭环 |
| AI 诊断报告 | 调用 Coze Bot 生成完整 Markdown 维护报告 |
| 智能对话助手 | 侧边栏聊天面板，支持流式输出 |
| 案例知识库 | 本地故障案例管理 + 同步至 Coze 知识库 |

---

## 文件结构

```
zhiwei-submit/
├── api.py          # Flask 后端（含全部 API、Coze 代理、数据库）
├── index.html      # 前端单文件（shadcn/ui 风格，无需打包）
├── data/           # 真实 CSV 数据集（4 个文件）
│   ├── MACHINE_SUMMARY_DATA._2025.csv
│   ├── MACHINE_LOG_DATA._2025.csv
│   ├── PRODUCT_ASSEMBLY_LINE_WITH_MACHINES_2025.csv
│   └── PRODUCT_ASSEMBLY_LINE_WITH_MACHINES_TESTS_2025.csv
└── README.md       # 本说明文件
```

> **注意**：`devices.db` 会在首次启动时自动从 CSV 生成，**无需预先提供**。

---

## 快速启动（3 步）

### 环境要求

- Python ≥ 3.9
- 联网（调用 Coze API）

### 第 1 步：安装依赖

```bash
pip install flask flask-cors requests
```

### 第 2 步：启动后端

```bash
python api.py
```

看到如下输出即代表启动成功：

```
[DB] loaded 15 devices, XXXX logs, XXXX tests
 * Running on http://0.0.0.0:5000
```

### 第 3 步：打开前端

浏览器访问：[http://localhost:5000](http://localhost:5000)

---

## 主要功能导航

| 页面 | 路径 | 功能 |
|------|------|------|
| 仪表板 | 首页 | 设备总览、风险 KPI、最新工单 |
| 设备列表 | 设备管理 | 15 台 CNC 状态、风险评分、详情面板 |
| 预测分析 | AI 预测 | 一键触发三级工作流、实时流式输出、6 张 AI 图表 |
| 工单管理 | 维护工单 | 工单列表、状态流转、优先级管理 |
| 运行日志 | 日志记录 | 历史传感器数据、时序趋势图 |
| 报告导出 | 报告 | Coze Bot 生成 Markdown 报告，支持下载 |
| 案例知识库 | 案例 | 导入本地案例 TXT、同步至 Coze 知识库 |
| AI 助手 | 右下角悬浮按钮 | 流式对话，基于当前设备上下文回答 |

---

## Coze 工作流说明

系统对接以下 Coze 资源（Token 已内置，可直接测试）：

| 工作流 | Workflow ID | 用途 |
|--------|-------------|------|
| WF_analyze | 7640415534379728905 | 故障诊断、RUL 预测、健康评分 |
| WF3 | 7640555138759082027 | 智能维护工单生成 |
| generate_visualization_charts2 | 7648909318586646537 | 生成 6 张 AI 分析图表 |
| 智维先知 Bot | 7633767240911388714 | 报告生成 + AI 对话 |

---

## 技术架构

```
前端 (index.html)
  └─ shadcn/ui 风格 · 原生 HTML/CSS/JS · 无需框架
       ↓ fetch / SSE 流式
后端 (api.py / Flask)
  ├─ /api/devices        设备数据 API
  ├─ /api/workflow/*     Coze 工作流代理（SSE 透传）
  ├─ /api/report/export  报告生成（Coze Bot）
  ├─ /api/chat/stream    流式 AI 对话
  └─ /api/image-proxy    图片跨域代理
       ↓ HTTP / SSE
Coze 平台
  ├─ WF_analyze → WF3 → 绘图工作流（三级闭环）
  └─ 智维先知 Bot（报告 + 对话）
       ↓
SQLite (devices.db)
  └─ devices / device_logs / machine_tests
     predictions / work_orders / workflow_snapshots / fault_cases
```

---

## 注意事项

1. **API Token 有效期**：内置 Token 为测试用，若遇鉴权错误请联系参赛方更新
2. **首次启动**：会从 CSV 重建数据库（约 3 秒），之后启动会跳过重建
3. **图表展示**：前端优先读取 Coze 绘图工作流返回的 ECharts JSON；若只返回图片或工作流不可用，则用 WF_analyze / 本地 CSV 数据在浏览器端绘制 6 张交互图表
4. **离线模式**：即使 Coze 调用失败，本地风险评分和工单生成仍可正常工作

---

## English Overview

**Zhiwei Xianzhi** is a CNC predictive-maintenance demo for the Miaopu Cup final. It combines local CSV / SQLite data, local risk scoring, Coze workflow proxies, work-order generation, report export, and a browser-side visualization workspace.

### Key Capabilities

| Capability | Description |
|------|------|
| Real-time risk scoring | Local CSV data plus fleet percentile baselines provide immediate device risk signals |
| Coze workflow loop | WF_analyze for diagnosis, WF3 for work orders, and a chart workflow for visualization data |
| Local ECharts rendering | The frontend reads returned ECharts specs when available, otherwise renders six charts from WF_analyze / local CSV data in the browser |
| AI reports and chat | Coze Bot powered report generation and streaming maintenance assistant |
| Fault case library | Local fault case import, storage, and optional sync to the Coze knowledge workflow |

### Quick Start

1. Install dependencies:

```bash
pip install flask flask-cors requests
```

2. Start the backend:

```bash
python api.py
```

3. Open the app:

[http://localhost:5000](http://localhost:5000)

### Architecture

```text
Frontend (index.html)
  - shadcn-inspired native HTML/CSS/JS
  - Chart.js for basic dashboard charts
  - ECharts SVG renderer for local workflow visualizations
  - fetch / SSE for workflow streaming

Backend (api.py / Flask)
  - /api/devices
  - /api/workflow/*
  - /api/report/export
  - /api/chat/stream
  - /api/image-proxy

Coze
  - WF_analyze
  - WF3
  - generate_visualization_charts
  - Zhiwei Xianzhi Bot

SQLite
  - devices
  - device_logs
  - machine_tests
  - predictions
  - work_orders
  - workflow_snapshots
  - fault_cases
```

### Notes

1. The bundled API token is for test/demo use only.
2. On first startup, the database can be rebuilt from CSV data.
3. The chart area no longer depends on six reserved image placeholders. It renders interactive local ECharts views from returned workflow specs or local fallback data.
4. Local risk scoring and local chart rendering remain available even when Coze calls fail.

---

*项目版本：决赛提交版 · 2026-06-09*
