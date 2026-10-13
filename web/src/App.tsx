/** 应用外壳：侧边导航 + 顶栏 + 页面切换。
 *
 * 页面实现拆分在 `pages/`（fleet / agents / knowledge / chat），
 * 通用原子组件在 `components/ui.tsx`——本文件只负责导航与全局数据流。
 * 主题（浅色/暗色）见 theme.ts；登录态接入见 B4（auth.tsx）。
 */

import { useCallback, useEffect, useState } from "react";
import { Component } from "react";
import type { ReactNode } from "react";
import {
  Activity, BookOpen, Bot, ClipboardList, Cpu, FileText, Gauge, LayoutDashboard, MessageSquare,
  Moon, RefreshCw, Network, ScrollText, Sun, Stethoscope
} from "lucide-react";
import { api, wsBase } from "./api";
import { getTheme, toggleTheme } from "./theme";
import { AgentMonitorPage, DiagnosticsPage } from "./pages/agents";
import { ChatPage, LogsPage, ReportsPage } from "./pages/chat";
import { MachinesPage, OverviewPage } from "./pages/fleet";
import { KnowledgePage, OperationsPage, TopologyPage } from "./pages/knowledge";
import type { Machine, Overview } from "./types";

type Page = "overview" | "machines" | "diagnostics" | "agents" | "chat" | "logs" | "reports" | "topology" | "knowledge" | "operations";

const navigation: Array<{ id: Page; label: string; hint: string; icon: typeof Gauge; badge?: boolean }> = [
  { id: "overview", label: "总览", hint: "Fleet", icon: LayoutDashboard },
  { id: "machines", label: "设备", hint: "Assets", icon: Cpu },
  { id: "diagnostics", label: "诊断中心", hint: "Agents", icon: Stethoscope },
  { id: "chat", label: "智能问答", hint: "Chat", icon: MessageSquare },
  { id: "reports", label: "维护报告", hint: "Reports", icon: FileText },
  { id: "agents", label: "Agent 监控", hint: "Autonomy", icon: Bot, badge: true },
  { id: "logs", label: "运行日志", hint: "Logs", icon: ScrollText },
  { id: "topology", label: "关联图谱", hint: "Graph", icon: Network },
  { id: "knowledge", label: "知识库", hint: "FMECA", icon: BookOpen },
  { id: "operations", label: "运维工单", hint: "Ops", icon: ClipboardList }
];

/** 页面级错误边界：单页异常不再整屏白屏。 */
class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  constructor(props: { children: ReactNode }) {
    super(props);
    this.state = { error: null };
  }
  static getDerivedStateFromError(error: Error): { error: Error } {
    return { error };
  }
  render(): ReactNode {
    if (this.state.error) {
      return <div className="error-boundary">
        <h2>页面渲染出错</h2>
        <p>该模块遇到异常，其他页面仍可正常使用。可尝试刷新数据。</p>
        <pre>{this.state.error.message}</pre>
        <button className="button" onClick={() => this.setState({ error: null })}>重试</button>
      </div>;
    }
    return this.props.children;
  }
}

function App(): JSX.Element {
  const [page, setPage] = useState<Page>("overview");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [machines, setMachines] = useState<Machine[]>([]);
  const [error, setError] = useState<string>("");
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [pendingCount, setPendingCount] = useState(0);
  const [dark, setDark] = useState<boolean>(() => getTheme() === "dark");

  const refresh = useCallback(async () => {
    try {
      const [nextOverview, nextMachines] = await Promise.all([api.overview(), api.machines()]);
      setOverview(nextOverview); setMachines(nextMachines); setUpdatedAt(new Date()); setError("");
    } catch (reason: unknown) { setError(reason instanceof Error ? reason.message : "无法连接后端服务"); }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    // WS 地址随 VITE_API_BASE 派生；登录态就绪后附带 token（B4）
    const token = localStorage.getItem("zhiwei_token");
    const socket = new WebSocket(`${wsBase()}/ws/realtime${token ? `?token=${encodeURIComponent(token)}` : ""}`);
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

  const active = navigation.find((item) => item.id === page);

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark">智</span><div><strong>智维先知</strong><small>可靠性中枢 / v2.1</small></div></div>
      <nav>{navigation.map((item) => {
        const Icon = item.icon;
        return <button key={item.id} className={`nav-item ${page === item.id ? "active" : ""}`} onClick={() => setPage(item.id)} title={`${item.label} · ${item.hint}`}>
          <Icon size={15} strokeWidth={1.75} />
          <span className="nav-text"><span>{item.label}{item.badge && pendingCount > 0 ? <em className="nav-badge">{pendingCount}</em> : null}</span><small>{item.hint}</small></span>
        </button>;
      })}</nav>
      <div className="sidebar-foot"><span className="live-dot" />实时数据链路<br /><small>SQLite · LangGraph · Ollama</small></div>
    </aside>
    <main className="main">
      <header className="topbar">
        <div><p className="eyebrow">CNC Predictive Maintenance</p><h1>{active?.label}</h1></div>
        <div className="top-actions">
          <span className="updated"><Activity size={11} style={{ verticalAlign: "-1px", marginRight: 4 }} />{updatedAt ? `更新于 ${updatedAt.toLocaleTimeString("zh-CN")}` : "正在连接"}</span>
          <button className="theme-toggle" title={dark ? "切换到浅色" : "切换到暗色"} onClick={() => setDark(toggleTheme() === "dark")}>
            {dark ? <Sun size={15} /> : <Moon size={15} />}
          </button>
          <button className="button ghost" onClick={() => void refresh()}><RefreshCw size={13} />刷新数据</button>
        </div>
      </header>
      {error && <div className="error-banner">后端连接异常：{error}</div>}
      <section className="content">
        <ErrorBoundary key={page}>
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
        </ErrorBoundary>
      </section>
    </main>
  </div>;
}

export default App;
