import { X } from "lucide-react";
import { useEffect } from "react";

interface ShortcutHelpProps {
  open: boolean;
  onClose: () => void;
}

const shortcuts = [
  { keys: "⌘B", desc: "切换左侧边栏" },
  { keys: "⌘J", desc: "切换右侧面板" },
  { keys: "⌘T", desc: "切换规划/执行模式" },
  { keys: "⌘,", desc: "打开设置" },
  { keys: "⌘K", desc: "打开命令面板" },
  { keys: "⌘N", desc: "新建对话" },
  { keys: "Enter", desc: "发送消息 / 确认计划" },
  { keys: "Shift+Enter", desc: "输入换行" },
  { keys: "Esc", desc: "取消执行 / 关闭弹窗" },
];

export function ShortcutHelp({ open, onClose }: ShortcutHelpProps) {
  // Esc to close
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="w-full max-w-md rounded-xl bg-bg-secondary shadow-2xl">
        <div className="flex items-center justify-between border-b border-border-subtle px-4 py-3">
          <h2 className="text-lg font-semibold text-text-primary">⌨️ 快捷键</h2>
          <button
            onClick={onClose}
            className="p-1 text-text-muted hover:text-text-secondary"
          >
            <X size={18} />
          </button>
        </div>
        <div className="p-4">
          <div className="space-y-2">
            {shortcuts.map((shortcut, index) => (
              <div
                key={index}
                className="flex items-center justify-between rounded-lg bg-bg-tertiary px-3 py-2"
              >
                <span className="text-sm text-text-secondary">{shortcut.desc}</span>
                <kbd className="rounded bg-bg-primary px-2 py-1 font-mono text-xs text-text-muted">
                  {shortcut.keys}
                </kbd>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
