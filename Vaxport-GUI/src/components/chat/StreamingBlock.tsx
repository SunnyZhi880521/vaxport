import type { ToolCall } from "../../types/chat";
import { ToolCallGroup } from "./ToolCallGroup";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { BASE_URL } from "../../lib/api";
import { cn } from "../../lib/utils";

const PHASES = [
  { id: "classify", label: "分类" },
  { id: "planning", label: "规划" },
  { id: "sql_gen", label: "生成SQL" },
  { id: "data_collection", label: "数据采集" },
  { id: "data_check", label: "数据校验" },
  { id: "analysis", label: "分析" },
  { id: "review", label: "审核" },
  { id: "fix", label: "修复" },
] as const;

export function PhaseStepper({ currentPhase }: { currentPhase: string }) {
  const currentIdx = PHASES.findIndex((p) => p.id === currentPhase);
  const maxShow = Math.min(PHASES.length, currentIdx + 2);
  const visiblePhases = PHASES.slice(0, Math.max(maxShow, 3));

  return (
    <div className="flex items-center gap-1.5 overflow-x-auto py-1">
      {visiblePhases.map((phase, i) => {
        const isPast = i < currentIdx;
        const isCurrent = i === currentIdx;
        return (
          <div key={phase.id} className="flex items-center gap-1.5">
            <div
              className={cn(
                "flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap transition-colors",
                isCurrent && "bg-accent-purple/20 text-accent-purple ring-1 ring-accent-purple/40",
                isPast && "bg-accent-green/15 text-accent-green",
                !isCurrent && !isPast && "bg-bg-tertiary text-text-muted"
              )}
            >
              {isPast && <span>✓</span>}
              {isCurrent && (
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent-purple" />
              )}
              <span>{phase.label}</span>
            </div>
            {i < visiblePhases.length - 1 && (
              <div className={cn("h-px w-3", isPast ? "bg-accent-green/40" : "bg-border-subtle")} />
            )}
          </div>
        );
      })}
    </div>
  );
}

/** 将本地文件路径转为后端代理 URL */
function resolveImageSrc(src: string | undefined): string | undefined {
  if (!src) return src;
  if (src.startsWith("http") || src.startsWith("data:")) return src;
  const vaxIdx = src.indexOf(".vaxport");
  if (vaxIdx >= 0) {
    const rel = src.slice(vaxIdx + ".vaxport/".length);
    return `${BASE_URL}/api/files/${rel}`;
  }
  if (!src.includes("/") && (src.endsWith(".png") || src.endsWith(".jpg") || src.endsWith(".svg"))) {
    return `${BASE_URL}/api/files/charts/${src}`;
  }
  return src;
}

const imgComponent = {
  img: ({ src, alt, ...props }: { src?: string; alt?: string }) => (
    <img
      src={resolveImageSrc(src)}
      alt={alt}
      className="max-w-full rounded-lg border border-border-subtle my-2"
      {...props}
    />
  ),
};

interface Props {
  agentLabel: string;
  planText: string;
  streamingText: string;
  toolCalls: ToolCall[];
}

export function StreamingBlock({ agentLabel, planText, streamingText, toolCalls }: Props) {
  return (
    <div className="mb-6">
      {/* Agent label */}
      <div className="mb-2 flex items-center gap-2 text-xs text-text-muted">
        <span>{agentLabel || "Agent"}</span>
        <div className="flex gap-1">
          <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-accent-purple" style={{ animationDelay: "0ms" }} />
          <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-accent-purple" style={{ animationDelay: "150ms" }} />
          <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-accent-purple" style={{ animationDelay: "300ms" }} />
        </div>
      </div>

      {/* Tool calls */}
      {toolCalls.length > 0 && (
        <ToolCallGroup toolCalls={toolCalls} />
      )}

      {/* Plan text (streaming) */}
      {planText && (
        <div className="mb-3 rounded-lg border border-accent-yellow/30 bg-accent-yellow/5 p-3">
          <div className="mb-1 text-xs font-medium text-accent-yellow">📋 执行计划</div>
          <div className="markdown-content plan-content text-sm">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={imgComponent}>{planText}</ReactMarkdown>
          </div>
        </div>
      )}

      {/* Streaming answer text */}
      {streamingText && (
        <div className="markdown-content text-sm leading-relaxed text-text-primary">
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={imgComponent}>{streamingText}</ReactMarkdown>
        </div>
      )}
    </div>
  );
}
