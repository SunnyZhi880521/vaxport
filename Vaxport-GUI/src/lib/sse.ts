import { BASE_URL } from "./api";

export type SSEEventType =
  | "meta"
  | "status"
  | "plan_chunk"
  | "plan_ready"
  | "text_chunk"
  | "tool_call"
  | "tool_result"
  | "sql"
  | "chart"
  | "answer"
  | "error"
  | "done"
  | "heartbeat";

export interface SSECallbacks {
  onMeta?: (data: Record<string, unknown>) => void;
  onStatus?: (data: Record<string, unknown>) => void;
  onPlanChunk?: (data: { text: string }) => void;
  onPlanReady?: (data: Record<string, unknown>) => void;
  onTextChunk?: (data: { text: string }) => void;
  onToolCall?: (data: Record<string, unknown>) => void;
  onToolResult?: (data: Record<string, unknown>) => void;
  onSql?: (data: { sql: string }) => void;
  onChart?: (data: { file_path: string; image: string }) => void;
  onAnswer?: (data: Record<string, unknown>) => void;
  onError?: (data: { message: string; fatal?: boolean }) => void;
  onDone?: () => void;
  onHeartbeat?: (data: { elapsed: number }) => void;
}

export class SSEClient {
  private abortController: AbortController | null = null;

  async send(
    query: string,
    planMode: boolean,
    _history: Array<{ role: string; content: string }>,
    callbacks: SSECallbacks
  ): Promise<void> {
    this.cancel();
    this.abortController = new AbortController();

    try {
      const res = await fetch(BASE_URL + "/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query, plan_mode: planMode }),
        signal: this.abortController.signal,
      });

      if (!res.ok || !res.body) {
        callbacks.onError?.({ message: `${res.status}: ${res.statusText}` });
        callbacks.onDone?.();
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let currentEvent = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          // SSE event type line
          if (line.startsWith("event:")) {
            currentEvent = line.slice(6).trim();
            continue;
          }

          // SSE data line
          if (line.startsWith("data:")) {
            const jsonStr = line.slice(5).trim();
            if (!jsonStr) {
              currentEvent = "";
              continue;
            }

            try {
              const data = JSON.parse(jsonStr);
              this.dispatchEvent(currentEvent, data, callbacks);
            } catch {
              // Skip malformed JSON
            }
            currentEvent = "";
            continue;
          }

          // Empty line = event boundary, reset
          if (line.trim() === "") {
            currentEvent = "";
          }
        }
      }
    } catch (err: unknown) {
      if (err instanceof Error && err.name !== "AbortError") {
        callbacks.onError?.({ message: err.message });
      }
    } finally {
      callbacks.onDone?.();
      this.abortController = null;
    }
  }

  cancel(): void {
    if (this.abortController) {
      this.abortController.abort();
      this.abortController = null;
    }
  }

  private dispatchEvent(
    eventType: string,
    data: Record<string, unknown>,
    callbacks: SSECallbacks
  ): void {
    switch (eventType) {
      case "meta":
        callbacks.onMeta?.(data);
        break;
      case "status":
        callbacks.onStatus?.(data);
        break;
      case "plan_chunk":
        callbacks.onPlanChunk?.(data as { text: string });
        break;
      case "plan_ready":
        callbacks.onPlanReady?.(data);
        break;
      case "text_chunk":
        callbacks.onTextChunk?.(data as { text: string });
        break;
      case "tool_call":
        callbacks.onToolCall?.(data);
        break;
      case "tool_result":
        callbacks.onToolResult?.(data);
        break;
      case "sql":
        callbacks.onSql?.(data as { sql: string });
        break;
      case "chart":
        callbacks.onChart?.(data as { file_path: string; image: string });
        break;
      case "answer":
        callbacks.onAnswer?.(data);
        break;
      case "error":
        callbacks.onError?.(data as { message: string; fatal?: boolean });
        break;
      case "done":
        // done is handled by onDone callback
        break;
      case "heartbeat":
        callbacks.onHeartbeat?.(data as { elapsed: number });
        break;
      default:
        // Unknown event, try to infer from data shape
        if (data.request_id) {
          callbacks.onMeta?.(data);
        } else if (data.answer) {
          callbacks.onAnswer?.(data);
        }
        break;
    }
  }
}
