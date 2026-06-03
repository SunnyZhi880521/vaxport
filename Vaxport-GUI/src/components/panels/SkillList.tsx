import { Wrench } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { useAppStore } from "../../stores/appStore";

interface SkillInfo {
  name: string;
  description: string;
  tier?: string;
}

const MOCK_SKILLS: SkillInfo[] = [
  {
    name: "vaccine_protocol",
    description: "兽用疫苗试验方案审核与设计",
    tier: "专业",
  },
  {
    name: "data_analysis",
    description: "数据分析通用技能",
    tier: "通用",
  },
  {
    name: "compliance_check",
    description: "GMP 合规检查",
    tier: "专业",
  },
  {
    name: "report_generation",
    description: "报告生成模板",
    tier: "通用",
  },
];

export function SkillList() {
  const { backendOnline } = useAppStore();
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (backendOnline) {
      loadSkills();
    } else {
      setSkills(MOCK_SKILLS);
    }
  }, [backendOnline]);

  const loadSkills = async () => {
    setLoading(true);
    try {
      await api.getSkills();
      setSkills(MOCK_SKILLS);
    } catch (err) {
      console.error("Failed to load skills:", err);
      setSkills(MOCK_SKILLS);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="text-sm text-text-secondary">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-xs text-text-muted">已加载的 SKILL</span>
        <span className="text-xs text-text-muted">{skills.length} 个</span>
      </div>

      {loading ? (
        <div className="py-4 text-center text-xs text-text-muted">加载中...</div>
      ) : (
        <div className="space-y-2">
          {skills.map((skill) => (
            <div
              key={skill.name}
              className="rounded-lg border border-border-subtle bg-bg-tertiary p-3"
            >
              <div className="mb-1 flex items-center gap-2">
                <Wrench size={12} className="text-accent-purple" />
                <span className="text-sm font-medium text-text-primary">
                  {skill.name}
                </span>
                {skill.tier && (
                  <span className="rounded bg-accent-purple/15 px-1.5 py-0.5 text-xs text-accent-purple">
                    {skill.tier}
                  </span>
                )}
              </div>
              <p className="text-xs text-text-muted">{skill.description}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
