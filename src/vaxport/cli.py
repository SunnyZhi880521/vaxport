"""CLI 入口 — REPL 终端交互 + 命令系统"""

import json
import time
import argparse
from pathlib import Path

from vaxport.config import Config, load_config
from vaxport.llm import LLMClient, create_llm_client
from vaxport.db import Database, create_database, MultiDatabase, create_multi_database
from vaxport.tools import ToolRegistry
from vaxport.agent import Agent, ProgressCallbacks
from vaxport.orchestrator import Orchestrator
from vaxport.skills import SkillRegistry
from vaxport.session import Session, write_audit_log, build_audit_entry
from vaxport import ui

class CLIProgressCallbacks(ProgressCallbacks):
    """CLI 层的进度回调 — 实时显示工具调用"""

    def __init__(self, debug_mode: bool = False):
        self.debug_mode = debug_mode

    def on_tool_call(self, tool_name: str, arguments: dict):
        ui.print_tool_call(tool_name, arguments)

    def on_tool_result(self, row_count: int, truncated: bool = False):
        ui.print_tool_result(row_count, truncated)

    def on_sql(self, sql: str):
        if self.debug_mode:
            ui.print_sql(sql)


class App:
    """vaxport 应用主控"""

    def __init__(self, config: Config):
        self.config = config
        self.llm: LLMClient = None
        self.db: Database = None
        self.tools: ToolRegistry = None
        self.orchestrator: Orchestrator = None
        self.skills: SkillRegistry = None
        self.session: Session = None
        self.debug_mode = False
        self._last_result: dict = {}  # 缓存最近一次查询结果，供 /status 使用

    def setup(self, resume_session: str = None, quiet: bool = False):
        """初始化所有组件"""
        self._quiet = quiet
        # 1. SKILL
        self.skills = SkillRegistry()
        self.skills.load_all()

        # 2. LLM
        self.llm = create_llm_client(self.config)

        # 3. DB（可选，连接失败不中断启动；多数据库支持）
        pg_status = "未连接"
        try:
            self.mdb = create_multi_database(self.config)
            self.db = self.mdb.get_active()  # 向后兼容（当前工作库）
            db_names = ", ".join(self.mdb.names)
            pg_status = f"{db_names}@{self.config.pg_host}"
        except Exception as e:
            self.mdb = None
            try:
                self.db = create_database(self.config)
                pg_status = f"{self.config.pg_host}/{self.config.pg_database}"
            except Exception as e2:
                ui.print_warning(f"数据库连接失败: {e2}\n部分功能不可用，可稍后重试。")

        # 4. Tool Registry
        self.tools = ToolRegistry(db=self.db, skill_registry=self.skills)

        # 注册内置 SKILL 工具
        self.tools.register(
            name="get_skill_detail",
            description="获取指定 SKILL 的完整说明文档。当需要了解某个技能的详细用法时使用。",
            parameters={
                "skill_name": {
                    "type": "string",
                    "description": "SKILL 名称，如 'paper-analyzer'",
                }
            },
            handler=lambda skill_name: (
                self.skills.get_skill_detail(skill_name)
                if self.skills else "SKILL 系统未初始化"
            ),
        )

        # 注册 schema 列表工具（含空 schema）
        self.tools.register(
            name="list_all_schemas",
            description="列出数据库中所有 schema 的名称、表数量、视图数量、物化视图数量。即使某个 schema 为空（表数为 0），也会显示。当你需要了解数据库中所有可用的 schema 时使用此工具。",
            parameters={},
            handler=lambda: (
                json.dumps(
                    {"schemas": self.db.list_all_schemas(),
                     "row_count": len(self.db.list_all_schemas())},
                    ensure_ascii=False, default=str
                )
                if self.db and self.db.is_connected
                else json.dumps({"error": "数据库未连接"}, ensure_ascii=False)
            ),
        )

        # 自动发现数据库 schema 并生成工具 (多库模式)
        # 单库时不传 db_name，保持工具名 query_schema_table（避免前缀膨胀）
        if self.mdb and self.mdb.is_connected and len(self.mdb.names) > 1:
            try:
                for name in self.mdb.names:
                    db = self.mdb.get(name)
                    self.tools.discover_and_register(db=db, db_name=name)
            except Exception as e:
                ui.print_warning(f"Schema 发现失败: {e}")
        elif self.db and self.db.is_connected:
            try:
                self.tools.discover_and_register()
            except Exception as e:
                ui.print_warning(f"Schema 发现失败: {e}")

        # 注册 SKILL Python 脚本
        if self.skills:
            self.tools.register_skill_scripts()

        # ── Schema 目录：持久化元数据缓存 ──
        from vaxport.schema_catalog import SchemaCatalog
        self.schema_catalog = SchemaCatalog()
        db_map = {}
        if self.mdb and self.mdb.is_connected:
            for name in self.mdb.names:
                db_map[name] = self.mdb.get(name)
        elif self.db and self.db.is_connected:
            db_map[self.config.pg_database] = self.db
        if db_map:
            try:
                self.schema_catalog.load_or_refresh(db_map)
            except Exception as e:
                ui.print_warning(f"Schema 目录加载失败: {e}")

        # ── 基础工具: 日期/文件/环境/表信息 ──
        self._register_basic_tools()

        # ── Phase 1: 注册报告生成工具 ──
        from vaxport.reports import generate_report as _generate_report
        self.tools.register(
            name="generate_report",
            description=(
                "生成 GMP 合规报告（Markdown 格式）。支持 5 种报告类型。"
                "apqr: 年度产品质量回顾报告（需 context 含 product_name/report_period/batches/metrics/deviations 等）。"
                "batch_record: 单批生产批记录摘要（需 context 含 batch_id/bioreactor_scale_l/cell_culture_summary/qc_result 等）。"
                "deviation_report: 偏差调查报告（需 context 含 deviation_id/description/investigation/root_cause/capa_actions/disposition）。"
                "lot_release: 批签发申报资料（需 context 含 batch_id/qc_result 等）。"
                "monthly_quality: 月度质量报告（需 context 含 month/batches/kpi 等）。"
                "context 和 params 均为 JSON 格式字符串。通常在调用前先用 query_* 工具收集所需数据。"
            ),
            parameters={
                "report_type": {
                    "type": "string",
                    "description": "报告类型: apqr, batch_record, deviation_report, lot_release, monthly_quality",
                },
                "context": {
                    "type": "string",
                    "description": "报告数据上下文，JSON 格式。将查询结果中的关键字段提取后传入",
                },
                "params": {
                    "type": "string",
                    "description": "额外参数，JSON 格式。如 '{\"product_name\":\"PEDV疫苗\"}'",
                },
            },
            handler=lambda report_type, context, params="{}": json.dumps(
                _generate_report(report_type, context, params), ensure_ascii=False, default=str
            ),
        )

        # ── Phase 1: 注册预置 SQL 模板 ──
        self._register_predefined_templates()

        # ── Phase 3: 注册 P1 工具 ──
        self._register_phase3_tools()

        # ── Phase 4: 注册 P2 工具 ──
        self._register_phase4_tools()

        # 5. Orchestrator（Phase 2: 多 Agent 编排 → Phase 3: 6 Agent）
        # CLI one-shot 模式：关闭交互确认和审核，保留规划以引导 ReAct 效率
        is_one_shot = not self._quiet
        self.orchestrator = Orchestrator(
            llm_client=self.llm,
            tool_registry=self.tools,
            config=self.config,
            max_rounds=self.config.max_tool_rounds,
            total_timeout=self.config.total_timeout,
            auto_plan=self.config.auto_plan,
            plan_confirm=False if is_one_shot else self.config.plan_confirm,
            auto_review=False if is_one_shot else self.config.auto_review,
        )

        # 注入 SKILL 上下文
        if self.skills:
            skills_prompt = self.skills.build_system_prompt_section()
            if skills_prompt:
                self.orchestrator.set_skills_context(skills_prompt)

        # 注入数据库表概况
        if self.db and self.db.is_connected:
            db_overview = self._build_db_overview()
            if db_overview:
                self.orchestrator.set_db_context(db_overview)

        # 注入 Schema 目录（补充外键、注释等元数据）
        if hasattr(self, 'schema_catalog') and self.schema_catalog._catalog:
            catalog_context = self.schema_catalog.build_context_section()
            if catalog_context:
                self.orchestrator.set_db_context(catalog_context)

        # 6. Session
        if resume_session:
            self.session = Session.load(resume_session)
            if self.session:
                ui.print_info(f"已恢复会话: {resume_session}")
            else:
                ui.print_warning(f"未找到会话: {resume_session}")
                self.session = Session()
        else:
            self.session = Session()

        # 7. 欢迎信息（textual 模式下跳过，由 TUI 自行渲染）
        if not self._quiet:
            ui.print_welcome(
                model=self.llm.active_model,
                backend=self.llm.active_backend,
                pg_host=self.config.pg_host,
                pg_db=self.config.pg_database,
                skills_count=self.skills.count if self.skills else 0,
            )

        # 8. 数据库表概览（textual 模式下跳过）
        if not self._quiet and self.db and self.db.is_connected:
            schema_summary = self.tools.get_schema_summary()
            ui.print_schema_overview(schema_summary)

    def handle_slash_command(self, cmd_line: str) -> bool:
        """处理 / 命令，返回 True 表示应退出 REPL"""
        parts = cmd_line.strip().split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        if cmd in ("/exit", "/quit"):
            self.session.save()
            ui.print_info("会话已保存。再见。")
            return True

        elif cmd == "/help":
            self._cmd_help()

        elif cmd == "/model":
            self._cmd_model(args)

        elif cmd == "/status":
            self._cmd_status()

        elif cmd == "/skills":
            self._cmd_skills()

        elif cmd == "/tools":
            self._cmd_tools()

        elif cmd == "/clear":
            self.session = Session()
            ui.print_info("对话上下文已清空。")

        elif cmd == "/history":
            self._cmd_history()

        elif cmd == "/debug":
            self.debug_mode = not self.debug_mode
            status = "开启" if self.debug_mode else "关闭"
            ui.print_info(f"调试模式已{status}")

        elif cmd == "/save":
            path = self.session.save(args if args else None)
            ui.print_info(f"会话已保存: {path}")

        elif cmd == "/refresh-schema":
            self._cmd_refresh_schema()

        else:
            ui.print_warning(f"未知命令: {cmd}。输入 /help 查看可用命令。")

        return False

    def _cmd_help(self):
        ui.console.print("""
[bold]可用命令:[/]

  [cyan]/model [aliyun|local][/]  切换 LLM 后端
  [cyan]/status[/]               显示当前状态
  [cyan]/skills[/]               列出已加载 SKILL
  [cyan]/tools[/]                列出可用查询工具
  [cyan]/clear[/]                清空对话上下文
  [cyan]/history[/]              当前会话对话摘要
  [cyan]/debug[/]                切换调试模式
  [cyan]/refresh-schema[/]       重新扫描数据库 schema
  [cyan]/save [name][/]          保存当前会话
  [cyan]/exit[/] 或 [cyan]/quit[/]  退出
  [cyan]/help[/]                 显示此帮助
""")

    def _cmd_model(self, args: str):
        if not args:
            ui.print_info(f"当前后端: {self.llm.active_backend}，模型: {self.llm.active_model}")
            ui.print_info(f"可用后端: {', '.join(self.llm.available_backends)}")
            return

        backend_map = {
            "aliyun": "aliyun",
            "local": "ollama",
            "ollama": "ollama",
        }
        target = backend_map.get(args.lower())
        if not target:
            ui.print_warning(f"未知后端: {args}。可用: aliyun, local (ollama)")
            return

        if self.llm.switch_backend(target):
            ui.print_info(f"已切换到 {target}，模型: {self.llm.active_model}")
        else:
            ui.print_warning(f"后端 {target} 不可用")

    def _cmd_status(self):
        backend_status = self.llm.get_status()
        last = self._last_result or {}
        ui.print_status({
            "agent_mode": "Orchestrator (4 Agent: 分析报告/质量监督/文档检索/通用)",
            "model": self.llm.active_model,
            "backend": "\n".join(
                f"{name}: {info['model']} ({'✓' if info['active'] else '✗'})"
                for name, info in backend_status.items()
            ),
            "tokens_used": last.get("tokens_used", 0),
            "context_window": last.get("context_window", 0),
            "token_pct": last.get("token_pct", 0),
            "pg_status": self._pg_status(),
            "turns": last.get("turns", 0),
            "skills_count": self.skills.count if self.skills else 0,
            "debug": self.debug_mode,
        })

    def _cmd_skills(self):
        if self.skills:
            skills = self.skills.list_skills()
            ui.print_skills_list(skills)
            ui.print_info(f"\n共 {len(skills)} 个 SKILL，目录: {self.skills.skills_dir}")
        else:
            ui.print_info("没有加载 SKILL")

    def _cmd_tools(self):
        tools = self.tools.list_tools()
        if tools:
            ui.print_tools_list(tools)
        else:
            ui.print_info("没有注册工具")

    def _cmd_history(self):
        summary = self.session.get_history_summary()
        if summary:
            ui.console.print(f"\n[bold]对话历史 ({len(self.session.messages)} 条消息):[/]")
            ui.console.print(summary)
        else:
            ui.print_info("当前会话无历史")

    def _cmd_refresh_schema(self):
        if not self.db or not self.db.is_connected:
            ui.print_warning("数据库未连接，无法刷新 schema")
            return
        try:
            if self.mdb and self.mdb.is_connected:
                for name in self.mdb.names:
                    db = self.mdb.get(name)
                    self.tools.discover_and_register(db=db, db_name=name)
            else:
                self.tools.discover_and_register()
            ui.print_info(f"Schema 已刷新，共 {len(self.tools.list_tools())} 个查询工具")
            # 同步更新 system prompt 中的数据库概况
            db_overview = self._build_db_overview()
            if db_overview and self.orchestrator:
                self.orchestrator.set_db_context(db_overview)
                ui.print_info("数据库概况已同步更新")
        except Exception as e:
            ui.print_error(f"Schema 刷新失败: {e}")

    def _pg_status(self) -> str:
        """多数据库连接状态"""
        if self.mdb and self.mdb.is_connected:
            db_list = ", ".join(
                f"{n}{'*' if n == self.mdb.active_name else ''}"
                for n in self.mdb.names
            )
            return f"{db_list}@{self.config.pg_host} (*=当前)"
        if self.db and self.db.is_connected:
            return f"{self.config.pg_host}/{self.config.pg_database} (已连接)"
        return "未连接"

    def _build_db_overview(self) -> str:
        """构建数据库表概况，注入 system prompt。LLM 查询前知道每表大概行数。"""
        dbs = []
        if self.mdb and self.mdb.is_connected:
            for name in self.mdb.names:
                dbs.append((name, self.mdb.get(name)))
        elif self.db and self.db.is_connected:
            dbs.append((self.config.pg_database, self.db))

        if not dbs:
            return ""

        lines = [
            "## 数据库表概况",
            "查询前检查行数：≤100 行可直接查全表，>500 行建议加 WHERE 过滤。",
            "[s]=≤100 [m]=100~1000 [l]=≥1000",
            "",
        ]

        for db_name, db in dbs:
            lines.append(f"### {db_name}")
            estimates = db.get_table_row_estimates()
            by_schema: dict[str, list] = {}
            for full_name, info in estimates.items():
                schema, table = full_name.split(".", 1)
                by_schema.setdefault(schema, []).append((table, info))

            for schema in sorted(by_schema.keys()):
                lines.append(f"  {schema}:")
                for table, info in sorted(by_schema[schema], key=lambda x: -x[1]["rows_estimate"]):
                    n = info["rows_estimate"]
                    tag = "s" if n <= 100 else "m" if n <= 1000 else "l"
                    col_names = [c["name"] if isinstance(c, dict) else c for c in info["columns"]]
                    cols = ", ".join(col_names[:8])
                    if len(col_names) > 8:
                        cols += ", ..."
                    flag = " ←大表，加过滤!" if n >= 1000 else ""
                    lines.append(f"    [{tag}~{n}] {table}: {cols}{flag}")
            lines.append("")

        return "\n".join(lines)

    def _register_predefined_templates(self):
        """Phase 1: 预注册常用跨表查询 SQL 模板"""
        templates = [
            # 1. 批次完整效价链
            {
                "name": "batch_full_chain",
                "description": "查询指定批次的完整效价链：种病毒效价 → 收获前效价 → 澄清后效价 → 半成品抗原含量 → 成品效价",
                "sql": """
                    SELECT
                        pb.batch_id,
                        vs.titer_tcid50_per_ml AS virus_seed_titer,
                        hi.pre_clarify_titer AS harvest_titer,
                        hi.post_clarify_titer AS clarified_titer,
                        sp.antigen_content_elisa_u_ml AS semi_product_elisa,
                        fq.potency_elisa AS final_potency_elisa,
                        fq.potency_tcid50 AS final_potency_tcid50
                    FROM production_batches pb
                    JOIN virus_seeds vs ON pb.virus_seed_id = vs.seed_id
                    JOIN harvest_inactivation hi ON pb.batch_id = hi.batch_id
                    JOIN semi_product sp ON pb.batch_id = sp.batch_id
                    JOIN final_product_qc fq ON pb.batch_id = fq.batch_id
                    WHERE pb.batch_id = %s
                """,
                "parameters": {"batch_id": {"type": "string", "description": "批次号"}},
            },
            # 2. OOS 汇总
            {
                "name": "oos_summary",
                "description": "查询指定时间段内所有过程控制和成品检验的 FAIL 项（OOS 汇总）",
                "sql": """
                    SELECT 'in_process' AS source, batch_id, sample_point, test_type,
                           result_value, spec_min, spec_max, test_date, notes
                    FROM in_process_tests
                    WHERE pass_fail = 'FAIL' AND test_date BETWEEN %s AND %s
                    UNION ALL
                    SELECT 'final_product' AS source, batch_id, '成品检验' AS sample_point,
                           CASE
                               WHEN sterility_test = 'FAIL' THEN 'sterility'
                               WHEN efficacy_challenge = 'FAIL' THEN 'efficacy_challenge'
                               WHEN potency_elisa < 32 THEN 'potency_elisa'
                               ELSE 'other'
                           END AS test_type,
                           CASE
                               WHEN sterility_test = 'FAIL' THEN 'FAIL'
                               WHEN efficacy_challenge = 'FAIL' THEN 'FAIL'
                               WHEN potency_elisa < 32 THEN potency_elisa::text
                               ELSE release_decision
                           END AS result_value,
                           NULL AS spec_min, NULL AS spec_max,
                           test_date, release_decision AS notes
                    FROM final_product_qc
                    WHERE (sterility_test = 'FAIL' OR efficacy_challenge = 'FAIL'
                           OR potency_elisa < 32 OR release_decision != 'released')
                      AND test_date BETWEEN %s AND %s
                    ORDER BY test_date DESC
                """,
                "parameters": {
                    "start_date": {"type": "string", "description": "开始日期 (YYYY-MM-DD)"},
                    "end_date": {"type": "string", "description": "结束日期 (YYYY-MM-DD)"},
                },
            },
            # 3. 批次质量汇总
            {
                "name": "batch_quality_summary",
                "description": "查询所有批次的质量汇总：生产信息 + 成品 QC 结果 + 放行决定",
                "sql": """
                    SELECT
                        pb.batch_id, pb.start_date, pb.bioreactor_scale_l,
                        pb.operator_team, pb.status AS batch_status,
                        fq.potency_elisa, fq.sterility_test,
                        fq.efficacy_challenge, fq.release_decision,
                        fq.test_date AS qc_date, fq.reviewer
                    FROM production_batches pb
                    LEFT JOIN final_product_qc fq ON pb.batch_id = fq.batch_id
                    ORDER BY pb.start_date
                """,
                "parameters": {},
            },
            # 4. 过程控制超标统计
            {
                "name": "in_process_fail_stats",
                "description": "按检测类型统计过程控制中的 FAIL 次数和涉及批次",
                "sql": """
                    SELECT
                        test_type,
                        COUNT(*) AS fail_count,
                        ARRAY_AGG(DISTINCT batch_id ORDER BY batch_id) AS affected_batches
                    FROM in_process_tests
                    WHERE pass_fail = 'FAIL'
                    GROUP BY test_type
                    ORDER BY fail_count DESC
                """,
                "parameters": {},
            },
            # 5. 培养基批次追溯
            {
                "name": "media_lot_trace",
                "description": "追溯指定培养基批次被哪些生产批次使用",
                "sql": """
                    SELECT
                        cm.medium_id, cm.medium_name, cm.lot_number, cm.supplier,
                        pb.batch_id, pb.start_date, pb.status,
                        hi.harvest_volume_l, hi.pre_clarify_titer
                    FROM culture_media cm
                    JOIN production_batches pb
                        ON cm.medium_id = pb.growth_medium_id
                        OR cm.medium_id = pb.maintenance_medium_id
                    LEFT JOIN harvest_inactivation hi ON pb.batch_id = hi.batch_id
                    WHERE cm.lot_number = %s
                    ORDER BY pb.start_date
                """,
                "parameters": {"lot_number": {"type": "string", "description": "培养基批号"}},
            },
            # 6. 种病毒代次与效价
            {
                "name": "virus_passage_vs_potency",
                "description": "分析种病毒代次与成品效价的关系",
                "sql": """
                    SELECT
                        vs.passage_level,
                        vs.seed_id,
                        COUNT(pb.batch_id) AS batch_count,
                        ROUND(AVG(fq.potency_elisa)::numeric, 2) AS avg_potency_elisa,
                        ROUND(STDDEV(fq.potency_elisa)::numeric, 2) AS std_potency_elisa,
                        MIN(fq.potency_elisa) AS min_potency,
                        MAX(fq.potency_elisa) AS max_potency
                    FROM virus_seeds vs
                    JOIN production_batches pb ON vs.seed_id = pb.virus_seed_id
                    JOIN final_product_qc fq ON pb.batch_id = fq.batch_id
                    WHERE fq.potency_elisa IS NOT NULL
                    GROUP BY vs.passage_level, vs.seed_id
                    ORDER BY vs.passage_level
                """,
                "parameters": {},
            },
            # 7. 操作班组对比
            {
                "name": "operator_team_comparison",
                "description": "对比不同操作班组的生产质量指标",
                "sql": """
                    SELECT
                        pb.operator_team,
                        COUNT(pb.batch_id) AS batch_count,
                        COUNT(CASE WHEN fq.release_decision = 'released' THEN 1 END) AS released_count,
                        ROUND(AVG(hi.pre_clarify_titer)::numeric, 2) AS avg_harvest_titer,
                        ROUND(AVG(fq.potency_elisa)::numeric, 2) AS avg_potency,
                        COUNT(CASE WHEN ipt.pass_fail = 'FAIL' THEN 1 END) AS process_fail_count
                    FROM production_batches pb
                    LEFT JOIN harvest_inactivation hi ON pb.batch_id = hi.batch_id
                    LEFT JOIN final_product_qc fq ON pb.batch_id = fq.batch_id
                    LEFT JOIN in_process_tests ipt ON pb.batch_id = ipt.batch_id AND ipt.pass_fail = 'FAIL'
                    GROUP BY pb.operator_team
                    ORDER BY pb.operator_team
                """,
                "parameters": {},
            },
            # 8. 规模放大效应
            {
                "name": "scale_up_comparison",
                "description": "对比不同生产规模的效价和质量指标",
                "sql": """
                    SELECT
                        CASE
                            WHEN pb.bioreactor_scale_l <= 50 THEN '50L'
                            WHEN pb.bioreactor_scale_l <= 200 THEN '200L'
                            ELSE '500L'
                        END AS scale_group,
                        COUNT(pb.batch_id) AS batch_count,
                        ROUND(AVG(cc.peak_density)::numeric, 2) AS avg_peak_cell_density,
                        ROUND(AVG(hi.pre_clarify_titer)::numeric, 2) AS avg_harvest_titer,
                        ROUND(AVG(fq.potency_elisa)::numeric, 2) AS avg_potency,
                        COUNT(CASE WHEN fq.release_decision != 'released' THEN 1 END) AS non_released_count
                    FROM production_batches pb
                    LEFT JOIN (
                        SELECT batch_id, MAX(cell_density_10e6_ml) AS peak_density
                        FROM cell_culture_log GROUP BY batch_id
                    ) cc ON pb.batch_id = cc.batch_id
                    LEFT JOIN harvest_inactivation hi ON pb.batch_id = hi.batch_id
                    LEFT JOIN final_product_qc fq ON pb.batch_id = fq.batch_id
                    GROUP BY scale_group
                    ORDER BY scale_group
                """,
                "parameters": {},
            },
            # 9. 细胞培养峰值密度与效价
            {
                "name": "cell_density_vs_titer",
                "description": "分析每批细胞培养峰值密度与收获效价的关系",
                "sql": """
                    SELECT
                        pb.batch_id,
                        pb.bioreactor_scale_l,
                        cc.peak_density,
                        hi.pre_clarify_titer AS harvest_titer,
                        fq.potency_elisa
                    FROM production_batches pb
                    JOIN (
                        SELECT batch_id, MAX(cell_density_10e6_ml) AS peak_density
                        FROM cell_culture_log GROUP BY batch_id
                    ) cc ON pb.batch_id = cc.batch_id
                    LEFT JOIN harvest_inactivation hi ON pb.batch_id = hi.batch_id
                    LEFT JOIN final_product_qc fq ON pb.batch_id = fq.batch_id
                    ORDER BY cc.peak_density DESC
                """,
                "parameters": {},
            },
            # 10. MOI 与效价
            {
                "name": "moi_vs_potency",
                "description": "分析不同 MOI 与收获效价的关系",
                "sql": """
                    SELECT
                        pb.moi,
                        COUNT(pb.batch_id) AS batch_count,
                        ROUND(AVG(hi.pre_clarify_titer)::numeric, 2) AS avg_harvest_titer,
                        ROUND(AVG(fq.potency_elisa)::numeric, 2) AS avg_potency,
                        ROUND(STDDEV(fq.potency_elisa)::numeric, 2) AS std_potency
                    FROM production_batches pb
                    LEFT JOIN harvest_inactivation hi ON pb.batch_id = hi.batch_id
                    LEFT JOIN final_product_qc fq ON pb.batch_id = fq.batch_id
                    WHERE fq.potency_elisa IS NOT NULL
                    GROUP BY pb.moi
                    ORDER BY pb.moi
                """,
                "parameters": {},
            },
            # 11. 病毒培养峰值 CPE 与效价
            {
                "name": "cpe_vs_titer",
                "description": "分析病毒培养阶段的峰值 CPE 与收获效价的关系",
                "sql": """
                    SELECT
                        pb.batch_id,
                        vc.peak_cpe,
                        vc.dpi_at_peak,
                        hi.pre_clarify_titer,
                        fq.potency_elisa
                    FROM production_batches pb
                    JOIN (
                        SELECT batch_id, MAX(cpe_pct) AS peak_cpe,
                               MIN(CASE WHEN cpe_pct = (SELECT MAX(cpe_pct) FROM virus_culture_log v2 WHERE v2.batch_id = v1.batch_id) THEN dpi END) AS dpi_at_peak
                        FROM virus_culture_log v1 GROUP BY batch_id
                    ) vc ON pb.batch_id = vc.batch_id
                    LEFT JOIN harvest_inactivation hi ON pb.batch_id = hi.batch_id
                    LEFT JOIN final_product_qc fq ON pb.batch_id = fq.batch_id
                    ORDER BY vc.peak_cpe DESC
                """,
                "parameters": {},
            },
            # 12. 批次时间线
            {
                "name": "batch_timeline",
                "description": "查询所有批次的完整时间线：生产日期 → 收获日期 → 灭活完成 → QC → 放行",
                "sql": """
                    SELECT
                        pb.batch_id,
                        pb.start_date,
                        hi.harvest_date,
                        hi.inactivation_completion_date,
                        fq.test_date AS qc_date,
                        fq.release_decision,
                        (hi.harvest_date - pb.start_date) AS culture_days,
                        (hi.inactivation_completion_date - hi.harvest_date) AS inactivation_days,
                        (fq.test_date - hi.inactivation_completion_date) AS qc_wait_days
                    FROM production_batches pb
                    LEFT JOIN harvest_inactivation hi ON pb.batch_id = hi.batch_id
                    LEFT JOIN final_product_qc fq ON pb.batch_id = fq.batch_id
                    ORDER BY pb.start_date
                """,
                "parameters": {},
            },
        ]

        for tmpl in templates:
            self.tools.register_custom_template(
                name=tmpl["name"],
                description=tmpl["description"],
                sql_template=tmpl["sql"],
                parameters=tmpl["parameters"],
            )

    def _register_basic_tools(self):
        """注册基础工具: 日期时间、文件读写、环境信息"""
        import json
        import os
        from datetime import datetime, date

        def _safe_read_file(path):
            cwd = os.getcwd()
            full_path = os.path.abspath(os.path.expanduser(path))
            if not full_path.startswith(cwd):
                return {"error": f"安全限制: 只能读取当前工作目录({cwd})下的文件"}
            try:
                with open(full_path, 'r') as f:
                    content = f.read(50000)
                return {"path": full_path, "content": content, "size": len(content)}
            except Exception as e:
                return {"error": str(e)}

        def _safe_write_file(path, content):
            cwd = os.getcwd()
            full_path = os.path.abspath(os.path.expanduser(path))
            if not full_path.startswith(cwd):
                return {"error": f"安全限制: 只能写入当前工作目录({cwd})下的文件"}
            try:
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, 'w') as f:
                    f.write(content)
                return {"path": full_path, "size": len(content), "status": "ok"}
            except Exception as e:
                return {"error": str(e)}

        self.tools.register(
            name="get_current_time",
            description="获取当前日期和时间。返回包含日期、时间、Unix时间戳的JSON。当需要知道今天日期或生成含日期的文档/报告时使用。",
            parameters={},
            handler=lambda: json.dumps({
                "datetime": datetime.now().isoformat(),
                "date": date.today().isoformat(),
                "unix_timestamp": int(time.time()),
            }, ensure_ascii=False, default=str),
        )

        self.tools.register(
            name="read_file",
            description="读取文件内容(限当前工作目录内)。path: 文件路径。",
            parameters={
                "path": {"type": "string", "description": "文件路径(相对或绝对，限制在当前工作目录内)"},
            },
            handler=lambda path: json.dumps(_safe_read_file(path), ensure_ascii=False),
        )

        self.tools.register(
            name="write_file",
            description="写入内容到文件(限当前工作目录内)。path: 文件路径。content: 文本内容。",
            parameters={
                "path": {"type": "string", "description": "文件路径(限制在当前工作目录内)"},
                "content": {"type": "string", "description": "要写入的文本内容"},
            },
            handler=lambda path, content: json.dumps(_safe_write_file(path, content), ensure_ascii=False),
        )

        self.tools.register(
            name="get_env_info",
            description="获取当前运行环境信息(Python版本/平台/工作目录/数据库连接)。",
            parameters={},
            handler=lambda: json.dumps({
                "python_version": __import__('sys').version,
                "platform": __import__('sys').platform,
                "cwd": __import__('os').getcwd(),
                "database": (
                    self.db.db_name if self.db and self.db.is_connected and getattr(self.db, 'db_name', None)
                    else ("connected" if self.db and self.db.is_connected else None)
                ),
            }, ensure_ascii=False, default=str),
        )

        # get_table_info: 查询单表完整元数据（列、主键、外键）
        _catalog = self.schema_catalog  # 闭包捕获
        self.tools.register(
            name="get_table_info",
            description=(
                "获取指定数据表的完整元数据：列清单（名称+类型+是否可空+是否主键）、"
                "主键列列表、外键关系（源列→目标表.目标列）、行数估算、表注释。"
                "参数 table_name 格式为 'schema.table'。"
            ),
            parameters={
                "table_name": {
                    "type": "string",
                    "description": "表全限定名，如 'analog_quality.deviations'",
                },
            },
            handler=lambda table_name: json.dumps(
                _catalog.get_table_info(table_name) or {
                    "error": f"表 {table_name} 不在 schema 目录中"
                },
                ensure_ascii=False, default=str,
            ),
        )

    def _register_phase3_tools(self):
        """Phase 3: 注册 P1 工具 (detect_anomaly, generate_chart,
        search_documents, index_documents)"""
        import json

        # 1. detect_anomaly
        from vaxport.anomaly import detect_anomaly as _detect_anomaly
        self.tools.register(
            name="detect_anomaly",
            description="SPC异常检测。method: oot(离群值)/drift(参数漂移)/degradation(设备劣化)。data: 数值数组JSON。",
            parameters={
                "data": {"type": "string", "description": "数值数组 JSON，如 '[1.2, 1.5, 1.3, 2.8, 1.4]'"},
                "method": {"type": "string", "description": "检测方法: oot/drift/degradation"},
                "options": {"type": "string", "description": "额外选项 JSON (可选)，如 '{\"window\":5}'"},
            },
            handler=lambda data, method, options="{}":
                json.dumps(_detect_anomaly(data, method, options), ensure_ascii=False, default=str),
        )

        # 1.5. run_statistics
        from vaxport.statistics import run_statistics as _run_statistics
        self.tools.register(
            name="run_statistics",
            description=(
                "统计计算。operation: basic_stats(均值/标准差/CV)/cpk(过程能力指数)/"
                "trend(线性趋势)/outlier(离群值IQR)/correlation(Pearson相关)/"
                "compare_groups(Welch t-test)/control_limits(3σ控制限)。"
                "data: 数值JSON，如 basic_stats: '{\"values\":[7.2,7.5,7.1]}'，"
                "correlation: '{\"x\":[1,2,3],\"y\":[7.2,7.5,7.1]}'。"
            ),
            parameters={
                "operation": {"type": "string",
                              "description": "统计操作: basic_stats/cpk/trend/outlier/correlation/compare_groups/control_limits"},
                "data": {"type": "string", "description": "输入数据JSON"},
                "options": {"type": "string",
                            "description": "选项JSON (可选)，如 '{\"usl\":10,\"lsl\":2}' 用于cpk"},
            },
            handler=lambda operation, data, options="{}":
                json.dumps(_run_statistics(operation, data, options), ensure_ascii=False, default=str),
        )

        # 2. generate_chart
        from vaxport.charts import generate_chart as _generate_chart
        self.tools.register(
            name="generate_chart",
            description=(
                "图表生成。chart_type: trend/control/pareto/heatmap/comparison。"
                "data 为以下格式之一（严格 JSON 字符串）:\n"
                "- trend: {\"x\": [\"2024-01\",\"2024-02\"], \"y\": [85.2,87.1], \"xlabel\": \"月份\", \"ylabel\": \"效价\"}\n"
                "- control: {\"values\": [85.2,87.1,83.5], \"xlabel\": \"批次\", \"ylabel\": \"测定值\"}\n"
                "- pareto: {\"categories\": [\"A\",\"B\",\"C\"], \"values\": [45,30,12], \"ylabel\": \"频次\"}\n"
                "- heatmap: {\"matrix\": [[0.9,0.5],[0.3,0.8]], \"xlabels\": [\"反应器\",\"冻干\"], \"ylabels\": [\"PRRSV\",\"PEDV\"]}\n"
                "- comparison: {\"groups\": {\"PRRSV\": [0.9,0.7], \"PEDV\": [0.5,0.3]}, \"ylabel\": \"风险评分\", \"bar_labels\": [\"反应器\",\"冻干\"]}\n"
                "options: 可选 JSON {\"title\": \"标题\", \"width\": 10, \"height\": 6}。"
                "**关键规则**: trend/comparison 的 x/groups 必须覆盖数据库中的**全部时间段/分组**（即使值为 0 也必须包含），禁止只传入有数据的月份。例如按月统计时，数据从 2024-01 到 2026-06，x 必须含全部 30 个月，对应 y 为 0 的也要写 0。"
                "**重要**: 返回的 file_path 是当前系统的绝对路径，引用时必须原样使用，**严禁**做任何格式转换或路径修改。"
                "**如果返回 error，禁止编造路径！在回答中如实告知用户图表生成失败及原因。**"
            ),
            parameters={
                "data": {"type": "string", "description": "图表数据JSON，格式严格按 chart_type 对应结构"},
                "chart_type": {"type": "string", "description": "图表类型: trend/control/pareto/heatmap/comparison"},
                "options": {"type": "string", "description": "图表选项JSON (可选)，如 '{\"title\":\"趋势图\"}'"},
            },
            handler=lambda data, chart_type, options="{}":
                _safe_generate_chart(_generate_chart, data, chart_type, options),
        )

        # 3. search_documents
        from vaxport.documents import search_documents as _search_documents
        self.tools.register(
            name="search_documents",
            description="RAG向量文档检索(pgvector+OpenAI embeddings)。doc_type: sop/regulation/deviation/literature/batch_history/all。",
            parameters={
                "query": {"type": "string", "description": "搜索查询"},
                "doc_type": {"type": "string", "description": "文档类型 (默认all)"},
                "top_k": {"type": "integer", "description": "返回结果数 (默认5)"},
            },
            handler=lambda query, doc_type="all", top_k=5:
                json.dumps(_search_documents(self.db, query, doc_type, top_k),
                          ensure_ascii=False, default=str),
        )

        # 4. index_documents
        from vaxport.documents import index_documents as _index_documents
        self.tools.register(
            name="index_documents",
            description="索引数据库表到向量存储。source_table: schema.table格式。text_columns: 逗号分隔文本列名。",
            parameters={
                "source_table": {"type": "string", "description": "源表 (schema.table)"},
                "text_columns": {"type": "string", "description": "文本列名，逗号分隔"},
                "doc_type": {"type": "string", "description": "文档类型标签 (默认general)"},
                "title_column": {"type": "string", "description": "标题列名 (可选)"},
            },
            handler=lambda source_table, text_columns, doc_type="general", title_column="":
                json.dumps(_index_documents(self.db, source_table, text_columns, doc_type, title_column),
                          ensure_ascii=False, default=str),
        )

    def _register_phase4_tools(self):
        """Phase 4: 注册 P2 工具 (run_prediction, detect_signal,
        analyze_image, web_search)"""
        import json

        # 1. run_prediction
        from vaxport.prediction import run_prediction as _run_prediction
        self.tools.register(
            name="run_prediction",
            description="时间序列预测。method: linear(线性)/exp_smooth(指数平滑)/moving_avg(移动平均)/degradation(降解动力学ICH Q1E)。data: 数值数组JSON。",
            parameters={
                "data": {"type": "string",
                         "description": "时间序列数据JSON，如 '[7.2,7.5,7.1,7.8,7.3]'"},
                "method": {"type": "string",
                           "description": "预测方法: linear/exp_smooth/moving_avg/degradation"},
                "options": {"type": "string",
                            "description": "选项JSON (可选)，如 '{\"horizon\":3}'"},
            },
            handler=lambda data, method, options="{}":
                json.dumps(_run_prediction(data, method, options),
                          ensure_ascii=False, default=str),
        )

        # 2. detect_signal
        from vaxport.signal_detection import detect_signal as _detect_signal
        self.tools.register(
            name="detect_signal",
            description="AEFI不良反应信号检测(PRR/ROR/BCPNN)。data: 2x2列联表JSON {a,b,c,d}。method: prr/ror/bcpnn/all。",
            parameters={
                "data": {"type": "string",
                         "description": "2x2列联表JSON: {a:目标药+目标AE, b:目标药+其他AE, c:其他药+目标AE, d:其他药+其他AE}"},
                "method": {"type": "string",
                           "description": "检测方法: prr/ror/bcpnn/all (默认all)"},
                "options": {"type": "string",
                            "description": "选项JSON (可选)，如 '{\"min_a\":3}'"},
            },
            handler=lambda data, method="all", options="{}":
                json.dumps(_detect_signal(data, method, options),
                          ensure_ascii=False, default=str),
        )

        # 3. analyze_image
        from vaxport.image_analysis import analyze_image as _analyze_image
        self.tools.register(
            name="analyze_image",
            description="AI图像分析(阿里百炼qwen-VL)。analysis_type: general/hplc/electrophoresis/microscopy/assay。image_path: 图像文件路径。",
            parameters={
                "image_path": {"type": "string",
                               "description": "图像文件路径 (PNG/JPG/TIFF)"},
                "analysis_type": {"type": "string",
                                  "description": "分析类型: general/hplc/electrophoresis/microscopy/assay"},
                "options": {"type": "string",
                            "description": "选项JSON (可选)，如 '{\"model\":\"qwen-vl-max\"}'"},
            },
            handler=lambda image_path, analysis_type="general", options="{}":
                json.dumps(_analyze_image(image_path, analysis_type, options),
                          ensure_ascii=False, default=str),
        )

        # 4. web_search
        from vaxport.web_search import web_search as _web_search
        self.tools.register(
            name="web_search",
            description="外部搜索(专利/法规/文献)。search_type: general/patent/regulation/literature/guideline。",
            parameters={
                "query": {"type": "string", "description": "搜索查询"},
                "search_type": {"type": "string",
                                "description": "搜索类型: general/patent/regulation/literature/guideline"},
                "options": {"type": "string",
                            "description": "选项JSON (可选)，如 '{\"max_results\":5}'"},
            },
            handler=lambda query, search_type="general", options="{}":
                json.dumps(_web_search(query, search_type, options),
                          ensure_ascii=False, default=str),
        )

    def run_query(self, query: str) -> dict:
        """执行一次查询"""
        start_time = time.time()

        ui.print_thinking()
        callbacks = CLIProgressCallbacks(debug_mode=self.debug_mode)
        result = self.orchestrator.run(query, callbacks=callbacks)
        ui.clear_thinking()

        self._last_result = result  # 缓存供 /status 使用

        elapsed_ms = int((time.time() - start_time) * 1000)

        # 记录审计日志
        try:
            entry = build_audit_entry(
                user=Path.home().name,
                model=f"{self.llm.active_model}({self.llm.active_backend})",
                query=query,
                sql_list=result.get("sql_queries", []),
                row_count=0,
                duration_ms=elapsed_ms,
                answer=result["answer"],
            )
            write_audit_log(entry)
        except Exception:
            pass

        return result


def run_one_shot(app: App, query: str):
    """一次性查询模式"""
    result = app.run_query(query)
    ui.print_llm_answer(result["answer"])
    if app.debug_mode:
        ui.print_debug_info(result)


def _safe_generate_chart(generate_fn, data: str, chart_type: str, options: str = "{}") -> str:
    """图表生成 + 文件存在性验证。防止 Agent 使用不存在的图表路径。"""
    import os as _os
    result = generate_fn(data, chart_type, options)
    if "error" in result:
        return json.dumps(result, ensure_ascii=False, default=str)
    file_path = result.get("file_path", "")
    if file_path and not _os.path.exists(file_path):
        return json.dumps({
            "error": f"图表文件未生成成功: {file_path} 不存在，请检查数据格式后重试 generate_chart",
            "hint": "确认 data 参数格式正确：comparison 需要 {\"groups\": {\"组名\": [v1,v2,...]}}，trend 需要 {\"x\": [...], \"y\": [...]}"
        }, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False, default=str)


def main():
    from vaxport import __version__
    parser = argparse.ArgumentParser(
        description=f"vaxport v{__version__} — 疫苗企业本地 LLM 数据分析终端工具"
    )
    parser.add_argument(
        "--version", "-V", action="version",
        version=f"vaxport v{__version__}",
    )
    parser.add_argument(
        "query", nargs="?", default=None,
        help="直接查询（非交互模式）"
    )
    parser.add_argument(
        "--resume", type=str, default=None,
        help="恢复指定会话"
    )
    parser.add_argument(
        "--list-sessions", action="store_true",
        help="列出已保存的会话"
    )
    parser.add_argument(
        "--install", action="store_true",
        help="创建配置目录并运行首次配置"
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="启动时开启调试模式"
    )

    args = parser.parse_args()

    # --install
    if args.install:
        config_dir = Path.home() / ".vaxport"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "sessions").mkdir(exist_ok=True)
        ui.print_info(f"配置目录已创建: {config_dir}")
        config = load_config()
        ui.print_info("配置完成。")
        return

    # --list-sessions
    if args.list_sessions:
        sessions = Session.list_sessions()
        from vaxport import ui as _ui
        _ui.print_sessions_list(sessions)
        return

    # 加载配置
    config = load_config()

    # 初始化应用
    app = App(config)
    # REPL 模式下 setup 不打印欢迎信息（由 REPL 自行渲染）
    app.setup(resume_session=args.resume, quiet=args.query is None)

    if args.debug:
        app.debug_mode = True

    # 执行模式
    if args.query:
        run_one_shot(app, args.query)
        app.session.save()
    else:
        # textual TUI 模式
        from vaxport.tui.app import VaxportApp
        tui_app = VaxportApp(
            config=app.config,
            llm=app.llm,
            db=app.db,
            tools=app.tools,
            orchestrator=app.orchestrator,
            skills=app.skills,
            session=app.session,
            debug_mode=app.debug_mode,
            mdb=app.mdb,
        )
        tui_app.run()


if __name__ == "__main__":
    main()