import { X, Database, Cpu, Palette, Keyboard, FolderOpen, Info } from "lucide-react";
import { cn } from "../../lib/utils";
import { useState, useEffect } from "react";
import { DatabaseSettings } from "./DatabaseSettings";
import { ModelSettings } from "./ModelSettings";
import { AppearanceSettings } from "./AppearanceSettings";

interface Props {
  open: boolean;
  onClose: () => void;
}

const TABS = [
  { id: "database", label: "数据库", icon: Database },
  { id: "model", label: "模型", icon: Cpu },
  { id: "appearance", label: "外观", icon: Palette },
  { id: "shortcuts", label: "快捷键", icon: Keyboard },
  { id: "export", label: "导出", icon: FolderOpen },
  { id: "about", label: "关于", icon: Info },
] as const;

type TabId = (typeof TABS)[number]["id"];

export function SettingsDialog({ open, onClose }: Props) {
  const [activeTab, setActiveTab] = useState<TabId>("database");

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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="flex h-[600px] w-[800px] overflow-hidden rounded-xl bg-bg-secondary shadow-2xl">
        {/* Left nav */}
        <nav className="w-48 border-r border-border-subtle bg-bg-primary p-3">
          <div className="mb-4 px-2 text-sm font-semibold text-text-primary">
            ⚙️ 设置
          </div>
          {TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={cn(
                "flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm transition-colors",
                activeTab === tab.id
                  ? "bg-accent-purple/15 text-accent-purple"
                  : "text-text-secondary hover:bg-bg-hover"
              )}
            >
              <tab.icon size={16} />
              {tab.label}
            </button>
          ))}
        </nav>

        {/* Content */}
        <div className="flex flex-1 flex-col">
          {/* Header */}
          <div className="flex items-center justify-between border-b border-border-subtle px-6 py-3">
            <h2 className="text-lg font-semibold text-text-primary">
              {TABS.find((t) => t.id === activeTab)?.label}
            </h2>
            <button
              onClick={onClose}
              className="rounded p-1 text-text-muted hover:bg-bg-hover hover:text-text-secondary"
            >
              <X size={18} />
            </button>
          </div>

          {/* Tab content */}
          <div className="flex-1 overflow-y-auto p-6">
            {activeTab === "database" && <DatabaseSettings />}
            {activeTab === "model" && <ModelSettings />}
            {activeTab === "appearance" && <AppearanceSettings />}
            {activeTab === "shortcuts" && <ShortcutsSettings />}
            {activeTab === "export" && <ExportSettings />}
            {activeTab === "about" && <AboutSettings />}
          </div>
        </div>
      </div>
    </div>
  );
}

function ShortcutsSettings() {
  const shortcuts = [
    { keys: "⌘N / Ctrl+N", desc: "新建对话" },
    { keys: "⌘, / Ctrl+,", desc: "打开设置" },
    { keys: "⌘K / Ctrl+K", desc: "命令面板" },
    { keys: "⌘B / Ctrl+B", desc: "切换左侧边栏" },
    { keys: "⌘J / Ctrl+J", desc: "切换右侧面板" },
    { keys: "⌘P / Ctrl+P", desc: "模型选择器" },
    { keys: "⌘D / Ctrl+D", desc: "数据库选择器" },
    { keys: "⌘T / Ctrl+T", desc: "切换规划/执行模式" },
    { keys: "⌘Y / Ctrl+Y", desc: "复制最后回答" },
    { keys: "Esc", desc: "取消执行 / 关闭弹窗" },
    { keys: "Enter", desc: "发送消息 / 确认计划" },
    { keys: "Shift+Enter", desc: "输入换行" },
  ];

  return (
    <div className="space-y-3">
      {shortcuts.map((s) => (
        <div
          key={s.keys}
          className="flex items-center justify-between rounded-lg bg-bg-tertiary px-4 py-2"
        >
          <span className="text-sm text-text-secondary">{s.desc}</span>
          <kbd className="rounded bg-bg-primary px-2 py-1 font-mono text-xs text-text-muted">
            {s.keys}
          </kbd>
        </div>
      ))}
    </div>
  );
}

function ExportSettings() {
  return (
    <div className="space-y-4">
      <div>
        <label className="mb-1 block text-sm text-text-secondary">默认导出格式</label>
        <div className="relative w-48">
          <select className="w-full appearance-none rounded-lg border border-border-subtle bg-bg-tertiary px-3 py-2 pr-8 text-sm text-text-primary focus:border-accent-purple focus:outline-none">
            <option value="markdown">Markdown</option>
            <option value="pdf">PDF</option>
            <option value="word">Word (.docx)</option>
          </select>
          <svg className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-text-muted" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m6 9 6 6 6-6"/></svg>
        </div>
      </div>
      <div>
        <label className="mb-1 block text-sm text-text-secondary">导出目录</label>
        <div className="flex gap-2">
          <input
            type="text"
            placeholder="默认: ~/Downloads"
            className="flex-1 rounded-lg border border-border-subtle bg-bg-tertiary px-3 py-2 text-sm text-text-primary placeholder:text-text-muted"
          />
          <button className="rounded-lg bg-bg-tertiary px-3 py-2 text-sm text-text-secondary hover:bg-bg-hover">
            浏览
          </button>
        </div>
      </div>
    </div>
  );
}

function AboutSettings() {
  return (
    <div className="text-center">
      <div className="mb-4 text-6xl">🔬</div>
      <h3 className="mb-1 text-lg font-semibold text-text-primary">
        Vaxport
      </h3>
      <p className="mb-1 text-sm text-text-muted">版本 1.3.0</p>
      <p className="mb-6 text-sm text-text-muted">
        疫苗企业数据分析终端
      </p>
      <div className="mx-auto max-w-sm rounded-lg bg-bg-tertiary p-4 text-left text-sm text-text-secondary">
        <p className="mb-2">基于 Tauri v2 + React + TypeScript 构建</p>
        <p className="mb-2">后端: Python FastAPI (现有 vaxport 代码)</p>
        <p>通信: HTTP REST + SSE 流式</p>
      </div>
    </div>
  );
}
