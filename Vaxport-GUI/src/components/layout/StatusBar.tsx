import { useAppStore } from "../../stores/appStore";
import { useChatStore } from "../../stores/chatStore";
import { cn } from "../../lib/utils";
import { PanelLeftOpen } from "lucide-react";

interface StatusBarProps {
  onSidebarToggle?: () => void;
}

export function StatusBar({ onSidebarToggle }: StatusBarProps) {
  const { backendOnline, sidebarOpen } = useAppStore();
  const { statusText, planMode, isBusy, setPlanMode } = useChatStore();

  const handleModeClick = () => {
    if (!isBusy) {
      setPlanMode(!planMode);
    }
  };

  return (
    <div
      className={cn(
        "flex items-center gap-3 border-t px-4 py-1.5 text-xs",
        "border-border-subtle bg-bg-secondary text-text-muted"
      )}
    >
      {/* Sidebar toggle when hidden */}
      {!sidebarOpen && onSidebarToggle && (
        <button
          onClick={onSidebarToggle}
          className="mr-1 rounded p-1 text-text-muted hover:bg-bg-hover hover:text-text-secondary"
          title="展开侧边栏"
        >
          <PanelLeftOpen size={14} />
        </button>
      )}

      {/* Mode - clickable */}
      <button
        onClick={handleModeClick}
        disabled={isBusy}
        className={cn(
          "font-medium transition-colors",
          planMode ? "text-accent-yellow" : "text-accent-green",
          !isBusy && "cursor-pointer hover:opacity-80"
        )}
        title="点击切换模式"
      >
        {planMode ? "📋 规划模式" : "⚡ 执行模式"}
      </button>

      <span className="text-border-strong">│</span>

      {/* Status text or idle info */}
      {isBusy ? (
        <span className="text-accent-yellow animate-pulse">{statusText || "处理中..."}</span>
      ) : (
        <span>空闲</span>
      )}

      <span className="flex-1" />

      {/* Backend status */}
      <span className={cn(backendOnline ? "text-accent-green" : "text-accent-red")}>
        {backendOnline ? "● 已连接" : "● 未连接"}
      </span>
    </div>
  );
}
