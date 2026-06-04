const BASE_URL = "http://localhost:8931";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(BASE_URL + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`${res.status}: ${body || res.statusText}`);
  }
  return res.json();
}

export const api = {
  // Status
  getStatus: () => request<Record<string, unknown>>("/api/status"),

  // Schemas
  getSchemas: () => request<Record<string, unknown>>("/api/schemas"),

  // Skills
  getSkills: () => request<unknown[]>("/api/skills"),

  // Models
  getModels: () => request<Record<string, unknown>>("/api/models"),
  switchModel: (backend: string, model: string) =>
    request<{ status: string }>("/api/models/switch", {
      method: "POST",
      body: JSON.stringify({ backend, model }),
    }),
  setTemperature: (agentName: string, temperature: number) =>
    request<{ status: string; agent_name: string; temperature: number }>("/api/temperature", {
      method: "POST",
      body: JSON.stringify({ agent_name: agentName, temperature }),
    }),

  // Session
  getSessionStatus: () => request<Record<string, unknown>>("/api/session/status"),
  getSessionHistory: () => request<unknown[]>("/api/session/history"),
  clearSession: () =>
    request<{ status: string }>("/api/session/clear", { method: "POST" }),
  saveSession: (name?: string) =>
    request<{ status: string }>("/api/session/save", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  resumeSession: (ref: string) =>
    request<{ status: string; message_count: number }>("/api/session/resume", {
      method: "POST",
      body: JSON.stringify({ session_ref: ref }),
    }),
  listSessions: () =>
    request<{ sessions: Array<{ file: string; start_time: string; first_query: string; message_count: number }>; count: number }>("/api/session/list"),
  deleteSession: (file: string) =>
    request<{ status: string }>("/api/session/delete", {
      method: "DELETE",
      body: JSON.stringify({ file }),
    }),

  // Chat actions
  confirmPlan: (requestId: string, confirmed: boolean, feedback?: string) =>
    request<{ status: string }>("/api/chat/confirm", {
      method: "POST",
      body: JSON.stringify({
        request_id: requestId,
        confirmed,
        feedback: feedback || "",
      }),
    }),
  cancelQuery: (requestId: string) =>
    request<{ status: string }>("/api/chat/cancel", {
      method: "POST",
      body: JSON.stringify({ request_id: requestId }),
    }),

  // Config
  getConfig: () => request<Record<string, unknown>>("/api/config"),
  updateConfig: (updates: Record<string, unknown>) =>
    request<{ status: string }>("/api/config/update", {
      method: "POST",
      body: JSON.stringify(updates),
    }),
  testDbConnection: (params: {
    host: string;
    port: number;
    database: string;
    user: string;
    password: string;
  }) =>
    request<{ status: string; message: string }>("/api/db/test", {
      method: "POST",
      body: JSON.stringify(params),
    }),

  // Debug
  toggleDebug: () =>
    request<{ debug_mode: boolean }>("/api/debug/toggle", { method: "POST" }),

  // Schema refresh
  refreshSchema: () =>
    request<{ status: string; tool_count: number }>("/api/schema/refresh", {
      method: "POST",
    }),

  // EAR
  getEARStats: () => request<Record<string, unknown>>("/api/ear/stats"),
  getSOPStatus: () => request<Record<string, unknown>>("/api/ear/sop/status"),
  getRoutingStats: () => request<Record<string, unknown>>("/api/ear/routing/stats"),
  submitFeedback: (taskId: string, satisfaction: boolean, notes?: string) =>
    request<{ status: string }>("/api/ear/feedback", {
      method: "POST",
      body: JSON.stringify({ task_id: taskId, satisfaction, notes: notes || "" }),
    }),

  // Shutdown
  shutdown: () =>
    request<{ status: string }>("/api/shutdown", { method: "POST" }),

  // Export
  exportMarkdown: (content: string, name?: string) =>
    request<{ export_path: string; images_copied: number }>("/api/export/markdown", {
      method: "POST",
      body: JSON.stringify({ content, name: name || null }),
    }),
};

export { BASE_URL };
