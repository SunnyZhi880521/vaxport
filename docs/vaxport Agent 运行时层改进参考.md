# 外部材料分析：vaxport Agent 运行时层改进参考（v2）

> 分析日期：2026-06-02
> 版本：v2（vaxport视角重写）

## 材料来源

1. **小红书** —「持续学习 Agent 的 Adapter 层」(by Orkas)
2. **论文** — *Adapting the Interface, Not the Model: Runtime Harness Adaptation for Deterministic LLM Agents* (arXiv:2605.22166v2, 2026-05-27, PKU)

---

## 一、vaxport 当前架构

```
Go TUI (Bubble Tea) ←── HTTP/SSE ──→ Python FastAPI (localhost:8931)
                                          │
                                          ├── orchestrator.py（Agent路由+计划确认）
                                          ├── agent.py（多Agent定义）
                                          ├── tools.py（30+业务工具脚本）
                                          ├── schema_catalog.py（DB schema元数据）
                                          ├── memory.py（向量DB存知识）
                                          ├── session.py（SQLite存历史tool call）
                                          └── skills.py（Claude Code SKILL兼容层）
```

核心特征：**多Agent + 多脚本 + 计划确认 + 确定性业务环境**

---

## 二、两篇材料的核心思想

### 材料1：Adapter层（小红书）

**核心**：在Agent与工具之间插入Adapter，从纠错反馈中持续学习，不改模型参数。

**启发**：
- 用户反复修改报告格式 → 应记住偏好
- SQL查询经常出错 → 应缓存已验证模式
- Agent路由经常选错 → 应学习映射

### 材料2：LIFE-Harness（论文）

**核心**：不改模型权重，改运行时接口（Runtime Harness）。四层各司其职：

| 层级 | 职责 |
|------|------|
| **Environment Contract** | 交互前：校准工具描述和schema约束 |
| **Procedural Skill** | 交互中：检索和复用标准化流程（SOP） |
| **Action Realization** | 执行前：校验和规范化动作 |
| **Trajectory Regulation** | 执行后：监控轨迹健康度，检测死循环/停滞 |

**实验结论**：18个模型，116/126组提升，平均88.5%。Harness从4B小模型训练，迁移到其他模型同样有效。

**关键设计原则**：
- **Harness独立于模型** — 规则写在Python侧，客户换LLM不影响
- **不改现有业务代码** — 中间件/装饰器形式插入
- **可审计** — 每层干预都有日志

---

## 三、从vaxport出发的批判性审视

### 3.1 已有系统覆盖了EAR的部分职责

| EAR层 | vaxport已有 | 重叠度 |
|-------|------------|--------|
| Contract（schema同步） | `schema_catalog.py` | **高重叠** |
| Skill（SOP复用） | `skills.py` + `memory.py` + `session.py` | **部分重叠，定位不同** |
| Action（参数校验） | 无 | **无重叠** |
| Trajectory（轨迹监控） | 无 | **无重叠** |

### 3.2 逐层审视

#### Contract层 — **大部分多余**

- vaxport已有`schema_catalog.py`做schema元数据管理
- 论文的"从轨迹自动诊断schema偏差"对vaxport过度设计：
  - DB schema变更频率：每月几次
  - schema错误的信号很明显：SQL执行报错就完了
- **结论：不需要独立的Contract层，把schema同步作为schema_catalog的定时刷新即可**

#### Skill层 — **最大的过度设计**

论文假设：
- 有**大量成功轨迹**可蒸馏（benchmark几百条）
- 轨迹之间有**可泛化的结构**

vaxport现实：
- 用户交互量小（企业用户，每月几十次有意义的分析任务）
- 每次分析任务差异大（不同疫苗、不同指标、不同时间范围）

另外，原文提到的"报告模板学习"不是Skill层的事，是**用户偏好配置**。

**但SOP蒸馏不能推迟到"半年后再说"，应该持续积累+阈值触发。**

#### Action层 — **唯一真正有新价值且简单的层**

SQL执行前校验语法、安全检查（防DROP/DELETE）、参数合法性：
- 实现简单（几百行代码）
- 不依赖任何数据积累
- 立即见效

**保留，作为Phase 1唯一重点。**

#### Trajectory层 — **有价值但阈值全是拍脑袋的**

死循环检测和token预算有用，但：
- max_retries=3, max_steps=20, loop_window=5, token_budget=50000 — 全无根据
- vaxport的tool call比benchmark简单得多，不太会出现复杂死循环
- **保留核心机制，但阈值必须从实际使用中校准**

### 3.3 三个被忽略的问题

#### 1. 反馈信号从哪来？

vaxport目前**没有结构化的用户反馈机制**：
- 用户说"这个报告格式不对" — 自然语言，不是训练信号
- 用户重新提交修改后的报告 — 隐式反馈，但怎么捕获？

**论文在benchmark里不需要考虑这个问题，因为benchmark有标准答案。vaxport没有。**

如果不解决反馈信号采集，Skill层的"蒸馏SOP"就是空谈。

#### 2. Agent路由问题被完全忽略了

原文提到的"Agent切换路由"在四层框架里找不到对应。但这可能是vaxport最大的痛点之一：
- 用户问统计分析问题 → orchestrator选错Agent → 结果不对
- 比SQL语法错误更常见、后果更严重

**EAR框架没覆盖orchestrator的路由决策层。**

#### 3. 怎么衡量改进？

EAR没有定义成功指标。加了中间件后：
- Agent任务成功率提高了多少？
- token消耗降低了多少？
- 用户满意度有变化吗？

**没有度量就没有改进的依据。实施前必须定义。**

---

## 四、精简后的方案：vaxport EAR

### 设计原则

- **Harness独立于模型** — 规则写在Python侧，适配客户换不同本地LLM
- **不改现有30+业务文件** — 中间件/装饰器形式插入
- **可审计** — 每层干预都有日志，符合疫苗行业合规
- **持续积累，阈值触发** — 数据到了就自动蒸馏，不等待

### 架构

```
┌─────────────────────────────────────────────────────────┐
│ orchestrator.py                                         │
│  ├── [Phase 3] SOP检索（命中→按SOP执行，未命中→正常规划）│
│  ├── [Phase 4] Agent路由优化（数据积累后）                │
│  └── 分配给Agent                                        │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│ Agent 构造 tool call                                    │
│  ├── [Phase 1] Guard Rails — 前置校验                   │
│  │   ├── SQL语法检查                                    │
│  │   ├── 安全检查（防DROP/DELETE）                      │
│  │   └── 参数合法性校验                                 │
│  ├── Tool 执行                                          │
│  └── [Phase 1] Guard Rails — 执行监控                   │
│      ├── 重试上限检测                                    │
│      ├── 步数上限检测                                    │
│      └── token预算预警                                  │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│ 任务完成                                                │
│  ├── [Phase 2] Feedback Loop — 反馈采集                 │
│  │   ├── 显式反馈（用户满意/不满意）                    │
│  │   └── 隐式反馈（用户是否重新生成结果）               │
│  ├── [Phase 3] SOP蒸馏 — 轨迹累积                       │
│  │   ├── 成功轨迹结构化日志                             │
│  │   └── 累积50条 → 自动触发蒸馏                        │
│  └── [Phase 4] 路由决策记录                             │
└─────────────────────────────────────────────────────────┘
```

### Phase 1 — Guard Rails（立即实施）

**职责**：tool call前置校验 + 执行监控

```python
class GuardRails:
    def __init__(self):
        self.max_retries = 3          # [可配置，从使用校准]
        self.max_total_steps = 20     # [可配置]
        self.token_budget = 50000     # [可配置]

    def validate_tool_call(self, tool_name, params):
        """执行前校验"""
        if tool_name == "db_query":
            sql = params.get("query", "")
            # 1. SQL语法检查（用sqlparse，不发到DB）
            if not self.sql_validator.check(sql):
                return ValidationResult(blocked=True, reason="SQL语法错误")
            # 2. 安全检查
            if self.has_dangerous_ops(sql):
                return ValidationResult(blocked=True, reason="SQL包含危险操作")
        # 3. 参数合法性校验
        if tool_name == "generate_chart":
            if params.get("type") not in ["bar", "line", "pie", "control_chart"]:
                return ValidationResult(blocked=True, reason="不支持的图表类型")
        return ValidationResult(blocked=False)

    def monitor_trajectory(self, step, history):
        """每步执行后检查轨迹健康度"""
        # 1. 死循环检测
        if self.is_looping(history[-5:]):
            return RegulationAction(action="break_loop")
        # 2. 重试上限
        if self.count_same_failures(history) >= self.max_retries:
            return RegulationAction(action="escalate")
        # 3. 步数预算
        if len(history) >= self.max_total_steps:
            return RegulationAction(action="force_conclude")
        # 4. Token预算
        if sum(s.token_usage for s in history) > self.token_budget * 0.8:
            return RegulationAction(action="warn_budget")
        return RegulationAction(action="continue")
```

**成功指标**：
- tool call失败率降低50%
- 死循环/停滞检测准确率>80%

**预计工作量**：1-2天

### Phase 2 — Feedback Loop（第2周开始）

**职责**：结构化采集用户反馈，为后续学习提供数据

```python
class FeedbackLoop:
    def capture_explicit_feedback(self, task_id, satisfaction):
        """显式反馈：用户点满意/不满意"""
        self.feedback_store.add(task_id, satisfaction, timestamp=now())

    def capture_implicit_feedback(self, task_id, trajectory):
        """隐式反馈：用户是否重新生成了结果"""
        # 如果用户在10分钟内对同一类任务又发了一次请求，可能是隐式不满意
        recent_tasks = self.session_store.get_recent_tasks(user_id, minutes=10)
        if self.is_similar_task(task_id, recent_tasks):
            self.feedback_store.add(task_id, "implicit_retry", timestamp=now())

    def log_trajectory(self, trajectory):
        """结构化轨迹日志"""
        self.trajectory_store.add({
            "task_id": trajectory.task_id,
            "task_type": trajectory.task_type,  # 统计分析/报告生成/异常检测
            "agent_assigned": trajectory.agent_name,
            "tool_calls": trajectory.tool_calls,
            "success": trajectory.success,
            "duration_seconds": trajectory.duration,
            "token_usage": trajectory.token_usage,
        })
```

**成功指标**：
- 反馈采集覆盖率>60%（60%的任务有显式或隐式反馈）
- 轨迹日志完整度>90%

**预计工作量**：2-3天

### Phase 3 — SOP蒸馏（持续运行）

**职责**：持续积累成功轨迹，阈值触发蒸馏

```python
class SOPDistiller:
    def __init__(self):
        self.success_trajectories = []
        self.trigger_threshold = 50      # [可配置] 累积50条触发一次蒸馏
        self.min_similarity = 0.7        # [可配置] 相似度阈值
        self.min_cluster_size = 5        # [可配置] 最少几条相似任务才蒸馏

    def on_task_complete(self, trajectory):
        """每次成功任务后立即记录"""
        if trajectory.success:
            self.success_trajectories.append(trajectory)
        # 检查是否达到触发阈值
        if len(self.success_trajectories) >= self.trigger_threshold:
            self.distill()

    def distill(self):
        """触发蒸馏"""
        # 1. 按任务类型聚类
        clusters = self.cluster_by_task_type(self.success_trajectories)
        # 2. 对每个聚类生成SOP（如果数据够且相似度高）
        for cluster in clusters:
            if len(cluster) >= self.min_cluster_size and self.cluster_similarity(cluster) >= self.min_similarity:
                sop = self.extract_sop_from_cluster(cluster)
                self.sop_store.add(sop)
        # 3. 清空已蒸馏的轨迹
        self.success_trajectories.clear()

    def retrieve_sop(self, task_description, context):
        """SOP检索（orchestrator调用）"""
        task_embedding = self.embed(task_description)
        candidates = self.sop_store.search(task_embedding, top_k=3)
        filtered = self.filter_by_context(candidates, context)
        # 置信度阈值：低于阈值不返回，走正常规划
        if filtered and filtered[0].confidence >= 0.8:
            return filtered[0]
        return None
```

**SOP存储结构**：
```json
{
  "id": "sop_quality_trend_001",
  "trigger_pattern": "质量趋势分析|CPK分析|过程能力",
  "steps": [
    {"action": "db_query", "template": "SELECT batch_no, {metric} FROM batch_info WHERE vaccine_name='{vaccine}'"},
    {"action": "compute_statistics", "method": "cpk"},
    {"action": "generate_chart", "type": "control_chart"},
    {"action": "format_report", "template": "quality_trend"}
  ],
  "success_count": 23,
  "confidence": 0.85
}
```

**监控面板**：
```
┌── SOP蒸馏状态 ──────────────────────┐
│ 累积轨迹: 23/50  ████████░░░░░░░░  │
│ 下次触发: 还差27条成功任务           │
│ 已生成SOP: 3个                       │
│ 平均相似度: 0.82                     │
│ 上次蒸馏: 2026-05-28 (12条→2个SOP)  │
└──────────────────────────────────────┘
```

**成功指标**：
- SOP命中率>30%（30%的任务能匹配到SOP）
- 命中SOP的任务平均耗时降低40%

**预计工作量**：3-5天（蒸馏逻辑+检索逻辑）

### Phase 4 — 路由优化（Phase 2数据积累后）

**职责**：从历史路由决策中学习最优映射

```python
class RouterOptimizer:
    def log_routing_decision(self, task_description, agent_assigned, success):
        """记录每次路由决策"""
        self.routing_store.add({
            "task_description": task_description,
            "agent_assigned": agent_assigned,
            "success": success,
        })

    def suggest_agent(self, task_description):
        """基于历史数据建议Agent"""
        # 数据积累够100条后启用
        if self.routing_store.count() < 100:
            return None
        # 找到相似任务，看哪个Agent成功率最高
        similar_tasks = self.routing_store.search_similar(task_description, top_k=10)
        best_agent = self.find_best_agent(similar_tasks)
        return best_agent if best_agent.success_rate > 0.7 else None
```

**成功指标**：
- 路由建议准确率>70%
- 用户接受建议率>50%

**预计工作量**：2-3天（依赖Phase 2数据）

---

## 五、对比总结

| 维度 | 原EAR方案（论文驱动） | 精简方案（vaxport驱动） |
|------|----------|---------|
| 代码量 | ~2000行+ | ~500行 |
| 新增存储 | 6个 | 2个（feedback_log, trajectory_log） |
| 数据依赖 | 需要大量历史轨迹 | 不依赖历史数据，持续积累 |
| 见效时间 | Phase 2之后 | Phase 1立即 |
| 维护成本 | 高（6个存储、多个模型） | 低（规则+配置+阈值触发） |
| 覆盖度 | 4层但忽略了路由和反馈 | 4阶段覆盖反馈、路由、SOP蒸馏 |
| SOP蒸馏 | 立即建embedding+向量检索 | 持续积累+阈值触发（累积50条→自动跑） |

---

## 六、实施路径

```
Week 1: Phase 1 — Guard Rails
  ├── tool call前置校验（SQL/参数/安全）
  └── 执行监控（重试/步数/token上限）

Week 2: Phase 2 — Feedback Loop
  ├── 用户反馈采集（显式+隐式）
  ├── 成功轨迹结构化日志
  └── 路由决策记录

Week 3-4: Phase 3 — SOP蒸馏
  ├── 轨迹累积存储（每次成功任务）
  ├── 阈值触发蒸馏（累积50条→自动跑）
  ├── 相似度过滤（>=0.7才蒸馏）
  └── SOP检索复用（orchestrator调用）

Month 2+: Phase 4 — 路由优化
  └── Agent路由决策学习（依赖Phase 2数据积累100条）
```

---

## 七、参考资源

- 论文 PDF：`/Users/zhixiaoguang/Downloads/2605.22166v2.pdf`
- 小红书原文（6 张截图）：`/Users/zhixiaoguang/Downloads/持续学习_Agent_的_Adapter_层_*.jpg`
- 论文代码：[GitHub](https://github.com/tsxu/life-harness)
