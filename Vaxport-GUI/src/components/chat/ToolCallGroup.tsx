import { useState } from "react";
import { ChevronRight, ChevronDown, Wrench } from "lucide-react";
import type { ToolCall } from "../../types/chat";

interface Props {
  toolCalls: ToolCall[];
}

export function ToolCallGroup({ toolCalls }: Props) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="mb-2 rounded-lg bg-bg-tertiary/50">
      {/* Summary header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 px-3 py-2 text-xs text-text-muted hover:text-text-secondary"
      >
        {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <Wrench size={12} />
        <span>
          {toolCalls.length} 次工具调用
          {toolCalls[toolCalls.length - 1]?.rowCount != null &&
            ` · 最近 ${toolCalls[toolCalls.length - 1].rowCount} 行`}
        </span>
      </button>

      {/* Expanded details */}
      {expanded && (
        <div className="border-t border-border-subtle px-3 py-2">
          {toolCalls.map((call, i) => (
            <div key={i} className="mb-1.5 last:mb-0">
              <div className="flex items-center gap-1 text-xs">
                <Wrench size={10} className="text-accent-cyan" />
                <span className="font-mono text-accent-cyan">{call.name}</span>
              </div>
              <div className="ml-4 text-xs text-text-muted">
                {call.rowCount != null && `${call.rowCount} 行`}
                {call.truncated && " (截断)"}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
