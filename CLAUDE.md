# vaxport — 项目级开发说明

疫苗企业本地 LLM 数据分析终端工具。用自然语言查询 PostgreSQL 数据库，支持 TUI + GUI 双界面。

## 架构概览

```
src/vaxport/
├── orchestrator.py    # 核心调度器：Agent ReAct 循环 + EAR 子系统
├── agent.py           # Agent 基类 + 4种专业 Agent
├── tools.py           # ToolRegistry：动态注册 + GuardRails 前置校验
├── skill_engine.py    # SKILL 程序性记忆（唯一入口，无 skills.py）
├── semantic_memory.py # Semantic Memory 语义召回（pgvector + embedding）
├── deep_research.py   # Deep Research 三阶段流水线
├── db.py              # Database 连接管理（只读 vlm_reader）
├── ear/               # EAR 子系统：guard_rails / sop_distiller / router_optimizer / skill_monitor
├── llm/               # LLM 客户端封装
├── tui/               # Textual TUI 界面（app.py + style.tcss）
└── api/               # FastAPI 后端（routes.py，供 GUI 调用）

Vaxport-GUI/           # Tauri + React GUI 前端
scripts/               # 数据生成、测试、构建脚本
docs/                  # 文档（demo-questions.md 等）
```

## 关键约束

- **Python 3.12+**，打包用 PyInstaller + Tauri sidecar
- **PostgreSQL 只读**：所有查询通过 `vlm_reader` 角色，禁止写入
- **LLM 提供商**：阿里百炼 DashScope（qwen3.7-max / qwen-max / deepseek-v4-pro）
- **SKILL 唯一源**：`SkillEngine`（`skill_engine.py`），不存在 `skills.py`
- **AgentType 键名**：`general / analyze_reporter / quality_supervision / document_search`（GUI 必须与后端一致）
- **GUI 用户名**：从后端 `/api/status` 的 `username` 字段获取（`getpass.getuser()`），不用 Tauri Shell

## 开发规范

- 修改数据结构前 `grep` 所有消费者，确保同步更新
- `tools.py` 的 `ToolRegistry` 接受 `SkillEngine` 类型（非旧的 `SkillRegistry`）
- TUI CSS 中每个 ID 必须有对应 widget，删除 widget 时同步删 CSS
- EAR 面板（TUI `#ear-panel`）高度固定 16 行，内部可滚动

## 打包流程

```bash
# 1. PyInstaller 打包 Python 后端
bash scripts/build-sidecar.sh

# 2. Tauri 打包 GUI（含 sidecar）
cd Vaxport-GUI && pnpm tauri build
```

详见 `scripts/build-sidecar.sh` 和 `Vaxport-GUI/src-tauri/tauri.conf.json`。
