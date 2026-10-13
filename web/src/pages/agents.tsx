/** 诊断中心与 Agent 自主监控（需求 1 通知优化 + 需求 8 审批联动展示）。

通知界面改造点：批准后后端自动生成维护报告，通知卡片直接展示
"报告已生成 → 前往报告页查看"的联动结果，不再只返回一行冷冰冰的 JSON。
 */

import { useCallback, useEffect, useState } from "react";
import { api, wsBase } from "../api";
import { AgentTimeline, DecisionPanel, DiagnosticsList, Empty, EvidenceChain, Metric, PanelTitle, RiskBadge } from "../components/ui";
import type { AgentActivity, Diagnostic, DiagnosticPayload, Machine, Notification } from "../types";

export function DiagnosticsPage({ machines }: { machines: Machine[] }): JSX.Element {
  const [machineId, setMachineId] = useState<string>(machines[0]?.id ?? "CNC_001");
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<(DiagnosticPayload & { id: string; thread_id: string }) | null>(null);
  const [history, setHistory] = useState<Diagnostic[]>([]);
  const [error, setError] = useState("");
  const refreshHistory = useCallback(async () => { try { setHistory(await api.diagnostics()); } catch { setHistory([]); } }, []);
  useEffect(() => { void refreshHistory(); }, [refreshHistory]);
  const trigger = async (): Promise<void> => {
    setRunning(true); setError("");
    try { const response = await api.trigger(machineId); setResult(response); await refreshHistory(); }
    catch (reason: unknown) { setError(reason instanceof Error ? reason.message : "诊断执行失败"); }
    finally { setRunning(false); }
  };
  return <>
    <section className="panel diagnostic-launch">
      <div><p className="eyebrow">Event-driven LangGraph workflow</p><h2>启动可追溯诊断</h2>
        <p className="muted">所有诊断必须经过 Monitor、E1–E7、RUL 区间、决策与 5 项 Reflection。</p></div>
      <div className="launch-controls">
        <select value={machineId} onChange={(event) => setMachineId(event.target.value)}>{machines.map((machine) => <option key={machine.id} value={machine.id}>{machine.id} · {machine.production_line}</option>)}</select>
        <button className="button primary" disabled={running || !machineId} onClick={() => void trigger()}>{running ? "正在编排…" : "触发诊断"}</button>
      </div>
    </section>
    {error && <div className="error-banner">{error}</div>}
    {result && <DiagnosticResult payload={result} />}
    <section className="panel"><PanelTitle title="诊断历史" />{history.length ? <DiagnosticsList diagnostics={history} /> : <Empty label="尚无持久化诊断记录。" />}</section>
  </>;
}

export function DiagnosticResult({ payload }: { payload: DiagnosticPayload & { id: string; thread_id: string } }): JSX.Element {
  const decision = payload.decision;
  return <>
    <div className="metric-grid">
      <Metric label="候选故障" value={payload.fault_mode ?? "正常"} note={payload.root_cause ?? "—"} />
      <Metric label="诊断置信度" value={`${((payload.confidence ?? 0) * 100).toFixed(1)}%`} note="规则 + 证据链融合" />
      <Metric label="RUL P10 / P50 / P90" value={`${payload.rul?.rul_p10 ?? "—"} / ${payload.rul?.rul_p50 ?? "—"} / ${payload.rul?.rul_p90 ?? "—"} h`} note="P10 为乐观寿命" />
      <Metric label="Reflection" value={payload.reflection_passed ? "通过" : "需关注"} note="5 项质量门" tone={payload.reflection_passed ? "normal" : "warning"} />
    </div>
    <div className="two-column">
      <section className="panel"><AgentTimeline events={payload.events ?? []} /><PanelTitle title="E1–E7 证据链" /><EvidenceChain evidence={payload.evidence_chain ?? []} /></section>
      <section className="panel">
        <PanelTitle title="维护决策 / What-if" />{decision ? <DecisionPanel decision={decision} /> : <Empty label="当前读数未触发异常，未生成维护决策。" />}
        <PanelTitle title="Reflection 自检" />
        {payload.reflection_checks ? <div className="check-list">{payload.reflection_checks.map((check) => <div key={check.id}><span className={check.passed ? "check pass" : "check fail"}>{check.passed ? "✓" : "×"}</span>{check.label}</div>)}</div> : null}
      </section>
    </div>
  </>;
}

export function AgentMonitorPage(): JSX.Element {
  const [activities, setActivities] = useState<AgentActivity[]>([]);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [loopRunning, setLoopRunning] = useState(false);
  const [lastScanAt, setLastScanAt] = useState<string | null>(null);
  const [lastSummary, setLastSummary] = useState<Record<string, unknown>>({});
  const [filter, setFilter] = useState<"all" | "critical" | "review">("all");
  const [reviewer, setReviewer] = useState("值班工程师");
  const [reviewNotes, setReviewNotes] = useState("");
  const [busConnected, setBusConnected] = useState(false);
  const [scanMessage, setScanMessage] = useState("");
  const [reviewedIds, setReviewedIds] = useState<string[]>([]);

  const refresh = useCallback(async () => {
    try {
      const [activity, pending] = await Promise.all([api.agentActivity(300), api.notifications("pending")]);
      setActivities(activity.items);
      setLoopRunning(activity.loop_running);
      setLastScanAt(activity.last_scan_at);
      setLastSummary(activity.last_summary ?? {});
      setNotifications(pending);
    } catch { /* backend may be down; keep prior state */ }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => { const id = setInterval(() => void refresh(), 8000); return () => clearInterval(id); }, [refresh]);

  useEffect(() => {
    // WS 地址随 VITE_API_BASE 派生；登录态就绪后附带 token（B4）
    const token = localStorage.getItem("zhiwei_token");
    const socket = new WebSocket(`${wsBase()}/ws/agent${token ? `?token=${encodeURIComponent(token)}` : ""}`);
    socket.onopen = () => setBusConnected(true);
    socket.onclose = () => setBusConnected(false);
    socket.onerror = () => setBusConnected(false);
    socket.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data) as { type: string; event?: AgentActivity; recent?: AgentActivity[]; loop_running?: boolean; last_scan_at?: string; last_summary?: Record<string, unknown> };
        if (payload.type === "snapshot") {
          if (payload.recent) setActivities(payload.recent);
          if (typeof payload.loop_running === "boolean") setLoopRunning(payload.loop_running);
          if (payload.last_scan_at) setLastScanAt(payload.last_scan_at);
          if (payload.last_summary) setLastSummary(payload.last_summary);
        } else if (payload.type === "activity" && payload.event) {
          setActivities((current) => [payload.event as AgentActivity, ...current].slice(0, 300));
          if ((payload.event as AgentActivity).severity === "critical" || (payload.event as AgentActivity).requires_human_review) {
            void api.notifications("pending").then(setNotifications).catch(() => undefined);
          }
        }
      } catch { /* ignore malformed frame */ }
    };
    return () => { socket.close(); };
  }, []);

  const forceScan = async (): Promise<void> => {
    setScanMessage("正在触发扫描…");
    try {
      const summary = await api.triggerScan() as { scanned: number; critical: number; warning: number; auto_triggered: string[] };
      setScanMessage(`扫描完成：${summary.scanned} 台，发现 ${summary.critical} 紧急 / ${summary.warning} 警告，自动触发 ${summary.auto_triggered.length} 次诊断`);
      await refresh();
    } catch (reason) { setScanMessage(reason instanceof Error ? reason.message : "扫描失败"); }
    setTimeout(() => setScanMessage(""), 5000);
  };

  const review = async (item: Notification, action: "approve" | "dismiss"): Promise<void> => {
    try {
      const response = await api.reviewNotification(item.id, action, reviewer, reviewNotes) as Notification & { report?: { report_id?: string; error?: string } | null };
      setReviewNotes("");
      setReviewedIds((current) => [...current, item.id]);
      if (action === "approve" && response.report) {
        setScanMessage(response.report.report_id
          ? `✅ 维护报告已自动生成（${response.report.report_id}），可在「维护报告」页查看`
          : `⚠️ 报告生成失败：${response.report.error ?? "未知错误"}`);
      }
      await refresh();
    } catch (reason) { setScanMessage(reason instanceof Error ? reason.message : "审核失败"); }
    setTimeout(() => setScanMessage(""), 8000);
  };

  const filteredActivities = activities.filter((item) => {
    if (filter === "critical") return item.severity === "critical" || item.severity === "error";
    if (filter === "review") return item.requires_human_review;
    return true;
  });
  const summaryTyped = lastSummary as { scanned?: number; healthy?: number; warning?: number; critical?: number; offline?: number; auto_triggered?: string[]; duration_ms?: number };

  return <>
    <section className="panel agent-status">
      <div className="agent-status-head">
        <div><p className="eyebrow">Autonomous agent</p><h2>Agent 自主监控中心</h2>
          <p className="muted">Agent 自主循环扫描 — 异常自动诊断、自动派单，关键事件通知你审核；高风险告警获批后自动生成维护报告。</p></div>
        <div className="agent-status-actions">
          <span className={`loop-pill ${loopRunning ? "running" : "stopped"}`}><span className="loop-dot" />{loopRunning ? "循环运行中" : "循环已停止"}</span>
          <span className={`bus-pill ${busConnected ? "connected" : "disconnected"}`}><span className="bus-dot" />实时通道 {busConnected ? "已连接" : "已断开"}</span>
          <button className="button primary" onClick={() => void forceScan()}>立即扫描一次</button>
        </div>
      </div>
      {scanMessage && <div className="notice-banner">{scanMessage}</div>}
      <div className="metric-grid">
        <Metric label="待审通知" value={String(notifications.length)} note="critical 级风险等待你确认" tone={notifications.length > 0 ? "critical" : "normal"} />
        <Metric label="最近扫描" value={summaryTyped.scanned ? `${summaryTyped.scanned} 台` : "—"} note={lastScanAt ? `${new Date(lastScanAt).toLocaleTimeString("zh-CN")} · ${summaryTyped.duration_ms ?? 0}ms` : "尚未完成首次扫描"} tone="default" />
        <Metric label="自动触发诊断" value={String(summaryTyped.auto_triggered?.length ?? 0)} note="本轮扫描中 Agent 自动启动的诊断" tone="warning" />
        <Metric label="健康 / 警告 / 紧急" value={`${summaryTyped.healthy ?? 0} / ${summaryTyped.warning ?? 0} / ${summaryTyped.critical ?? 0}`} note="最近一轮扫描结果" tone={summaryTyped.critical ? "critical" : (summaryTyped.warning ? "warning" : "normal")} />
      </div>
    </section>
    <div className="two-column">
      <section className="panel">
        <div className="panel-title">
          <h3>Agent 活动实时流</h3>
          <div className="filter-pills">
            <button className={filter === "all" ? "selected" : ""} onClick={() => setFilter("all")}>全部 {activities.length}</button>
            <button className={filter === "critical" ? "selected" : ""} onClick={() => setFilter("critical")}>紧急 {activities.filter((a) => a.severity === "critical" || a.severity === "error").length}</button>
            <button className={filter === "review" ? "selected" : ""} onClick={() => setFilter("review")}>需审核 {activities.filter((a) => a.requires_human_review).length}</button>
          </div>
        </div>
        <div className="agent-stream">
          {filteredActivities.length === 0 && <Empty label={loopRunning ? "Agent 已启动，等待首次扫描结果…" : "Agent 循环未启动"} />}
          {filteredActivities.map((item) => (
            <article key={`${item.id}-${item.timestamp}`} className={`agent-event ${item.severity} ${item.requires_human_review ? "needs-review" : ""}`}>
              <div className="agent-event-time">{new Date(item.timestamp).toLocaleTimeString("zh-CN")}</div>
              <div className="agent-event-body">
                <header>
                  <span className={`agent-tag severity-${item.severity}`}>{item.severity}</span>
                  <strong>{item.agent_name}</strong>
                  <span className="agent-action">{item.action}</span>
                  {item.machine_id && <em className="agent-machine">{item.machine_id}</em>}
                  {item.requires_human_review && <em className="review-tag">需审核</em>}
                </header>
                <p>{item.reason}</p>
                {Object.keys(item.details).length > 0 && <pre className="agent-details">{JSON.stringify(item.details, null, 2)}</pre>}
              </div>
            </article>
          ))}
        </div>
      </section>
      <section className="panel">
        <PanelTitle title={`待审通知 (${notifications.length})`} />
        {notifications.length === 0 && <Empty label={reviewedIds.length ? "刚才的通知已处理完毕。" : "暂无待审通知。Agent 只在紧急情况下通知你。"} />}
        <div className="review-box">
          <input value={reviewer} onChange={(e) => setReviewer(e.target.value)} placeholder="审核人姓名" />
          <textarea value={reviewNotes} onChange={(e) => setReviewNotes(e.target.value)} placeholder="审核备注（可选）" />
        </div>
        {notifications.map((item) => (
          <article key={item.id} className={`notification ${item.severity}`}>
            <header>
              <span className={`agent-tag severity-${item.severity}`}>{item.severity}</span>
              <strong>{item.title}</strong>
              <time>{new Date(item.created_at).toLocaleString("zh-CN")}</time>
            </header>
            <p>{item.message}</p>
            <p className="notification-machine">{item.machine_id && <RiskBadge severity={item.severity} />} {item.machine_id ?? ""}</p>
            <div className="notification-actions">
              <button className="button primary" onClick={() => void review(item, "approve")}>批准并生成报告</button>
              <button className="button ghost" onClick={() => void review(item, "dismiss")}>驳回</button>
            </div>
          </article>
        ))}
      </section>
    </div>
  </>;
}
