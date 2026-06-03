import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { Activity, Database, MessageSquare, Route } from "lucide-react";

interface EARStats {
  trajectory: {
    total: number;
    success_rate: number;
    avg_duration: number;
    avg_tokens: number;
  };
  feedback: {
    total: number;
    explicit: number;
    satisfied: number;
    unsatisfied: number;
  };
  routing: Record<string, { count: number; success_rate: number }>;
}

interface SOPStatus {
  buffer_count: number;
  trigger_threshold: number;
  next_trigger_in: number;
  sop_count: number;
  avg_confidence: number;
}

export function EARPanel() {
  const [stats, setStats] = useState<EARStats | null>(null);
  const [sop, setSop] = useState<SOPStatus | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadStats();
    const timer = setInterval(loadStats, 10000);
    return () => clearInterval(timer);
  }, []);

  const loadStats = async () => {
    try {
      const [s, sop] = await Promise.all([api.getEARStats(), api.getSOPStatus()]);
      setStats(s as unknown as EARStats);
      setSop(sop as unknown as SOPStatus);
    } catch (err) {
      console.error("加载EAR统计失败:", err);
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return <div className="text-xs text-text-muted">加载中...</div>;
  }

  if (!stats) {
    return <div className="text-xs text-text-muted">暂无数据</div>;
  }

  return (
    <div className="space-y-4">
      {/* 轨迹统计 */}
      <section>
        <h3 className="mb-2 flex items-center gap-1.5 text-xs font-medium text-text-secondary">
          <Activity size={12} />
          任务轨迹
        </h3>
        <div className="space-y-1.5 rounded-lg bg-bg-tertiary/50 p-3 text-xs">
          <div className="flex justify-between">
            <span className="text-text-muted">总任务数</span>
            <span className="text-text-primary">{stats.trajectory.total}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-text-muted">成功率</span>
            <span className="text-text-primary">{(stats.trajectory.success_rate * 100).toFixed(0)}%</span>
          </div>
          <div className="flex justify-between">
            <span className="text-text-muted">平均耗时</span>
            <span className="text-text-primary">{stats.trajectory.avg_duration.toFixed(1)}s</span>
          </div>
          <div className="flex justify-between">
            <span className="text-text-muted">平均Token</span>
            <span className="text-text-primary">{stats.trajectory.avg_tokens.toFixed(0)}</span>
          </div>
        </div>
      </section>

      {/* SOP蒸馏状态 */}
      {sop && (
        <section>
          <h3 className="mb-2 flex items-center gap-1.5 text-xs font-medium text-text-secondary">
            <Database size={12} />
            SOP蒸馏
          </h3>
          <div className="space-y-1.5 rounded-lg bg-bg-tertiary/50 p-3 text-xs">
            <div className="flex justify-between">
              <span className="text-text-muted">已累积轨迹</span>
              <span className="text-text-primary">
                {sop.buffer_count}/{sop.trigger_threshold}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-text-muted">下次触发</span>
              <span className="text-text-primary">还差{sop.next_trigger_in}条</span>
            </div>
            <div className="flex justify-between">
              <span className="text-text-muted">已生成SOP</span>
              <span className="text-text-primary">{sop.sop_count}个</span>
            </div>
            <div className="flex justify-between">
              <span className="text-text-muted">平均置信度</span>
              <span className="text-text-primary">{(sop.avg_confidence * 100).toFixed(0)}%</span>
            </div>
            {/* 进度条 */}
            <div className="mt-2">
              <div className="h-1.5 w-full rounded-full bg-bg-primary">
                <div
                  className="h-1.5 rounded-full bg-accent-purple transition-all"
                  style={{ width: `${(sop.buffer_count / sop.trigger_threshold) * 100}%` }}
                />
              </div>
            </div>
          </div>
        </section>
      )}

      {/* 反馈统计 */}
      <section>
        <h3 className="mb-2 flex items-center gap-1.5 text-xs font-medium text-text-secondary">
          <MessageSquare size={12} />
          用户反馈
        </h3>
        <div className="space-y-1.5 rounded-lg bg-bg-tertiary/50 p-3 text-xs">
          <div className="flex justify-between">
            <span className="text-text-muted">总反馈数</span>
            <span className="text-text-primary">{stats.feedback.total}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-text-muted">显式反馈</span>
            <span className="text-text-primary">{stats.feedback.explicit}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-text-muted">满意</span>
            <span className="text-text-primary text-accent-green">{stats.feedback.satisfied}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-text-muted">不满意</span>
            <span className="text-text-primary text-accent-red">{stats.feedback.unsatisfied}</span>
          </div>
        </div>
      </section>

      {/* 路由统计 */}
      {Object.keys(stats.routing).length > 0 && (
        <section>
          <h3 className="mb-2 flex items-center gap-1.5 text-xs font-medium text-text-secondary">
            <Route size={12} />
            Agent路由
          </h3>
          <div className="space-y-1.5 rounded-lg bg-bg-tertiary/50 p-3 text-xs">
            {Object.entries(stats.routing).map(([agent, data]) => (
              <div key={agent} className="flex justify-between">
                <span className="text-text-muted">{agent}</span>
                <span className="text-text-primary">
                  {data.count}次 · {(data.success_rate * 100).toFixed(0)}%
                </span>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
