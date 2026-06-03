import { create } from "zustand";
import type { ChatMessage, AgentType, ToolCall, Decision } from "../types/chat";
import { uid } from "../lib/utils";

interface ChartItem {
  id: string;
  filePath: string;
  image: string;
}

interface ChatState {
  messages: ChatMessage[];
  isBusy: boolean;
  activeRequestId: string | null;
  statusText: string;
  planMode: boolean;
  charts: ChartItem[];

  // Plan confirm state
  waitingConfirm: boolean;
  planText: string;
  planDecisions: Decision[];

  // Execution phase tracking
  currentPhase: string;

  // Current streaming state
  currentAgentType: AgentType | null;
  currentAgentLabel: string;
  toolCalls: ToolCall[];
  planBuffer: string;
  textBuffer: string;

  // Actions
  addUserMessage: (content: string) => void;
  addAgentMessage: (content: string, agentType: AgentType, agentLabel: string) => void;
  addErrorMessage: (content: string) => void;
  setBusy: (busy: boolean) => void;
  setActiveRequestId: (id: string | null) => void;
  setStatusText: (text: string) => void;
  setPlanMode: (mode: boolean) => void;
  setCurrentPhase: (phase: string) => void;

  // Plan confirm
  showPlanConfirm: (planText: string, decisions: Decision[]) => void;
  hidePlanConfirm: () => void;

  // Streaming updates
  setCurrentAgent: (type: AgentType, label: string) => void;
  addToolCall: (call: ToolCall) => void;
  appendPlanChunk: (text: string) => void;
  appendTextChunk: (text: string) => void;
  finalizeAnswer: (answer: string, turns: number, tokensUsed: number, tokenPct: number, sqlQueries: string[], taskId: string) => void;
  resetStreaming: () => void;

  // Session
  clearMessages: () => void;
  restoreMessages: (msgs: Array<{ role: string; content: string; time?: string }>) => void;

  // Charts
  addChart: (chart: { filePath: string; image: string }) => void;
  clearCharts: () => void;

  // Interaction messages (追问)
  addInteractionMsg: (role: "user" | "agent", content: string) => void;
}

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  isBusy: false,
  activeRequestId: null,
  statusText: "",
  planMode: false,
  charts: [],
  currentPhase: "",

  waitingConfirm: false,
  planText: "",
  planDecisions: [],

  currentAgentType: null,
  currentAgentLabel: "",
  toolCalls: [],
  planBuffer: "",
  textBuffer: "",

  addUserMessage: (content) =>
    set((s) => ({
      messages: [
        ...s.messages,
        { id: uid(), role: "user", content, timestamp: Date.now() },
      ],
    })),

  addAgentMessage: (content, agentType, agentLabel) =>
    set((s) => ({
      messages: [
        ...s.messages,
        {
          id: uid(),
          role: "agent",
          content,
          agentType,
          agentLabel,
          toolCalls: s.toolCalls.length > 0 ? [...s.toolCalls] : undefined,
          timestamp: Date.now(),
        },
      ],
    })),

  addErrorMessage: (content) =>
    set((s) => ({
      messages: [
        ...s.messages,
        { id: uid(), role: "error", content, timestamp: Date.now() },
      ],
    })),

  setBusy: (busy) => set({ isBusy: busy }),
  setActiveRequestId: (id) => set({ activeRequestId: id }),
  setStatusText: (text) => set({ statusText: text }),
  setPlanMode: (mode) => set({ planMode: mode }),
  setCurrentPhase: (phase) => set({ currentPhase: phase }),

  showPlanConfirm: (planText, decisions) =>
    set({ waitingConfirm: true, planText, planDecisions: decisions }),
  hidePlanConfirm: () =>
    set({ waitingConfirm: false, planText: "", planDecisions: [], planBuffer: "" }),

  setCurrentAgent: (type, label) =>
    set({ currentAgentType: type, currentAgentLabel: label }),
  addToolCall: (call) =>
    set((s) => ({ toolCalls: [...s.toolCalls, call] })),
  appendPlanChunk: (text) =>
    set((s) => ({ planBuffer: s.planBuffer + text })),
  appendTextChunk: (text) =>
    set((s) => ({ textBuffer: s.textBuffer + text })),

  finalizeAnswer: (answer, turns, tokensUsed, tokenPct, sqlQueries, taskId) =>
    set((s) => ({
      messages: [
        ...s.messages.filter((m) => m.agentLabel !== "执行计划"),
        {
          id: uid(),
          role: "agent",
          content: answer,
          agentType: s.currentAgentType || "general",
          agentLabel: s.currentAgentLabel,
          toolCalls: s.toolCalls.length > 0 ? [...s.toolCalls] : undefined,
          turns,
          tokensUsed,
          tokenPct,
          sqlQueries,
          taskId,
          timestamp: Date.now(),
        },
      ],
      isBusy: false,
      activeRequestId: null,
      statusText: "",
    })),

  resetStreaming: () =>
    set({
      currentAgentType: null,
      currentAgentLabel: "",
      currentPhase: "",
      toolCalls: [],
      planBuffer: "",
      textBuffer: "",
    }),

  clearMessages: () => set({ messages: [] }),

  restoreMessages: (msgs) =>
    set({
      messages: msgs.map((m) => ({
        id: uid(),
        role: m.role === "user" ? "user" : "agent",
        content: m.content,
        agentType: m.role === "assistant" ? "general" : undefined,
        agentLabel: m.role === "assistant" ? "Agent" : undefined,
        timestamp: m.time ? new Date(m.time).getTime() : Date.now(),
      })),
    }),

  addInteractionMsg: (role, content) =>
    set((s) => ({
      messages: [
        ...s.messages,
        {
          id: uid(),
          role,
          content,
          messageType: "interaction",
          timestamp: Date.now(),
        },
      ],
    })),

  addChart: (chart) =>
    set((s) => ({
      charts: [
        ...s.charts,
        { id: uid(), filePath: chart.filePath, image: chart.image },
      ],
    })),

  clearCharts: () => set({ charts: [] }),
}));
