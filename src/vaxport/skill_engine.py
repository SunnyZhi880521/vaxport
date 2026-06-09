"""SKILL 引擎 — 匹配、加载、注入到 Agent system prompt"""

import re
import sys
from pathlib import Path
from typing import Optional

import yaml


if getattr(sys, "frozen", False):
    SKILLS_DIR = Path(sys._MEIPASS) / "vaxport" / "skills"
else:
    SKILLS_DIR = Path(__file__).parent / "skills"


class Skill:
    """单个 SKILL 的运行时表示"""

    def __init__(self, skill_dir: Path):
        self.dir = skill_dir
        self.dir_name = skill_dir.name
        self.name = skill_dir.name
        self.description = ""
        self.metadata: dict = {}
        self.content: str = ""
        self.checklist: dict = {}
        self._loaded = False

    def load(self):
        """加载 SKILL.md 和 checklist.yaml"""
        if self._loaded:
            return

        # 加载 skill.md
        skill_md = self.dir / "skill.md"
        if skill_md.exists():
            raw = skill_md.read_text(encoding="utf-8")
            # 解析 frontmatter
            fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)', raw, re.DOTALL)
            if fm_match:
                self.metadata = yaml.safe_load(fm_match.group(1)) or {}
                self.content = fm_match.group(2)
                self.name = self.metadata.get("name", self.dir_name)
                self.description = self.metadata.get("description", "")
            else:
                self.content = raw

        # 加载 checklist.yaml
        checklist_file = self.dir / "checklist.yaml"
        if checklist_file.exists():
            self.checklist = yaml.safe_load(
                checklist_file.read_text(encoding="utf-8")
            ) or {}

        self._loaded = True

    @property
    def short_desc(self) -> str:
        """简短描述（用于列表显示）"""
        return self.description[:80] + "..." if len(self.description) > 80 else self.description

    @property
    def availability_badge(self) -> str:
        """SKILL 状态标记"""
        if self.checklist:
            return "🔧 含检查清单"
        return "📋 领域指导"

    def matches(self, user_input: str) -> float:
        """计算用户输入与此 SKILL 的匹配度

        使用 any-match 策略：任意一个关键词命中即算匹配。
        返回值 = 命中关键词数 / 总关键词数（用于排序选最佳）。
        """
        self.load()
        keywords = self.metadata.get("keywords", [])
        if not keywords:
            return 0.0

        input_lower = user_input.lower()
        matched = sum(1 for kw in keywords if kw.lower() in input_lower)
        if matched == 0:
            return 0.0
        # 归一化但保证至少 1 个命中就有足够权重
        return max(matched / len(keywords), 0.2)

    def to_prompt_section(self) -> str:
        """将 SKILL 转换为 system prompt 片段"""
        self.load()
        return f"""
## 当前任务指导：{self.metadata.get('name', self.name)}

{self.content}

请按照以上框架完成分析。
"""


class SkillEngine:
    """SKILL 引擎 — 管理 SKILL 的发现和匹配"""

    def __init__(self, skills_dir: Optional[Path] = None):
        self.skills_dir = skills_dir or SKILLS_DIR
        self._skills: dict[str, Skill] = {}
        self._loaded = False

    def _discover(self):
        """扫描 skills 目录，发现所有 SKILL"""
        if self._loaded:
            return

        if not self.skills_dir.exists():
            self._loaded = True
            return

        for entry in self.skills_dir.iterdir():
            if entry.is_dir() and (entry / "skill.md").exists():
                skill = Skill(entry)
                self._skills[entry.name] = skill

        self._loaded = True

    def match_skill(self, user_input: str, threshold: float = 0.1) -> Optional[Skill]:
        """匹配最佳 SKILL

        Args:
            user_input: 用户输入文本
            threshold: 最低匹配度阈值

        Returns:
            最佳匹配的 SKILL，或 None
        """
        self._discover()

        best_skill = None
        best_score = 0.0

        for skill in self._skills.values():
            score = skill.matches(user_input)
            if score > best_score:
                best_score = score
                best_skill = skill

        if best_score >= threshold:
            best_skill.load()
            return best_skill

        return None

    def get_skill(self, name: str) -> Optional[Skill]:
        """按名称获取 SKILL"""
        self._discover()
        skill = self._skills.get(name)
        if skill:
            skill.load()
        return skill

    def load_all(self):
        """加载所有 SKILL（兼容 SkillRegistry 接口）"""
        self._discover()

    def list_skills(self) -> list[Skill]:
        """列出所有已加载的 SKILL"""
        self._discover()
        result = []
        for name, skill in self._skills.items():
            skill.load()
            result.append(skill)
        return result

    def get_skill_detail(self, name: str) -> str:
        """获取 SKILL 的完整内容"""
        skill = self._skills.get(name)
        if not skill:
            return f"未找到 SKILL: {name}"
        skill.load()
        return skill.content or f"SKILL {name} 无详细内容。"

    def build_system_prompt_section(self) -> str:
        """构建注入 system prompt 的 SKILL 描述部分"""
        self._discover()
        if not self._skills:
            return ""

        lines = ["\n## 可用领域技能 (SKILL)"]
        for name, skill in sorted(self._skills.items()):
            skill.load()
            skill_name = skill.name or name
            desc = skill.description or "无描述"
            keywords = ", ".join(skill.metadata.get("keywords", [])[:5])
            lines.append(f"- **{skill_name}**: {desc}")
            if keywords:
                lines.append(f"  关键词: {keywords}")
        lines.append("\n系统会根据用户问题自动匹配合适的 SKILL 并注入领域知识。")
        return "\n".join(lines)

    def get_executable_scripts(self) -> list[tuple[str, dict]]:
        """获取所有可执行 Python 脚本（兼容旧接口，vaxport SKILL 无可执行脚本）"""
        return []

    @property
    def count(self) -> int:
        self._discover()
        return len(self._skills)
