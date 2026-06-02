"""SKILL 兼容层 — 三级模型兼容 Claude Code SKILL 生态"""

import re
from pathlib import Path
from typing import Optional


SKILLS_DIR = Path.home() / ".agents" / "skills"


class SkillInfo:
    """单个 SKILL 的信息"""

    def __init__(self, path: Path):
        self.path = path
        self.dir_name = path.parent.name
        self.name = ""
        self.description = ""
        self.has_python_scripts = False
        self.has_other_scripts = False
        self.python_scripts: list[Path] = []
        self.other_scripts: list[Path] = []
        self.md_content = ""
        self._loaded = False

    def load(self):
        """加载 SKILL.md 并解析 frontmatter"""
        if self._loaded:
            return

        # 支持 SKILL.md 或 skill.md
        skill_md = self.path
        if not skill_md.exists():
            skill_md_candidates = [
                self.path.parent / "skill.md",
                self.path.parent / "SKILL.md",
            ]
            for cand in skill_md_candidates:
                if cand.exists():
                    skill_md = cand
                    break

        if not skill_md.exists():
            self._loaded = True
            return

        content = skill_md.read_text(encoding="utf-8", errors="ignore")

        # 解析 YAML frontmatter
        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", content, re.DOTALL)
        if fm_match:
            frontmatter = fm_match.group(1)
            self.md_content = fm_match.group(2)

            # 简单 YAML 解析（不依赖 yaml 库，避免依赖问题）
            for line in frontmatter.split("\n"):
                line = line.strip()
                if line.startswith("name:"):
                    self.name = line[5:].strip().strip('"').strip("'")
                elif line.startswith("description:"):
                    desc = line[12:].strip().strip('"').strip("'")
                    self.description = desc
        else:
            self.md_content = content

        # 发现脚本
        scripts_dir = self.path.parent / "scripts"
        if scripts_dir.exists():
            for f in scripts_dir.iterdir():
                if f.suffix == ".py":
                    self.python_scripts.append(f)
                    self.has_python_scripts = True
                elif f.suffix in (".sh", ".js", ".ts", ".cjs", ".mjs"):
                    self.other_scripts.append(f)
                    self.has_other_scripts = True

        self._loaded = True

    @property
    def availability_badge(self) -> str:
        """可执行状态标记"""
        if self.has_python_scripts:
            return "🔧 可执行"
        if self.has_other_scripts:
            return "⚠️ 不可用"
        return "  📖 指令"

    @property
    def short_desc(self) -> str:
        """简短描述（用于列表显示）"""
        return self.description[:80] + "..." if len(self.description) > 80 else self.description


class SkillRegistry:
    """SKILL 注册表"""

    def __init__(self, skills_dir: Optional[Path] = None):
        self.skills_dir = skills_dir or SKILLS_DIR
        self._skills: dict[str, SkillInfo] = {}

    def load_all(self):
        """扫描并加载所有 SKILL"""
        if not self.skills_dir.exists():
            return

        for item in self.skills_dir.iterdir():
            if not item.is_dir():
                continue
            skill_md = item / "SKILL.md"
            if not skill_md.exists():
                skill_md = item / "skill.md"
            if skill_md.exists():
                skill = SkillInfo(skill_md)
                skill.load()
                self._skills[skill.dir_name] = skill

    def list_skills(self) -> list[SkillInfo]:
        """列出所有已加载的 SKILL"""
        return list(self._skills.values())

    def get_skill(self, name: str) -> Optional[SkillInfo]:
        """按名称获取 SKILL"""
        return self._skills.get(name)

    def get_executable_scripts(self) -> list[tuple[str, dict]]:
        """获取所有可执行 Python 脚本"""
        result = []
        for name, skill in self._skills.items():
            if skill.has_python_scripts:
                result.append((name, {
                    "name": skill.name or name,
                    "description": skill.description,
                    "python_scripts": skill.python_scripts,
                }))
        return result

    def build_system_prompt_section(self) -> str:
        """构建注入 system prompt 的 SKILL 描述部分"""
        if not self._skills:
            return ""

        lines = ["\n## 可用技能 (SKILL)"]
        for name, skill in sorted(self._skills.items()):
            skill_name = skill.name or name
            desc = skill.description or "无描述"
            lines.append(f"- **{skill_name}**: {desc}")
        lines.append("\n如需了解某个技能的详细信息，使用 get_skill_detail 工具。")
        return "\n".join(lines)

    def get_skill_detail(self, name: str) -> str:
        """获取 SKILL 的完整内容"""
        skill = self._skills.get(name)
        if not skill:
            return f"未找到技能: {name}"
        return skill.md_content or f"技能 {name} 无详细内容。"

    @property
    def count(self) -> int:
        return len(self._skills)