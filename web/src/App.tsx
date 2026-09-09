/** 应用外壳：侧边导航 + 顶栏 + 页面路由。

页面实现拆分在 `pages/`（fleet / agents / knowledge / chat），
通用原子组件在 `components/ui.tsx`——本文件只负责导航与全局数据流，
尺寸锁定样式见 styles.css 的 `.app-shell` / `.content`。
 */

import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { AgentMonitorPage, DiagnosticsPage } from "./pages/agents";
import { ChatPage, LogsPage, ReportsPage } from "./pages/chat";
import { MachinesPage, OverviewPage } from "./pages/fleet";
import { KnowledgePage, OperationsPage, TopologyPage } from "./pages/knowledge";
import type { Machine, Overview } from "./types";

type Page = "overview" | "machines" | "diagnostics" | "agents" | "chat" | "logs" | "reports" | "topology" | "knowledge" | "operations";

const navigation: Array<{ id: Page; label: string; hint: string; badge?: boolean }> = [
  { id: "overview", label: "总览", hint: "Fleet" },
  { id: "machines", label: "设备", hint: "Assets" },
  { id: "diagnostics", label: "诊断中心", hint: "Agents" },
  { id: "chat", label: "智能问答", hint: "Chat" },
  { id: "reports", label: "维护报告", hint: "Reports" },
  { id: "agents", label: "Agent 监控", hint: "Autonomy", badge: true },
  { id: "logs", label: "运行日志", hint: "Logs" },
  { id: "topology", label: "关联图谱", hint: "Graph" },
  { id: "knowledge", label: "知识库", hint: "FMECA" },
  { id: "operations", label: "运维工单", hint: "Ops" }
];

function App(): JSX.Element {
  const [page, setPage] = useState<Page>("overview");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [machines, setMachines] = useState<Machine[]>([]);
  const [error, setError] = useState<string>("");
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [pendingCount, setPendingCount] = useState(0);

  const refresh = useCallback(async () => {
    try {
      const [nextOverview, nextMachines] = await Promise.all([api.overview(), api.machines()]);
      setOverview(nextOverview); setMachines(nextMachines); setUpdatedAt(new Date()); setError("");
    } catch (reason: unknown) { setError(reason instanceof Error ? reason.message : "无法连接后端服务"); }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    const socket = new WebSocket("ws://127.0.0.1:8000/ws/realtime");
    socket.onopen = () => socket.send(JSON.stringify({ action: "snapshot" }));
    socket.onmessage = () => { void refresh(); };
    return () => socket.close();
  }, [refresh]);
  // 侧边栏待审徽标：让"需要我处理的事"在任意页面都可见
  useEffect(() => {
    const poll = setInterval(() => { void api.notifications("pending").then((items) => setPendingCount(items.length)).catch(() => undefined); }, 10000);
    void api.notifications("pending").then((items) => setPendingCount(items.length)).catch(() => undefined);
    return () => clearInterval(poll);
  }, []);

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark">智</span><div><strong>智维先知</strong><small>可靠性中枢 / v2.1</small></div></div>
      <nav>{navigation.map((item) => <button key={item.id} className={`nav-item ${page === item.id ? "active" : ""}`} onClick={() => setPage(item.id)}>
        <span>{item.label}{item.badge && pendingCount > 0 ? <em className="nav-badge">{pendingCount}</em> : null}</span>
        <small>{item.hint}</small>
      </button>)}</nav>
      <div className="sidebar-foot"><span className="live-dot" />实时数据链路<br /><small>SQLite · LangGraph · Ollama</small></div>
    </aside>
    <main className="main">
      <header className="topbar">
        <div><p className="eyebrow">CNC Predictive Maintenance</p><h1>{navigation.find((item) => item.id === page)?.label}</h1></div>
        <div className="top-actions"><span className="updated">{updatedAt ? `更新于 ${updatedAt.toLocaleTimeString("zh-CN")}` : "正在连接"}</span><button className="button ghost" onClick={() => void refresh()}>刷新数据</button></div>
      </header>
      {error && <div className="error-banner">后端连接异常：{error}</div>}
      <section className="content">
        {page === "overview" && <OverviewPage overview={overview} machines={machines} onNavigate={setPage} />}
        {page === "machines" && <MachinesPage machines={machines} />}
        {page === "diagnostics" && <DiagnosticsPage machines={machines} />}
        {page === "agents" && <AgentMonitorPage />}
        {page === "chat" && <ChatPage />}
        {page === "logs" && <LogsPage />}
        {page === "reports" && <ReportsPage />}
        {page === "topology" && <TopologyPage />}
        {page === "knowledge" && <KnowledgePage />}
        {page === "operations" && <OperationsPage machines={machines} />}
      </section>
    </main>
  </div>;
}

export default App;
