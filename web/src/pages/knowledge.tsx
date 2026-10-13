/** 关联图谱 / 知识库（需求 6：混合检索结果可视化）/ 运维工单。 */

import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { CasesList, Empty, PanelTitle, RulesTable } from "../components/ui";
import type { CaseItem, Diagnostic, Machine, Rule, Topology, TopologyNode } from "../types";

/** 传感器指标 → 故障图"征兆"节点的映射。
 *
 * 这是把"设备当前哪几个维度异常"翻译成因果图入口的字典——没有它，图谱只是
 * 一张静态的关系图；有了它，选一台设备就能沿因果链推演它会怎么坏、坏到哪。
 */
const SYMPTOM_METRICS: Record<string, string[]> = {
  voltage_instability: ["op_voltage", "power_factor", "harmonic_distortion"],
  current_spike: ["op_amperage", "spindle_load"],
  temperature_rise: ["op_temperature", "coolant_temp", "bearing_temp"],
  vibration_increase: ["vibration_rms", "motor_vibration"],
  speed_drop: ["rotor_speed"],
};

const LAYER_X: Record<string, number> = { symptom: 178, fault: 590, consequence: 1000 };
const NODE_RADIUS: Record<string, number> = { symptom: 30, fault: 34, consequence: 30 };

export function TopologyPage(): JSX.Element {
  const [topology, setTopology] = useState<Topology | null>(null);
  const [machines, setMachines] = useState<Machine[]>([]);
  const [diagnostics, setDiagnostics] = useState<Diagnostic[]>([]);
  const [selected, setSelected] = useState<string>("");

  useEffect(() => {
    void api.topology().then(setTopology).catch(() => setTopology(null));
    void api.machines().then(setMachines).catch(() => setMachines([]));
    void api.diagnostics().then(setDiagnostics).catch(() => setDiagnostics([]));
  }, []);

  // 每台设备只保留最近一次诊断——图谱要给的是"当前状态",不是历史堆积
  const latestByMachine = useMemo(() => {
    const map = new Map<string, Diagnostic>();
    for (const item of [...diagnostics].sort((a, b) => a.created_at.localeCompare(b.created_at))) map.set(item.machine_id, item);
    return map;
  }, [diagnostics]);

  const riskyMachines = useMemo(
    () => machines.filter((machine) => ["critical", "emergency", "warning"].includes(String(machine.live_severity ?? ""))),
    [machines],
  );

  /** 因果追溯：从设备的故障模式出发，向上取征兆、向下取后果，得到高亮子图。 */
  const trace = useMemo(() => {
    if (!topology || !selected) return null;
    const diagnostic = latestByMachine.get(selected);
    const faultName = diagnostic?.payload?.fault_mode ?? "";
    const faultNode = topology.nodes.find((node) => node.type === "fault" && node.label === faultName) ?? null;
    const anomalous = new Set(diagnostic?.payload?.anomaly_dims ?? []);
    const symptomIds = new Set(
      topology.nodes
        .filter((node) => node.type === "symptom" && (SYMPTOM_METRICS[node.id.replace("fault:", "")] ?? []).some((metric) => anomalous.has(metric)))
        .map((node) => node.id),
    );
    const nodeIds = new Set<string>();
    const edgeKeys = new Set<string>();
    if (faultNode) {
      nodeIds.add(faultNode.id);
      const walk = (direction: "up" | "down"): void => {
        const stack = [faultNode.id];
        const seen = new Set<string>([faultNode.id]);
        while (stack.length) {
          const current = stack.pop() as string;
          for (const edge of topology.edges) {
            const next = direction === "up" ? (edge.target === current ? edge.source : null) : (edge.source === current ? edge.target : null);
            if (!next || seen.has(next)) continue;
            seen.add(next);
            nodeIds.add(next);
            edgeKeys.add(`${edge.source}->${edge.target}`);
            stack.push(next);
          }
        }
      };
      walk("up");
      walk("down");
    }
    return { diagnostic, faultName, faultNode, symptomIds, nodeIds, edgeKeys };
  }, [topology, selected, latestByMachine]);

  if (!topology) return <Loading label="加载故障因果图谱" />;

  return <>
    <section className="panel graph-panel">
      <div className="graph-header">
        <div>
          <p className="eyebrow">Fault causality graph</p>
          <h2>故障因果追溯</h2>
          <p className="muted">中间层是 FMECA 故障模式，左侧是它在传感器上的征兆，右侧是若不处置会演化的后果。选一台设备，就能看到它的异常指标落在哪条因果链上。</p>
        </div>
        <div className="graph-controls">
          <select value={selected} onChange={(event) => setSelected(event.target.value)}>
            <option value="">不选中设备（查看全图）</option>
            <optgroup label={`高风险设备 ${riskyMachines.length} 台`}>
              {riskyMachines.map((machine) => <option key={machine.id} value={machine.id}>{machine.id} · {machine.production_line}</option>)}
            </optgroup>
            <optgroup label="全部设备">
              {machines.filter((machine) => !riskyMachines.includes(machine)).map((machine) => <option key={machine.id} value={machine.id}>{machine.id}</option>)}
            </optgroup>
          </select>
        </div>
      </div>
      <CausalCanvas topology={topology} trace={trace} />
      <div className="legend">
        <span><i className="legend-dot symptom" />征兆（传感器指标）</span>
        <span><i className="legend-dot fault" />故障模式（FMECA）</span>
        <span><i className="legend-dot consequence" />后果</span>
        <span>共 {topology.nodes.filter((node) => node.type.startsWith("fault_")).length} 个节点、{topology.edges.filter((edge) => edge.type === "causal").length} 条因果关系；连线上的数字是因果概率</span>
      </div>
    </section>

    {trace && <section className="panel trace-panel">
      <PanelTitle title={`因果追溯 · ${selected}`} />
      {trace.faultName
        ? <>
          <div className="trace-chain">
            <span className="trace-step">{trace.symptomIds.size} 项异常征兆</span>
            <i>→</i>
            <span className="trace-step strong">{trace.faultName}</span>
            <i>→</i>
            <span className="trace-step">{Math.max((trace.nodeIds.size ?? 0) - 1, 0)} 个下游后果</span>
          </div>
          <p className="muted">
            设备最近一次诊断（{trace.diagnostic ? new Date(trace.diagnostic.created_at).toLocaleString("zh-CN") : "—"}）
            判定为「{trace.faultName}」{trace.faultNode ? "" : "（该故障模式暂无对应因果节点）"}。
            {(trace.diagnostic?.payload?.anomaly_dims?.length ?? 0) > 0 && `当前异常维度：${(trace.diagnostic?.payload?.anomaly_dims ?? []).join("、")}。`}
          </p>
          {trace.diagnostic?.payload?.root_cause && <p className="muted">根因：{trace.diagnostic.payload.root_cause}</p>}
        </>
        : <Empty label="该设备暂无诊断记录，无法定位故障模式。建议先到诊断中心触发一次诊断。" />}
    </section>}
  </>;
}

function Loading({ label }: { label: string }): JSX.Element {
  return <div className="loading"><span className="spinner" />{label}</div>;
}

interface CausalTrace {
  diagnostic: Diagnostic | undefined;
  faultName: string;
  faultNode: TopologyNode | null;
  symptomIds: Set<string>;
  nodeIds: Set<string>;
  edgeKeys: Set<string>;
}

function CausalCanvas({ topology, trace }: { topology: Topology; trace: CausalTrace | null }): JSX.Element {
  const nodes = useMemo(() => topology.nodes.filter((node) => node.type.startsWith("fault_")), [topology]);
  const nodeIds = useMemo(() => new Set(nodes.map((node) => node.id)), [nodes]);
  const edges = useMemo(() => topology.edges.filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target)), [topology, nodeIds]);
  const positions = useMemo(() => {
    const map = new Map<string, { x: number; y: number }>();
    for (const type of ["symptom", "fault", "consequence"]) {
      const group = nodes.filter((node) => node.type === `fault_${type}`);
      group.forEach((node, index) => {
        const y = 92 + index * (568 / Math.max(group.length - 1, 1));
        map.set(node.id, { x: LAYER_X[type], y });
      });
    }
    return map;
  }, [nodes]);

  const isDimmed = (nodeId: string): boolean => Boolean(trace?.faultNode) && !trace?.nodeIds.has(nodeId);
  const isDimmedEdge = (source: string, target: string): boolean => Boolean(trace?.faultNode) && !trace?.edgeKeys.has(`${source}->${target}`);

  return <div className="topology-canvas"><svg viewBox="0 0 1200 740" role="img" aria-label="故障因果追溯图谱">
    <g>
      <text className="layer-label" x={LAYER_X.symptom} y={46} textAnchor="middle">传感器征兆</text>
      <text className="layer-label" x={LAYER_X.fault} y={46} textAnchor="middle">故障模式（FMECA）</text>
      <text className="layer-label" x={LAYER_X.consequence} y={46} textAnchor="middle">演化后果</text>
    </g>
    <g>{edges.map((edge, index) => {
      const source = positions.get(edge.source);
      const target = positions.get(edge.target);
      if (!source || !target) return null;
      const dim = isDimmedEdge(edge.source, edge.target);
      const midpointX = (source.x + target.x) / 2;
      const midpointY = (source.y + target.y) / 2;
      return <g key={`${edge.source}-${edge.target}-${index}`} className={dim ? "dim" : ""}>
        <line x1={source.x} y1={source.y} x2={target.x} y2={target.y} className="graph-edge causal" />
        <rect className="edge-label-bg" x={midpointX - 17} y={midpointY - 9} width={34} height={16} rx={8} />
        <text className="edge-label" x={midpointX} y={midpointY + 3} textAnchor="middle">{Math.round((edge.probability ?? 0) * 100)}%</text>
      </g>;
    })}</g>
    <g>{nodes.map((node) => {
      const point = positions.get(node.id);
      if (!point) return null;
      const layer = node.type.replace("fault_", "");
      const isSymptomHit = trace?.symptomIds.has(node.id);
      const isFaultHit = trace?.faultNode?.id === node.id;
      const classes = [layer, isDimmed(node.id) ? "dim" : "", isFaultHit ? "active-fault" : "", isSymptomHit ? "active-symptom" : ""].filter(Boolean).join(" ");
      return <g key={node.id} transform={`translate(${point.x}, ${point.y})`} className="graph-node">
        <circle r={NODE_RADIUS[layer]} className={classes} />
        <text y={NODE_RADIUS[layer] + 16} textAnchor="middle">{node.label}</text>
      </g>;
    })}</g>
  </svg></div>;
}

interface HybridChunk { id: string; score: number; doc_type: string; source: string; section: string[]; text: string; bm25_rank: number | null; dense_rank: number | null; }

export function KnowledgePage(): JSX.Element {
  const [rules, setRules] = useState<Rule[]>([]);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<{ fmeca: Rule[]; cases: CaseItem[]; chunks: HybridChunk[] } | null>(null);
  const [searched, setSearched] = useState("");
  const [expandedChunk, setExpandedChunk] = useState<string | null>(null);
  useEffect(() => { void api.fmeca().then(setRules).catch(() => setRules([])); }, []);
  const search = async (): Promise<void> => {
    if (!query.trim()) return;
    const response = await api.search(query) as { fmeca: Rule[]; cases: CaseItem[]; chunks?: HybridChunk[] };
    setResults({ fmeca: response.fmeca ?? [], cases: response.cases ?? [], chunks: response.chunks ?? [] });
    setSearched(query);
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
    {results && <section className="panel">
      <PanelTitle title={`混合检索结果 (${results.chunks.length})`} />
      {results.chunks.length ? <div className="chunk-list">{results.chunks.map((chunk) => {
        const expanded = expandedChunk === chunk.id;
        return <article key={chunk.id} className="chunk-item">
          <header><span className="tag">{chunk.doc_type}</span><strong>{chunk.section?.join(" › ") ?? chunk.source}</strong><em>RRF {chunk.score.toFixed(4)}</em></header>
          <p>{expanded || chunk.text.length <= 220 ? chunk.text : `${chunk.text.slice(0, 220)}…`}</p>
          {chunk.text.length > 220 && <button className="text-button expand-toggle" onClick={() => setExpandedChunk(expanded ? null : chunk.id)}>
            {expanded ? "收起" : `展开全文（共 ${chunk.text.length} 字）`}
          </button>}
          <small>BM25 排名 {chunk.bm25_rank ?? "—"} · 向量排名 {chunk.dense_rank ?? "—"}</small>
        </article>;
      })}</div> : <Empty label={`没有召回与「${searched}」相关的知识片段。可换用参数名或故障名提问，例如「电流异常升高」「温度升高 电流正常」。`} />}
    </section>}
    {results && <div className="two-column">
      <section className="panel"><PanelTitle title="匹配 FMECA 规则" />{results.fmeca.length ? <RulesTable rules={results.fmeca} /> : <Empty label="没有命中结构化规则。" />}</section>
      <section className="panel"><PanelTitle title="相似历史案例" />{results.cases.length ? <CasesList cases={results.cases} /> : <Empty label="没有命中历史案例。" />}</section>
    </div>}
    <section className="panel"><PanelTitle title={`FMECA 规则库 (${rules.length})`} /><RulesTable rules={rules} /></section>
  </>;
}

export function OperationsPage({ machines, canWrite = true }: { machines: Array<{ id: string }>; canWrite?: boolean }): JSX.Element {
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
        <button className="button primary" disabled={!canWrite} title={canWrite ? "" : "需要操作员或管理员权限"} onClick={() => void create()}>新建工单</button>
      </div>
    </section>
    {message && <div className="notice-banner">{message}</div>}
    <section className="panel">
      <PanelTitle title="工单队列" />
      {orders.length ? <div className="compact-table">{orders.map((item) => <div className="table-row order-row" key={item.id}><span className="order-id">{item.id}</span><div><strong>{item.machine_id}</strong><small>{item.maintenance_type} · {item.technician}</small></div><span className="tag">{item.status}</span><time>{new Date(item.start_time).toLocaleString("zh-CN")}</time></div>)}</div> : <Empty label="尚无工单。" />}
    </section>
  </>;
}
