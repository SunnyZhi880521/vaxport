import { useRef, useEffect, useCallback } from "react";
import { useChatStore } from "../../stores/chatStore";
import { WelcomeMessage } from "./WelcomeMessage";
import { Message } from "./Message";
import { StreamingBlock } from "./StreamingBlock";
import { PhaseStepper } from "./StreamingBlock";

interface ChatAreaProps {
  onRetry?: (userMessage: string) => void;
}

export function ChatArea({ onRetry }: ChatAreaProps) {
  const { messages, isBusy, statusText, currentPhase, planBuffer, textBuffer, currentAgentLabel, toolCalls } = useChatStore();
  const bottomRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const isUserScrollingRef = useRef(false);
  const scrollTimeoutRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  // Find the last user message (current question being answered)
  const lastUserMsg = isBusy
    ? [...messages].reverse().find((m) => m.role === "user" && m.messageType !== "interaction")
    : null;

  const isNearBottom = useCallback(() => {
    const el = containerRef.current;
    if (!el) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight < 50;
  }, []);

  const handleScroll = useCallback(() => {
    if (scrollTimeoutRef.current) clearTimeout(scrollTimeoutRef.current);
    const nearBottom = isNearBottom();
    isUserScrollingRef.current = !nearBottom;
    scrollTimeoutRef.current = setTimeout(() => {
      const nb = isNearBottom();
      isUserScrollingRef.current = !nb;
    }, 150);
  }, [isNearBottom]);

  const rafRef = useRef<number>(0);

  const scrollToBottom = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, []);

  useEffect(() => {
    if (isBusy && !isUserScrollingRef.current) {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      rafRef.current = requestAnimationFrame(scrollToBottom);
    }
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [messages, isBusy, statusText, currentPhase, planBuffer, textBuffer, toolCalls, scrollToBottom]);

  useEffect(() => {
    return () => {
      if (scrollTimeoutRef.current) clearTimeout(scrollTimeoutRef.current);
    };
  }, []);

  const handleRetry = (index: number) => {
    for (let i = index - 1; i >= 0; i--) {
      if (messages[i].role === "user") {
        onRetry?.(messages[i].content);
        return;
      }
    }
  };

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {/* Sticky header: current question + phase stepper */}
      {isBusy && lastUserMsg && (
        <div className="border-b border-border-subtle bg-bg-primary px-6 py-3">
          <div className="mb-1.5 text-xs text-text-muted">
            ▸ 当前问题
          </div>
          <div className="mb-2 text-sm font-medium text-text-primary line-clamp-2">
            {lastUserMsg.content}
          </div>
          {currentPhase && <PhaseStepper currentPhase={currentPhase} />}
          {statusText && (
            <div className="mt-1 rounded bg-bg-tertiary/50 px-3 py-1 text-xs text-text-muted">
              {statusText}
            </div>
          )}
        </div>
      )}

      {/* Scrollable content area */}
      <div ref={containerRef} onScroll={handleScroll} className="flex-1 overflow-y-auto px-6 py-4">
        {messages.length === 0 && !isBusy && <WelcomeMessage />}

        {messages.map((msg, index) => (
          <Message
            key={msg.id}
            message={msg}
            onRetry={msg.role === "agent" ? () => handleRetry(index) : undefined}
          />
        ))}

        {isBusy && (
          <StreamingBlock
            agentLabel={currentAgentLabel}
            planText={planBuffer}
            streamingText={textBuffer}
            toolCalls={toolCalls}
          />
        )}

        <div ref={bottomRef} />
      </div>
    </div>
  );
}
