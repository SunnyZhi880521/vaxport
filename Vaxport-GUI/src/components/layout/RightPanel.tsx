import { Database, Image, Wrench, X, Activity } from "lucide-react";
import { cn } from "../../lib/utils";
import { useAppStore } from "../../stores/appStore";
import { SchemaBrowser } from "../panels/SchemaBrowser";
import { ChartPreview } from "../panels/ChartPreview";
import { SkillList } from "../panels/SkillList";
import { EARPanel } from "../panels/EARPanel";

export function RightPanel() {
  const { rightPanelTab, setRightPanelTab, toggleRightPanel } = useAppStore();

  const mainTabs = [
    { id: "schema" as const, label: "数据表", icon: Database },
    { id: "charts" as const, label: "图表", icon: Image },
    { id: "skills" as const, label: "SKILL", icon: Wrench },
  ];

  return (
    <aside className="flex w-80 flex-col border-l border-border-subtle bg-bg-secondary">
      {/* Main tabs */}
      <div className="flex items-center border-b border-border-subtle px-2">
        {mainTabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setRightPanelTab(tab.id)}
            className={cn(
              "flex items-center gap-1.5 px-3 py-2 text-xs transition-colors",
              rightPanelTab === tab.id
                ? "border-b-2 border-accent-purple text-text-primary"
                : "text-text-muted hover:text-text-secondary"
            )}
          >
            <tab.icon size={12} />
            {tab.label}
          </button>
        ))}
        <div className="flex-1" />
        <button
          onClick={toggleRightPanel}
          className="p-1 text-text-muted hover:text-text-secondary"
        >
          <X size={14} />
        </button>
      </div>

      {/* Main content */}
      <div className="flex-1 overflow-y-auto p-3">
        {rightPanelTab === "schema" && <SchemaBrowser />}
        {rightPanelTab === "charts" && <ChartPreview />}
        {rightPanelTab === "skills" && <SkillList />}
      </div>

      {/* EAR panel - fixed at bottom */}
      <div className="shrink-0 border-t border-border-subtle">
        <div className="flex items-center gap-1.5 px-3 py-2 text-xs font-medium text-text-secondary">
          <Activity size={12} />
          EAR 统计
        </div>
        <div className="max-h-44 overflow-y-auto px-3 pb-3">
          <EARPanel />
        </div>
      </div>
    </aside>
  );
}
