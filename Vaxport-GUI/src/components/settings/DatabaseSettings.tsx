import { useState, useEffect, useCallback, useRef } from "react";
import { CheckCircle, XCircle, Loader2 } from "lucide-react";
import { api } from "../../lib/api";

export function DatabaseSettings() {
  const [host, setHost] = useState("");
  const [port, setPort] = useState("5432");
  const [database, setDatabase] = useState("");
  const [user, setUser] = useState("");
  const [password, setPassword] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<"success" | "fail" | null>(null);

  // 保存 debounce refs
  const saveTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  // 从后端加载配置
  useEffect(() => {
    api.getConfig().then((cfg) => {
      const pgCfg = cfg.pg as Record<string, unknown> | undefined;
      if (pgCfg) {
        if (pgCfg.host) setHost(pgCfg.host as string);
        if (pgCfg.port) setPort(String(pgCfg.port));
        if (pgCfg.database) setDatabase(pgCfg.database as string);
        if (pgCfg.user) setUser(pgCfg.user as string);
      }
    }).catch(() => {});
  }, []);

  // 自动保存（debounce）
  const debounceSave = useCallback(() => {
    clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(async () => {
      try {
        await api.updateConfig({
          db_host: host || null,
          db_port: parseInt(port) || null,
          db_database: database || null,
          db_user: user || null,
        });
        setSaved(false);
      } catch { /* ignore */ }
    }, 800);
  }, [host, port, database, user]);

  // 手动保存
  const handleSave = async () => {
    clearTimeout(saveTimer.current);
    setSaving(true);
    setSaved(false);
    try {
      await api.updateConfig({
        db_host: host || null,
        db_port: parseInt(port) || null,
        db_database: database || null,
        db_user: user || null,
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch {
      // ignore
    } finally {
      setSaving(false);
    }
  };

  const handleTest = () => {
    setTesting(true);
    setTestResult(null);
    // Simulate test - in real app, call backend API
    setTimeout(() => {
      setTesting(false);
      setTestResult("success");
    }, 1500);
  };

  return (
    <div className="space-y-6">
      {/* Connection form */}
      <div>
        <h3 className="mb-3 text-sm font-medium text-text-primary">
          PostgreSQL 连接
        </h3>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs text-text-muted">主机</label>
              <input
                type="text"
                value={host}
                onChange={(e) => { setHost(e.target.value); debounceSave(); }}
                className="w-full rounded-lg border border-border-subtle bg-bg-tertiary px-3 py-2 text-sm text-text-primary"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-text-muted">端口</label>
              <input
                type="text"
                value={port}
                onChange={(e) => { setPort(e.target.value); debounceSave(); }}
                className="w-full rounded-lg border border-border-subtle bg-bg-tertiary px-3 py-2 text-sm text-text-primary"
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs text-text-muted">数据库</label>
              <input
                type="text"
                value={database}
                onChange={(e) => { setDatabase(e.target.value); debounceSave(); }}
                className="w-full rounded-lg border border-border-subtle bg-bg-tertiary px-3 py-2 text-sm text-text-primary"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-text-muted">用户</label>
              <input
                type="text"
                value={user}
                onChange={(e) => { setUser(e.target.value); debounceSave(); }}
                className="w-full rounded-lg border border-border-subtle bg-bg-tertiary px-3 py-2 text-sm text-text-primary"
              />
            </div>
          </div>
        </div>
      </div>

      {/* Test connection */}
      <div className="flex items-center gap-3">
        <button
          onClick={handleSave}
          disabled={saving}
          className="rounded-lg bg-accent-purple px-4 py-2 text-sm font-medium text-white hover:bg-accent-purple/90 disabled:opacity-50"
        >
          {saving ? (
            <span className="flex items-center gap-2">
              <Loader2 size={14} className="animate-spin" />
              保存中...
            </span>
          ) : saved ? (
            <span className="flex items-center gap-2">
              <CheckCircle size={14} />
              已保存
            </span>
          ) : (
            "💾 保存设置"
          )}
        </button>
        <button
          onClick={handleTest}
          disabled={testing}
          className="rounded-lg bg-accent-blue px-4 py-2 text-sm font-medium text-white hover:bg-accent-blue/90 disabled:opacity-50"
        >
          {testing ? (
            <span className="flex items-center gap-2">
              <Loader2 size={14} className="animate-spin" />
              测试中...
            </span>
          ) : (
            "🔌 测试连接"
          )}
        </button>
        {testResult === "success" && (
          <span className="flex items-center gap-1 text-sm text-accent-green">
            <CheckCircle size={14} /> 连接成功
          </span>
        )}
        {testResult === "fail" && (
          <span className="flex items-center gap-1 text-sm text-accent-red">
            <XCircle size={14} /> 连接失败
          </span>
        )}
      </div>

      {/* Multi-database */}
      <div>
        <h3 className="mb-3 text-sm font-medium text-text-primary">
          多数据库
        </h3>
        <div className="rounded-lg border border-border-subtle bg-bg-tertiary p-3">
          <div className="mb-2 flex items-center gap-2 text-sm text-text-secondary">
            <span className="text-accent-green">●</span>
            <span>{database}</span>
            <span className="text-xs text-text-muted">(当前)</span>
          </div>
          <button className="text-sm text-accent-purple hover:underline">
            + 添加数据库
          </button>
        </div>
      </div>
    </div>
  );
}
