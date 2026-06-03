import { useChatStore } from "../../stores/chatStore";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { X } from "lucide-react";

interface Props {
  onConfirm: (confirmed: boolean, feedback?: string) => void;
}

export function PlanConfirm({ onConfirm }: Props) {
  const { waitingConfirm, planText } = useChatStore();

  if (!waitingConfirm) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="mx-4 max-h-[80vh] w-full max-w-2xl rounded-xl bg-bg-secondary shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border-subtle px-4 py-3">
          <span className="text-sm font-medium text-text-primary">
            📋 执行计划 — 请确认
          </span>
          <button
            onClick={() => onConfirm(false)}
            className="p-1 text-text-muted hover:text-text-secondary"
          >
            <X size={16} />
          </button>
        </div>

        {/* Plan content */}
        <div className="max-h-[55vh] overflow-y-auto p-4">
          <div className="plan-content">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{planText}</ReactMarkdown>
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-3 border-t border-border-subtle px-4 py-3">
          <button
            onClick={() => onConfirm(true)}
            className="rounded-lg bg-accent-purple px-4 py-2 text-sm font-medium text-white hover:bg-accent-purple/90"
          >
            ▶ 确认执行
          </button>
          <button
            onClick={() => onConfirm(false)}
            className="rounded-lg bg-bg-tertiary px-4 py-2 text-sm text-text-secondary hover:bg-bg-hover"
          >
            取消
          </button>
        </div>
      </div>
    </div>
  );
}
