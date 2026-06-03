/** Chat message types */
export type MessageRole = "user" | "agent" | "system" | "error";

export type AgentType =
  | "general"
  | "analyze_reporter"
  | "quality_supervision"
  | "doc_search"
  | "alert_monitor";

export const AGENT_LABELS: Record<AgentType, string> = {
  general: "🤖 通用 Agent",
  analyze_reporter: "📊 统计分析 Agent",
  quality_supervision: "⚖️ 质量监督 Agent",
  doc_search: "🔍 文档检索 Agent",
  alert_monitor: "🔔 预警监控 Agent",
};

export interface ToolCall {
  name: string;
  args: Record<string, unknown>;
  rowCount?: number;
  truncated?: boolean;
  duration?: number;
}

export interface Decision {
  id: string;
  question: string;
  options: string[];
  selected?: number;
}

export interface PlanData {
  planText: string;
  hasDecisions: boolean;
  decisions: Decision[];
}

export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  agentType?: AgentType;
  agentLabel?: string;
  toolCalls?: ToolCall[];
  planData?: PlanData;
  turns?: number;
  tokensUsed?: number;
  tokenPct?: number;
  timestamp: number;
  sqlQueries?: string[];
  taskId?: string;
  messageType?: "normal" | "interaction" | "plan";
}

/** SSE event types from FastAPI backend */
export interface SSEMetaEvent {
  request_id: string;
  agent_type: AgentType;
  agent_label: string;
}

export interface SSEStatusEvent {
  text: string;
}

export interface SSEPlanChunkEvent {
  text: string;
}

export interface SSEPlanReadyEvent {
  plan_text: string;
  has_decisions: boolean;
  decisions: Decision[];
}

export interface SSETextChunkEvent {
  text: string;
}

export interface SSEToolCallEvent {
  name: string;
  args: Record<string, unknown>;
}

export interface SSEToolResultEvent {
  row_count: number;
  truncated: boolean;
}

export interface SSEAnswerEvent {
  answer: string;
  agent_chain: string[];
  turns: number;
  tokens_used: number;
  token_pct: number;
  sql_queries: string[];
  task_id: string;
}

export interface SSEErrorEvent {
  message: string;
  fatal?: boolean;
}
