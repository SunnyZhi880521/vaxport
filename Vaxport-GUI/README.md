# Vaxport

疫苗企业数据分析终端 - 桌面图形界面版本

基于 Tauri v2 + React + TypeScript 构建的跨平台桌面应用，用于疫苗企业本地 LLM 数据分析。

## ✨ 功能特性

### 核心功能
- **对话交互**: 支持 SSE 流式回答，实时显示 Agent 思考过程
- **多 Agent 系统**: 通用 Agent、分析报告、质量监督、文档检索、预警监控
- **计划确认**: 复杂任务自动生成执行计划，用户确认后再执行
- **工具调用可视化**: 实时显示数据库查询、分析工具调用过程

### 界面功能
- **三栏布局**: 左侧边栏 + 中央对话区 + 右侧面板
- **Schema 浏览器**: 树形展示数据库表结构，支持搜索过滤
- **图表预览**: 实时展示 Agent 生成的 matplotlib 图表（SSE chart 事件 → base64 PNG）
- **会话历史**: 侧边栏展示历史会话列表，支持恢复和删除
- **命令面板**: ⌘K 快速访问所有功能
- **设置页面**: 数据库、模型（按 Agent 独立 Temperature）、外观、快捷键完整配置
- **深色/浅色主题**: 支持 Tokyo Night 深色和浅色主题切换
- **Markdown 渲染**: 完整支持 GitHub Flavored Markdown

### 后端集成
- **自动启动**: 应用启动时自动检查并启动 FastAPI 后端
- **进程管理**: 应用退出时自动停止后端进程
- **状态监控**: 实时显示后端连接状态

### 快捷键
| 快捷键 | 功能 |
|--------|------|
| ⌘N / Ctrl+N | 新建对话 |
| ⌘, / Ctrl+, | 打开设置 |
| ⌘K / Ctrl+K | 命令面板 |
| ⌘B / Ctrl+B | 切换左侧边栏 |
| ⌘J / Ctrl+J | 切换右侧面板 |
| ⌘T / Ctrl+T | 切换规划/执行模式 |

## 🚀 开发

### 环境要求
- Node.js >= 18
- pnpm >= 9
- Rust >= 1.77.2
- Python 3.12+ (用于后端)

### 安装依赖

```bash
# 安装前端依赖
pnpm install

# 安装 Rust (如果未安装)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```

### 开发模式

```bash
# 启动开发服务器 (前端 + Tauri)
pnpm tauri dev
```

这会自动启动 Vite 开发服务器和 Tauri 窗口，支持热重载。

### 构建生产版本

```bash
# 构建 macOS 应用
pnpm tauri build
```

构建产物位于:
- `src-tauri/target/release/bundle/macos/Vaxport GUI.app`
- `src-tauri/target/release/bundle/dmg/Vaxport GUI_0.1.0_aarch64.dmg`

## 📦 项目结构

```
Vaxport-GUI/
├── src/                          # React 前端源码
│   ├── components/
│   │   ├── layout/              # 布局组件 (Sidebar, StatusBar, RightPanel)
│   │   ├── chat/                # 对话组件 (ChatArea, Message, PlanConfirm)
│   │   ├── input/               # 输入组件 (InputArea)
│   │   ├── panels/              # 面板组件 (SchemaBrowser, ChartPreview, SkillList)
│   │   ├── settings/            # 设置页面组件
│   │   └── CommandPalette.tsx   # 命令面板
│   ├── stores/                  # Zustand 状态管理
│   │   ├── appStore.ts          # 应用状态 (主题、面板、后端状态)
│   │   └── chatStore.ts         # 对话状态 (消息、流式数据)
│   ├── lib/                     # 工具库
│   │   ├── api.ts               # HTTP API 客户端
│   │   ├── sse.ts               # SSE 流式客户端
│   │   ├── backend.ts           # Tauri 后端管理
│   │   └── utils.ts             # 工具函数
│   ├── types/                   # TypeScript 类型定义
│   ├── App.tsx                  # 主应用组件
│   └── main.tsx                 # 入口文件
├── src-tauri/                   # Tauri Rust 后端
│   ├── src/
│   │   └── lib.rs               # 后端进程管理命令
│   └── tauri.conf.json          # Tauri 配置
├── public/                      # 静态资源
└── package.json
```

## 🔧 技术栈

- **前端框架**: React 19 + TypeScript
- **构建工具**: Vite
- **状态管理**: Zustand
- **样式**: TailwindCSS
- **桌面框架**: Tauri v2
- **后端**: Python FastAPI (复用 vaxport 现有代码)
- **通信协议**: HTTP REST + SSE (Server-Sent Events)

## 🔌 后端配置

应用会自动启动 FastAPI 后端:

```bash
python3 -m uvicorn vaxport.api.server:app --host 0.0.0.0 --port 8931
```

确保已安装 vaxport Python 包:

```bash
cd /path/to/vaxport
pip install -e .
```

后端配置文件: `~/.vaxport/config.yaml`

## 📝 开发说明

### 前端开发
- 使用 Vite 进行热重载开发
- 所有 UI 组件在 `src/components/` 目录
- 状态管理使用 Zustand，分为 appStore 和 chatStore

### 后端集成
- Tauri 通过 `invoke` 调用 Rust 命令管理后端进程
- 前端通过 HTTP/SSE 与 FastAPI 后端通信
- 支持后端自动启动和优雅退出

### 主题系统
- 使用 CSS 变量实现主题切换
- 深色主题: Tokyo Night 风格
- 浅色主题: 现代简约风格

## 🐛 已知问题

- 需要预先安装 vaxport Python 包
- 首次启动时后端可能需要几秒钟初始化

## 版本历史

### v1.3.0 (2026-06-03)

- **跨平台打包**: 支持 DMG (macOS) / EXE (Windows) / DEB (Linux) 一键安装
- **版本号同步**: 与 vaxport 后端统一版本号 1.3.0

### v1.2.0 (2026-06-03)

- **图表预览功能完善**: SSE chart 事件 → base64 PNG → 右侧面板实时展示
- **会话历史**: 侧边栏展示历史会话（支持恢复/删除），过滤 0 条消息无效会话
- **按 Agent 配置 Temperature**: ModelSettings 展示 5 个 Agent 独立 temperature 输入（上下箭头步进 0.1），附带推荐值说明
- **模型设置优化**: 取消全局 slider，改为 per-agent 数字输入

### v0.1.0 (2026-05-31)

- 初始版本：三栏布局、SSE 流式对话、Schema 浏览器、命令面板、主题切换

## 📄 许可证

MIT
