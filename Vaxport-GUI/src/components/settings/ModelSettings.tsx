import { useState, useEffect, useCallback, useRef } from "react";
import { Eye, EyeOff, ChevronUp, ChevronDown } from "lucide-react";
import { api } from "../../lib/api";

const AGENTS = [
  { id: "task_assigner", label: "任务分配", recommendedTemp: 0.0, reason: "纯路由分类，需要确定性输出" },
  { id: "general", label: "通用 Agent", recommendedTemp: 0.1, reason: "工具调用需要精确参数" },
  { id: "analyze_reporter", label: "分析报告", recommendedTemp: 0.3, reason: "分析文本需要一定创造性" },
  { id: "quality_supervision", label: "质量监督", recommendedTemp: 0.1, reason: "质检需要精确判断" },
  { id: "document_search", label: "文档检索", recommendedTemp: 0.2, reason: "检索需要灵活性" },
];

const MODELS = [
  "deepseek-v4-pro",
  "deepseek-v4-flash",
  "qwen3.7-max",
  "qwen-max",
  "qwen-plus",
  "glm-5.1",
];

export function ModelSettings() {
  const [backend, setBackend] = useState<"aliyun" | "local">("aliyun");
  const [apiKey, setApiKey] = useState("");
  const [apiKeyDirty, setApiKeyDirty] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [model, setModel] = useState("deepseek-v4-pro");
  const [baseUrl, setBaseUrl] = useState(
    "https://dashscope.aliyuncs.com/compatible-mode/v1"
  );
  const [ollamaUrl, setOllamaUrl] = useState("http://localhost:11434");
  const [ollamaModel, setOllamaModel] = useState("");

  // Agent preferences
  const [autoPlan, setAutoPlan] = useState(true);
  const [planConfirm, setPlanConfirm] = useState(false);
  const [autoQc, setAutoQc] = useState(true);

  // Per-agent temperature & model
  const [temperatures, setTemperatures] = useState<Record<string, number>>({});
  const [agentModels, setAgentModels] = useState<Record<string, string | null>>({});

  // Debounce refs for text inputs
  const baseUrlTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const ollamaUrlTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const ollamaModelTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  // Load config from backend on mount
  useEffect(() => {
    api.getConfig().then((cfg) => {
      const apiCfg = cfg.api as Record<string, unknown> | undefined;
      const localCfg = cfg.local as Record<string, unknown> | undefined;
      const agentCfg = cfg.agent as Record<string, unknown> | undefined;

      if (apiCfg) {
        if (apiCfg.aliyun_key) setApiKey(apiCfg.aliyun_key as string);
        if (apiCfg.aliyun_model) setModel(apiCfg.aliyun_model as string);
        if (apiCfg.aliyun_base_url) setBaseUrl(apiCfg.aliyun_base_url as string);
      }
      if (localCfg) {
        if (localCfg.ollama_url) setOllamaUrl(localCfg.ollama_url as string);
        if (localCfg.ollama_model) setOllamaModel(localCfg.ollama_model as string);
      }
      if (agentCfg) {
        const pb = agentCfg.primary_backend as string | undefined;
        if (pb) setBackend(pb as "aliyun" | "local");
        if (agentCfg.auto_plan !== undefined) setAutoPlan(agentCfg.auto_plan as boolean);
        if (agentCfg.plan_confirm !== undefined) setPlanConfirm(agentCfg.plan_confirm as boolean);
        if (agentCfg.auto_review !== undefined) setAutoQc(agentCfg.auto_review as boolean);
        const temps = agentCfg.agent_temperatures as Record<string, number> | undefined;
        if (temps) setTemperatures(temps);
        const models = agentCfg.agent_models as Record<string, string | null> | undefined;
        if (models) setAgentModels(models);
      }
    }).catch(() => {});
  }, []);

  const handleBackendChange = useCallback((b: "aliyun" | "local") => {
    setBackend(b);
    api.updateConfig({ backend: b }).catch(() => {});
  }, []);

  const handleModelChange = useCallback((m: string) => {
    setModel(m);
    api.updateConfig({ model: m }).catch(() => {});
  }, []);

  const handleApiKeyBlur = useCallback(() => {
    if (apiKeyDirty && apiKey) {
      api.updateConfig({ api_key: apiKey }).catch(() => {});
      setApiKeyDirty(false);
    }
  }, [apiKey, apiKeyDirty]);

  const handleBaseUrlChange = useCallback((v: string) => {
    setBaseUrl(v);
    clearTimeout(baseUrlTimer.current);
    baseUrlTimer.current = setTimeout(() => {
      api.updateConfig({ base_url: v }).catch(() => {});
    }, 800);
  }, []);

  const handleOllamaUrlChange = useCallback((v: string) => {
    setOllamaUrl(v);
    clearTimeout(ollamaUrlTimer.current);
    ollamaUrlTimer.current = setTimeout(() => {
      api.updateConfig({ ollama_url: v }).catch(() => {});
    }, 800);
  }, []);

  const handleOllamaModelChange = useCallback((v: string) => {
    setOllamaModel(v);
    clearTimeout(ollamaModelTimer.current);
    ollamaModelTimer.current = setTimeout(() => {
      api.updateConfig({ ollama_model: v }).catch(() => {});
    }, 800);
  }, []);

  const handleTempChange = useCallback((agentName: string, delta: number) => {
    const current = temperatures[agentName] ?? 0.1;
    const newVal = Math.round(Math.max(0, Math.min(2, current + delta)) * 10) / 10;
    setTemperatures((prev) => ({ ...prev, [agentName]: newVal }));
    api.setTemperature(agentName, newVal).catch(() => {});
  }, [temperatures]);

  const handleAgentModelChange = useCallback((agentName: string, modelId: string) => {
    const val = modelId === "__inherit__" ? null : modelId;
    setAgentModels((prev) => ({ ...prev, [agentName]: val }));
    api.updateConfig({ agent_model: { agent_name: agentName, model: val } }).catch(() => {});
  }, []);

  const handleAutoPlan = useCallback((v: boolean) => {
    setAutoPlan(v);
    api.updateConfig({ auto_plan: v }).catch(() => {});
  }, []);

  const handlePlanConfirm = useCallback((v: boolean) => {
    setPlanConfirm(v);
    api.updateConfig({ plan_confirm: v }).catch(() => {});
  }, []);

  const handleAutoQc = useCallback((v: boolean) => {
    setAutoQc(v);
    api.updateConfig({ auto_qc: v }).catch(() => {});
  }, []);

  return (
    <div className="space-y-6">
      {/* Backend selection */}
      <div>
        <h3 className="mb-3 text-sm font-medium text-text-primary">API 后端</h3>
        <div className="space-y-3 rounded-lg border border-border-subtle bg-bg-tertiary p-4">
          {/* Aliyun */}
          <label className="flex cursor-pointer items-start gap-3">
            <input
              type="radio"
              name="backend"
              checked={backend === "aliyun"}
              onChange={() => handleBackendChange("aliyun")}
              className="mt-1"
            />
            <div className="flex-1">
              <div className="text-sm font-medium text-text-primary">
                阿里百炼 (DashScope)
              </div>
              {backend === "aliyun" && (
                <div className="mt-3 space-y-2">
                  <div>
                    <label className="mb-1 block text-xs text-text-muted">API Key</label>
                    <div className="flex gap-2">
                      <input
                        type={showKey ? "text" : "password"}
                        value={apiKey}
                        onChange={(e) => { setApiKey(e.target.value); setApiKeyDirty(true); }}
                        onBlur={handleApiKeyBlur}
                        placeholder="sk-xxxxxxxx"
                        className="flex-1 rounded-lg border border-border-subtle bg-bg-primary px-3 py-1.5 text-sm text-text-primary placeholder:text-text-muted"
                      />
                      <button
                        onClick={() => setShowKey(!showKey)}
                        className="rounded-lg bg-bg-primary px-2 text-text-muted hover:text-text-secondary"
                      >
                        {showKey ? <EyeOff size={14} /> : <Eye size={14} />}
                      </button>
                    </div>
                  </div>
                  <div>
                    <label className="mb-1 block text-xs text-text-muted">模型</label>
                    <select
                      value={model}
                      onChange={(e) => handleModelChange(e.target.value)}
                      className="w-full rounded-lg border border-border-subtle bg-bg-primary px-3 py-1.5 text-sm text-text-primary"
                    >
                      {MODELS.map((m) => <option key={m}>{m}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="mb-1 block text-xs text-text-muted">Base URL</label>
                    <input
                      type="text"
                      value={baseUrl}
                      onChange={(e) => handleBaseUrlChange(e.target.value)}
                      className="w-full rounded-lg border border-border-subtle bg-bg-primary px-3 py-1.5 text-sm text-text-primary"
                    />
                  </div>
                </div>
              )}
            </div>
          </label>

          {/* Ollama */}
          <label className="flex cursor-pointer items-start gap-3">
            <input
              type="radio"
              name="backend"
              checked={backend === "local"}
              onChange={() => handleBackendChange("local")}
              className="mt-1"
            />
            <div className="flex-1">
              <div className="text-sm font-medium text-text-primary">
                Ollama (本地)
              </div>
              {backend === "local" && (
                <div className="mt-3 space-y-2">
                  <div>
                    <label className="mb-1 block text-xs text-text-muted">URL</label>
                    <input
                      type="text"
                      value={ollamaUrl}
                      onChange={(e) => handleOllamaUrlChange(e.target.value)}
                      className="w-full rounded-lg border border-border-subtle bg-bg-primary px-3 py-1.5 text-sm text-text-primary"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-xs text-text-muted">模型</label>
                    <input
                      type="text"
                      value={ollamaModel}
                      onChange={(e) => handleOllamaModelChange(e.target.value)}
                      placeholder="e.g. qwen3:14b"
                      className="w-full rounded-lg border border-border-subtle bg-bg-primary px-3 py-1.5 text-sm text-text-primary placeholder:text-text-muted"
                    />
                  </div>
                </div>
              )}
            </div>
          </label>
        </div>
      </div>

      {/* Agent preferences */}
      <div>
        <h3 className="mb-3 text-sm font-medium text-text-primary">
          Agent 偏好配置
        </h3>
        <div className="space-y-3 rounded-lg border border-border-subtle bg-bg-tertiary p-4">
          {AGENTS.map((agent) => {
            const temp = temperatures[agent.id] ?? 0.1;
            const agentModelVal = agentModels[agent.id];
            return (
              <div key={agent.id} className="space-y-1">
                <div className="flex items-center gap-2">
                  <span className="w-20 shrink-0 text-sm text-text-secondary">{agent.label}</span>
                  <select
                    value={agentModelVal ?? "__inherit__"}
                    onChange={(e) => handleAgentModelChange(agent.id, e.target.value)}
                    className="flex-1 rounded-lg border border-border-subtle bg-bg-primary px-2 py-1 text-sm text-text-primary"
                  >
                    <option value="__inherit__">跟随全局</option>
                    {MODELS.map((m) => <option key={m} value={m}>{m}</option>)}
                  </select>
                  <div className="flex items-center gap-0.5">
                    <button
                      onClick={() => handleTempChange(agent.id, -0.1)}
                      className="rounded bg-bg-primary p-0.5 text-text-muted hover:bg-bg-hover hover:text-text-secondary"
                      title="降低 0.1"
                    >
                      <ChevronDown size={12} />
                    </button>
                    <span className="w-8 text-center font-mono text-xs text-text-primary">
                      {temp.toFixed(1)}
                    </span>
                    <button
                      onClick={() => handleTempChange(agent.id, 0.1)}
                      className="rounded bg-bg-primary p-0.5 text-text-muted hover:bg-bg-hover hover:text-text-secondary"
                      title="升高 0.1"
                    >
                      <ChevronUp size={12} />
                    </button>
                  </div>
                </div>
                <div className="ml-22 text-xs text-text-muted">
                  Temperature 推荐 {agent.recommendedTemp.toFixed(1)} — {agent.reason}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Auto plan & QC */}
      <div>
        <h3 className="mb-3 text-sm font-medium text-text-primary">
          自动规划与质检
        </h3>
        <div className="space-y-2">
          <label className="flex items-center gap-2 text-sm text-text-secondary">
            <input
              type="checkbox"
              checked={autoPlan}
              onChange={(e) => handleAutoPlan(e.target.checked)}
            />
            执行前自动生成计划
          </label>
          <label className="flex items-center gap-2 text-sm text-text-secondary">
            <input
              type="checkbox"
              checked={planConfirm}
              onChange={(e) => handlePlanConfirm(e.target.checked)}
            />
            计划需用户确认
          </label>
          <label className="flex items-center gap-2 text-sm text-text-secondary">
            <input
              type="checkbox"
              checked={autoQc}
              onChange={(e) => handleAutoQc(e.target.checked)}
            />
            执行后自动质检
          </label>
        </div>
      </div>
    </div>
  );
}
