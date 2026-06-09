import { Wrench } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { useAppStore } from "../../stores/appStore";

interface SkillInfo {
  name: string;
  description: string;
  dir_name?: string;
  has_checklist?: boolean;
  keywords?: string[];
}

export function SkillList() {
  const { backendOnline } = useAppStore();
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (backendOnline) {
      loadSkills();
    } else {
      setSkills([]);
    }
  }, [backendOnline]);

  const loadSkills = async () => {
    setLoading(true);
    try {
      const response = await api.getSkills();
      if (response && response.skills) {
        setSkills(response.skills);
      } else {
        setSkills([]);
      }
    } catch (err) {
      console.error("Failed to load skills:", err);
      setSkills([]);
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
      ) : skills.length === 0 ? (
        <div className="py-4 text-center text-xs text-text-muted">暂无 SKILL</div>
      ) : (
        <div className="space-y-2">
          {skills.map((skill) => (
            <div
              key={skill.dir_name || skill.name}
              className="rounded-lg border border-border-subtle bg-bg-tertiary p-3"
            >
              <div className="mb-1 flex items-center gap-2">
                <Wrench size={12} className="text-accent-purple" />
                <span className="text-sm font-medium text-text-primary">
                  {skill.name}
                </span>
                {skill.has_checklist && (
                  <span className="rounded bg-accent-purple/15 px-1.5 py-0.5 text-xs text-accent-purple">
                    含检查清单
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
