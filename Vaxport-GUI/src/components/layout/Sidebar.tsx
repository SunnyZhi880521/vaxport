import {
  MessageSquarePlus,
  History,
  Settings,
  Moon,
  Sun,
  Keyboard,
  PanelLeftClose,
  Trash2,
} from "lucide-react";
import { useState, useEffect, useCallback } from "react";
import { useAppStore } from "../../stores/appStore";
import { useChatStore } from "../../stores/chatStore";
import { api } from "../../lib/api";
import { ShortcutHelp } from "../ShortcutHelp";

interface SessionItem {
  file: string;
  start_time: string;
  first_query: string;
  message_count: number;
}

interface SidebarProps {
  onSettingsOpen?: () => void;
  onToggle?: () => void;
}

export function Sidebar({ onSettingsOpen, onToggle }: SidebarProps) {
  const { theme, setTheme } = useAppStore();
  const { clearMessages, restoreMessages } = useChatStore();
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [loading, setLoading] = useState(true);

  const loadSessions = useCallback(async () => {
    try {
      const res = await api.listSessions();
      setSessions(res.sessions);
    } catch {
      setSessions([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSessions();
  }, [loadSessions]);

  const handleNewChat = () => {
    clearMessages();
  };

  const handleRestore = async (session: SessionItem) => {
    try {
      const res = await api.resumeSession(session.file);
      if (res.status === "ok") {
        const history = await api.getSessionHistory() as unknown as { messages: Array<{ role: string; content: string; time?: string }> };
        if (history?.messages) {
          restoreMessages(history.messages);
        }
      }
    } catch (err) {
      console.error("恢复会话失败:", err);
    }
  };

  const handleDelete = async (e: React.MouseEvent, file: string) => {
    e.stopPropagation();
    try {
      await api.deleteSession(file);
      setSessions((prev) => prev.filter((s) => s.file !== file));
    } catch (err) {
      console.error("删除会话失败:", err);
    }
  };

  return (
    <>
      <aside className="flex w-60 flex-col border-r border-border-subtle bg-bg-secondary">
        {/* Header with toggle */}
        <div className="flex items-center justify-between px-4 py-3">
          <div className="flex items-center gap-2">
            <span className="text-xl">🔬</span>
            <span className="text-sm font-semibold text-text-primary">Vaxport</span>
          </div>
          {onToggle && (
            <button
              onClick={onToggle}
              className="rounded p-1 text-text-muted hover:bg-bg-hover hover:text-text-secondary"
              title="收起侧边栏"
            >
              <PanelLeftClose size={16} />
            </button>
          )}
        </div>

        {/* New chat button */}
        <div className="px-3 pb-2">
          <button
            onClick={handleNewChat}
            className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm text-text-secondary hover:bg-bg-hover"
          >
            <MessageSquarePlus size={16} />
            新对话
          </button>
        </div>

        {/* Session history */}
        <div className="flex-1 overflow-y-auto px-3 py-2">
          <div className="mb-1 px-2 text-xs text-text-muted">会话历史</div>
          {loading ? (
            <div className="flex items-center gap-2 px-3 py-1.5 text-sm text-text-muted">
              <History size={14} />
              加载中...
            </div>
          ) : sessions.length === 0 ? (
            <div className="flex items-center gap-2 px-3 py-1.5 text-sm text-text-muted">
              <History size={14} />
              暂无历史
            </div>
          ) : (
            <div className="space-y-1">
              {sessions.map((session) => (
                <div
                  key={session.file}
                  onClick={() => handleRestore(session)}
                  className="group flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-sm text-text-secondary hover:bg-bg-hover"
                >
                  <History size={14} className="shrink-0 text-text-muted" />
                  <div className="flex-1 overflow-hidden">
                    <div className="truncate text-text-primary">
                      {session.first_query || "会话"}
                    </div>
                    <div className="text-xs text-text-muted">
                      {session.message_count} 条消息
                    </div>
                  </div>
                  <button
                    onClick={(e) => handleDelete(e, session.file)}
                    className="shrink-0 opacity-0 group-hover:opacity-100 hover:text-accent-red"
                    title="删除会话"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Bottom controls */}
        <div className="border-t border-border-subtle px-3 py-2">
          <button
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            className="flex w-full items-center gap-2 rounded-lg px-3 py-1.5 text-sm text-text-secondary hover:bg-bg-hover"
          >
            {theme === "dark" ? <Moon size={14} /> : <Sun size={14} />}
            {theme === "dark" ? "浅色模式" : "深色模式"}
          </button>
          <button
            onClick={() => setShowShortcuts(true)}
            className="flex w-full items-center gap-2 rounded-lg px-3 py-1.5 text-sm text-text-secondary hover:bg-bg-hover"
          >
            <Keyboard size={14} />
            快捷键
          </button>
          <button
            onClick={() => onSettingsOpen?.()}
            className="flex w-full items-center gap-2 rounded-lg px-3 py-1.5 text-sm text-text-secondary hover:bg-bg-hover"
          >
            <Settings size={14} />
            设置
          </button>
        </div>
      </aside>

      {/* Shortcut help modal */}
      <ShortcutHelp open={showShortcuts} onClose={() => setShowShortcuts(false)} />
    </>
  );
}
