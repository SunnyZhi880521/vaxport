import { useState, useRef, useEffect, type KeyboardEvent } from "react";
import { Send, Square, Paperclip, Camera, Check } from "lucide-react";
import { useChatStore } from "../../stores/chatStore";
import { cn } from "../../lib/utils";

interface Props {
  onSend: (text: string) => void;
  onCancel: () => void;
  onConfirmPlan: (confirmed: boolean, feedback?: string) => void;
  onAttach?: () => void;
  onCapture?: () => void;
}

export function InputArea({ onSend, onCancel, onConfirmPlan, onAttach, onCapture }: Props) {
  const [text, setText] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const { isBusy, planMode, waitingConfirm } = useChatStore();

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  }, [text]);

  const CONFIRM_KEYWORDS = ["开始执行", "确认执行", "确认", "执行", "ok", "yes", "好", "好的", "可以", "开始", "go", "y"];

  const handleSubmit = () => {
    if (waitingConfirm) {
      const trimmed = text.trim();
      if (!trimmed || CONFIRM_KEYWORDS.includes(trimmed.toLowerCase())) {
        onConfirmPlan(true);
      } else {
        onConfirmPlan(true, trimmed);
      }
      setText("");
      return;
    }

    const trimmed = text.trim();
    if (!trimmed) return;
    onSend(trimmed);
    setText("");
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
    if (e.key === "Escape" && waitingConfirm) {
      e.preventDefault();
      onConfirmPlan(false);
      setText("");
    }
  };

  return (
    <div className="border-t border-border-subtle bg-bg-secondary px-4 py-3">
      {/* Plan mode indicator */}
      {planMode && !waitingConfirm && (
        <div className="mb-2 text-center text-xs text-accent-yellow">
          📋 规划模式 — Agent 自动生成计划并执行，无需确认
        </div>
      )}

      {/* Plan review indicator */}
      {waitingConfirm && (
        <div className="mb-2 flex items-center justify-center gap-2">
          <span className="rounded bg-accent-purple/15 px-2 py-0.5 text-xs font-medium text-accent-purple">
            📋 审核执行计划
          </span>
          <span className="text-xs text-text-muted">
            Enter 确认执行 · 输入修改意见后 Enter 更新计划 · Esc 取消
          </span>
        </div>
      )}

      <div className="mx-auto flex max-w-3xl items-end gap-2">
        {/* Action buttons - horizontal left */}
        <div className="flex items-center gap-0.5 pb-0.5">
          <button
            onClick={onAttach}
            className="rounded-lg p-2 text-text-muted hover:bg-bg-hover hover:text-text-secondary transition-colors"
            title="上传文件"
          >
            <Paperclip size={18} />
          </button>
          <button
            onClick={onCapture}
            className="rounded-lg p-2 text-text-muted hover:bg-bg-hover hover:text-text-secondary transition-colors"
            title="截图"
          >
            <Camera size={18} />
          </button>
        </div>

        {/* Text input */}
        <textarea
          ref={textareaRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={
            waitingConfirm
              ? "输入修改意见后 Enter 更新计划，或直接 Enter 确认执行..."
              : "输入查询内容... Enter 发送, Shift+Enter 换行"
          }
          rows={1}
          className={cn(
            "flex-1 resize-none rounded-xl border px-4 py-2.5 text-sm text-text-primary placeholder:text-text-muted focus:outline-none transition-colors",
            waitingConfirm
              ? "border-accent-purple/50 bg-bg-tertiary focus:border-accent-purple"
              : "border-border-subtle bg-bg-tertiary focus:border-accent-purple"
          )}
        />

        {/* Send / Cancel / Confirm button */}
        {isBusy && !waitingConfirm ? (
          <button
            onClick={onCancel}
            className="rounded-xl bg-accent-red/20 p-2.5 text-accent-red hover:bg-accent-red/30 transition-colors"
          >
            <Square size={18} />
          </button>
        ) : waitingConfirm ? (
          <button
            onClick={handleSubmit}
            className="rounded-xl bg-accent-purple p-2.5 text-white hover:bg-accent-purple/90 shadow-sm transition-colors"
            title="确认执行 (Enter)"
          >
            <Check size={18} />
          </button>
        ) : (
          <button
            onClick={handleSubmit}
            disabled={!text.trim()}
            className={cn(
              "rounded-xl p-2.5 transition-all",
              text.trim()
                ? "bg-accent-purple text-white hover:bg-accent-purple/90 shadow-sm"
                : "bg-bg-tertiary text-text-muted"
            )}
          >
            <Send size={18} />
          </button>
        )}
      </div>
    </div>
  );
}