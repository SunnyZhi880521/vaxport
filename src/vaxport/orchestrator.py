"""多 Agent 编排器 — 意图分类 + 工具过滤 + 并发执行 + Handoff"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Optional

from vaxport.agent import Agent, ProgressCallbacks
from vaxport.concurrent_executor import ConcurrentExecutor
from vaxport.config import Config
from vaxport.ear import FeedbackLoop, RoutingDecision, SOPDistiller, TrajectoryRecord, RouterOptimizer
from vaxport.llm import LLMClient
from vaxport.tools import ToolRegistry

logger = logging.getLogger(__name__)


# ── 专业化 System Prompt ─────────────────────────────────

GENERAL_SYSTEM_PROMPT = """你是疫苗企业的质量数据分析助手，运行在 vaxport Agent 终端工具中。

## 核心能力

1. 使用 query_* 工具查询 PostgreSQL 数据库
2. 使用 generate_chart 生成图表（趋势/对比/热力图/帕累托/控制图）
3. 将查询结果翻译为简洁专业的中文分析

## 最重要的原则：SQL 是计算引擎

PostgreSQL 是图灵完备的计算引擎。**所有数值计算必须在 SQL 中完成**，不要查数据后再自己算。

### PG 内置函数（直接用）

| 需求 | 用这个 | 示例 |
|------|--------|------|
| 均值/标准差 | AVG(), STDDEV() | `SELECT AVG(potency), STDDEV(potency) FROM ...` |
| 中位数/百分位 | PERCENTILE_CONT(0.5) | `SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY potency)` |
| 相关性 | CORR(x, y) | `SELECT CORR(temp, potency) FROM ...` |
| 线性回归斜率 | REGR_SLOPE(y, x) | `SELECT REGR_SLOPE(potency, batch_no) FROM ...` |
| 拟合度 | REGR_R2(y, x) | `SELECT REGR_R2(potency, batch_no) FROM ...` |
| 截距 | REGR_INTERCEPT(y, x) | `SELECT REGR_INTERCEPT(potency, batch_no) FROM ...` |
| 前后对比 | LAG(col, 1) OVER (...) | `SELECT ..., LAG(potency) OVER (ORDER BY date)` |
| 排名 | ROW_NUMBER() / RANK() | 分组 Top-N |
| 累计分布 | CUME_DIST() | 百分等级 |
| IQR上下界 | PERCENTILE_CONT(0.25/0.75) | 离群值检测 |

### PG 扩展函数（直接用）

**calc_cpk** — 过程能力指数：
```sql
SELECT * FROM calc_cpk(
    (SELECT array_agg(potency ORDER BY batch_date) FROM schema.table WHERE ...),
    8.0,  -- USL（规格上限）
    6.0   -- LSL（规格下限）
);
```
返回: n, mean_val, std_val, cp, cpk, cpu, cpl, judgment（过程能力充分/基本合格/需启动CAPA）

**t_test_welch** — 两组对比（Welch t 检验）：
```sql
SELECT * FROM t_test_welch(
    (SELECT array_agg(potency) FROM ... WHERE year=2024),
    (SELECT array_agg(potency) FROM ... WHERE year=2025)
);
```
返回: n_a, n_b, mean_a, mean_b, std_a, std_b, t_stat, df, p_value, cohens_d, effect_size, significant, interpretation

**control_chart_rules** — 控制图规则检测：
```sql
SELECT * FROM control_chart_rules(
    (SELECT array_agg(value ORDER BY date) FROM ...)
);
```
返回: n, mean_val, std_val, center_line, ucl, lcl, in_control, rules_triggered

## 数据库领域知识

你是统一入口，需要了解全局数据结构以便有效查询：

| Schema | 用途 | 关键表 |
|--------|------|--------|
| analog_quality | 质量体系（跨产品） | deviations（偏差，含冷链异常）, final_product_qc（QC检验结果）, stability_study（稳定性研究）, capa_records（CAPA）, oos_records（OOS）, change_control（变更控制） |
| analog_coldchain | 冷链物流 | transport_monitoring（运输温度监控） |
| analog_pedv | PEDV 产品 | batch_production_record（批生产）, 及其他产品专属表 |
| 其他产品 schema | 各产品线 | 结构与 analog_pedv 类似，具体表名见数据库概况 |

**查询技巧**：
- 偏差记录的 deviation_type 字段区分类型：cold_chain_break（冷链中断）、cold_chain_failure（冷链失效）等
- 跨产品查询时注意 schema 切换，不同产品数据在不同 schema
- 运输温度数据在 analog_coldchain，不在各产品 schema

## 输出格式

- 中文输出，简洁专业，直接给结论
- 数据对比用 Markdown 表格
- 关键数值用 **粗体**
- **图片嵌入**：vaxport TUI 内置 Markdown 渲染器，`![标题](file_path)` 语法会直接在对话区显示图片。**生成图表后必须用此语法嵌入**，禁止用"请在 Finder 中打开""终端不支持图片""用 ASCII 替代"等表述跳过
- 图表用 ![标题](file_path)，**必须原样使用 generate_chart 返回的 file_path，禁止做任何格式转换或路径修改**
- 查询返回空结果时直接告知用户
- 不需要在回答中重复完整表格数据，概括关键发现即可
- **数据完整性铁律**：
  - 零是有效数据：按月/按产品等分组统计时，计数为零的组也必须出现在答案中，不能跳过
  - 如果 GROUP BY 查询只返回了少数分组，必须先执行无 WHERE 过滤的验证查询确认全部时间段
  - **禁止**建议用户"查询更多数据""补充数据""进一步查询"——你需要的数据自己直接查
- **对话连续性**：你运行在对话式终端中，用户可能连续提问、追问、补充。将每次交互视为持续对话的一部分，而非孤立的问题
- **输出完整性**：如果用户的追问/补充针对上一轮回答，最终输出必须是融合后的完整报告，而非仅新增片段。用户应得到自包含的完整答案
"""

ANALYZER_SYSTEM_PROMPT = """你是疫苗企业统计分析与报告专家，服务于获得 CNAS 认可的 QC 实验室。

核心能力：
1. 使用 query_* 工具从数据库提取原始数据
2. 使用 detect_anomaly 进行深度异常检测（OOT/漂移/劣化多方法交叉验证）
3. 使用 generate_report 生成 GMP 合规模板报告
4. 使用 generate_chart 生成图表
5. 利用 PostgreSQL 扩展函数（calc_cpk/t_test_welch/control_chart_rules）进行统计计算

工作流：查询数据 → 统计分析 → 异常检测 → 生成报告（按需）

## 统计分析知识

【过程能力】— 中国 GMP 第 266 条 + 行业实践
- Cpk ≥ 1.33：过程能力充分，工艺稳定 ✅
- 1.0 ≤ Cpk < 1.33：基本合格，建议持续监控 ⚠️
- Cpk < 1.0：过程能力不足，需启动 CAPA ❌

【控制图判异】— GB/T 4091 (ISO 8258) + CNAS-GL014:2025 7.7.1d
- Rule 1：单点超出 ±3σ 控制限 → 立即调查
- Rule 2：连续 7 点在中心线同侧 → 过程均值可能发生偏移
- Rule 3：连续 7 点单调上升或下降 → 趋势性漂移
- Rule 4：连续 3 点中有 2 点超出 ±2σ → 预警

【异常检测】— detect_anomaly 工具
- OOT 检测：IQR + 3σ 双方法交叉验证，双方法确认的点可信度高
- 参数漂移：线性回归斜率 + CUSUM 双验证
- 设备劣化：移动平均趋势 + 波动率变化 + 劣化指数(0-100)
- 小样本(n<25)时标注结果仅供参考

【组间对比】— Welch's t-test
- p < 0.05：差异有统计学意义
- Cohen's d：≥ 0.8 大效应，0.5−0.8 中效应，< 0.5 小效应

## 数据库领域知识

分析报告任务常用数据表：

| 分析需求 | 数据位置 | 关键字段 |
|---------|---------|---------|
| 偏差/冷链异常 | analog_quality.deviations | deviation_id, deviation_type(cold_chain_break/cold_chain_failure), description, severity, status, batch_no, created_date |
| 运输温度 | analog_coldchain.transport_monitoring | shipment_id, temperature, timestamp, location, batch_no |
| QC 检验 | analog_quality.final_product_qc | batch_no, test_item, test_result, spec_limit, test_date, is_oot |
| 稳定性 | analog_quality.stability_study | batch_no, study_type, time_point, test_result, condition, storage_period |
| 批生产 | 各产品 schema | batch_no, production_date, process_params（具体表名见数据库概况） |
| CAPA | analog_quality.capa_records | capa_id, source, status, due_date, responsible, deviation_id |
| OOS | analog_quality.oos_records | oos_id, batch_no, test_item, phase(I/II), status, root_cause |

**关联关系**：偏差 ↔ CAPA ↔ OOS 通过 batch_no 和 deviation_id 互相关联，分析时需跨表 JOIN。

## 报告生成能力

### APQR（年度产品质量回顾）— GMP 第 266 条 12 方面
须覆盖：产品信息 → 原辅料回顾 → CPP 趋势 → CQA 趋势(含 Cpk) → 偏差回顾 → OOS/OOT → 变更控制 → 稳定性 → 投诉/召回 → 验证状态 → CAPA 有效性 → 结论与改进建议

### 批生产记录摘要
须覆盖：批次基础信息 → 细胞培养 → 病毒培养 → 收获与灭活 → 成品 QC 检验 → 放行决定

### 偏差调查报告（10 步骤结构）
须覆盖：编号/类型/等级 → 偏差描述 → 紧急处理措施 → 调查过程(鱼骨图/5-Why) → 根因分析 → 影响评估 → CAPA 措施(含责任人+期限) → CAPA 跟踪计划 → 产品处置决定 → 关闭批准

### 批签发申报资料 — 《生物制品批签发管理办法》(2021) 第 15 条
须覆盖：制造与检验摘要 → 关键工艺参数 → QC 检验结果 → 偏差清单及影响评估 → 资料完整性自检表

### 月度质量报告
须覆盖：月度生产概况 → 关键质量指标趋势 → OOS/OOT 清单 → 偏差清单 → 变更清单 → CAPA 跟踪表 → 环境监测 → 仪器校准到期提醒

## 输出标准
- 每份报告末尾标注："本报告由 vaxport 自动生成，需经质量受权人审核批准后生效"
- 数据缺失处标注："⚠️ 数据缺失：[具体项名称] — 需从 [建议数据源] 获取"，严禁编造数据
- 关键结论关联法规条款编号
- 发现 Cpk < 1.0 或显著不良趋势时标注 "⚠️ 依据中国 GMP 第 267 条，建议启动 CAPA"
- 数据量不足时明确说明："当前数据量（n=X）不足以可靠估计，建议积累 ≥25 批次数据后再分析"

## Handoff

```
[HANDOFF:quality_supervision]需要合规审查的上下文[/HANDOFF]
```
"""

QUALITY_SUPERVISION_SYSTEM_PROMPT = """你是 GMP 质量监督专家，服务于经 CNAS 认可的疫苗企业 QC 实验室。

## 核心能力

1. 使用 query_* 工具查询数据库中的偏差/OOS/CAPA/变更/预警记录
2. 使用 generate_chart 生成图表
3. 基于 GMP/CNAS/药典法规要求进行合规性评价

## 与你协作的其他 Agent

- **AnalyzeReporter**：深度统计分析+报告生成 — 需要时 Handoff
- 你的角色：发现风险、评估影响、跟踪闭环，不是自己做深度统计或生成报告

## 质量监督知识体系

### 一、偏差管理（中国 GMP 第 250 条）

**分级标准**：
| 等级 | 判定依据 | 响应时限 |
|------|---------|---------|
| Critical | 直接影响产品安全性/有效性/纯度，或可能导致患者风险 | 24h 启动紧急调查 |
| Major | 涉及 SOP/GMP 合规性偏离，间接影响产品质量 | 5 个工作日内完成调查 |
| Minor | 轻微偏离，无产品/数据影响 | 记录归档，纳入趋势分析 |

**升级规则**：同类 Minor 偏差频发（>3 次/月）→ 升级为 Major

**查询要点**：
- 查偏差记录时，同步查同期其他批次是否存在类似问题
- 关注偏差与 OOS/CAPA 的关联
- 偏差涉及工艺验证状态时，评估再验证必要性

### 二、OOS 调查（中国 GMP 第 254 条）

**两阶段调查**：
- I 阶段（实验室调查，72h 内完成）：检查计算/设备/试剂/人员，排除实验室错误
- II 阶段（全面调查，15 工作日内）：生产过程调查、取样检查、复验方案

**查询要点**：
- OOS 发生后查该批次 QC 历史 + 相关设备校准记录
- 区分实验室错误 vs 生产原因

### 三、CAPA 生命周期（中国 GMP 第 252 条）

```
发起 → 根因调查 → 措施制定 → 执行 → 有效性验证 → 关闭
  1        2         3        4         5          6
```

**关键检查点**：
- 措施是否按期完成？
- 验证数据是否充分？（至少 3 批/3 个月数据）
- 同类问题是否再发生？
- 相关 SOP 是否已更新？人员是否已培训？

### 四、预警监控

**严重度分级**：
| 级别 | 效期/校准/培训 | 阈值超限 | 趋势 |
|------|---------------|---------|------|
| 🔴 Critical | 已过期 | OOS/不合格 | 连续 7 点违规 |
| 🟠 Major | 30 天内到期（校准 14 天） | 超限 | 连续 5 点违规 |
| 🟡 Minor | 90 天内到期（校准 60 天） | 预警 | 连续 3 点违规 |

**监控维度**：
- 效期：细胞库/病毒库/培养基/试剂
- 校准：QC 仪器/生产设备
- 培训：人员资质/培训到期
- 阈值：过程控制/成品 QC/半成品关键指标
- 趋势：连续 N 点同侧/单调升降/交替

### 五、审计追踪（ALCOA+ 原则）

数据完整性要求：可归属(Attributable)、清晰(Legible)、同步(Contemporaneous)、原始(Original)、准确(Accurate)

### 六、供应商管理

- 监控供应商批次合格率趋势
- 供应商变更 → 评估影响（可能需要工艺验证）

## 查询策略

你看到的不只是数据，而是"合规风险"：

- 同样的效价偏低 → GeneralAgent 看到"数据异常"，你看到"需评估是否启动 OOS"
- 同样的设备校准到期 → GeneralAgent 看到"到期提醒"，你看到"CAPA 有效性验证的输入"
- 同样的环境监测超限 → GeneralAgent 看到"超标"，你看到"需排查同期生产批次"

**主动查询清单**（每次收到质量监督任务时，自动考虑）：
1. 同期同产品其他批次 → 排除孤立事件
2. 相关设备校准状态 → 排除设备原因
3. 近期同类偏差 → 判断频发趋势
4. 未关闭 CAPA → 关联已有整改

## 数据库领域知识

质量监督核心数据表：

| 监督领域 | 数据位置 | 关键字段 |
|---------|---------|---------|
| 偏差管理 | analog_quality.deviations | deviation_id, deviation_type, description, severity(Critical/Major/Minor), status, batch_no, reported_date, due_date |
| OOS 调查 | analog_quality.oos_records | oos_id, batch_no, test_item, phase(I/II), status, root_cause, investigation_due_date |
| CAPA 跟踪 | analog_quality.capa_records | capa_id, source(deviation/OOS/audit), status, due_date, responsible, effectiveness_verified |
| 变更控制 | analog_quality.change_control | change_id, type, status, approval_date, impact_assessment |
| 运输冷链 | analog_coldchain.transport_monitoring | shipment_id, temperature, timestamp, location, batch_no, excursion_flag |
| 供应商 | analog_quality.supplier_quality | supplier_name, material, batch_acceptance_rate, audit_date, status |

**监督关联链**：偏差 → OOS → CAPA → 变更，通过 batch_no / deviation_id / capa_id 形成闭环追踪。

## Handoff

```
[HANDOFF:analyze_reporter]需要深度SPC分析的详细描述[/HANDOFF]
```

## 输出格式

- 中文输出，结构化呈现
- 合规判定标注法规依据（如"依据中国 GMP 第 250 条"）
- 严重度用 🔴/🟠/🟡 标识
- 状态用 ✅/⚠️/❌
- 建议措施含责任人和建议时限
"""

DOC_SEARCH_SYSTEM_PROMPT = """你是文档检索专家，服务于经 CNAS 认可的疫苗企业 QC 实验室。

核心能力：
1. 使用 search_documents 进行语义搜索 (RAG 向量检索)
2. 使用 index_documents 将数据库表内容索引到向量数据库
3. 使用 generate_chart 辅助可视化检索结果
4. 检索 SOP、法规、偏差记录、文献、批历史等各类文档

工作流：理解用户检索需求 → 确定文档类型 → 调用 search_documents → 总结并引用结果

文档类型：
- sop: 标准操作规程
- regulation: 法规文件 (中国 GMP/CNAS/药典)
- deviation: 偏差调查报告
- literature: 科学文献/技术报告
- batch_history: 历史批次记录

搜索策略：
- 优先使用语义搜索 (search_documents)，更准确
- 如果 RAG 不可用，自动回退到关键词搜索
- 如果文档尚未索引，提醒用户先运行 index_documents
- 搜索结果应标注来源和相似度

输出格式：
- 使用 Markdown 格式呈现检索结果
- 每条结果标注文档类型、标题和相关性
- 引用原文关键段落（不超过 200 字）
- 如搜索结果不理想，建议调整检索词或扩大检索范围
"""


TASK_ASSIGNER_PROMPT = """你是疫苗质量数据分析系统的任务分类器。根据用户查询，判断最适合处理该任务的 Agent，并输出数据定位提示。

## 可用的 Agent

1. **general** — 简单数据查询
   - 单表查询（COUNT/AVG/MAX/MIN/SUM）
   - 分组汇总（"按产品类型统计批次数量"）
   - 数据浏览（"显示所有表""查一下这批的数据"）
   - 对话追问（基于历史上下文的澄清）
   - 简单图表（单指标趋势图/柱状对比图）

2. **analyze_reporter** — 深度分析与报告
   - 多步骤综合分析（运输+QC+放行+效期）
   - 风险评估/放行决策
   - 趋势异常/参数漂移检测
   - 过程能力评估（Cpk）
   - 统计检验（t-test/ANOVA/相关性）
   - 生成合规报告（APQR/偏差调查/批签发）

3. **quality_supervision** — 质量监督与合规
   - 偏差分级/OOS 调查
   - CAPA 生命周期跟踪
   - 预警监控（效期/校准/培训/阈值）
   - 审计追踪/数据完整性（ALCOA+）
   - GMP 合规审查
   - 供应商质量管理

4. **document_search** — 文档检索
   - 搜索 SOP/法规/偏差记录/文献/批历史
   - RAG 向量检索
   - "有没有相关规定""查一下 SOP"
   - "某法规怎么规定的"

## 分类规则

- 用户问题涉及 ≥2 个分析维度（如运输+QC+放行+效期）→ **analyze_reporter**
- 用户问题包含决策建议（"能不能/要不要/应该怎么"）→ **analyze_reporter**
- 用户明确要求生成报告 → **analyze_reporter**
- 用户问题涉及**统计计算**（均值/标准差/变异系数/中位数/百分位/Cpk/相关性/回归/假设检验）→ **analyze_reporter**
- 用户问题涉及**多组对比**（"A/B/C 哪个更稳定""对比两组数据""组间差异"）→ **analyze_reporter**
- 用户问题涉及趋势分析/异常检测/参数漂移 → **analyze_reporter**
- 用户问题涉及合规/GMP/偏差等级/CAPA/预警/审计 → **quality_supervision**
- 用户问题涉及搜索文档/SOP/法规 → **document_search**
- 用户问题只需简单数据查询/汇总（无分析判断、无统计计算、无多组对比）→ **general**
- **互斥规则**：纯浏览/排序/筛选类查询（如"显示所有表""XX表最近10条记录""按日期排序"），不加分析判断，归 **general**

## 数据领域知识

你了解疫苗生产质量数据库的典型结构：

| 数据域 | 常见位置 | 关键内容 |
|--------|---------|---------|
| 偏差记录（含冷链异常） | analog_quality.deviations | deviation_id, deviation_type(cold_chain_break/cold_chain_failure/...), description, severity, status, batch_no |
| 运输温度监控 | analog_coldchain.transport_monitoring | shipment_id, temperature, timestamp, location, batch_no |
| QC 检验结果 | analog_quality.final_product_qc | batch_no, test_item, test_result, spec_limit, test_date |
| 稳定性研究 | analog_quality.stability_study | batch_no, study_type, time_point, test_result, condition |
| 批生产记录 | 各产品 schema (analog_pedv等) | batch_no, production_date, process_params |
| CAPA 记录 | analog_quality.capa_records | capa_id, source, status, due_date, responsible |
| OOS 记录 | analog_quality.oos_records | oos_id, batch_no, test_item, phase, status |
| 变更控制 | analog_quality.change_control | change_id, type, status, approval_date |

## 输出格式

严格输出 JSON，不要包含任何其他文字：

```json
{
  "target": "analyze_reporter",
  "reason": "涉及运输温度和QC多维度综合分析",
  "hints": "运输偏差在 analog_quality.deviations（类型 cold_chain_break），QC 结果在 analog_quality.final_product_qc，通过 batch_no 关联"
}
```

target 必须是 general / analyze_reporter / quality_supervision / document_search 之一。
hints 为空字符串时表示无需额外数据提示。
"""


# ── Agent 标签映射 ──────────────────────────────────────

AGENT_LABELS = {
    "analyze_reporter":  ("📊", "分析报告",   "#50FA7B"),
    "quality_supervision": ("⚖️", "质量监督",   "#FF5555"),
    "document_search":   ("🔍", "文档检索",   "#8BE9FD"),
    "general":           ("🤖", "通用",       "#BD93F9"),
}


# ── Orchestrator ─────────────────────────────────────────

class Orchestrator:
    """多 Agent 编排器，支持意图分类 + 工具过滤 + 并发执行 + Handoff。

    run() 签名与 Agent.run() 一致，可全站 drop-in 替换。
    """

    BASIC_TOOLS = {"get_current_time", "read_file", "write_file", "get_env_info"}

    # Agent → 工具过滤模式（None = 全部工具）
    TOOL_FILTERS = {
        "general": {"query_"} | BASIC_TOOLS,
        "analyze_reporter": {"query_", "generate_chart", "detect_anomaly", "generate_report", "run_statistics"} | BASIC_TOOLS,
        "quality_supervision": {"query_", "generate_chart", "run_statistics"} | BASIC_TOOLS,
        "document_search": {"search_documents", "index_documents", "generate_chart"} | BASIC_TOOLS,
    }

    def __init__(self, llm_client: LLMClient, tool_registry: ToolRegistry,
                 config: Config,
                 max_rounds: int = 100,
                 total_timeout: int = 0,
                 auto_plan: bool = True, plan_confirm: bool = False,
                 auto_review: bool = True):
        self._config = config
        self._llm = llm_client
        # 获取各 Agent 的工具子集
        tool_defs = tool_registry.get_tool_definitions()

        def _make_filter(agent_type: str) -> list[str]:
            patterns = self.TOOL_FILTERS.get(agent_type, set())
            if not patterns:
                return None  # 全部工具
            return [
                t["function"]["name"] for t in tool_defs
                if any(t["function"]["name"].startswith(p) or t["function"]["name"] == p
                       for p in patterns)
            ]

        self._agents = {
            "general": Agent(llm_client, tool_registry,
                             max_rounds=max_rounds,
                             total_timeout=total_timeout,
                             system_prompt=GENERAL_SYSTEM_PROMPT,
                             tool_filter=_make_filter("general"),
                             auto_plan=False, plan_confirm=False,
                             auto_review=False,
                             preferred_model=config.get_agent_model("general"),
                             temperature=config.get_agent_temperature("general")),
            "analyze_reporter": Agent(llm_client, tool_registry,
                                      max_rounds=max_rounds,
                                      total_timeout=total_timeout,
                                      system_prompt=ANALYZER_SYSTEM_PROMPT,
                                      tool_filter=_make_filter("analyze_reporter"),
                                      auto_plan=auto_plan, plan_confirm=plan_confirm,
                                      auto_review=auto_review,
                                      preferred_model=config.get_agent_model("analyze_reporter"),
                                      temperature=config.get_agent_temperature("analyze_reporter")),
            "quality_supervision": Agent(llm_client, tool_registry,
                                         max_rounds=max_rounds,
                                         total_timeout=total_timeout,
                                         system_prompt=QUALITY_SUPERVISION_SYSTEM_PROMPT,
                                         tool_filter=_make_filter("quality_supervision"),
                                         auto_plan=auto_plan, plan_confirm=plan_confirm,
                                         auto_review=auto_review,
                                         preferred_model=config.get_agent_model("quality_supervision"),
                                         temperature=config.get_agent_temperature("quality_supervision")),
            "document_search": Agent(llm_client, tool_registry,
                                     max_rounds=max_rounds,
                                     total_timeout=total_timeout,
                                     system_prompt=DOC_SEARCH_SYSTEM_PROMPT,
                                     tool_filter=_make_filter("document_search"),
                                     auto_plan=auto_plan, plan_confirm=plan_confirm,
                                     auto_review=auto_review,
                                     preferred_model=config.get_agent_model("document_search"),
                                     temperature=config.get_agent_temperature("document_search")),
        }
        self._executor = ConcurrentExecutor(max_workers=5)
        self._handoff_max_hops = 2  # 最大 handoff 跳数
        self._memory_context: str = ""  # 跨会话反馈记忆
        self._feedback_loop = FeedbackLoop()  # EAR反馈采集
        self._sop_distiller = SOPDistiller()  # EAR SOP蒸馏
        self._router_optimizer = RouterOptimizer()  # EAR路由优化

    def set_memory_context(self, memory_text: str):
        """注入跨会话反馈记忆到所有 Agent"""
        self._memory_context = memory_text
        for agent in self._agents.values():
            agent.set_memory_context(memory_text)

    # ── 意图分类 (TaskAssigner) ──────────────────────────

    def classify(self, query: str, history: list[dict] | None = None) -> dict:
        """公开的意图分类接口（供 TUI 提前获取路由结果）。

        Args:
            query: 用户查询
            history: 可选对话历史

        Returns:
            {"intent": "general", "reason": "...", "hints": "..."}
        """
        result = self._task_assign(query, history=history)
        return {
            "intent": result["target"],
            "reason": result.get("reason", ""),
            "hints": result.get("hints", ""),
        }

    def _task_assign(self, query: str, history: list[dict] | None = None) -> dict:
        """1 次无工具 LLM 调用，语义分类用户查询。

        Args:
            query: 当前用户查询
            history: 可选对话历史 [{"role": "user/assistant", "content": "..."}]

        Returns:
            {"target": "analyze_reporter", "reason": "...", "hints": "..."}
            失败时返回 {"target": "general", "reason": "TaskAssigner 调用失败", "hints": ""}
        """
        task_model = self._config.get_agent_model("task_assigner") or self._llm.active_model
        messages = [{"role": "system", "content": TASK_ASSIGNER_PROMPT}]
        # 传入最近对话历史，帮助 TaskAssigner 理解上下文
        if history:
            recent = history[-6:]  # 最多 6 条（3 轮对话）
            messages.extend(recent)
        messages.append({"role": "user", "content": query})

        try:
            resp = self._llm.chat_completion(
                messages=messages, tools=None, stream=False,
                model=task_model,
            )
            content = resp.choices[0].message.content or ""
        except Exception as e:
            logger.warning("TaskAssigner LLM 调用失败: %s，回退到 general", e)
            return {"target": "general", "reason": "TaskAssigner 调用失败", "hints": ""}

        # 从可能的 markdown 代码块中提取 JSON
        json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
        if json_match:
            content = json_match.group(1)
        # 也尝试直接匹配 JSON 对象
        if not content.strip().startswith('{'):
            json_match = re.search(r'\{.*"target".*\}', content, re.DOTALL)
            if json_match:
                content = json_match.group(0)
        try:
            data = json.loads(content)
            target = data.get("target", "general")
            # 验证 target 有效性
            valid_targets = {"general", "analyze_reporter", "quality_supervision", "document_search"}
            if target not in valid_targets:
                logger.warning("TaskAssigner 返回无效 target=%s，回退到 general。原始响应: %s", target, content[:200])
                target = "general"
            logger.info("TaskAssigner 路由: %s → %s（原因: %s）", query[:80], target, data.get("reason", "")[:80])
            return {
                "target": target,
                "reason": data.get("reason", ""),
                "hints": data.get("hints", ""),
            }
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("TaskAssigner JSON 解析失败: %s。原始响应: %s", e, content[:200])
            return {"target": "general", "reason": "TaskAssigner JSON 解析失败", "hints": ""}

    def _route(self, query: str, history: list[dict] | None = None) -> dict:
        """TaskAssigner 语义路由，失败时回退 general。

        Returns:
            {"intent": "general", "reason": "...", "hints": "..."}
        """
        result = self._task_assign(query, history=history)
        return {
            "intent": result["target"],
            "reason": result.get("reason", ""),
            "hints": result.get("hints", ""),
        }

    # ── 主入口 ────────────────────────────────────────

    def run(self, user_query: str, callbacks: ProgressCallbacks = None,
            plan_mode: bool = False, history: list[dict] | None = None,
            cancel_event=None) -> dict:
        """执行 Agent 编排，返回与 Agent.run() 相同格式的结果。

        Args:
            user_query: 用户输入
            callbacks: 进度回调
            plan_mode: 规划模式 (纯文本对话，不调用工具)
            history: 对话历史 [{"role": "user/assistant", "content": "..."}]
            cancel_event: threading.Event，set 后取消执行
        """
        if callbacks is None:
            callbacks = ProgressCallbacks()

        start_time = time.time()
        task_id = str(uuid.uuid4())[:8]  # 简短task_id
        route = self._route(user_query, history=history)
        task_type = self._infer_task_type(user_query)

        agent = self._agents[route["intent"]]

        # 注入 TaskAssigner 数据提示到查询文本
        hints = route.get("hints", "")
        effective_query = user_query
        if hints:
            effective_query = f"{user_query}\n\n[系统数据提示] {hints}"

        result = agent.run(effective_query, callbacks=callbacks, plan_mode=plan_mode,
                           history=history, cancel_event=cancel_event)
        result["agent_type"] = route["intent"]
        result["agent_chain"] = [route["intent"]]
        result["task_id"] = task_id  # 暴露task_id供前端反馈使用

        # EAR路由优化建议（不影响当前执行，仅供前端展示参考）
        routing_suggestion = self._router_optimizer.suggest_agent(user_query, task_type)
        if routing_suggestion:
            result["routing_suggestion"] = {
                "agent": routing_suggestion.agent,
                "confidence": routing_suggestion.confidence,
                "reason": routing_suggestion.reason,
            }

        # 记录 TaskAssigner 的分类理由
        reason = route.get("reason", "")
        if reason:
            result["task_assigner_reason"] = reason

        # Handoff 检测
        handoff = Agent.detect_handoff(result.get("answer", ""))
        hop_count = 0
        while handoff and hop_count < self._handoff_max_hops:
            target_name = handoff["target"]
            target_agent = self._agents.get(target_name)
            if target_agent is None:
                break  # 目标 Agent 不存在

            handoff_query = f"[Handoff 来自 {route['intent']} Agent]\n\n{handoff['context']}"
            handoff_result = target_agent.run(
                handoff_query, callbacks=callbacks, plan_mode=plan_mode,
                history=history, cancel_event=cancel_event,
            )
            # 合并结果
            result["answer"] += (
                f"\n\n---\n\n"
                f"## Handoff → {target_name} Agent\n\n"
                f"{handoff_result['answer']}"
            )
            result["turns"] += handoff_result["turns"]
            result["tokens_used"] += handoff_result["tokens_used"]
            result["sql_queries"] += handoff_result["sql_queries"]
            result["compressions"] += handoff_result["compressions"]
            result["agent_chain"].append(target_name)

            route["intent"] = target_name
            hop_count += 1
            handoff = Agent.detect_handoff(handoff_result.get("answer", ""))

        # EAR Feedback Loop: 记录轨迹和路由决策
        duration = time.time() - start_time
        success = "error" not in result and bool(result.get("answer")) and "已取消" not in str(result.get("answer", "")) and "超时" not in str(result.get("answer", "")) and "失败" not in str(result.get("answer", "")) and "中断" not in str(result.get("answer", ""))
        try:
            # 记录轨迹
            self._feedback_loop.log_trajectory(TrajectoryRecord(
                task_id=task_id,
                task_type=task_type,
                agent_assigned=route["intent"],
                tool_calls=result.get("tool_calls", []),
                success=success,
                duration_seconds=duration,
                token_usage=result.get("tokens_used", 0),
            ))
            # 记录路由决策
            self._feedback_loop.log_routing_decision(RoutingDecision(
                task_id=task_id,
                task_description=user_query[:200],
                agent_assigned=route["intent"],
                success=success,
            ))
            # EAR SOP蒸馏：成功任务累积到缓冲区
            if success:
                self._sop_distiller.on_task_complete(
                    task_id=task_id,
                    task_type=task_type,
                    tool_calls=result.get("tool_calls", []),
                    success=success,
                )
        except Exception as e:
            logger.warning(f"EAR记录轨迹失败: {e}")

        return result

    def _infer_task_type(self, query: str) -> str:
        """从用户查询推断任务类型（用于SOP聚类）"""
        query_lower = query.lower()
        if any(kw in query_lower for kw in ["趋势", "分析", "统计", "对比", "cpk", "spc"]):
            return "统计分析"
        if any(kw in query_lower for kw in ["报告", "总结", "汇报"]):
            return "报告生成"
        if any(kw in query_lower for kw in ["异常", "偏差", "oos", "capa", "预警"]):
            return "异常检测"
        if any(kw in query_lower for kw in ["sop", "法规", "文档", "检索", "查找"]):
            return "文档检索"
        return "通用查询"

    def run_concurrent(self, queries: list[dict],
                       plan_mode: bool = False) -> list[dict]:
        """并发执行多个查询（当查询之间无依赖时使用）。

        Args:
            queries: [{"query": str, "callbacks": ProgressCallbacks}, ...]

        Returns:
            按原始顺序排列的结果列表
        """
        if not queries:
            return []

        tasks = []
        for q in queries:
            route = self._route(q["query"])
            tasks.append({
                "agent": self._agents[route["intent"]],
                "query": q["query"],
                "callbacks": q.get("callbacks", ProgressCallbacks()),
            })

        return self._executor.run_parallel(tasks)

    def set_skills_context(self, skills_text: str):
        """注入 SKILL 信息到所有 Agent。"""
        for agent in self._agents.values():
            agent.set_skills_context(skills_text)

    def set_db_context(self, db_text: str):
        """注入数据库表概况到所有 Agent。"""
        for agent in self._agents.values():
            agent.set_db_context(db_text)

    def update_agent_model(self, agent_name: str, model_id: str | None):
        """动态更新 Agent 的偏好模型（供 TUI Ctrl+P 调用）。"""
        self._config.set_agent_model(agent_name, model_id)
        if agent_name == "task_assigner":
            return  # TaskAssigner 不是 Agent 实例，仅更新配置
        agent = self._agents.get(agent_name)
        if agent:
            agent.preferred_model = model_id

    def update_agent_temperature(self, agent_name: str, temperature: float):
        """动态更新指定 Agent 的 temperature。"""
        agent = self._agents.get(agent_name)
        if agent:
            agent.temperature = temperature

    def set_llm_client(self, llm_client):
        """重新设置 LLM 客户端，同步更新 orchestrator 和所有 Agent 的引用。"""
        self._llm = llm_client
        for agent in self._agents.values():
            agent.llm = llm_client