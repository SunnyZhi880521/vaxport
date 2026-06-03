import { useState, useEffect, useMemo } from "react";
import { Search } from "lucide-react";

interface CommandItem {
  id: string;
  label: string;
  description?: string;
  shortcut?: string;
  icon?: string;
  action: () => void;
}

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
  commands: CommandItem[];
}

export function CommandPalette({ open, onClose, commands }: CommandPaletteProps) {
  const [search, setSearch] = useState("");

  // Reset search when dialog opens/closes
  useEffect(() => {
    if (open) {
      setSearch("");
    }
  }, [open]);

  // Filter commands based on search
  const filteredCommands = useMemo(() => {
    if (!search) return commands;

    const lowerSearch = search.toLowerCase();
    return commands.filter(
      (cmd) =>
        cmd.label.toLowerCase().includes(lowerSearch) ||
        cmd.description?.toLowerCase().includes(lowerSearch)
    );
  }, [commands, search]);

  // Handle keyboard navigation
  useEffect(() => {
    if (!open) return;

    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      } else if (e.key === "Enter" && filteredCommands.length > 0) {
        e.preventDefault();
        filteredCommands[0].action();
        onClose();
      }
    };

    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose, filteredCommands]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 pt-[20vh]">
      <div className="w-full max-w-xl overflow-hidden rounded-lg bg-bg-secondary shadow-2xl">
        {/* Search input */}
        <div className="flex items-center gap-3 border-b border-border-subtle px-4">
          <Search size={18} className="text-text-muted" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="输入命令..."
            autoFocus
            className="flex-1 bg-transparent py-3 text-text-primary placeholder:text-text-muted focus:outline-none"
          />
          <kbd className="rounded bg-bg-tertiary px-2 py-0.5 text-xs text-text-muted">
            ESC
          </kbd>
        </div>

        {/* Command list */}
        <div className="max-h-[400px] overflow-y-auto py-2">
          {filteredCommands.length === 0 ? (
            <div className="px-4 py-8 text-center text-text-muted">
              未找到匹配的命令
            </div>
          ) : (
            filteredCommands.map((cmd) => (
              <button
                key={cmd.id}
                onClick={() => {
                  cmd.action();
                  onClose();
                }}
                className="flex w-full items-center gap-3 px-4 py-2 text-left hover:bg-bg-tertiary"
              >
                {cmd.icon && <span className="text-lg">{cmd.icon}</span>}
                <div className="flex-1">
                  <div className="text-text-primary">{cmd.label}</div>
                  {cmd.description && (
                    <div className="text-sm text-text-muted">{cmd.description}</div>
                  )}
                </div>
                {cmd.shortcut && (
                  <kbd className="rounded bg-bg-primary px-2 py-0.5 text-xs text-text-muted">
                    {cmd.shortcut}
                  </kbd>
                )}
              </button>
            ))
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center gap-4 border-t border-border-subtle px-4 py-2 text-xs text-text-muted">
          <span className="flex items-center gap-1">
            <kbd className="rounded bg-bg-tertiary px-1.5 py-0.5">↑↓</kbd>
            导航
          </span>
          <span className="flex items-center gap-1">
            <kbd className="rounded bg-bg-tertiary px-1.5 py-0.5">↵</kbd>
            执行
          </span>
          <span className="flex items-center gap-1">
            <kbd className="rounded bg-bg-tertiary px-1.5 py-0.5">ESC</kbd>
            关闭
          </span>
        </div>
      </div>
    </div>
  );
}
