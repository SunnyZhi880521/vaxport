import { useAppStore } from "../../stores/appStore";

export function AppearanceSettings() {
  const { theme, setTheme, fontSize, setFontSize, density, setDensity } = useAppStore();

  return (
    <div className="space-y-6">
      {/* Theme */}
      <div>
        <h3 className="mb-3 text-sm font-medium text-text-primary">主题</h3>
        <div className="flex gap-3">
          <label className="flex items-center gap-2 text-sm text-text-secondary cursor-pointer">
            <input
              type="radio"
              name="theme"
              checked={theme === "light"}
              onChange={() => setTheme("light")}
            />
            ☀️ 浅色
          </label>
          <label className="flex items-center gap-2 text-sm text-text-secondary cursor-pointer">
            <input
              type="radio"
              name="theme"
              checked={theme === "dark"}
              onChange={() => setTheme("dark")}
            />
            🌙 深色
          </label>
          <label className="flex items-center gap-2 text-sm text-text-secondary cursor-pointer">
            <input
              type="radio"
              name="theme"
              checked={theme === "system"}
              onChange={() => setTheme("system")}
            />
            💻 跟随系统
          </label>
        </div>
      </div>

      {/* Font size */}
      <div>
        <h3 className="mb-3 text-sm font-medium text-text-primary">字体大小</h3>
        <div className="flex items-center gap-3">
          <span className="text-xs text-text-muted">小</span>
          <input
            type="range"
            min="12"
            max="18"
            value={fontSize}
            onChange={(e) => setFontSize(Number(e.target.value))}
            className="flex-1"
          />
          <span className="text-xs text-text-muted">大</span>
          <span className="w-12 rounded bg-bg-tertiary px-2 py-1 text-center text-sm text-text-secondary">
            {fontSize}px
          </span>
        </div>
      </div>

      {/* Message density */}
      <div>
        <h3 className="mb-3 text-sm font-medium text-text-primary">消息密度</h3>
        <div className="flex gap-3">
          <label className="flex items-center gap-2 text-sm text-text-secondary cursor-pointer">
            <input
              type="radio"
              name="density"
              checked={density === "comfy"}
              onChange={() => setDensity("comfy")}
            />
            舒适
          </label>
          <label className="flex items-center gap-2 text-sm text-text-secondary cursor-pointer">
            <input
              type="radio"
              name="density"
              checked={density === "compact"}
              onChange={() => setDensity("compact")}
            />
            紧凑
          </label>
        </div>
      </div>

      {/* Panels */}
      <div>
        <h3 className="mb-3 text-sm font-medium text-text-primary">默认面板状态</h3>
        <div className="space-y-2">
          <label className="flex items-center gap-2 text-sm text-text-secondary">
            <input type="checkbox" defaultChecked />
            左侧边栏默认展开
          </label>
          <label className="flex items-center gap-2 text-sm text-text-secondary">
            <input type="checkbox" defaultChecked />
            右侧面板默认展开
          </label>
        </div>
      </div>
    </div>
  );
}