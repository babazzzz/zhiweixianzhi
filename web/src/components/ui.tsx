/** 通用 UI 原子组件（shadcn 风格：中性色 / 1px 边框 / radius 10px）。 */

import type { ReactNode } from "react";
import type { AgentEvent, CaseItem, Decision, Diagnostic, Evidence, Rule, WorkOrder } from "../types";

export function Metric({ label, value, note, tone = "default", chart }: { label: string; value: string; note: string; tone?: "default" | "normal" | "warning" | "critical"; chart?: ReactNode }): JSX.Element {
  return <article className={`metric-card ${tone}`}><span>{label}</span><strong>{value}</strong><small>{note}</small>{chart}</article>;
}

export function PanelTitle({ title, action, onAction }: { title: string; action?: string; onAction?: () => void }): JSX.Element {
  return <div className="panel-title"><h3>{title}</h3>{action && <button className="text-button" onClick={onAction}>{action} →</button>}</div>;
}

export function Empty({ label }: { label: string }): JSX.Element {
  return <div className="empty">{label}</div>;
}

export function Loading({ label }: { label: string }): JSX.Element {
  return <div className="loading"><span className="spinner" />{label}</div>;
}

export function formatPercent(value: number): string { return `${(value * 100).toFixed(1)}%`; }

export function EvidenceChain({ evidence }: { evidence: Evidence[] }): JSX.Element {
  return <div className="evidence-list">{evidence.map((item) => <article key={item.id}><span>{item.id}</span><div><strong>{item.source}</strong><p>{item.content}</p></div><em>{(item.confidence * 100).toFixed(0)}%</em></article>)}</div>;
}

export function AgentTimeline({ events }: { events: AgentEvent[] }): JSX.Element {
  return <div className="agent-timeline">{events.map((event, index) => <div key={`${event.agent}-${index}`}><span className="timeline-node">{index + 1}</span><div><strong>{event.agent}</strong><p>{event.message}</p><small>{event.status} · {new Date(event.timestamp).toLocaleTimeString("zh-CN")}</small></div></div>)}</div>;
}

export function DecisionPanel({ decision }: { decision: Decision }): JSX.Element {
  return <div><div className="decision-summary"><strong>{decision.recommendation}</strong><p>{decision.timing} · 预计停机 {decision.downtime_hours}h · 净收益 ¥{decision.net_benefit.toLocaleString()}</p></div><div className="decision-options">{decision.options.map((option) => <div key={option.name}><strong>{option.name}</strong><span>总成本 ¥{option.total_cost.toLocaleString()}</span><small>直接 ¥{option.direct_cost.toLocaleString()} · 风险 ¥{option.risk_cost.toLocaleString()}</small></div>)}</div><div className="what-if"><strong>What-if</strong><span>减载 {decision.what_if.derated_pct}% → RUL +{decision.what_if.rul_extension_hours}h</span><span>72h 故障概率 {formatPercent(decision.what_if.failure_probability_72h)}</span></div>{decision.parts.length > 0 && <p className="parts"><b>备件：</b>{decision.parts.join("、")}</p>}</div>;
}

export function DiagnosticsList({ diagnostics }: { diagnostics: Diagnostic[] }): JSX.Element {
  return <div className="compact-table">{diagnostics.map((item) => <div className="table-row" key={item.id}><span className={`status-dot ${item.severity}`} /><div><strong>{item.machine_id}</strong><small>{item.payload.fault_mode ?? "未触发故障"}</small></div><span>{(item.confidence * 100).toFixed(0)}%</span><time>{new Date(item.created_at).toLocaleString("zh-CN")}</time></div>)}</div>;
}

export function OrdersList({ orders }: { orders: WorkOrder[] }): JSX.Element {
  return <div className="compact-table">{orders.map((item) => <div className="table-row order-row" key={item.id}><span className="order-id">{item.id}</span><div><strong>{item.machine_id}</strong><small>{item.maintenance_type} · {item.technician}</small></div><span className="tag">{item.status}</span><time>{new Date(item.start_time).toLocaleString("zh-CN")}</time></div>)}</div>;
}

export function RulesTable({ rules }: { rules: Rule[] }): JSX.Element {
  return <div className="rules-list">{rules.map((rule) => <details key={rule.fault_code}><summary><span>F-{String(rule.fault_code).padStart(2, "0")}</span><strong>{rule.fault_name}</strong><small>{rule.system} / {rule.component}</small><i className={`tag ${rule.severity}`}>{rule.severity}</i></summary><div className="rule-body"><p><b>机理：</b>{rule.physical_mechanism}</p><p><b>检测：</b>{rule.detection_method}</p><p><b>SOP：</b>{rule.sop_steps.join(" → ")}</p><p><b>备件：</b>{rule.required_parts.join("、") || "按现场检查结果确定"}</p></div></details>)}</div>;
}

export function CasesList({ cases }: { cases: CaseItem[] }): JSX.Element {
  return <div className="cases-list">{cases.map((item) => <article key={item.id}><span>{item.id}</span><strong>{item.machine_id} · 故障码 {item.fault_code}</strong><p>{item.description}</p><small>根因：{item.root_cause}；处置：{item.resolution}</small></article>)}</div>;
}

/** 风险徽章：让"故障/高风险设备一眼可见"（需求 1）。 */
export function RiskBadge({ severity }: { severity: string | null | undefined }): JSX.Element {
  const level = severity ?? "normal";
  const label = { critical: "高风险", warning: "警告", emergency: "紧急", normal: "正常", running: "运行中", maintenance: "保养中", offline: "离线" }[level] ?? level;
  return <span className={`risk-badge ${level}`}>{label}</span>;
}

/** 健康条：带阈值的水平进度条，替代干巴巴的百分比文字。 */
export function HealthBar({ score }: { score: number | null | undefined }): JSX.Element {
  const pct = score === null || score === undefined ? null : Math.round(score * 100);
  const tone = pct === null ? "unknown" : pct < 55 ? "critical" : pct < 75 ? "warning" : "good";
  return <span className={`health-bar ${tone}`} title={pct === null ? "无读数" : `健康度 ${pct}%`}><i style={{ width: `${pct ?? 0}%` }} />{pct !== null && <b>{pct}%</b>}</span>;
}
