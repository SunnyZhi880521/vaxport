import { Image, Download, Trash2, Eye } from "lucide-react";
import { useState } from "react";
import { useAppStore } from "../../stores/appStore";

export function ChartPreview() {
  const charts = useAppStore((s) => s.charts);
  const clearCharts = useAppStore((s) => s.clearCharts);
  const [selectedChart, setSelectedChart] = useState<string | null>(null);

  const handleDownload = (chart: { id: string; image: string }) => {
    const link = document.createElement("a");
    link.href = `data:image/png;base64,${chart.image}`;
    link.download = `chart-${chart.id.slice(0, 8)}.png`;
    link.click();
  };

  if (charts.length === 0) {
    return (
      <div className="text-sm text-text-secondary">
        <div className="mb-3 flex items-center justify-between">
          <span className="text-xs text-text-muted">图表预览</span>
        </div>
        <div className="rounded-lg border border-dashed border-border-subtle p-8 text-center">
          <Image size={32} className="mx-auto mb-2 text-text-muted" />
          <p className="text-xs text-text-muted">
            Agent 生成的图表将显示在这里
          </p>
          <p className="mt-2 text-xs text-text-muted">
            尝试提问"生成趋势图"或"对比分析"
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="text-sm text-text-secondary">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-xs text-text-muted">
          图表预览 ({charts.length})
        </span>
        <button
          onClick={clearCharts}
          className="rounded p-1 text-text-muted hover:bg-bg-hover hover:text-accent-red"
          title="清空所有图表"
        >
          <Trash2 size={14} />
        </button>
      </div>

      <div className="space-y-3">
        {charts.map((chart) => (
          <div
            key={chart.id}
            className="group rounded-lg border border-border-subtle bg-bg-tertiary overflow-hidden"
          >
            <div className="relative aspect-video bg-bg-primary">
              <img
                src={`data:image/png;base64,${chart.image}`}
                alt="Generated chart"
                className="h-full w-full object-contain"
              />
              <div className="absolute right-2 top-2 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                <button
                  onClick={() => handleDownload(chart)}
                  className="rounded bg-bg-secondary/80 p-1.5 text-text-secondary hover:bg-bg-secondary"
                  title="下载"
                >
                  <Download size={14} />
                </button>
                <button
                  onClick={() => setSelectedChart(chart.id)}
                  className="rounded bg-bg-secondary/80 p-1.5 text-text-secondary hover:bg-bg-secondary"
                  title="查看大图"
                >
                  <Eye size={14} />
                </button>
              </div>
            </div>
            <div className="px-3 py-2 text-xs text-text-muted">
              {new Date(chart.timestamp).toLocaleTimeString()}
            </div>
          </div>
        ))}
      </div>

      {selectedChart && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-8"
          onClick={() => setSelectedChart(null)}
        >
          <div className="max-h-full max-w-full overflow-auto">
            {charts
              .filter((c) => c.id === selectedChart)
              .map((chart) => (
                <img
                  key={chart.id}
                  src={`data:image/png;base64,${chart.image}`}
                  alt="Chart preview"
                  className="max-h-[90vh] max-w-[90vw] object-contain"
                />
              ))}
          </div>
        </div>
      )}
    </div>
  );
}
