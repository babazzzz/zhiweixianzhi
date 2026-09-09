export type Severity = "normal" | "warning" | "critical" | "emergency" | "offline" | "running" | "maintenance";

export interface Reading {
  timestamp: string;
  metrics: Record<string, number>;
  fault_code: number;
  health_score: number | null;
}

export interface Machine {
  id: string;
  model: string;
  manufacture_date: string;
  last_maintenance: string | null;
  next_maintenance: string | null;
  last_repair: string | null;
  daily_output: number;
  unit_cost: number;
  production_line: string;
  status: Severity;
  latest_reading: { timestamp: string; metrics: Record<string, number>; health_score: number | null } | null;
  latest_diagnostic: { id: string; severity: string; confidence: number } | null;
  live_health_score?: number | null;
  live_anomaly_score?: number | null;
  live_severity?: Severity;
}

export interface Evidence {
  id: string;
  source: string;
  content: string;
  confidence: number;
  supporting_data: Record<string, string | number | boolean>;
}

export interface Rule {
  fault_code: number;
  fault_name: string;
  system: string;
  component: string;
  evidence_signals: string[];
  physical_mechanism: string;
  detection_method: string;
  sop_steps: string[];
  required_parts: string[];
  estimated_hours: number;
  engineer_level: string;
  severity: string;
}

export interface CaseItem {
  id: string;
  machine_id: string;
  fault_code: number;
  description: string;
  root_cause: string;
  resolution: string;
  rul_hours: number | null;
  risk_path: string;
  created_at: string;
}

export interface DiagnosticPayload {
  machine_id?: string;
  severity?: Severity;
  health_score?: number;
  anomaly_score?: number;
  anomaly_dims?: string[];
  fault_mode?: string;
  fault_code?: number;
  root_cause?: string;
  confidence?: number;
  evidence_chain?: Evidence[];
  rul?: Record<string, number | string>;
  decision?: Decision;
  report?: { title: string; markdown: string; generated_at: string };
  reflection_checks?: ReflectionCheck[];
  reflection_passed?: boolean;
  events?: AgentEvent[];
}

export interface Diagnostic {
  id: string;
  machine_id: string;
  created_at: string;
  status: string;
  severity: Severity;
  confidence: number;
  payload: DiagnosticPayload;
}

export interface DecisionOption {
  name: string;
  downtime_hours: number;
  direct_cost: number;
  risk_cost: number;
  total_cost: number;
}

export interface Decision {
  recommendation: string;
  timing: string;
  action: string;
  downtime_hours: number;
  direct_cost: number;
  risk_cost: number;
  net_benefit: number;
  options: DecisionOption[];
  what_if: { derated_pct: number; rul_extension_hours: number; failure_probability_72h: number };
  parts: string[];
  estimated_hours: number;
}

export interface ReflectionCheck { id: string; label: string; passed: boolean; }
export interface AgentEvent { agent: string; status: string; message: string; timestamp: string; }
export interface Overview { fleet: { total: number; normal: number; warning: number; critical: number; offline: number; average_health: number }; machines: Machine[]; diagnostics: Diagnostic[]; orders: WorkOrder[]; }
export interface TopologyNode { id: string; label: string; type: string; status?: Severity; line?: string; }
export interface TopologyEdge { source: string; target: string; type: string; probability?: number; }
export interface Topology { nodes: TopologyNode[]; edges: TopologyEdge[]; }
export interface WorkOrder { id: string; machine_id: string; diagnostic_id: string | null; maintenance_type: string; start_time: string; end_time: string | null; downtime_hours: number; parts_cost: number; labor_cost: number; parts_json: string[]; technician: string; notes: string; status: string; }

// === Agent 自主行为类型 ===

export interface AgentActivity {
  id: number;
  timestamp: string;
  agent_name: string;
  machine_id: string | null;
  action: string;
  severity: "info" | "warning" | "critical" | "error";
  reason: string;
  details: Record<string, unknown>;
  requires_human_review: boolean;
}

export interface AgentActivityResponse {
  items: AgentActivity[];
  count: number;
  loop_running: boolean;
  last_scan_at: string | null;
  last_summary: {
    scanned: number;
    healthy: number;
    warning: number;
    critical: number;
    offline: number;
    auto_triggered: string[];
    pending_human_review: string[];
    started_at: string;
    finished_at: string;
    duration_ms: number;
  } | Record<string, never>;
}

export interface Notification {
  id: string;
  created_at: string;
  machine_id: string | null;
  severity: string;
  title: string;
  message: string;
  status: "pending" | "approved" | "dismissed";
  reviewed_at: string | null;
  reviewer: string | null;
  review_notes: string | null;
  related_activity_id: number | null;
  related_diagnostic_id: string | null;
  related_order_id: string | null;
  report?: Record<string, unknown> | null;
}

// === 聊天 / 日志 / 报告 / 技能 / 记忆 类型 ===

export interface ChatCitation { id?: string; source?: string; section?: unknown; doc_type?: string; }

export interface ChatTurn {
  thread_id: string;
  intent: string;
  machine_id: string | null;
  answer: string;
  citations: ChatCitation[];
  facts: Array<{ type: string; data: unknown }>;
  llm_used: boolean;
}

export interface ChatThread { id: string; title: string; created_at: string; updated_at: string; message_count: number; summary: string | null; }

export interface ChatStoredMessage { id: number; role: "user" | "assistant"; content: string; created_at: string; intent: string | null; tools: unknown[]; citations: ChatCitation[]; }

export interface LogItem { ts: string; level: string; component: string; trace_id?: string; message?: string; [key: string]: unknown; }

export interface ReportSummary {
  id: string; machine_id: string; diagnostic_id: string | null; notification_id: string | null;
  order_id: string | null; title: string; summary: string; status: string;
  generated_at: string; generated_by: string; approved_by: string | null;
}

export interface ReportDetail extends ReportSummary { markdown: string; meta_json: string; }

export interface SkillExampleDef { description: string; payload: Record<string, unknown>; }

export interface SkillMetaDef {
  id: string; name: string; version: string; description: string; category: string;
  deterministic: boolean; requires_llm: boolean; tags: string[]; examples: SkillExampleDef[];
}

export interface MemoryItem {
  id: string; kind: "machine" | "fact" | "preference" | "lesson"; subject: string;
  content: string; source: string; confidence: number; pinned: number | boolean;
  access_count: number; created_at: string; updated_at: string;
}
