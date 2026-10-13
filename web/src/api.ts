import type { AgentActivity, AgentActivityResponse, CaseItem, ChatStoredMessage, ChatThread, ChatTurn, Diagnostic, DiagnosticPayload, LogItem, Machine, MemoryItem, Notification, Overview, Reading, ReportDetail, ReportSummary, Rule, SkillMetaDef, Topology, WorkOrder } from "./types";

const baseUrl = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

/** WebSocket 基址：由 API 基址派生（http→ws / https→wss），避免硬编码。 */
export function wsBase(): string {
  return baseUrl.replace(/^http/, "ws");
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, { ...init, headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) } });
  if (!response.ok) throw new Error(`${response.status}: ${await response.text()}`);
  return response.json() as Promise<T>;
}

export const api = {
  overview: (): Promise<Overview> => request<Overview>("/api/overview"),
  machines: async (): Promise<Machine[]> => (await request<{ items: Machine[] }>("/api/machines")).items,
  machine: (id: string): Promise<{ machine: Machine; health: DiagnosticPayload | null; history: Reading[]; diagnostics: Diagnostic[]; orders: WorkOrder[] }> => request(`/api/machines/${id}`),
  diagnostics: async (): Promise<Diagnostic[]> => (await request<{ items: Diagnostic[] }>("/api/diagnostics")).items,
  trigger: (machineId: string): Promise<DiagnosticPayload & { id: string; thread_id: string }> => request("/api/diagnostics/trigger", { method: "POST", body: JSON.stringify({ machine_id: machineId }) }),
  fmeca: async (): Promise<Rule[]> => (await request<{ items: Rule[] }>("/api/knowledge/fmeca")).items,
  search: (query: string): Promise<{ fmeca: Rule[]; cases: CaseItem[] }> => request("/api/knowledge/search", { method: "POST", body: JSON.stringify({ query }) }),
  topology: (): Promise<Topology> => request<Topology>("/api/topology"),
  orders: async (): Promise<WorkOrder[]> => (await request<{ items: WorkOrder[] }>("/api/operations/orders")).items,
  createOrder: (machineId: string, diagnosticId: string | null): Promise<{ id: string; status: string }> => request("/api/operations/orders", { method: "POST", body: JSON.stringify({ machine_id: machineId, diagnostic_id: diagnosticId }) }),
  // === Agent 自主行为接口 ===
  agentActivity: async (limit = 200, machineId?: string): Promise<AgentActivityResponse> => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (machineId) params.set("machine_id", machineId);
    return request<AgentActivityResponse>(`/api/agent/activity?${params.toString()}`);
  },
  triggerScan: (): Promise<Record<string, unknown>> => request<Record<string, unknown>>("/api/agent/scan", { method: "POST" }),
  notifications: async (status: string = "pending"): Promise<Notification[]> => (await request<{ items: Notification[] }>(`/api/notifications?status=${status}`)).items,
  reviewNotification: (id: string, action: "approve" | "dismiss", reviewer: string, notes: string): Promise<Notification> => request<Notification>(`/api/notifications/${id}/review`, { method: "POST", body: JSON.stringify({ action, reviewer, notes }) }),
  // === 聊天查询（需求 2） ===
  chat: (question: string, threadId?: string | null): Promise<ChatTurn> => request("/api/chat", { method: "POST", body: JSON.stringify({ question, thread_id: threadId ?? undefined }) }),
  chatThreads: async (): Promise<ChatThread[]> => (await request<{ items: ChatThread[] }>("/api/chat/threads")).items,
  chatThreadMessages: (threadId: string): Promise<ChatThread & { messages: ChatStoredMessage[] }> => request(`/api/chat/threads/${threadId}`),
  // === 日志（需求 4） ===
  logs: async (params: { limit?: number; level?: string; component?: string; trace_id?: string } = {}): Promise<{ items: LogItem[]; count: number; stats: Record<string, unknown> }> => {
    const search = new URLSearchParams();
    if (params.limit) search.set("limit", String(params.limit));
    if (params.level) search.set("level", params.level);
    if (params.component) search.set("component", params.component);
    if (params.trace_id) search.set("trace_id", params.trace_id);
    return request(`/api/logs?${search.toString()}`);
  },
  logTrace: (traceId: string): Promise<{ trace_id: string; app: LogItem[]; llm: LogItem[]; tools: LogItem[] }> => request(`/api/logs/trace/${traceId}`),
  // === 维护报告（需求 8） ===
  reports: async (machineId?: string): Promise<ReportSummary[]> => (await request<{ items: ReportSummary[] }>(`/api/reports${machineId ? `?machine_id=${machineId}` : ""}`)).items,
  report: (id: string): Promise<ReportDetail> => request(`/api/reports/${id}`),
  pushReport: (id: string, channel: string, webhookUrl?: string): Promise<{ report_id: string; channel: string; pushed: boolean; status_code: number; detail: string }> =>
    request(`/api/reports/${id}/push`, { method: "POST", body: JSON.stringify({ channel, webhook_url: webhookUrl || undefined }) }),
  // === 技能注册表（需求 3） ===
  skills: async (): Promise<SkillMetaDef[]> => (await request<{ items: SkillMetaDef[] }>("/api/skills")).items,
  runSkill: (id: string, payload: Record<string, unknown>): Promise<Record<string, unknown>> => request(`/api/skills/${id}/run`, { method: "POST", body: JSON.stringify({ payload }) }),
  // === 记忆（需求 5） ===
  memories: async (params: { subject?: string; kind?: string; query?: string } = {}): Promise<MemoryItem[]> => {
    const search = new URLSearchParams();
    if (params.subject) search.set("subject", params.subject);
    if (params.kind) search.set("kind", params.kind);
    if (params.query) search.set("query", params.query);
    return (await request<{ items: MemoryItem[] }>(`/api/memory?${search.toString()}`)).items;
  },
  remember: (kind: string, subject: string, content: string, pinned = false): Promise<{ id: string }> => request("/api/memory", { method: "POST", body: JSON.stringify({ kind, subject, content, pinned }) }),
  forget: (id: string): Promise<{ deleted: string }> => request(`/api/memory/${id}`, { method: "DELETE" })
};
