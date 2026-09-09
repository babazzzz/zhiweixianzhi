/** 关联图谱 / 知识库（需求 6：混合检索结果可视化）/ 运维工单。 */

import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { CasesList, Empty, PanelTitle, RulesTable } from "../components/ui";
import type { CaseItem, Rule, Topology, TopologyEdge, TopologyNode } from "../types";

export function TopologyPage(): JSX.Element {
  const [topology, setTopology] = useState<Topology | null>(null);
  const [mode, setMode] = useState<"production" | "causal">("production");
  useEffect(() => { void api.topology().then(setTopology).catch(() => setTopology(null)); }, []);
  if (!topology) return <Loading label="加载设备关联图谱" />;
  const nodes = topology.nodes.filter((node) => mode === "production" ? node.type === "machine" || node.type === "line" : node.type.startsWith("fault_"));
  const allowed = new Set(nodes.map((node) => node.id));
  const edges = topology.edges.filter((edge) => allowed.has(edge.source) && allowed.has(edge.target));
  return <section className="panel graph-panel">
    <div className="graph-header">
      <div><p className="eyebrow">Knowledge graph</p><h2>设备与故障关联图谱</h2>
        <p className="muted">设备—产线关系来自设备主数据；故障关系来自 FMECA 因果传播图。</p></div>
      <div className="segmented">
        <button className={mode === "production" ? "selected" : ""} onClick={() => setMode("production")}>生产设备关联</button>
        <button className={mode === "causal" ? "selected" : ""} onClick={() => setMode("causal")}>故障因果图</button>
      </div>
    </div>
    <TopologyCanvas nodes={nodes} edges={edges} mode={mode} />
    <div className="legend"><span><i className="legend-dot line" />产线</span><span><i className="legend-dot machine" />设备</span><span><i className="legend-dot fault" />故障/症状/后果</span><span>共 {nodes.length} 节点，{edges.length} 条真实关联</span></div>
  </section>;
}

function Loading({ label }: { label: string }): JSX.Element {
  return <div className="loading"><span className="spinner" />{label}</div>;
}

function TopologyCanvas({ nodes, edges, mode }: { nodes: TopologyNode[]; edges: TopologyEdge[]; mode: "production" | "causal" }): JSX.Element {
  const positions = useMemo(() => {
    const map = new Map<string, { x: number; y: number }>();
    if (mode === "production") {
      const lines = nodes.filter((item) => item.type === "line");
      const machines = nodes.filter((item) => item.type === "machine");
      lines.forEach((line, index) => map.set(line.id, { x: 120, y: 115 + index * 148 }));
      machines.forEach((machine) => {
        const lineIndex = lines.findIndex((line) => line.id === machine.line);
        const positionInLine = machines.filter((item) => item.line === machine.line).findIndex((item) => item.id === machine.id);
        map.set(machine.id, { x: 300 + (positionInLine % 10) * 86, y: 62 + Math.max(0, lineIndex) * 148 + Math.floor(positionInLine / 10) * 58 });
      });
    } else {
      nodes.forEach((node, index) => {
        const angle = (Math.PI * 2 * index) / Math.max(nodes.length, 1) - Math.PI / 2;
        map.set(node.id, { x: 530 + Math.cos(angle) * 300, y: 280 + Math.sin(angle) * 190 });
      });
    }
    return map;
  }, [mode, nodes]);
  return <div className="topology-canvas"><svg viewBox="0 0 1200 740" role="img" aria-label="设备与故障关联图谱">
    <g>{edges.map((edge, index) => { const source = positions.get(edge.source); const target = positions.get(edge.target); return source && target ? <line key={`${edge.source}-${edge.target}-${index}`} x1={source.x} y1={source.y} x2={target.x} y2={target.y} className={`graph-edge ${edge.type}`} /> : null; })}</g>
    <g>{nodes.map((node) => { const point = positions.get(node.id); if (!point) return null; const nodeClass = node.type === "line" ? "line" : node.type === "machine" ? `machine ${node.status ?? "running"}` : "fault"; return <g key={node.id} transform={`translate(${point.x}, ${point.y})`} className="graph-node"><circle r={node.type === "line" ? 24 : node.type === "machine" ? 13 : 22} className={nodeClass} /><text y={node.type === "machine" ? 29 : 43} textAnchor="middle">{node.label}</text></g>; })}</g>
  </svg></div>;
}

interface HybridChunk { id: string; score: number; doc_type: string; source: string; section: string[]; text: string; bm25_rank: number | null; dense_rank: number | null; }

export function KnowledgePage(): JSX.Element {
  const [rules, setRules] = useState<Rule[]>([]);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<{ fmeca: Rule[]; cases: CaseItem[]; chunks: HybridChunk[] } | null>(null);
  useEffect(() => { void api.fmeca().then(setRules).catch(() => setRules([])); }, []);
  const search = async (): Promise<void> => {
    if (!query.trim()) return;
    const response = await api.search(query) as { fmeca: Rule[]; cases: CaseItem[]; chunks?: HybridChunk[] };
    setResults({ fmeca: response.fmeca ?? [], cases: response.cases ?? [], chunks: response.chunks ?? [] });
  };
  return <>
    <section className="panel search-panel">
      <div><p className="eyebrow">Hybrid retrieval：BM25 + bge-m3 语义 + RRF 融合</p><h2>知识库检索</h2>
        <p className="muted">语料按文档结构自适应切分（手册章节 / SOP 步骤 / 表格行），检索支持故障码过滤与元数据筛选。</p></div>
      <div className="search-box">
        <input value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void search(); }} placeholder="例如：温度持续升高 电流正常" />
        <button className="button primary" onClick={() => void search()}>检索</button>
      </div>
    </section>
    {results && results.chunks.length > 0 && <section className="panel">
      <PanelTitle title={`混合检索结果 (${results.chunks.length})`} />
      <div className="chunk-list">{results.chunks.map((chunk) => <article key={chunk.id} className="chunk-item">
        <header><span className="tag">{chunk.doc_type}</span><strong>{chunk.section?.join(" › ") ?? chunk.source}</strong><em>RRF {chunk.score.toFixed(4)}</em></header>
        <p>{chunk.text.slice(0, 220)}…</p>
        <small>BM25 排名 {chunk.bm25_rank ?? "—"} · 向量排名 {chunk.dense_rank ?? "—"}</small>
      </article>)}</div>
    </section>}
    {results && <div className="two-column">
      <section className="panel"><PanelTitle title="匹配 FMECA 规则" />{results.fmeca.length ? <RulesTable rules={results.fmeca} /> : <Empty label="没有命中结构化规则。" />}</section>
      <section className="panel"><PanelTitle title="相似历史案例" />{results.cases.length ? <CasesList cases={results.cases} /> : <Empty label="没有命中历史案例。" />}</section>
    </div>}
    <section className="panel"><PanelTitle title={`FMECA 规则库 (${rules.length})`} /><RulesTable rules={rules} /></section>
  </>;
}

export function OperationsPage({ machines }: { machines: Array<{ id: string }> }): JSX.Element {
  const [orders, setOrders] = useState<Array<{ id: string; machine_id: string; maintenance_type: string; technician: string; status: string; start_time: string }>>([]);
  const [machineId, setMachineId] = useState(machines[0]?.id ?? "CNC_001");
  const [message, setMessage] = useState("");
  useEffect(() => { void api.orders().then((items) => setOrders(items as typeof orders)).catch(() => setOrders([])); }, []);
  const create = async (): Promise<void> => {
    try { const order = await api.createOrder(machineId, null); setMessage(`已创建工单 ${order.id}`); }
    catch (reason: unknown) { setMessage(reason instanceof Error ? reason.message : "创建失败"); }
  };
  return <>
    <section className="panel operations-launch">
      <div><p className="eyebrow">Human-in-the-loop</p><h2>维护工单与人工审批</h2>
        <p className="muted">Agent 只给出建议；维护执行需要由工程师确认后创建工单。</p></div>
      <div className="launch-controls">
        <select value={machineId} onChange={(event) => setMachineId(event.target.value)}>{machines.map((machine) => <option key={machine.id} value={machine.id}>{machine.id}</option>)}</select>
        <button className="button primary" onClick={() => void create()}>新建工单</button>
      </div>
    </section>
    {message && <div className="notice-banner">{message}</div>}
    <section className="panel">
      <PanelTitle title="工单队列" />
      {orders.length ? <div className="compact-table">{orders.map((item) => <div className="table-row" key={item.id}><span className="order-id">{item.id}</span><div><strong>{item.machine_id}</strong><small>{item.maintenance_type} · {item.technician}</small></div><span className="tag">{item.status}</span><time>{new Date(item.start_time).toLocaleString("zh-CN")}</time></div>)}</div> : <Empty label="尚无工单。" />}
    </section>
  </>;
}
