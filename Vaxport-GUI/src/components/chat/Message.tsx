import type { ChatMessage } from "../../types/chat";
import { AGENT_LABELS } from "../../types/chat";
import { formatTime, formatTokens, cn } from "../../lib/utils";
import { getUsernameAsync } from "../../utils/os";
import { ToolCallGroup } from "./ToolCallGroup";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { Copy, RotateCcw, FileDown, Check, ThumbsUp, ThumbsDown } from "lucide-react";
import { writeText } from "@tauri-apps/plugin-clipboard-manager";
import { useState, useEffect } from "react";
import { api, BASE_URL } from "../../lib/api";

/** 将本地文件路径转为后端代理 URL */
function resolveImageSrc(src: string | undefined): string | undefined {
  if (!src) return src;
  if (src.startsWith("http") || src.startsWith("data:")) return src;
  // 完整路径: /Users/.../.vaxport/charts/xxx.png → /api/files/charts/xxx.png
  const vaxIdx = src.indexOf(".vaxport");
  if (vaxIdx >= 0) {
    const rel = src.slice(vaxIdx + ".vaxport/".length);
    return `${BASE_URL}/api/files/${rel}`;
  }
  // 纯文件名（相对路径）: chart_xxx.png → /api/files/charts/chart_xxx.png
  if (!src.includes("/") && (src.endsWith(".png") || src.endsWith(".jpg") || src.endsWith(".svg"))) {
    return `${BASE_URL}/api/files/charts/${src}`;
  }
  return src;
}

interface Props {
  message: ChatMessage;
  onRetry?: () => void;
}

export function Message({ message, onRetry }: Props) {
  const [copied, setCopied] = useState(false);
  const [feedback, setFeedback] = useState<"up" | "down" | null>(null);
  const [username, setUsername] = useState("用户");

  useEffect(() => {
    getUsernameAsync().then(setUsername);
  }, []);

  if (message.role === "user") {
    // Interaction message (追问) — distinct style
    if (message.messageType === "interaction") {
      return (
        <div className="mb-3 ml-8 border-l-2 border-dashed border-accent-purple/30 pl-3">
          <div className="mb-1 text-xs text-accent-purple">追问</div>
          <div className="text-sm text-text-primary whitespace-pre-wrap">
            {message.content}
          </div>
        </div>
      );
    }

    return (
      <div className="mb-4 flex justify-end">
        <div className="max-w-[80%] rounded-lg bg-accent-purple/15 px-4 py-2.5">
          <div className="mb-1 text-right text-xs text-text-muted">▸ {username}</div>
          <div className="whitespace-pre-wrap text-sm text-text-primary">
            {message.content}
          </div>
        </div>
      </div>
    );
  }

  if (message.role === "error") {
    return (
      <div className="mb-4 rounded-lg border border-border-subtle bg-bg-tertiary/50 px-4 py-2.5">
        <div className="text-sm text-text-muted">⚠️ {message.content}</div>
      </div>
    );
  }

  const handleExport = async () => {
    try {
      const result = await api.exportMarkdown(message.content);
      const imgNote = result.images_copied > 0 ? `（含 ${result.images_copied} 张图片）` : "";
      await writeText(result.export_path);
      alert("导出成功" + imgNote + "\n路径已复制到剪贴板：\n" + result.export_path);
    } catch (err) {
      console.error("导出失败:", err);
      alert("导出失败: " + (err as Error).message);
    }
  };

  const handleCopy = async () => {
    try {
      await writeText(message.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error("复制失败:", err);
    }
  };

  // Agent message
  const agentLabel = message.agentLabel || (message.agentType ? AGENT_LABELS[message.agentType] : "Agent");

  return (
    <div className="mb-6">
      {/* Agent label */}
      <div className="mb-2 flex items-center gap-2 text-xs text-text-muted">
        <span>{agentLabel}</span>
        <span>{formatTime(message.timestamp)}</span>
      </div>

      {/* Tool calls (collapsible) */}
      {message.toolCalls && message.toolCalls.length > 0 && (
        <ToolCallGroup toolCalls={message.toolCalls} />
      )}

      {/* Markdown content */}
      <div className={cn("markdown-content text-sm leading-relaxed text-text-primary", message.messageType === "plan" && "plan-content")}>
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          rehypePlugins={[rehypeHighlight]}
          components={{
            img: ({ src, alt, ...props }) => (
              <img
                src={resolveImageSrc(src)}
                alt={alt}
                className="max-w-full rounded-lg border border-border-subtle my-2"
                {...props}
              />
            ),
          }}
        >
          {message.content}
        </ReactMarkdown>
      </div>

      {/* Action buttons */}
      <div className="mt-2 flex items-center gap-1">
        <button
          onClick={handleCopy}
          className="flex items-center gap-1 rounded px-2 py-1 text-xs text-text-muted hover:bg-bg-hover hover:text-text-secondary"
        >
          {copied ? <><Check size={12} className="text-accent-green" /> 已复制</> : <><Copy size={12} /> 复制</>}
        </button>
        <button
          onClick={handleExport}
          className="flex items-center gap-1 rounded px-2 py-1 text-xs text-text-muted hover:bg-bg-hover hover:text-text-secondary"
        >
          <FileDown size={12} /> 导出
        </button>
        {onRetry && (
          <button
            onClick={onRetry}
            className="flex items-center gap-1 rounded px-2 py-1 text-xs text-text-muted hover:bg-bg-hover hover:text-text-secondary"
          >
            <RotateCcw size={12} /> 重试
          </button>
        )}
        {/* EAR反馈按钮 */}
        {message.taskId && (
          <>
            <button
              onClick={async () => {
                if (feedback) return;
                try {
                  await api.submitFeedback(message.taskId!, true);
                  setFeedback("up");
                } catch (err) {
                  console.error("提交反馈失败:", err);
                }
              }}
              className={cn(
                "flex items-center gap-1 rounded px-2 py-1 text-xs transition-colors",
                feedback === "up"
                  ? "text-accent-green"
                  : "text-text-muted hover:bg-bg-hover hover:text-text-secondary"
              )}
            >
              <ThumbsUp size={12} />
            </button>
            <button
              onClick={async () => {
                if (feedback) return;
                try {
                  await api.submitFeedback(message.taskId!, false);
                  setFeedback("down");
                } catch (err) {
                  console.error("提交反馈失败:", err);
                }
              }}
              className={cn(
                "flex items-center gap-1 rounded px-2 py-1 text-xs transition-colors",
                feedback === "down"
                  ? "text-accent-red"
                  : "text-text-muted hover:bg-bg-hover hover:text-text-secondary"
              )}
            >
              <ThumbsDown size={12} />
            </button>
          </>
        )}
        {message.turns != null && (
          <span className="ml-2 text-xs text-text-muted">
            {message.turns} 轮 · {formatTokens(message.tokensUsed ?? 0)} tokens
          </span>
        )}
      </div>
    </div>
  );
}
