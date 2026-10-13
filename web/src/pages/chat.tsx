/** 聊天查询窗口（需求 2）：设备信息 / 维护 SOP / 检测记录。

后端为第三条 LangGraph 图（classify → dispatch → compose），数据获取全部
走确定性 SQL / 混合检索，LLM 只负责把结果组织成人话；未配置 DeepSeek 时
自动用本地 qwen2.5:7b，再不行直接模板化工具结果——聊天窗口永远有答案。
会话历史由记忆模块（conversation_threads/messages）持久化，可回看。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Empty, Loading, PanelTitle } from "../components/ui";
import { MarkdownView } from "../components/markdown";
import { downloadMarkdown, downloadWord, printReport } from "../reportExport";
import type { ChatThread, ChatTurn, ReportDetail } from "../types";

interface Bubble { role: "user" | "assistant"; content: string; meta?: string; llm?: boolean; }

const SUGGESTIONS = ["CNC_078 的主轴温度高怎么处理？", "CNC_052 最近修过什么？", "现在全厂有多少高风险设备？", "轴承磨损的维护 SOP 是什么？"];

export function ChatPage(): JSX.Element {
  const [threadId, setThreadId] = useState<string | null>(null);
  const [threads, setThreads] = useState<ChatThread[]>([]);
  const [bubbles, setBubbles] = useState<Bubble[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);

  const refreshThreads = useCallback(async () => {
    try { setThreads(await api.chatThreads()); } catch { /* 后端离线时静默 */ }
  }, []);
  useEffect(() => { void refreshThreads(); }, [refreshThreads]);
  useEffect(() => { listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" }); }, [bubbles]);

  /** 开一个新话题：清空当前会话与气泡，下一条提问会让后端新建 thread。 */
  const startNewThread = (): void => {
    setThreadId(null);
    setBubbles([]);
    setInput("");
  };

  /** 切到历史会话时把消息读回来，避免只显示一句提示、看不到上下文。 */
  const openThread = async (thread: ChatThread): Promise<void> => {
    setThreadId(thread.id);
    setBubbles([{ role: "assistant", content: "正在载入会话记录…" }]);
    try {
      const detail = await api.chatThreadMessages(thread.id);
      setBubbles(detail.messages.length
        ? detail.messages.map((message) => ({ role: message.role, content: message.content, meta: message.intent ?? undefined }))
        : [{ role: "assistant", content: `会话「${thread.title}」暂无消息记录。` }]);
    } catch {
      setBubbles([{ role: "assistant", content: `已切换到会话「${thread.title}」，继续提问即可。` }]);
    }
  };

  const send = async (question: string): Promise<void> => {
    const text = question.trim();
    if (!text || busy) return;
    setBusy(true);
    setInput("");
    setBubbles((current) => [...current, { role: "user", content: text }, { role: "assistant", content: "…正在查询工具与组织答案" }]);
    try {
      const turn: ChatTurn = await api.chat(text, threadId);
      const metaBits = [turn.intent, turn.machine_id ?? null, turn.llm_used ? "LLM 组织" : "模板兜底"].filter(Boolean).join(" · ");
      setBubbles((current) => [...current.slice(0, -1), { role: "assistant", content: turn.answer, meta: metaBits, llm: turn.llm_used }]);
      setThreadId(turn.thread_id);
      void refreshThreads();
    } catch (reason: unknown) {
      setBubbles((current) => [...current.slice(0, -1), { role: "assistant", content: `查询失败：${reason instanceof Error ? reason.message : "未知错误"}` }]);
    } finally { setBusy(false); }
  };

  return <div className="chat-layout">
    <aside className="panel chat-threads">
      <div className="panel-title">
        <h3>会话历史</h3>
        <button className="button ghost new-thread" onClick={startNewThread}>＋ 新建对话</button>
      </div>
      {threads.length === 0 && <Empty label="暂无会话，发第一条提问即可。" />}
      <div className="thread-list">
        {threads.map((thread) => <button key={thread.id} className={`thread-row ${thread.id === threadId ? "selected" : ""}`} onClick={() => void openThread(thread)}>
          <strong>{thread.title}</strong>
          <small>{thread.message_count} 条 · {new Date(thread.updated_at).toLocaleString("zh-CN")}</small>
        </button>)}
      </div>
    </aside>
    <section className="panel chat-main">
      <div className="chat-header">
        <PanelTitle title="设备查询助手" />
        <p className="muted">可查：设备档案 · 维护 SOP · 诊断/维修记录 · 知识库。数据来自数据库与混合检索，不编造。</p>
      </div>
      <div className="chat-messages" ref={listRef}>
        {bubbles.length === 0 && <div className="chat-welcome">
          <h3>问点什么，比如：</h3>
          <div className="suggestion-row">{SUGGESTIONS.map((item) => <button key={item} onClick={() => void send(item)}>{item}</button>)}</div>
        </div>}
        {bubbles.map((bubble, index) => <article key={index} className={`chat-bubble ${bubble.role}`}>
          <p>{bubble.content}</p>
          {bubble.meta && <small>{bubble.meta}</small>}
        </article>)}
      </div>
      <div className="chat-input">
        <input value={input} placeholder="输入问题，回车发送…" disabled={busy}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => { if (event.key === "Enter") void send(input); }} />
        <button className="button primary" disabled={busy || !input.trim()} onClick={() => void send(input)}>{busy ? "查询中…" : "发送"}</button>
      </div>
    </section>
  </div>;
}

export function LogsPage(): JSX.Element {
  const [items, setItems] = useState<Array<Record<string, unknown>>>([]);
  const [stats, setStats] = useState<Record<string, unknown>>({});
  const [level, setLevel] = useState("");
  const [component, setComponent] = useState("");
  const [traceId, setTraceId] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const data = await api.logs({ limit: 300, level: level || undefined, component: component || undefined });
      setItems(data.items as Array<Record<string, unknown>>);
      setStats(data.stats);
      setError("");
    } catch (reason: unknown) { setError(reason instanceof Error ? reason.message : "日志读取失败"); }
  }, [level, component]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { const id = setInterval(() => void load(), 10000); return () => clearInterval(id); }, [load]);

  const openTrace = async (): Promise<void> => {
    if (!traceId.trim()) return;
    try {
      const data = await api.logTrace(traceId.trim());
      setItems((data.app ?? []) as Array<Record<string, unknown>>);
      setError(`已按 trace_id ${data.trace_id} 过滤（含 LLM ${data.llm?.length ?? 0} 条 / 工具 ${data.tools?.length ?? 0} 条审计）`);
    } catch (reason: unknown) { setError(reason instanceof Error ? reason.message : "trace 查询失败"); }
  };

  return <div className="logs-layout">
    <div className="metric-grid">
      {Object.entries(stats).slice(0, 4).map(([key, value]) => <article className="metric-card" key={key}><span>{key}</span><strong>{typeof value === "object" ? "—" : String(value)}</strong><small>来自 data/logs/</small></article>)}
    </div>
    <section className="panel">
      <div className="panel-title"><h3>程序日志（app.log · 10 秒自动刷新）</h3>
        <div className="logs-controls">
          <select value={level} onChange={(event) => setLevel(event.target.value)}><option value="">全部级别</option><option>INFO</option><option>WARNING</option><option>ERROR</option></select>
          <input value={component} placeholder="组件过滤，如 llm / chat" onChange={(event) => setComponent(event.target.value)} />
          <input value={traceId} placeholder="按 Trace-ID 追链路" onChange={(event) => setTraceId(event.target.value)} />
          <button className="button ghost" onClick={() => void openTrace()}>追链路</button>
          <button className="button ghost" onClick={() => void load()}>刷新</button>
        </div>
      </div>
      {error && <div className="notice-banner">{error}</div>}
      <div className="log-table">
        <div className="log-row head"><span>时间</span><span>级别</span><span>组件</span><span>Trace</span><span>内容</span></div>
        {items.length === 0 && <Empty label="暂无日志。触发一次诊断或提问后再来看。" />}
        {items.map((item, index) => <div key={index} className={`log-row level-${String(item.level ?? "INFO").toLowerCase()}`}>
          <span>{String(item.ts ?? "").slice(11, 23)}</span>
          <span><i className={`tag ${String(item.level).toLowerCase()}`}>{String(item.level ?? "")}</i></span>
          <span>{String(item.component ?? "")}</span>
          <code>{String(item.trace_id ?? "").slice(0, 8)}</code>
          <span className="log-message">{String(item.message ?? JSON.stringify(item))}</span>
        </div>)}
      </div>
      <p className="muted">落盘位置：<code>data/logs/app.log</code>（应用日志）、<code>data/logs/llm_audit.jsonl</code>（每次 LLM 调用的模型/耗时/Token）、<code>data/logs/tool_calls.jsonl</code>（每次 Skill/工具调用与参数）。报错时把 Trace-ID 贴进上面输入框即可看到完整调用链。</p>
    </section>
  </div>;
}

export function ReportsPage({ canAdmin = true }: { canAdmin?: boolean }): JSX.Element {
  const [reports, setReports] = useState<ReportSummaryLite[]>([]);
  const [detail, setDetail] = useState<ReportDetail | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [channel, setChannel] = useState("feishu");
  const [webhook, setWebhook] = useState("");
  const [pushing, setPushing] = useState(false);

  const openReport = useCallback(async (id: string): Promise<void> => {
    try { setDetail(await api.report(id)); setNotice(""); }
    catch (reason: unknown) { setError(reason instanceof Error ? reason.message : "报告详情加载失败"); }
  }, []);

  const load = useCallback(async () => {
    try { setReports(await api.reports()); setError(""); }
    catch (reason: unknown) { setError(reason instanceof Error ? reason.message : "报告加载失败"); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  // 默认打开最新一份，避免右侧一开始是空白
  useEffect(() => { if (!detail && reports.length) void openReport(reports[0].id); }, [reports, detail, openReport]);

  const push = async (): Promise<void> => {
    if (!detail) return;
    setPushing(true);
    setNotice("正在推送到群机器人…");
    try {
      const response = await api.pushReport(detail.id, channel, webhook.trim() || undefined);
      setNotice(response.pushed
        ? `✅ 已推送到 ${response.channel}（HTTP ${response.status_code}）`
        : `⚠️ 推送未成功（HTTP ${response.status_code}）：${response.detail || "请检查 Webhook 地址"}`);
    } catch (reason: unknown) {
      setNotice(`推送失败：${reason instanceof Error ? reason.message : "未知错误"}`);
    } finally { setPushing(false); setTimeout(() => setNotice(""), 8000); }
  };

  return <div className="split-layout">
    <section className="asset-list panel">
      <PanelTitle title={`维护报告 (${reports.length})`} action="刷新" onAction={() => void load()} />
      {reports.length === 0 && <Empty label="暂无报告。在 Agent 监控页批准一条高风险通知后，系统会自动生成。" />}
      {reports.map((item) => <button key={item.id} className={`asset-row ${detail?.id === item.id ? "selected" : ""}`} onClick={() => void openReport(item.id)}>
        <span className="status-dot critical" />
        <div><strong>{item.machine_id} 维护报告</strong><small>{item.summary}</small></div>
        <em>{new Date(item.generated_at).toLocaleDateString("zh-CN")}</em>
      </button>)}
    </section>
    <section className="asset-detail">
      <section className="panel report-detail">
        {error && <div className="error-banner">{error}</div>}
        {notice && <div className="notice-banner">{notice}</div>}
        {detail ? <>
          <div className="report-toolbar">
            <div className="report-actions">
              <button className="button ghost" onClick={() => downloadMarkdown(detail)}>导出 Markdown</button>
              <button className="button ghost" onClick={() => downloadWord(detail)}>导出 Word</button>
              <button className="button ghost" onClick={() => printReport(detail)}>打印 / 存 PDF</button>
            </div>
            <div className="report-push">
              <select value={channel} onChange={(event) => setChannel(event.target.value)}>
                <option value="feishu">飞书</option>
                <option value="wecom">企业微信</option>
                <option value="dingtalk">钉钉</option>
              </select>
              <input value={webhook} placeholder="群机器人 Webhook（留空用 .env 配置）"
                onChange={(event) => setWebhook(event.target.value)} />
              <button className="button primary" disabled={pushing || !canAdmin} title={canAdmin ? "" : "需要管理员权限"} onClick={() => void push()}>{pushing ? "推送中…" : "推送到群"}</button>
            </div>
          </div>
          <article className="report-body">
            <h2>{detail.title}</h2>
            <p className="muted">报告编号 {detail.id} · 生成于 {new Date(detail.generated_at).toLocaleString("zh-CN")}</p>
            <MarkdownView markdown={detail.markdown} />
          </article>
        </> : <Loading label="选择左侧报告查看正文" />}
      </section>
    </section>
  </div>;
}

type ReportSummaryLite = { id: string; machine_id: string; title: string; summary: string; generated_at: string };
