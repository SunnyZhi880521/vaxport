const SUGGESTIONS = [
  "查询所有产品线的批次统计",
  "PEDV-2024 效价趋势分析",
  "评估质量体系成熟度",
  "检查冷链温度异常",
];

export function WelcomeMessage() {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <div className="mb-4 text-5xl">🔬</div>
      <h1 className="mb-2 text-xl font-semibold text-text-primary">
        疫苗企业数据分析终端
      </h1>
      <p className="mb-8 text-sm text-text-muted">
        用自然语言查询疫苗生产数据库，AI 驱动的统计分析、质量监督与合规报告
      </p>

      {/* Suggestion cards */}
      <div className="grid max-w-lg grid-cols-2 gap-3">
        {SUGGESTIONS.map((s) => (
          <button
            key={s}
            className="rounded-lg border border-border-subtle bg-bg-secondary px-4 py-3 text-left text-sm text-text-secondary transition-colors hover:border-accent-purple/50 hover:bg-bg-tertiary"
          >
            💡 {s}
          </button>
        ))}
      </div>
    </div>
  );
}
