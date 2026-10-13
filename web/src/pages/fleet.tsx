/** 总览与设备页（需求 1：总览美化 + 高风险一眼可见 + 面板锁定尺寸）。

总览改造点：
* 设备矩阵按风险优先排序（critical → warning → 其余按健康分升序），
  前 12 台是"最需要看的设备"，不再按编号平铺；
* 每格带 RiskBadge + HealthBar，绿黄红一眼分清；
* 高风险队列同屏并置，点击直达诊断。
 */

import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { TrendChart } from "../TrendChart";
import { DiagnosticsList, Empty, HealthBar, Loading, Metric, OrdersList, PanelTitle, RiskBadge } from "../components/ui";
import type { Diagnostic, DiagnosticPayload, Machine, Overview, WorkOrder } from "../types";

type Page = "overview" | "machines" | "diagnostics" | "agents" | "chat" | "logs" | "reports" | "topology" | "knowledge" | "operations";

const RISK_RANK: Record<string, number> = { critical: 0, emergency: 0, warning: 1, maintenance: 2, offline: 3, normal: 4, running: 4 };
const RISKY = ["critical", "emergency", "warning"];

function riskOf(machine: Machine): string {
  return machine.live_severity ?? machine.latest_diagnostic?.severity ?? machine.status ?? "normal";
}

function isRisky(machine: Machine): boolean {
  return RISKY.includes(riskOf(machine));
}

export function OverviewPage({ overview, machines, onNavigate }: { overview: Overview | null; machines: Machine[]; onNavigate: (page: Page) => void }): JSX.Element {
  const [line, setLine] = useState<string>("all");
  const [onlyRisk, setOnlyRisk] = useState(false);
  if (!overview) return <Loading label="正在读取真实设备数据" />;
  const fleet = overview.fleet;
  // 风险优先：最需要关注的设备排最前，一眼看到红色
  const sorted = [...machines].sort((a, b) => {
    const rankA = RISK_RANK[riskOf(a)] ?? 4;
    const rankB = RISK_RANK[riskOf(b)] ?? 4;
    if (rankA !== rankB) return rankA - rankB;
    return (a.live_health_score ?? 1) - (b.live_health_score ?? 1);
  });
  const risky = sorted.filter(isRisky);
  // 产线维度：来自设备主数据的 production_line 字段（100 台全覆盖）
  const lines = Array.from(new Set(machines.map((machine) => machine.production_line))).sort();
  const lineStats = lines.map((name) => {
    const members = machines.filter((machine) => machine.production_line === name);
    const scored = members.filter((machine) => machine.live_health_score != null);
    const average = scored.length ? scored.reduce((sum, machine) => sum + (machine.live_health_score ?? 0), 0) / scored.length : 0;
    return { name, total: members.length, risky: members.filter(isRisky).length, average };
  });
  const scoped = sorted.filter((machine) => (line === "all" || machine.production_line === line) && (!onlyRisk || isRisky(machine)));

  return <>
    <div className="metric-grid">
      <Metric label="在线设备" value={`${fleet.total - fleet.offline} / ${fleet.total}`} note="已纳入监测链路" />
      <Metric label="平均健康度" value={`${(fleet.average_health * 100).toFixed(1)}%`} note="动态基线计算" tone={fleet.average_health < 0.65 ? "warning" : "normal"} />
      <Metric label="高风险设备" value={String(fleet.critical)} note="需优先评审" tone="critical" />
      <Metric label="未处置告警" value={String(fleet.warning + fleet.critical)} note="来自实时异常检测" tone="warning" />
    </div>

    <section className="panel matrix-panel">
      <div className="panel-title">
        <h3>设备健康矩阵 · 风险优先（{scoped.length} / {machines.length} 台）</h3>
        <button className="text-button" onClick={() => onNavigate("machines")}>查看设备 →</button>
      </div>
      <div className="matrix-toolbar">
        <div className="filter-pills">
          <button className={line === "all" ? "selected" : ""} onClick={() => setLine("all")}>全部产线 {machines.length}</button>
          {lineStats.map((item) => <button key={item.name} className={line === item.name ? "selected" : ""} onClick={() => setLine(item.name)}>
            {item.name} {item.total}{item.risky > 0 ? ` · ${item.risky} 风险` : ""}
          </button>)}
        </div>
        <label className="matrix-toggle"><input type="checkbox" checked={onlyRisk} onChange={(event) => setOnlyRisk(event.target.checked)} />只看风险设备</label>
      </div>
      {line !== "all" && <p className="muted matrix-hint">
        {lineStats.find((item) => item.name === line)?.total} 台设备 · 高风险 {lineStats.find((item) => item.name === line)?.risky} 台 ·
        平均健康度 {((lineStats.find((item) => item.name === line)?.average ?? 0) * 100).toFixed(1)}%
      </p>}
      <div className="machine-grid">
        {scoped.map((machine) => {
          const risk = riskOf(machine);
          return <button className={`machine-cell ${isRisky(machine) ? "risky" : ""}`} key={machine.id}
            title={`${machine.id} · ${machine.production_line} · ${risk}`} onClick={() => onNavigate("machines")}>
            <header><b>{machine.id.replace("CNC_", "#")}</b><RiskBadge severity={risk} /></header>
            <HealthBar score={machine.live_health_score ?? machine.latest_reading?.health_score ?? null} />
            <small className="machine-line">{machine.production_line}</small>
          </button>;
        })}
        {scoped.length === 0 && <Empty label="该筛选条件下没有设备。" />}
      </div>
    </section>

    <div className="two-column">
      <section className="panel">
        <PanelTitle title={`实时风险队列 (${risky.length})`} action="进入诊断中心" onAction={() => onNavigate("diagnostics")} />
        {risky.length
          ? <div className="queue">{risky.slice(0, 10).map((machine) => <div className="queue-row" key={machine.id}>
              <span className={`status-dot ${riskOf(machine)}`} />
              <div><strong>{machine.id}<small className="queue-line"> · {machine.production_line}</small></strong><p><RiskBadge severity={riskOf(machine)} />{machine.live_health_score != null && ` 健康度 ${(machine.live_health_score * 100).toFixed(1)}%`}{machine.latest_diagnostic && ` · 置信度 ${(machine.latest_diagnostic.confidence * 100).toFixed(0)}%`}</p></div>
              <button className="text-button" onClick={() => onNavigate("diagnostics")}>诊断</button>
            </div>)}</div>
          : <Empty label="暂无异常设备；系统仍持续监控。" />}
      </section>
      <section className="panel">
        <PanelTitle title="最近诊断" action="全部记录" onAction={() => onNavigate("diagnostics")} />
        {overview.diagnostics.length ? <DiagnosticsList diagnostics={overview.diagnostics.slice(0, 6)} /> : <Empty label="尚未触发诊断。" />}
      </section>
    </div>
    <section className="panel">
      <PanelTitle title="运维工单" action="工单中心" onAction={() => onNavigate("operations")} />
      {overview.orders.length ? <OrdersList orders={overview.orders.slice(0, 6)} /> : <Empty label="尚无人工确认的维护工单。" />}
    </section>
  </>;
}

export function MachinesPage({ machines }: { machines: Machine[] }): JSX.Element {
  const [selectedId, setSelectedId] = useState<string>(machines[0]?.id ?? "CNC_001");
  const [detail, setDetail] = useState<{ machine: Machine; health: DiagnosticPayload | null; history: Array<{ timestamp: string; metrics: Record<string, number>; fault_code: number; health_score: number | null }>; diagnostics: Diagnostic[]; orders: WorkOrder[] } | null>(null);
  const load = useCallback(async () => { try { setDetail(await api.machine(selectedId)); } catch { setDetail(null); } }, [selectedId]);
  useEffect(() => { void load(); }, [load]);
  return <div className="split-layout">
    <section className="asset-list panel"><PanelTitle title={`设备清单 (${machines.length})`} />
      {machines.map((machine) => <button className={`asset-row ${machine.id === selectedId ? "selected" : ""}`} key={machine.id} onClick={() => setSelectedId(machine.id)}>
        <span className={`status-dot ${machine.live_severity ?? machine.status}`} />
        <div><strong>{machine.id}</strong><small>{machine.production_line} · {machine.model}</small></div>
        <em>{machine.live_health_score === null || machine.live_health_score === undefined ? "—" : `${(machine.live_health_score * 100).toFixed(0)}%`}</em>
      </button>)}
    </section>
    <section className="asset-detail">{detail ? <MachineDetail detail={detail} onRefresh={load} /> : <Loading label="读取设备实时与历史数据" />}</section>
  </div>;
}

function MachineDetail({ detail, onRefresh }: { detail: { machine: Machine; health: DiagnosticPayload | null; history: Array<{ timestamp: string; metrics: Record<string, number>; fault_code: number; health_score: number | null }>; diagnostics: Diagnostic[]; orders: WorkOrder[] }; onRefresh: () => Promise<void> }): JSX.Element {
  const [activeMetrics, setActiveMetrics] = useState<string[]>(["op_temperature", "op_amperage", "vibration_rms"]);
  const allMetrics = ["op_temperature", "op_amperage", "op_voltage", "rotor_speed", "vibration_rms", "motor_power", "cutting_force", "feed_force", "spindle_load", "coolant_temp", "coolant_pressure", "oil_pressure", "noise_level", "tool_wear", "surface_roughness", "position_error", "power_factor", "harmonic_distortion", "bearing_temp", "motor_vibration", "ambient_temp", "humidity"];
  const metrics = allMetrics.filter((metric) => detail.history.some((item) => metric in item.metrics));
  return <>
    <section className="panel detail-heading">
      <div><p className="eyebrow">{detail.machine.production_line} · {detail.machine.model}</p><h2>{detail.machine.id}</h2>
        <p className="muted">制造 {detail.machine.manufacture_date} · 日产 {detail.machine.daily_output.toLocaleString()} 件 · 单位生产成本 ¥{detail.machine.unit_cost}</p></div>
      <div className="health-pill"><span className={`status-dot ${detail.health?.severity ?? detail.machine.status}`} />{detail.health?.health_score !== undefined ? `${(detail.health.health_score * 100).toFixed(1)}% 健康度` : "无实时数据"}</div>
    </section>
    <section className="panel">
      <PanelTitle title="传感器趋势（真实历史窗口）" action="刷新" onAction={() => void onRefresh()} />
      <div className="metric-selector">{metrics.map((metric) => <label key={metric}><input type="checkbox" checked={activeMetrics.includes(metric)} onChange={() => setActiveMetrics((current) => current.includes(metric) ? current.filter((item) => item !== metric) : [...current, metric])} />{metric}</label>)}</div>
      <TrendChart readings={detail.history} metrics={activeMetrics} />
    </section>
    <div className="two-column">
      <section className="panel"><PanelTitle title="最新诊断与 E1–E7" />{detail.diagnostics[0] ? <EvidenceLite diagnostic={detail.diagnostics[0]} /> : <Empty label="尚未运行诊断。请在诊断中心触发。" />}</section>
      <section className="panel"><PanelTitle title="维护记录" />{detail.orders.length ? <OrdersList orders={detail.orders} /> : <Empty label="当前无维护工单。" />}</section>
    </div>
  </>;
}

function EvidenceLite({ diagnostic }: { diagnostic: Diagnostic }): JSX.Element {
  const evidence = diagnostic.payload.evidence_chain ?? [];
  if (!evidence.length) return <Empty label="该诊断未生成证据链。" />;
  return <div className="evidence-list">{evidence.map((item) => <article key={item.id}><span>{item.id}</span><div><strong>{item.source}</strong><p>{item.content}</p></div><em>{(item.confidence * 100).toFixed(0)}%</em></article>)}</div>;
}
