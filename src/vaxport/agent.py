"""ReAct Agent 引擎 — Think → Act → Observe 循环 + 自动上下文压缩"""

import json
import os
import re
import signal
import subprocess
import threading
import time
from typing import Optional

from vaxport.context import (
    COMPRESS_THRESHOLD,
    MAX_ROUNDS,
    count_tokens,
    get_context_window,
    trim_context,
    truncate_tool_result,
)
from vaxport.ear import GuardRails, StepRecord
from vaxport.llm import LLMClient
from vaxport.tools import ToolRegistry


def _levenshtein_distance(s1: str, s2: str) -> int:
    """计算两个字符串的编辑距离（Levenshtein distance）"""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)

    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


def _string_similarity(s1: str, s2: str) -> float:
    """计算两个字符串的相似度（0.0~1.0），1.0 表示完全相同"""
    if not isinstance(s1, str) or not isinstance(s2, str):
        return 0.0
    if s1 == s2:
        return 1.0
    if not s1 or not s2:
        return 0.0

    max_len = max(len(s1), len(s2))
    distance = _levenshtein_distance(s1, s2)
    return 1.0 - (distance / max_len)


def _value_similarity(v1, v2) -> float:
    """计算两个值的相似度（0.0~1.0），支持字符串和数值"""
    if v1 == v2:
        return 1.0

    # 字符串比较
    if isinstance(v1, str) and isinstance(v2, str):
        return _string_similarity(v1, v2)

    # 数值比较
    if isinstance(v1, (int, float)) and isinstance(v2, (int, float)):
        if v1 == 0 and v2 == 0:
            return 1.0
        if v1 == 0 or v2 == 0:
            return 0.0
        # 相对差：|v1 - v2| / max(|v1|, |v2|)
        relative_diff = abs(v1 - v2) / max(abs(v1), abs(v2))
        return 1.0 - relative_diff

    # 其他类型不匹配
    return 0.0


def _args_similarity(args1: dict, args2: dict) -> float:
    """计算两个参数集的相似度（0.0~1.0）"""
    if not isinstance(args1, dict) or not isinstance(args2, dict):
        return 0.0

    keys1 = set(args1.keys())
    keys2 = set(args2.keys())

    if keys1 != keys2:
        return 0.0  # 参数键不同，认为不相似

    if not keys1:
        return 1.0  # 都为空

    similarities = []
    for key in keys1:
        sim = _value_similarity(args1.get(key), args2.get(key))
        similarities.append(sim)

    return sum(similarities) / len(similarities)


def _is_diverse_batch(tool_name: str, recent_calls: list[tuple[str, dict]], threshold: float = 0.6) -> bool:
    """判断近期调用是否为多样化的批处理（如对比多个菌株）

    返回 True 表示参数值多样化，应视为合法批处理，不应触发死循环检测
    """
    # 提取同工具的参数值
    same_tool_calls = [args for name, args in recent_calls if name == tool_name]

    if len(same_tool_calls) < 2:
        return False

    # 计算所有两两调用的平均相似度
    total_sim = 0.0
    count = 0
    for i in range(len(same_tool_calls)):
        for j in range(i + 1, len(same_tool_calls)):
            sim = _args_similarity(same_tool_calls[i], same_tool_calls[j])
            total_sim += sim
            count += 1

    if count == 0:
        return False

    avg_similarity = total_sim / count

    # 如果平均相似度低于阈值，说明参数值多样化
    return avg_similarity < threshold


class ProgressCallbacks:
    """Agent 执行进度回调 — 供 CLI 层注入 UI 反馈"""

    def __init__(self):
        self.plan_feedback = ""  # 计划确认时用户补充的决策反馈

    def get_pending_feedback(self) -> str | None:
        """返回待处理的用户交互消息（追问），消费后清空。子类覆盖。"""
        return None

    def on_thinking(self, description: str = ""):
        pass

    def on_tool_call(self, tool_name: str, arguments: dict):
        pass

    def on_tool_result(self, row_count: int, truncated: bool = False):
        pass

    def on_sql(self, sql: str):
        pass

    def on_thinking_text(self, text: str):
        """ReAct 中间回合的思考文本（非流式，整个思考段一次性交付）"""
        pass

    def on_thinking_chunk(self, text: str):
        """ReAct 思考文本流式块 — 直接进入 thinking widget"""
        pass

    def mark_answer_start(self):
        """标记当前累积位置为最终答案起点（排除前面的思考文本）"""
        pass

    def clear_thinking(self):
        """清空思考过程 widget（最终答案已转入 answer widget）"""
        pass

    def on_text_chunk(self, text: str):
        pass

    def on_plan_chunk(self, text: str):
        """规划阶段流式文本块"""
        pass

    def on_chart(self, file_path: str):
        """图表生成完成，传递文件路径"""
        pass

    def on_plan(self, plan_text: str) -> bool:
        """PRE-HOOK: 返回 True 继续执行，False 取消。
        默认自动确认，子类可覆盖实现用户交互。"""
        return True


class AgentLoopState:
    """Agent 循环状态追踪"""

    def __init__(self):
        self.turns = 0
        self.tool_call_signatures: list[set] = []
        self.last_tool_names: set = set()
        self.param_retry_count: dict = {}
        self._last_tool_call_sig: tuple = None
        self._consecutive_same_count: int = 0
        self.compression_count = 0  # 压缩次数
        self._tool_result_cache: dict[str, str] = {}  # (tool_name, args_json) → result
        self._all_tool_calls_summary: list[dict] = []  # 已调用工具摘要列表
        self._recent_tool_params: list[tuple[str, dict]] = []  # [(tool_name, parsed_args), ...] 最近 N 次调用


PLAN_PROMPT = """## 规划阶段（不可调用工具）

你是任务规划专家。请严格按以下模板分析用户问题并输出执行计划。

**重要原则**：
1. 当存在多种分析方案或参数选择时，直接选择最优方案执行，无需等待用户确认。选择依据：
   - 数据完整性：优先选择能覆盖完整数据的方案
   - 分析深度：优先选择能提供更深入洞察的方案
   - 可视化效果：优先选择最直观清晰的展示方式
2. **禁止输出确认语句**：不要在规划末尾添加"确认以上计划后，我将立即执行"、"请确认后开始执行"等任何确认提示语。规划输出后立即自动执行。

### 一、任务理解
[用一句话概括用户需求]

### 二、数据需求
| 序号 | 表名(schema.table) | 查询条件 | 目的 |
|------|-------------------|---------|------|
| 1 | ... | WHERE ... | 获取... |

### 三、执行步骤
| 步骤 | 操作类型 | 工具名称 | 关键参数 | 预期产出 |
|------|---------|---------|---------|---------|
| 1 | 查询 | query_xxx | date=2024 | 50行 |
| 2 | 统计 | run_statistics | basic_stats | 均值/标准差 |

### 四、输出章节（根据任务复杂度确定章节数量，从"一"开始连续编号，简单任务 2-3 章，复杂分析 5-7 章或更多）
- ## 一、[标题]
- ## 二、[标题]
- （根据实际需要继续增加章节，不要限制数量）

### 五、可视化需求
| 图表类型 | 数据来源 | 用途 |
|---------|---------|------|
| ... 或 "无需图表"

### 六、风险点
- [可能遇到的问题及应对]

## 对话连续性判断（最优先，在输出计划前判断）

如果上下文中有上一轮对话，先判断当前问题是否属于追问/补充：

**回复 SKIP_PLAN（仅这三个字母，不输出其他内容）**：
- 用户要求补充/追加上一轮报告的某个方面（"补充XX""加上XX""还有XX"）
- 用户要求修正/更正上一轮回答中的错误
- 用户针对上一轮回答进行追问/澄清（"XX数据再详细说说"）
- 用户说"继续""然后呢""还有呢"
- 用户问"为什么XX""怎么没有XX"等基于上一轮结果的追问

**正常输出计划**：
- 全新话题、全新产品、全新时间范围
- 与上一轮无关联的独立分析需求

现在开始规划用户的问题。"""

REVIEW_PROMPT = """## 审核阶段（不可调用工具）

请对照以下清单检查刚生成的答案，逐项确认：

### 结构检查
- [ ] 输出章节是否与规划阶段一致？遗漏的请补全
- [ ] 章节编号是否从"一"开始连续？

### 内容检查
- [ ] 用户问题的每一项要求是否都有对应回答？
- [ ] 所有比较维度（年份/产品/指标）是否都已覆盖？
- [ ] **零值检查**：如果用户要求"按月统计"或按其他维度分组统计，答案是否覆盖了全部时间段/分组（包括计数为 0 的项）？零也是数据，不能跳过
- [ ] **建议用户自查检测（关键）**：答案中是否包含"建议查询更多数据""建议用户补充""需X月数据验证""可进一步查询"等让用户自行获取数据的表述？如有，标记为 🔴 严重缺陷——Agent 应自己获取而非建议用户操作

### 图表检查（关键）
- [ ] 如答案中包含图表，图表描述/标题是否覆盖了用户要求的所有分组维度？
- [ ] 图表图例/坐标轴标签是否包含具体含义（产品名/时间/指标名），而非仅通用代号（A/B/C）？
- [ ] 如用户要求"各产品对比"，图表中必须出现产品名称作为分组，而非仅班组/批次等二级维度
- [ ] Markdown 图片引用 ![标题](file_path) 中的 file_path 是否为实际文件路径（非占位符 "file_path"）？
- [ ] 🔴 **路径格式检查**：图片路径是否与 generate_chart 工具返回的 file_path 完全一致（逐字符）？检查是否有格式转换（如 Unix↔Windows 路径互转）、路径截断、添加/删除前缀等。如有任何差异，标记为 🔴 严重缺陷
- [ ] 🔴 **图片跳过检查**：生成了图表（调用了 generate_chart）但答案中未用 `![标题](path)` 嵌入，而是用了"请在 Finder 中打开""终端不支持图片""用 ASCII 替代""手动查看"等表述？如有，标记为 🔴 严重缺陷——vaxport TUI 支持 Markdown 内联图片，必须嵌入而非让用户手动打开

### 数据检查
- [ ] 引用的数值与查询结果是否一致？
- [ ] 是否有编造或推测的数据？

### 输出格式
- 如全部通过，回复 "✅ 审核通过"
- 如有未通过项，列出问题清单（每行一个），格式: "- 🔴 [类别] 问题描述"
- **禁止输出修正后的答案，只输出问题清单！**

原始答案：
---
{answer}
---"""

FIX_PROMPT = """## 自动修复阶段（可调用工具）

审核发现了以下问题，请使用工具逐一修复：

{qc_findings}

修复规则：
0. **数据不完整/零值缺失（最高优先级）**：如审核指出答案缺少零值月份/分组，或包含"建议查询更多数据"等让用户自行获取数据的表述，必须：
   - 先调用 query_database 执行无 WHERE 过滤的 GROUP BY 验证查询，获取完整时间段/分组覆盖范围
   - 将过滤查询结果与完整时间段合并，缺失月份补零
   - 如有图表，用包含零值的完整数据重新调用 generate_chart（趋势图必须覆盖所有月份，零值月份也要在 X 轴上显示）
   - 删除答案中所有"建议查询更多数据""建议用户补充""需更多数据验证"等表述，改为实际数据结论
1. **图表路径问题（最高优先级）**：如 Markdown 图片引用 ![...](file_path) 中的路径与 generate_chart 返回的 file_path 不一致（包括时间戳不同、路径截断、添加/删除前缀等任何差异），必须：
   - 找到 generate_chart 工具调用的返回结果，获取真实的 file_path
   - 用真实路径替换错误路径，**逐字符完全一致**
   - **禁止脑补路径**：如果找不到原始返回结果，重新调用 generate_chart 生成图表
   - 修改后再次逐字符对比，确认路径与工具返回的 file_path 完全一致
2. **图表信息不全**：如审核指出图表缺少某个分组维度（如缺少产品名称、年份标签等），必须：
   - 先调用 query_database 重新查询包含该维度的完整数据
   - 再调用 generate_chart 重新生成，确保图例/坐标轴包含所有要求的维度
   - 用新图表替换旧图表引用
3. **图片跳过/未嵌入/ASCII替代（新增）**：如审核指出已生成图表但答案中未用 `![标题](path)` 嵌入，或使用 ASCII 字符画/Unicode 框图/代码块模拟图表替代了真实图片，必须：
   - 重新调用 generate_chart 生成图表（参数与之前相同，或根据上下文推断）
   - 用 `![标题](file_path)` 语法嵌入到答案中（file_path 为工具返回的路径，逐字符复制）
   - 删除所有 ASCII 字符画、Unicode 框图、代码块模拟等替代品
   - 删除所有"手动打开""Finder 查看""终端限制"等表述
4. 保持原有答案结构、章节标题和文字内容完全不变，**只修复具体问题，不要修改其他内容**
5. 修复完成后，输出完整的修正后答案（从一级标题开始，包含所有原有章节）
6. 不要增删章节，不要改写已有内容，只做问题修复

原始答案（需要修复）：
---
{answer}
---"""

SQL_GEN_PROMPT = """## SQL 查询生成

根据上述执行计划中的"数据需求"表，生成所有需要的 SQL SELECT 查询。

数据库概况（表名/列名）已包含在上文 System Prompt 中。

输出格式（严格 JSON，不要任何其他文字）:
```json
{
  "queries": [
    {"sql": "SELECT ... FROM schema.table WHERE ... GROUP BY ...", "purpose": "各产品月度效价均值"},
    {"sql": "SELECT ...", "purpose": "仓储湿度月度统计"}
  ]
}
```

规则:
- 只生成 SELECT 语句，禁止 INSERT/UPDATE/DELETE/DROP/TRUNCATE
- 不需要 LIMIT（系统自动限制 5000 行）
- 列名和表名必须来自数据库概况中已有的信息，不要编造
- 查询之间有依赖时标注顺序（靠前的先执行）
- 覆盖计划中列出的所有数据需求
- **GROUP BY 必须包含零值**：如果按月份/产品等维度分组统计，禁止只用 WHERE 过滤条件导致零值分组消失。必须包含一条无 WHERE 过滤的 COUNT(*) GROUP BY 查询来获取完整时间段/分组覆盖，或将过滤条件放入 COUNT 内部（如 `COUNT(*) FILTER (WHERE alarm_flag)`），确保所有分组都出现在结果中"""

ANALYSIS_PROMPT = """## 分析阶段

数据采集完成。现在基于以上数据进行分析，按执行计划中的输出章节组织答案。

**可视化要求**：如果分析涉及以下场景，必须调用 generate_chart：
- 热力图/空间分布/相关性矩阵 → chart_type="heatmap"
- 趋势变化/时间序列 → chart_type="trend"
- 多组对比 → chart_type="comparison"（**每组有多个柱子时，必须提供 bar_labels 标明每个柱子代表什么指标**，如 "bar_labels": ["pH值","抗原含量","内毒素"]）
- 帕累托分析 → chart_type="pareto"
- 控制图/质量监控 → chart_type="control"
- 排名/评分/综合对比 → chart_type="comparison"（将各产品得分作为 groups 传入）
- 二维分布/优先级矩阵 → chart_type="heatmap" 或 chart_type="comparison"

调用后图片保存到本地，**必须使用 generate_chart 返回结果中的 file_path 原样引用**。在 Markdown 中用 ![标题](file_path) 引用。

🔴 **路径精确性铁律（最高优先级）**：
- generate_chart 返回的 file_path 是一个完整的文件路径字符串，**必须逐字符原样复制**到 ![标题](file_path) 中
- **禁止**对路径做任何修改：禁止添加/删除前缀、禁止格式转换、禁止改写任何字符、**禁止脑补或猜测路径**
- 路径中的时间戳（如 1780470752327）是工具自动生成的，**绝不允许修改或替换**
- 如果你不确定路径是什么，回头看 generate_chart 的返回结果，不要自己编

🔴 **图表生成铁律**：
- vaxport TUI 支持 Markdown 内联图片显示，`![标题](path)` 会直接在对话区渲染图片
- **绝对禁止**用以下任何方式替代 generate_chart 调用：ASCII 字符画、Unicode 框图、代码块模拟图表、CSS/HTML 图表、纯文字描述代替图表
- 任何需要可视化呈现的数据（排名对比、趋势变化、分布关系、矩阵定位），都必须调用 generate_chart 生成 PNG 图片
- **不要自己画图**——你的任务是调用工具生成图片，然后用 ![](path) 嵌入

**数据完整性第一原则**：
- 零是有效数据：如果某些月份/分组没有符合过滤条件的记录，必须在答案中明确标注"X月: 0次"，不能跳过这些月份
- 如果 GROUP BY 查询只返回了少数月份，必须先执行一条无 WHERE 过滤的 COUNT(*) GROUP BY 验证查询，确认数据覆盖的所有时间段
- 将验证查询的完整时间段与过滤查询的结果合并，缺失月份补零，确保答案覆盖全部时间段
- **禁止**在答案中使用"建议查询更多数据""需补充X月数据""需更多数据验证"等让用户自行获取数据的表述。你需要的数据，自己直接查，不要建议用户去做

**对比分析输出规范**：
- 涉及多个对象（菌株/产品/批次/供应商等）对比时，**必须使用 Markdown 表格**呈现数据，禁止只用纯文字叙述
- 表格中必须包含**排名列**（如"排名"、"排序"）或按关键指标从高到低/从低到高排序，让读者一眼看出最优/最差
- 每个对比维度（如产量、质量、稳定性）各一张表 + 一张图，图表结合
- 最终结论章节使用"综合排名表"汇总所有维度，给出综合排序

可以继续调用图表生成、统计分析等工具完成分析任务。

**追问合并规则**：如果当前处于追问/补充模式，输出必须是完整的融合报告，将新分析插入原报告章节结构中，而非仅输出补充章节。回答开头简要说明补充了什么内容，然后输出完整报告。"""

FOLLOWUP_MODE_PROMPT = """## 当前模式：追问补充

用户正在对上一轮回答进行追问/补充。重要规则：

1. **工具调用**：根据实际需要决定是否调用工具查询数据。如果需要新数据才能回答，就调用工具；如果已有上下文足够，就直接回复。不强制也不禁止。
2. **输出完整性铁律**：最终输出必须是融合后的**完整报告**。将新增内容插入到原报告的适当位置，保持章节编号连续、结构完整。
3. 禁止只输出新增片段——用户应得到一份自包含的完整答案，不需要回看上一轮就能理解全部内容。
4. 回答开头简要说明补充了什么（如"已将运输温度影响分析补充至原报告第二章"），然后输出完整报告。
5. **自然对话**：如果不确定用户意图或需要澄清，可以直接反问。不需要输出完整报告格式——像正常对话一样提问即可。
6. 可以引用上一轮回答中的数据，但最终输出必须独立完整。"""


class Agent:
    """ReAct Agent，处理自然语言 → SQL 查询 → 分析结论"""

    SYSTEM_PROMPT = """你是疫苗企业的质量分析助手，运行在 vaxport Agent 终端工具中。

你的核心能力：
1. 理解用户用中文提出的数据分析需求
2. 调用数据库查询工具获取数据
3. 基于返回的数据进行分析，给出专业结论

输出格式规则：
- 分析结果用中文输出，简洁专业
- 数据对比/列表使用 Markdown 表格展示（| 列1 | 列2 |）
- 关键数值使用 **粗体** 突出
- 结构化内容使用 ### 标题分层
- 趋势/模式使用 - 无序列表总结
- 查询工具返回的 JSON 中包含 rows 数组和 row_count
- 如果查询返回空结果 (row_count=0)，告知用户未找到匹配数据
- **数据完整性铁律**：零是有效数据，分组统计时零值项必须展示；你需要的额外数据自己直接查，禁止建议用户"查询更多数据""补充数据"
- **对话连续性**：你运行在对话式终端中，用户可能连续提问、追问、补充。将每次交互视为持续对话的一部分，而非孤立的问题
- **输出完整性**：如果用户的追问/补充针对上一轮回答，最终输出必须是融合后的完整报告，而非仅新增片段。用户应得到自包含的完整答案
- 无需在回答中重复完整的表格数据，概括关键发现即可"""

    COMPRESSION_PROMPT = """请将以下对话历史压缩为一段简洁的上下文摘要（200 字以内），保留关键信息：

- 用户的核心问题和需求
- 已执行过的重要查询（表名、条件）
- 已获得的关键数据发现和结论
- 当前分析进展到哪一步

只输出摘要文本，不要加标题或格式。"""

    def __init__(self, llm_client: LLMClient, tool_registry: ToolRegistry,
                 max_rounds: int = MAX_ROUNDS,
                 total_timeout: int = 0, system_prompt: str = None,
                 tool_filter: list[str] = None,
                 auto_plan: bool = True, plan_confirm: bool = True,
                 auto_review: bool = True, preferred_model: str | None = None,
                 temperature: float = 0.1):
        self.llm = llm_client
        self.tools = tool_registry
        self.max_rounds = max_rounds
        self.total_timeout = total_timeout
        self.temperature = temperature
        self.debug_mode = False
        self._system_prompt = system_prompt if system_prompt else self.SYSTEM_PROMPT
        self._tool_filter = tool_filter
        self._auto_plan = auto_plan
        self._plan_confirm = plan_confirm
        self._auto_review = auto_review
        self.preferred_model = preferred_model  # None = 继承全局默认
        self._memory_context: str = ""  # 跨会话反馈记忆，由外部注入
        self._session_tables: set[str] = set()  # 本会话查询过的表
        self._trajectory_history: list[StepRecord] = []  # EAR轨迹历史

    def set_memory_context(self, memory_text: str):
        """注入跨会话反馈记忆到 system prompt"""
        self._memory_context = memory_text

    def set_skills_context(self, skills_text: str):
        """注入 SKILL 信息到 system prompt"""
        self._system_prompt = self.SYSTEM_PROMPT + "\n\n" + skills_text

    def set_db_context(self, db_text: str):
        """注入数据库表概况到 system prompt（追加方式，兼容已有 skills）"""
        self._system_prompt = self._system_prompt + "\n\n" + db_text

    @staticmethod
    def detect_handoff(text: str) -> Optional[dict]:
        """检测文本中的 Handoff 信号。

        格式: [HANDOFF:target_agent]context[/HANDOFF]

        Returns:
            {"target": "analyze_reporter", "context": "Cpk < 1.0, need analysis and report"} 或 None
        """
        if not text:
            return None
        m = re.search(r'\[HANDOFF:(\w+)\](.*?)\[/HANDOFF\]', text, re.DOTALL)
        if m:
            return {"target": m.group(1), "context": m.group(2).strip()}
        return None

    def _get_context_window(self) -> int:
        """获取当前模型的上下文窗口（优先从 LLM 客户端动态获取）"""
        model = self.preferred_model or self.llm.active_model
        dynamic = self.llm.get_model_max_tokens(model)
        if dynamic > 0:
            return dynamic
        return get_context_window(model)

    def _compress_history(self, messages: list) -> list:
        """压缩对话历史：用 LLM 总结旧消息，替换为上下文摘要

        保留：system prompt + 压缩后的摘要 + 最近 3 轮交互
        """
        context_window = self._get_context_window()

        # 分离 system prompt
        if messages and messages[0]["role"] == "system":
            system_msg = messages[0]
            rest = messages[1:]
        else:
            system_msg = None
            rest = list(messages)

        # 找到最近 3 轮 user 消息的起始位置
        user_indices = [i for i, m in enumerate(rest) if m["role"] == "user"]
        if len(user_indices) <= 3:
            return messages  # 不需要压缩

        keep_from = user_indices[-4] + 1  # 保留最近 3 轮
        old_messages = rest[:keep_from]
        recent_messages = rest[keep_from:]

        # 调用 LLM 生成摘要
        summary_text = ""
        try:
            compress_messages = [
                {"role": "system", "content": self.COMPRESSION_PROMPT},
                {"role": "user", "content": json.dumps(
                    [{"role": m["role"], "content": str(m.get("content", ""))[:500]}
                     for m in old_messages if m["role"] in ("user", "assistant")
                    ], ensure_ascii=False
                )},
            ]
            resp = self.llm.chat_completion(
                messages=compress_messages, tools=None, stream=False,
                model=self.preferred_model, temperature=self.temperature,
            )
            summary_text = resp.choices[0].message.content or ""
            self.llm.record_success()
        except Exception:
            self.llm.record_failure()
            summary_text = f"(对话历史压缩失败，保留了最近 {len(recent_messages)} 条消息)"

        # 提取降级标注（在压缩中保护，不被裁剪丢失）
        degradation_msgs = []
        for msg in old_messages + recent_messages:
            if msg.get("role") == "system" and "【系统强制警告" in str(msg.get("content", "")):
                degradation_msgs.append(msg)

        # 重建消息列表
        rebuilt = []
        if system_msg:
            rebuilt.append(system_msg)
        # 降级标注紧接 system prompt，确保不被后续压缩影响
        for dm in degradation_msgs:
            rebuilt.append(dm)
        rebuilt.append({
            "role": "system",
            "content": f"📋 对话历史摘要（第 {self._state.compression_count + 1} 次压缩）:\n{summary_text}",
        })
        rebuilt.extend(recent_messages)

        return rebuilt

    def _generate_plan(self, user_query: str, history: list[dict] | None = None,
                       callbacks: ProgressCallbacks | None = None) -> str:
        """PRE-HOOK: 强制 LLM 生成结构化执行计划（不可调工具）。

        传入对话历史以便判断是否为追问/补充。
        """
        plan_messages = [
            {"role": "system", "content": PLAN_PROMPT},
        ]
        # 注入最近几轮对话，让 LLM 判断上下文连续性
        if history:
            for h in history[-6:]:  # 最近 3 轮交互
                if h.get("role") in ("user", "assistant"):
                    plan_messages.append({
                        "role": h["role"],
                        "content": str(h.get("content", ""))[:500],
                    })
        plan_messages.append({"role": "user", "content": user_query})
        try:
            stream = self.llm.chat_completion(
                messages=plan_messages, tools=None, stream=True,
                model=self.preferred_model, temperature=self.temperature,
            )
            plan_parts: list[str] = []
            for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    plan_parts.append(delta.content)
                    if callbacks:
                        callbacks.on_plan_chunk(delta.content)
            self.llm.record_success()
            return "".join(plan_parts)
        except Exception:
            self.llm.record_failure()
            return ""

    def _run_review(self, user_query: str, plan_text: str, answer: str,
                 sql_log: list | None = None) -> str:
        """POST-HOOK: 强制 LLM 审核答案（不可调工具）。

        返回: 原答案 + 审核发现（追加式，不替换原答案）
        """
        review_prompt = REVIEW_PROMPT.format(answer=answer)
        # 构建 SQL 查询上下文，让 QC 知道实际查询了哪些数据维度
        sql_context = ""
        if sql_log:
            queries_text = "\n".join([
                f"- `{s[:200] if isinstance(s, str) else s.get('sql', '')[:200]}`"
                + (f" → {s.get('row_count', '?')} 行" if isinstance(s, dict) else "")
                for s in sql_log[:20]
            ])
            sql_context = f"\n\n## 实际执行的 SQL 查询（供图表检查参考）\n{queries_text}"
        review_messages = [
            {"role": "system", "content": review_prompt},
            {"role": "user", "content": f"原始问题：{user_query}\n\n规划内容：\n{plan_text[:2000]}{sql_context}"},
        ]
        try:
            resp = self.llm.chat_completion(
                messages=review_messages, tools=None, stream=False,
                model=self.preferred_model, temperature=self.temperature,
            )
            self.llm.record_success()
            review_result = resp.choices[0].message.content or ""
            # 全部通过 → 返回原答案
            if "审核通过" in review_result and len(review_result) < 500:
                return answer
            # 有问题 → 追加到原答案末尾，不替换
            return answer + "\n\n---\n\n## ⚠️ 审核发现问题\n\n" + review_result
        except Exception:
            self.llm.record_failure()
            return answer  # 审核失败不阻塞，降级返回原答案

    def _run_fix(self, user_query: str, answer: str, qc_findings: str) -> tuple[str, int]:
        """POST-HOOK: 自动修复审核发现的问题（可调用工具）。

        对可修复的问题（图表路径占位符等）重新调用工具修复，
        返回修复后的完整答案。

        Returns:
            (fixed_answer, fix_count) — 修复后答案和修复项数
        """
        fix_prompt = FIX_PROMPT.format(answer=answer, qc_findings=qc_findings)

        messages = [
            {"role": "system", "content": fix_prompt},
            {"role": "user", "content": f"原始问题：{user_query}\n\n请修复上述审核发现的问题，输出完整答案。"},
        ]

        tool_definitions = self.tools.get_tool_definitions()
        if self._tool_filter is not None and tool_definitions:
            _filter_set = set(self._tool_filter)
            tool_definitions = [
                t for t in tool_definitions
                if t["function"]["name"] in _filter_set
            ]

        callbacks = ProgressCallbacks()
        fixed_answer = ""

        for _turn in range(3):  # 最多 3 轮修复
            collected_content, tool_calls_list, error = self._single_llm_turn(
                messages, tool_definitions, callbacks)

            if error:
                return answer, 0

            if tool_calls_list is not None:
                self._append_tool_results(messages, tool_calls_list, collected_content,
                                          callbacks, [])
                continue
            else:
                fixed_answer = collected_content or ""
                self.llm.record_success()
                break

        if not fixed_answer or len(fixed_answer) < len(answer) * 0.3:
            # 修复结果异常（空或大幅缩水），重试一次
            messages.append({"role": "user", "content": "你的回答太短了，请输出完整的修复后答案（保留所有原始章节和表格）。"})
            for _retry in range(2):
                collected_content, tool_calls_list, error = self._single_llm_turn(
                    messages, tool_definitions, callbacks)
                if error:
                    break
                if tool_calls_list is not None:
                    self._append_tool_results(messages, tool_calls_list, collected_content,
                                              callbacks, [])
                    continue
                if collected_content and len(collected_content) >= len(answer) * 0.3:
                    fixed_answer = collected_content
                    self.llm.record_success()
                    break
                # LLM 输出仍不够长，追加更强烈的纠正提示
                messages.append({"role": "user", "content": "仍然太短！请从原始答案的第一章开始，逐章复制并修改有问题的部分，输出完整答案（需包含所有表格和图表引用）。"})

        if not fixed_answer or len(fixed_answer) < len(answer) * 0.3:
            return answer, 0

        # 统计修复项数：审核发现的 🔴 项 + 建议用户自查等严重项
        fix_count = qc_findings.count("- 🔴")
        if fix_count == 0:
            fix_count = qc_findings.count("🔴")
        return fixed_answer, max(fix_count, 1)

    def _generate_sql_queries(self, plan_text: str) -> list[dict]:
        """从执行计划生成 SQL 查询列表（1 次 LLM 调用，无工具）。

        Returns:
            [{"sql": "SELECT ...", "purpose": "获取月度效价"}, ...]
            失败时返回空列表，回退到 ReAct 正常流程。
        """
        messages = [
            {"role": "system", "content": SQL_GEN_PROMPT},
            {"role": "user", "content": f"数据库概况:\n{self._system_prompt[-4000:]}\n\n执行计划:\n{plan_text}"},
        ]
        try:
            resp = self.llm.chat_completion(
                messages=messages, tools=None, stream=False,
                model=self.preferred_model, temperature=self.temperature,
            )
            self.llm.record_success()
            content = resp.choices[0].message.content or ""
        except Exception:
            self.llm.record_failure()
            return []

        # 从可能的 markdown 代码块中提取 JSON
        json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
        if json_match:
            content = json_match.group(1)
        # 也尝试直接匹配 JSON 对象
        if not content.strip().startswith('{'):
            json_match = re.search(r'\{.*"queries".*\}', content, re.DOTALL)
            if json_match:
                content = json_match.group(0)
        try:
            data = json.loads(content)
            return data.get("queries", [])
        except (json.JSONDecodeError, KeyError):
            return []

    def _execute_sql_batch(self, queries: list[dict],
                            callbacks: ProgressCallbacks,
                            sql_log: list) -> str:
        """批量执行 SQL 查询，返回格式化的结果文本。

        Returns:
            可直接作为 system message content 注入对话的结果文本。
        """
        if not self.tools.db or not self.tools.db.is_connected:
            return ""

        callbacks.on_thinking(f"📋 批量数据采集 ({len(queries)} 条查询)")

        results = []
        for i, q in enumerate(queries):
            sql = (q.get("sql", "") or "").strip()
            purpose = q.get("purpose", f"查询{i+1}")
            if not sql:
                continue
            if not sql.upper().lstrip().startswith("SELECT"):
                results.append({"purpose": purpose, "error": "仅允许 SELECT 语句"})
                continue
            try:
                callbacks.on_tool_call("batch_sql", {"sql": sql[:120], "purpose": purpose})
                callbacks.on_sql(sql)
                data = self.tools.db.execute_query(sql)
                sql_log.append(sql)
                row_count = data.get("row_count", 0)
                callbacks.on_tool_result(row_count, data.get("truncated", False))
                results.append({
                    "purpose": purpose,
                    "row_count": row_count,
                    "rows": data.get("rows", []),
                })
            except Exception as e:
                error_str = str(e)
                # 判断是否值得重试：语法错误不重试
                is_syntax_error = any(
                    kw in error_str.lower()
                    for kw in ["syntax error", "syntax_error", "42601"]
                )
                if not is_syntax_error:
                    try:
                        data = self.tools.db.execute_query(sql)
                        sql_log.append(sql)
                        row_count = data.get("row_count", 0)
                        callbacks.on_tool_result(
                            row_count, data.get("truncated", False)
                        )
                        results.append({
                            "purpose": f"{purpose}（重试成功）",
                            "row_count": row_count,
                            "rows": data.get("rows", []),
                        })
                        continue
                    except Exception as retry_error:
                        error_str = f"重试也失败: {retry_error}"
                results.append({"purpose": purpose, "error": error_str})

        if not results:
            return ""

        # 构建汇总 + 详细数据
        summary = []
        for i, r in enumerate(results):
            if "error" in r:
                summary.append(f"  [{i+1}] ❌ {r['purpose']}: {r['error']}")
            else:
                summary.append(f"  [{i+1}] ✅ {r['purpose']}: {r['row_count']} 行")

        detail_json = json.dumps(results, ensure_ascii=False, default=str)
        detail_json = truncate_tool_result(detail_json)

        return (
            f"## 批量数据采集结果\n\n"
            f"已执行 {len(results)} 条查询:\n"
            + "\n".join(summary) +
            f"\n\n详细数据:\n```json\n{detail_json}\n```"
        )

    def _single_llm_turn(self, messages: list, tool_definitions: list,
                          callbacks: ProgressCallbacks, cancel_event=None,
                          stream_content: bool = True):
        """单轮 LLM 调用。返回 (collected_content, tool_calls_list, error)。
        tool_calls_list 为 None 表示 LLM 返回了文本答案（非工具调用）。
        stream_content=False 时不调用 on_text_chunk，由调用方决定如何展示。
        """
        try:
            stream = self.llm.chat_completion(
                messages=messages,
                tools=tool_definitions if tool_definitions else None,
                stream=True,
                model=self.preferred_model,
                temperature=self.temperature,
            )
            content_parts: list[str] = []
            tool_call_acc: dict[int, dict] = {}

            for chunk in stream:
                if cancel_event and cancel_event.is_set():
                    try:
                        stream.close()
                    except Exception:
                        pass
                    return "", None, "⏸️ 已取消"
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta is None:
                    continue
                if delta.content:
                    content_parts.append(delta.content)
                    if stream_content:
                        callbacks.on_text_chunk(delta.content)
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_call_acc:
                            tool_call_acc[idx] = {
                                "id": "",
                                "function": {"name": "", "arguments": ""},
                            }
                        entry = tool_call_acc[idx]
                        if tc.id:
                            entry["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                entry["function"]["name"] += tc.function.name
                            if tc.function.arguments:
                                entry["function"]["arguments"] += tc.function.arguments

            self.llm.record_success()
            collected_content = "".join(content_parts)

            if len(tool_call_acc) == 0:
                return collected_content, None, None  # 文本答案

            _sorted_tcs = [
                tool_call_acc[k] for k in sorted(tool_call_acc.keys())
                if tool_call_acc[k]['function']['name']
            ]
            tool_calls_list = [
                type('ToolCall', (), {
                    'id': tc['id'],
                    'function': type('Function', (), {
                        'name': tc['function']['name'],
                        'arguments': tc['function']['arguments'],
                    }),
                })
                for tc in _sorted_tcs
            ]
            return collected_content, tool_calls_list, None
        except Exception as e:
            self.llm.record_failure()
            return "", None, str(e)

    def _append_tool_results(self, messages: list, tool_calls_list: list,
                              collected_content: str, callbacks: ProgressCallbacks,
                              sql_log: list, state: AgentLoopState = None) -> None:
        """执行工具调用并追加 assistant + tool 消息到 messages。"""
        messages.append({
            "role": "assistant",
            "content": collected_content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in tool_calls_list
            ],
        })

        for tc in tool_calls_list:
            tool_name = tc.function.name
            try:
                arguments = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                arguments = {}

            # ── 工具结果缓存：相同工具+相同参数 → 直接返回缓存 ──
            cache_key = f"{tool_name}:{tc.function.arguments}"
            if state and cache_key in state._tool_result_cache:
                result = state._tool_result_cache[cache_key]
                callbacks.on_tool_call(tool_name, arguments)
                try:
                    result_data = json.loads(result)
                    row_count = result_data.get("row_count", 0)
                    truncated = result_data.get("truncated", False)
                    callbacks.on_tool_result(row_count, truncated)
                    callbacks.on_thinking(f"⚡ {tool_name}（缓存命中，跳过重复执行）")
                except (json.JSONDecodeError, TypeError):
                    callbacks.on_tool_result(0)
            else:
                callbacks.on_tool_call(tool_name, arguments)
                result = self.tools.execute(tool_name, arguments)
                if state:
                    state._tool_result_cache[cache_key] = result

                try:
                    result_data = json.loads(result)
                    if "sql" in result_data:
                        sql_log.append(result_data["sql"])
                        callbacks.on_sql(result_data["sql"])
                    row_count = result_data.get("row_count", 0)
                    truncated = result_data.get("truncated", False)
                    callbacks.on_tool_result(row_count, truncated)
                except (json.JSONDecodeError, TypeError):
                    result_data = {}
                    callbacks.on_tool_result(0)

                # 检测图表生成工具调用
                if tool_name == "generate_chart" and isinstance(result_data, dict):
                    file_path = result_data.get("file_path", "")
                    if file_path:
                        callbacks.on_chart(file_path)

                # 自动追踪查询过的表名（用于后续追问上下文注入）
                if tool_name.startswith("query_") and "error" not in (result_data if isinstance(result_data, dict) else {}):
                    self._extract_and_record_tables(arguments)

                # EAR Guard Rails: 记录轨迹并监控
                success = "error" not in result_data if isinstance(result_data, dict) else True
                self._trajectory_history.append(StepRecord(
                    tool_name=tool_name,
                    arguments=arguments,
                    success=success,
                ))
                regulation = self.tools.guard_rails.monitor_trajectory(self._trajectory_history)
                if regulation.action != "continue":
                    regulation_msg = f"\n\n[轨迹监控提示]: {regulation.message}"
                    result = result + regulation_msg

            # 记录到摘要列表（用于上下文注入）
            if state:
                state._all_tool_calls_summary.append({
                    "tool": tool_name,
                    "args_keys": list(arguments.keys()) if isinstance(arguments, dict) else [],
                })

            result = truncate_tool_result(result)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

    def _extract_and_record_tables(self, arguments: dict):
        """从 query_* 工具参数中提取 SQL 表名，加入会话追踪集合。

        sqlparse 提取失败时静默跳过，不阻塞工具执行。
        """
        sql_text = arguments.get("sql", "")
        if not sql_text:
            return
        try:
            import sqlparse
            from sqlparse.sql import Identifier, IdentifierList
            from sqlparse.tokens import Keyword

            parsed = sqlparse.parse(sql_text)
            if not parsed:
                return

            statement = parsed[0]
            from_seen = False
            for token in statement.flatten():
                if token.ttype is Keyword and token.value.upper() in (
                    "FROM", "JOIN", "INNER JOIN", "LEFT JOIN",
                    "RIGHT JOIN", "FULL JOIN", "CROSS JOIN",
                ):
                    from_seen = True
                    continue
                if from_seen and isinstance(token, (Identifier, IdentifierList)):
                    for ident in (
                        token.get_identifiers()
                        if isinstance(token, IdentifierList)
                        else [token]
                    ):
                        name = str(ident).split()[0].strip('"').strip()
                        if "." in name:
                            self._session_tables.add(name.lower())
                        # 纯表名也记录（可能在后续匹配中推断 schema）
                        elif name and not name.upper().startswith("SELECT"):
                            self._session_tables.add(name.lower())
                    from_seen = False
                elif from_seen and token.ttype is not None:
                    from_seen = False
        except Exception:
            pass  # 解析失败不影响工具执行

    # ── 数据充分性检查 ──

    DATA_SUFFICIENCY_PROMPT = """你是数据完整性检查员。根据以下信息判断数据分析所需的数据是否充分：

执行计划中的数据需求：
{plan_data_requirements}

已执行的 SQL 及其结果：
{sql_results_summary}

判断规则：
1. 检查计划中列出的每张表是否在已执行的 SQL 中出现
2. 如果某条 SQL 返回 0 行，考虑是否表名/条件写错
3. 如果 GROUP BY 查询只返回了少量分组，可能需要无 WHERE 的验证查询

输出格式（严格 JSON）：
```json
{{
  "judgment": "SUFFICIENT|INSUFFICIENT",
  "missing_queries": [
    {{"sql": "SELECT ... FROM schema.table ...", "purpose": "补查原因"}}
  ],
  "note": "简要说明判断理由"
}}
```

如果数据充分，missing_queries 返回空数组 []。"""

    @staticmethod
    def _extract_tables_from_plan(plan_text: str) -> set[str]:
        """从 Plan 的'二、数据需求'表格中提取表名引用"""
        import re
        tables: set[str] = set()
        # 找到"二、数据需求"到下一个"### 三、"或"### 四、"之间的内容
        data_section = re.search(
            r'###?\s*二[、\s]*数据需求(.*?)(?=###?\s*三[、\s]|###?\s*四[、\s]|$)',
            plan_text, re.DOTALL,
        )
        if data_section:
            for m in re.finditer(
                r'([a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z_][a-zA-Z0-9_]*)',
                data_section.group(0),
            ):
                tables.add(m.group(1).lower())
        return tables

    @staticmethod
    def _extract_tables_from_sql(sql: str) -> set[str]:
        """从 SQL 中提取 FROM/JOIN 子句的表名（正则 + sqlparse 双保险）"""
        import re

        tables: set[str] = set()

        # 方案 1: 正则提取 schema.table 模式（覆盖大部分场景）
        for m in re.finditer(
            r'\b([a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z_][a-zA-Z0-9_]*)',
            sql, re.IGNORECASE,
        ):
            # 跳过 SQL 关键字后可能误匹配的内容
            word = m.group(1).upper()
            if word in ('CURRENT_DATE', 'CURRENT_TIMESTAMP', 'INTERVAL.YEAR',
                         'INTERVAL.MONTH', 'INTERVAL.DAY'):
                continue
            tables.add(m.group(1).lower())

        return tables

    @staticmethod
    def _table_name_match(plan_table: str, sql_table: str) -> bool:
        """模糊匹配 Plan 中的表名和 SQL 中的表名"""
        from difflib import SequenceMatcher

        pt = plan_table.lower().strip()
        st = sql_table.lower().strip()
        if pt == st:
            return True
        # schema.table 匹配纯 table 名
        if "." in st and "." not in pt:
            if st.split(".", 1)[1] == pt:
                return True
        if "." in pt and "." not in st:
            if pt.split(".", 1)[1] == st:
                return True
        # 编辑距离近似匹配
        if SequenceMatcher(None, pt, st).ratio() > 0.85:
            return True
        return False

    @staticmethod
    def _is_duplicate_sql(sql1: str, existing_sqls: set[str]) -> bool:
        """判断 sql1 是否与已执行的 SQL 重复（sqlparse 归一化后比对）"""
        import sqlparse
        from difflib import SequenceMatcher

        def normalize(s: str) -> str:
            try:
                parsed = sqlparse.parse(s)
                if parsed:
                    return parsed[0].value.upper().strip()
            except Exception:
                pass
            return s.upper().strip()

        norm1 = normalize(sql1)
        for es in existing_sqls:
            norm2 = normalize(es)
            if norm1 == norm2:
                return True
            if SequenceMatcher(None, norm1, norm2).ratio() > 0.9:
                return True
        return False

    def _llm_sufficiency_check(self, plan_text: str,
                                sql_queries: list[dict],
                                sql_log: list) -> dict:
        """1 次 LLM 调用做充分性判断。返回 SUFFICIENT 或含 missing_queries 的 dict。"""
        import re

        # 精简 Plan 数据需求
        data_section = ""
        m = re.search(
            r'###?\s*二[、\s]*数据需求(.*?)(?=###?\s*三[、\s]|###?\s*四[、\s]|$)',
            plan_text, re.DOTALL,
        )
        if m:
            data_section = m.group(1).strip()[:1500]

        # 构建 SQL 结果摘要
        sql_summary_lines = []
        for q in sql_queries:
            purpose = q.get("purpose", "")
            sql = q.get("sql", "")[:200]
            sql_summary_lines.append(f"- [{purpose}] `{sql}`")

        prompt = self.DATA_SUFFICIENCY_PROMPT.format(
            plan_data_requirements=data_section or "（未找到数据需求表）",
            sql_results_summary="\n".join(sql_summary_lines) or "（无已执行的 SQL）",
        )

        try:
            resp = self.llm.chat_completion(
                messages=[
                    {"role": "system", "content": "你是数据完整性检查员。只输出 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                tools=None, stream=False,
                model=self.preferred_model, temperature=self.temperature,
            )
            self.llm.record_success()
            content = resp.choices[0].message.content or ""
        except Exception:
            self.llm.record_failure()
            return "SUFFICIENT"

        # 解析 JSON
        import json as _json
        json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
        if json_match:
            content = json_match.group(1)
        try:
            data = _json.loads(content)
            judgment = data.get("judgment", "SUFFICIENT")
            if judgment == "INSUFFICIENT":
                missing = data.get("missing_queries", [])
                existing_sqls = {q.get("sql", "").strip() for q in sql_queries}
                filtered_missing = []
                for mq in missing:
                    mq_sql = mq.get("sql", "").strip()
                    if self._is_duplicate_sql(mq_sql, existing_sqls):
                        continue
                    if not mq_sql.upper().lstrip().startswith("SELECT"):
                        continue
                    filtered_missing.append(mq)
                    existing_sqls.add(mq_sql)
                if filtered_missing:
                    return {
                        "result": "INSUFFICIENT_FORCE",
                        "missing_queries": filtered_missing,
                    }
            return "SUFFICIENT"
        except (_json.JSONDecodeError, KeyError):
            return "SUFFICIENT"

    def _force_insufficient_check(self, missing_tables: set,
                                   _plan_text: str,
                                   _sql_log: list) -> dict:
        """强制补查：用代码生成 COUNT(*) + LIMIT 3 探查 SQL"""
        queries = []
        for table_name in missing_tables:
            if "." in table_name:
                schema, table = table_name.split(".", 1)
                queries.append({
                    "sql": f"SELECT COUNT(*) AS row_count FROM {schema}.{table}",
                    "purpose": f"验证表 {table_name} 是否存在及行数",
                })
                queries.append({
                    "sql": f"SELECT * FROM {schema}.{table} LIMIT 3",
                    "purpose": f"探查表 {table_name} 的数据样例",
                })

        if not queries:
            return "SUFFICIENT"

        return {
            "result": "INSUFFICIENT_FORCE",
            "missing_queries": queries,
        }

    def _check_data_sufficiency(self, plan_text: str,
                                 sql_queries: list[dict],
                                 sql_log: list,
                                 batch_result_text: str) -> dict:
        """检查批量 SQL 结果是否覆盖了 Plan 中的所有数据需求。

        Returns:
            "SUFFICIENT" | "INSUFFICIENT_FORCE" | "INSUFFICIENT_LLM"
            或包含 missing_queries 的 dict
        """
        if not plan_text or not sql_queries:
            return "SUFFICIENT"

        # Step 1: 代码层提取并匹配 Plan 表 ↔ SQL 表
        plan_tables = self._extract_tables_from_plan(plan_text)
        if not plan_tables:
            return "SUFFICIENT"

        sql_tables: set[str] = set()
        for q in sql_queries:
            sql_tables.update(
                self._extract_tables_from_sql(q.get("sql", ""))
            )

        matched_plan_tables: set[str] = set()
        for pt in plan_tables:
            for st in sql_tables:
                if self._table_name_match(pt, st):
                    matched_plan_tables.add(pt)
                    break

        missing_plan_tables = plan_tables - matched_plan_tables

        # Step 2: 分级判断
        if len(missing_plan_tables) == 0:
            return self._llm_sufficiency_check(
                plan_text, sql_queries, sql_log
            )
        elif len(missing_plan_tables) == 1:
            result = self._llm_sufficiency_check(
                plan_text, sql_queries, sql_log
            )
            if result == "SUFFICIENT":
                return "INSUFFICIENT_LLM"
            return result
        else:
            return self._force_insufficient_check(
                missing_plan_tables, plan_text, sql_log
            )

    # CPU 卡死检测参数
    STUCK_CPU_CHECKS = 5        # 连续 N 次 CPU=0.0% 判定卡死
    STUCK_MIN_ELAPSED = 1200    # 至少运行 20 分钟
    MONITOR_INTERVAL = 60       # 每 60s 检查一次

    @staticmethod
    def _get_process_cpu(pid: int) -> float:
        """获取进程 CPU 使用率，失败返回 -1"""
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "%cpu="],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                return float(result.stdout.strip())
        except (subprocess.TimeoutExpired, ValueError):
            pass
        return -1.0

    def _start_watchdog(self):
        """启动 CPU 卡死检测 daemon 线程，返回 stop_event"""
        pid = os.getpid()
        stop_event = threading.Event()
        zero_count = [0]

        def _watch():
            start = time.time()
            while not stop_event.is_set():
                stop_event.wait(self.MONITOR_INTERVAL)
                if stop_event.is_set():
                    return
                cpu = self._get_process_cpu(pid)
                elapsed = time.time() - start
                if cpu < 0:
                    zero_count[0] = 0
                    continue
                if cpu == 0.0:
                    zero_count[0] += 1
                    if zero_count[0] <= 2:  # 只输出前两次，减少噪音
                        import sys
                        print(f"  [watchdog] CPU=0.0% ({zero_count[0]}/{self.STUCK_CPU_CHECKS}) elapsed={int(elapsed)}s",
                              file=sys.stderr, flush=True)
                else:
                    if zero_count[0] > 0:
                        import sys
                        print(f"  [watchdog] CPU recovered → {cpu}%", file=sys.stderr, flush=True)
                    zero_count[0] = 0
                if zero_count[0] >= self.STUCK_CPU_CHECKS and elapsed >= self.STUCK_MIN_ELAPSED:
                    import sys
                    print(f"\n⚠️ vaxport 进程卡死（CPU=0.0% 持续 {self.STUCK_CPU_CHECKS} 次检测，"
                          f"已运行 {int(elapsed)}s），自动退出。", file=sys.stderr, flush=True)
                    os._exit(1)

        t = threading.Thread(target=_watch, daemon=True)
        t.start()
        return stop_event

    def run(self, user_query: str, callbacks: ProgressCallbacks = None,
            plan_mode: bool = False, history: list[dict] | None = None,
            cancel_event=None) -> dict:
        """执行 Agent 循环，返回最终回答和元数据

        Args:
            user_query: 用户输入
            callbacks: 进度回调
            plan_mode: 规划模式 (True=纯文本对话不调工具)
            history: 对话历史 [{"role": "user/assistant", "content": "..."}]
            cancel_event: threading.Event，set 后取消执行
        """
        if callbacks is None:
            callbacks = ProgressCallbacks()

        system_prompt = self._system_prompt
        if plan_mode:
            system_prompt += (
                "\n\n## 当前模式：规划讨论\n"
                "你现在处于规划模式，不能调用任何工具。你的任务是：\n"
                "1. 理解用户需求，提出澄清问题\n"
                "2. 分析可能的方案和路径\n"
                "3. 给出结构化的执行计划\n"
                "4. 等待用户确认后切换到执行模式\n"
                "不要给出最终答案，只做规划和讨论。"
            )

        # 注入跨会话反馈记忆
        if self._memory_context:
            system_prompt += self._memory_context

        # 注入本会话已查询过的数据表
        if self._session_tables:
            tables_str = ", ".join(sorted(self._session_tables))
            system_prompt += (
                f"\n\n## 本会话已查询过的数据表\n"
                f"以下表已在本会话中访问过，可直接基于已有认知查询（不需要重新探查表结构）：\n"
                f"{tables_str}"
            )

        messages = [
            {"role": "system", "content": system_prompt},
        ]

        # 注入对话历史（system prompt 之后、当前用户查询之前）
        if history:
            for h in history:
                if h.get("role") in ("user", "assistant"):
                    messages.append({
                        "role": h["role"],
                        "content": h.get("content", ""),
                    })

        messages.append({"role": "user", "content": user_query})

        # ── PRE-HOOK: 规划 ──
        plan_text = ""
        if self._auto_plan and not plan_mode:
            plan_text = self._generate_plan(user_query, history, callbacks)
            # SKIP_PLAN: 追问/补充 → 跳过规划管道，进入对话模式
            if plan_text and plan_text.strip() == "SKIP_PLAN":
                plan_text = ""
                messages.append({
                    "role": "system",
                    "content": FOLLOWUP_MODE_PROMPT,
                })
            elif plan_text and self._plan_confirm:
                if not callbacks.on_plan(plan_text):
                    return {
                        "answer": "⏸️ 计划已取消",
                        "turns": 0, "tokens_used": 0, "context_window": self._get_context_window(),
                        "token_pct": 0, "sql_queries": [], "model": self.preferred_model or self.llm.active_model,
                        "backend": self.llm.active_backend, "compressions": 0,
                        "agent_type": "", "agent_chain": [],
                    }
                # 收集用户决策反馈
                feedback = getattr(callbacks, 'plan_feedback', '').strip()
                if feedback:
                    plan_text = plan_text + f"\n\n### 用户决策\n已确认并采纳以下决策：\n{feedback}"
            if plan_text:
                messages.append({
                    "role": "system",
                    "content": f"📋 执行计划（按此计划逐步执行）:\n{plan_text}",
                })

        state = AgentLoopState()
        self._state = state
        tool_definitions = [] if plan_mode else self.tools.get_tool_definitions()

        # 工具子集过滤
        if self._tool_filter is not None and tool_definitions:
            _filter_set = set(self._tool_filter)
            tool_definitions = [
                t for t in tool_definitions
                if t["function"]["name"] in _filter_set
            ]
        sql_log = []
        final_answer = ""
        context_window = self._get_context_window()

        # ── 批量数据采集阶段 (Plan → SQL → 代码直接执行, 无需 ReAct) ──
        if plan_text and self._auto_plan and not plan_mode:
            callbacks.on_thinking("📋 生成 SQL 查询...")
            sql_queries = self._generate_sql_queries(plan_text)
            batch_result_text = ""
            if sql_queries:
                batch_result_text = self._execute_sql_batch(sql_queries, callbacks, sql_log) or ""
                if batch_result_text:
                    messages.append({"role": "system", "content": batch_result_text})

            # ── 数据充分性检查 ──
            if sql_queries:
                sufficiency_result = self._check_data_sufficiency(
                    plan_text, sql_queries, sql_log,
                    batch_result_text if batch_result_text else "",
                )
                if isinstance(sufficiency_result, dict) and sufficiency_result.get("result") == "INSUFFICIENT_FORCE":
                    missing_sqls = sufficiency_result.get("missing_queries", [])
                    if missing_sqls:
                        callbacks.on_thinking(
                            f"⚠️ 数据不足，补查 {len(missing_sqls)} 条..."
                        )
                        supplement_text = self._execute_sql_batch(
                            missing_sqls, callbacks, sql_log
                        )
                        if supplement_text:
                            messages.append({
                                "role": "system",
                                "content": (
                                    "## 补充数据采集（数据完整性检查后追加）\n\n"
                                    f"{supplement_text}"
                                ),
                            })
                elif sufficiency_result == "INSUFFICIENT_LLM":
                    # 缺失 1 张表但 LLM 判充分 → 注入警告
                    plan_tables = self._extract_tables_from_plan(plan_text)
                    sql_tables: set[str] = set()
                    for q in sql_queries:
                        sql_tables.update(
                            self._extract_tables_from_sql(q.get("sql", ""))
                        )
                    matched = set()
                    for pt in plan_tables:
                        for st in sql_tables:
                            if self._table_name_match(pt, st):
                                matched.add(pt)
                                break
                    missing = plan_tables - matched
                    if missing:
                        messages.append({
                            "role": "system",
                            "content": (
                                "⚠️ 以下表在计划中列出但未被查询，请确认是否需要补查：\n"
                                f"{', '.join(sorted(missing))}\n"
                                "如果确认不需要，请继续分析。"
                            ),
                        })

            # ── 部分失败降级标注 ──
            if batch_result_text and "❌" in batch_result_text:
                messages.append({
                    "role": "system",
                    "content": (
                        "⚠️ 【系统强制警告 — 数据完整性】上述批量查询中有部分失败。\n"
                        "你的回答必须包含一节 '## 数据完整性说明'，明确说明：\n"
                        "1. 哪些分析部分因数据缺失无法完成\n"
                        "2. 哪些结论是基于现有数据可确认的\n"
                        "禁止用已有数据推测缺失数据部分的结论。"
                    ),
                })

            messages.append({"role": "system", "content": ANALYSIS_PROMPT})
            callbacks.on_thinking("📊 分析阶段")

        # ── 主 ReAct 循环 (分析阶段) ──
        start_time = time.time()  # 从分析阶段开始计时，排除规划和数据采集耗时
        watchdog_stop = self._start_watchdog()
        try:
            for turn in range(self.max_rounds):
                # 取消检查
                if cancel_event and cancel_event.is_set():
                    final_answer = "⏸️ 执行已取消"
                    break

                # 交互追问检查
                feedback = callbacks.get_pending_feedback()
                if feedback:
                    messages.append({
                        "role": "user",
                        "content": f"[追问] {feedback}",
                    })
                    callbacks.on_thinking(f"💬 收到追问，正在回应...")

                # 总超时检查（0 = 不限制）
                if self.total_timeout > 0 and time.time() - start_time > self.total_timeout:
                    final_answer = "⚠️ 分析超时（总时长超过限制），请简化问题后重试。"
                    break

                state.turns = turn + 1

                # ── 上下文压缩检查 ──
                current_tokens = count_tokens(messages)
                if current_tokens > int(context_window * COMPRESS_THRESHOLD):
                    messages = self._compress_history(messages)
                    state.compression_count += 1
                    current_tokens = count_tokens(messages)

                # 常规裁剪
                messages, was_trimmed = trim_context(
                    messages, self.preferred_model or self.llm.active_model, context_window
                )
                if was_trimmed:
                    messages.append({
                        "role": "system",
                        "content": "⚠️ 对话历史已自动裁剪以保持在上下文窗口内。",
                    })

                # ── 上下文注入：已调用工具摘要（防止 LLM 重复调用）──
                injected = False
                if state._all_tool_calls_summary:
                    summary_lines = []
                    for s in state._all_tool_calls_summary:
                        keys = ", ".join(s["args_keys"]) if s["args_keys"] else "无参数"
                        summary_lines.append(f"- {s['tool']}({keys})")
                    summary_text = (
                        "以下是本轮对话中你已经调用过的工具列表。"
                        "请勿重复调用相同工具+相同参数，如需相同数据请直接使用已有结果。\n"
                        + "\n".join(summary_lines)
                    )
                    messages.append({"role": "system", "content": summary_text})
                    injected = True

                # 调用 LLM（流式）
                collected_content, tool_calls_list, error = self._single_llm_turn(
                    messages, tool_definitions if tool_definitions else None, callbacks,
                    cancel_event=cancel_event)

                # 移除注入的摘要消息（避免污染历史）
                if injected:
                    messages.pop()

                if error:
                    final_answer = f"❌ LLM 调用失败: {error}\n当前后端: {self.llm.active_backend}，已自动尝试切换。"
                    break

                if tool_calls_list is not None:
                    # 思考文本已通过 on_text_chunk 流式输出到 answer widget
                    # 标记思考文本结束位置，后续 answer widget 只展示答案部分
                    callbacks.mark_answer_start()
                    # ── 死循环检测 ──
                    current_signatures = frozenset(
                        (tc.function.name, tc.function.arguments)
                        for tc in tool_calls_list
                    )

                    # 1. 乒乓检测：A→B→A→B→A→B→A→B→A→B→A（至少5个完整交替周期才触发）
                    sigs = state.tool_call_signatures[-10:]
                    if len(sigs) == 10 and sigs[0] == sigs[2] == sigs[4] == sigs[6] == sigs[8] and sigs[1] == sigs[3] == sigs[5] == sigs[7] == sigs[9] and sigs[0] != sigs[1]:
                        final_answer = "⚠️ 检测到工具调用在 A→B→A 模式间切换，已中断。"
                        break

                    # 2. 连续相同调用检测
                    for tc in tool_calls_list:
                        sig_key = (tc.function.name, tc.function.arguments)
                        if state._last_tool_call_sig == sig_key:
                            state._consecutive_same_count += 1
                        else:
                            state._consecutive_same_count = 1
                        state._last_tool_call_sig = sig_key
                        if state._consecutive_same_count >= 5:
                            final_answer = f"⚠️ 工具 {tc.function.name} 连续 {state._consecutive_same_count} 次使用相同参数调用，已中断。"
                            break

                    # 3. 渐进式相似度检测（同工具、仅微调参数）
                    if not final_answer:
                        for tc in tool_calls_list:
                            try:
                                cur_args = json.loads(tc.function.arguments)
                            except json.JSONDecodeError:
                                cur_args = {}

                            # 检查是否为多样化的批处理（如对比多个菌株）
                            if _is_diverse_batch(tc.function.name, state._recent_tool_params):
                                # 合法批处理，不计入相似度检测
                                state._recent_tool_params.append((tc.function.name, cur_args))
                                continue

                            # 统计同工具近期调用中，参数值高度相似的次数
                            high_sim_count = 0
                            for prev_name, prev_args in state._recent_tool_params:
                                if prev_name != tc.function.name:
                                    continue
                                if not isinstance(prev_args, dict) or not isinstance(cur_args, dict):
                                    continue

                                # 计算参数集的相似度
                                sim = _args_similarity(prev_args, cur_args)
                                # 相似度 >= 0.85 认为高度相似（如日期仅差1天、阈值微调等）
                                if sim >= 0.85:
                                    high_sim_count += 1

                            # 阈值从 3 提高到 5，给批处理留出空间
                            if high_sim_count >= 5:
                                final_answer = f"⚠️ 工具 {tc.function.name} 连续 {high_sim_count+1} 次参数高度相似调用，已中断。请换用不同分析方法或工具。"
                                break

                            state._recent_tool_params.append((tc.function.name, cur_args))

                        # 只保留最近 20 条记录
                        state._recent_tool_params = state._recent_tool_params[-20:]

                    if final_answer:
                        break

                    state.tool_call_signatures.append(current_signatures)
                    self._append_tool_results(messages, tool_calls_list, collected_content,
                                              callbacks, sql_log, state)
                    callbacks.on_thinking("分析查询结果...")
                    continue

                else:
                    # LLM 直接回复 → 终止；答案已由 on_text_chunk 流式输出到 answer widget
                    final_answer = collected_content or ""
                    self.llm.record_success()
                    break

        finally:
            watchdog_stop.set()

        # 循环结束无答案
        if not final_answer and state.turns >= self.max_rounds:
            final_answer = "⚠️ 达到最大分析轮次，请简化问题后重试。"

        # ── POST-HOOK: 自动审核 + 自动修复（最多 3 轮） ──
        if self._auto_review and final_answer and not plan_mode and "已中断" not in final_answer and "超时" not in final_answer and "失败" not in final_answer:
            delimiter = "\n\n---\n\n## ⚠️ 审核发现问题\n\n"
            for review_attempt in range(3):
                review_answer = self._run_review(user_query, plan_text, final_answer, sql_log)
                # 审核通过 → 直接使用
                if "⚠️ 审核发现问题" not in review_answer:
                    final_answer = review_answer
                    break
                # 审核发现问题 → 提取并修复
                parts = review_answer.split(delimiter, 1)
                original_answer = parts[0]
                review_findings = parts[1] if len(parts) > 1 else ""

                fixed_answer, fix_count = self._run_fix(
                    user_query, original_answer, review_findings)

                if fix_count > 0:
                    final_answer = fixed_answer  # 用修复后答案进入下一轮审核
                    continue
                else:
                    # 修复失败，保留原答案进入下一轮（给 LLM 另一次机会）
                    final_answer = original_answer
                    continue
            else:
                # 3 轮后仍未通过 → 附加简短提示，不 dump 审核清单
                if "⚠️ 审核发现问题" in final_answer:
                    final_answer = final_answer.split(delimiter, 1)[0]
                final_answer += "\n\n> ⚠️ 部分内容经自动审核后可能仍需完善，可追问补充。"

        # Token 统计
        token_count = count_tokens(messages)
        context_window = self._get_context_window()

        return {
            "answer": final_answer,
            "turns": state.turns,
            "tokens_used": token_count,
            "context_window": context_window,
            "token_pct": round(token_count / context_window * 100, 1) if context_window > 0 else 0,
            "sql_queries": sql_log,
            "model": self.preferred_model or self.llm.active_model,
            "backend": self.llm.active_backend,
            "compressions": state.compression_count,
        }