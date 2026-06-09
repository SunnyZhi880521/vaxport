"""Tool 注册表 — 动态生成 + 白名单模板 + 统一调度"""

import json
import re
from typing import Any, Callable, Optional

from vaxport.db import Database
from vaxport.ear import GuardRails
from vaxport.skills import SkillRegistry


class ToolRegistry:
    """工具注册表，管理所有 LLM 可调用的 Tool"""

    def __init__(self, db: Optional[Database] = None, skill_registry: Optional[SkillRegistry] = None):
        self.db = db
        self.skill_registry = skill_registry
        self._tools: dict[str, dict] = {}  # tool_name → tool_definition
        self._handlers: dict[str, Callable] = {}  # tool_name → handler function
        self._custom_templates: dict[str, dict] = {}  # 自定义 SQL 模板
        self._tool_meta: dict[str, dict] = {}  # tool_name → {schema, table, obj_type, db_name?}
        self.guard_rails = GuardRails()  # EAR Guard Rails

    def register(self, name: str, description: str, parameters: dict, handler: Callable,
                 required: Optional[list] = None):
        """注册一个 Tool。

        required 默认为所有 parameters 的 key；传空列表表示全部可选。
        """
        if required is None:
            required = list(parameters.keys())
        tool_def = {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": parameters,
                    "required": required,
                },
            },
        }
        self._tools[name] = tool_def
        self._handlers[name] = handler

    def register_custom_template(self, name: str, description: str,
                                  sql_template: str, parameters: dict):
        """注册自定义 SQL 模板"""
        self._custom_templates[name] = {
            "description": description,
            "sql": sql_template,
            "parameters": parameters,
        }

        def handler(**kwargs):
            if not self.db:
                return json.dumps({"error": "数据库未连接"}, ensure_ascii=False)
            params = tuple(kwargs.get(p, None) for p in parameters)
            result = self.db.execute_query(sql_template, params)
            return json.dumps(result, ensure_ascii=False, default=str)

        self.register(name, description, parameters, handler)

    def discover_and_register(self, db=None, db_name: str = "",
                              exclude_schemas: Optional[list] = None):
        """从数据库自动发现 schema 并注册查询 Tool。

        db: 指定 Database 实例 (多库模式); None 则用 self.db
        db_name: 数据库显示名称, 用于工具名前缀和元数据
        """
        _db = db or self.db
        if not _db or not _db.is_connected:
            return

        schema_info = _db.discover_schema(exclude_schemas)

        for table_key, info in schema_info.get("tables", {}).items():
            self._register_table_tool(info, "table", db=_db, db_name=db_name)

        for view_key, info in schema_info.get("views", {}).items():
            self._register_table_tool(info, "view", db=_db, db_name=db_name)

        for mv_key, info in schema_info.get("matviews", {}).items():
            self._register_table_tool(info, "materialized view", db=_db, db_name=db_name)

    def _register_table_tool(self, info: dict, obj_type: str,
                             db=None, db_name: str = ""):
        """为单个表/视图注册查询 Tool。

        db: 此工具专属的 Database 实例; None 则用 self.db
        db_name: 数据库名称, 用于工具名前缀和元数据
        """
        _db = db or self.db
        schema = info["schema"]
        table = info.get("table") or info.get("view") or info.get("matview")
        columns = info.get("columns", [])

        if db_name:
            safe_db = re.sub(r'[^a-zA-Z0-9]', '_', db_name)
            tool_name = f"query_{safe_db}_{schema}_{table}"
        else:
            tool_name = f"query_{schema}_{table}"
        self._tool_meta[tool_name] = {
            "schema": schema, "table": table, "obj_type": obj_type,
            "db_name": db_name,
        }
        col_desc = ", ".join(
            f"{c['column_name']} ({c['data_type']}{', nullable' if c['is_nullable'] == 'YES' else ''})"
            for c in columns[:10]
        )
        if len(columns) > 10:
            col_desc += f", ... 共 {len(columns)} 列"

        description = (
            f"查询 {obj_type} {schema}.{table} 的数据。"
            f"列: {col_desc}。"
            f"返回最多 5000 行。建议优先用列过滤缩小范围。若结果被截断，请加日期/产品等过滤条件重新查询。"
        )

        # 动态参数：所有列都可选过滤
        parameters = {}
        # 值得添加范围过滤的数据类型
        RANGE_TYPES = {
            "date", "timestamp", "timestamptz", "timestamp without time zone",
            "timestamp with time zone", "time", "timetz", "time without time zone",
            "time with time zone", "integer", "bigint", "smallint", "int", "int2",
            "int4", "int8", "numeric", "decimal", "real", "float", "float4", "float8",
            "double precision", "serial", "bigserial", "smallserial",
        }
        for col in columns:
            safe_name = col["column_name"]
            if re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", safe_name):
                parameters[safe_name] = {
                    "type": "string",
                    "description": f"按 {safe_name} 过滤（可选）",
                }
                # 日期/数值列自动添加范围查询参数
                dt = (col.get("data_type") or "").lower()
                if dt in RANGE_TYPES:
                    parameters[f"{safe_name}_from"] = {
                        "type": "string",
                        "description": f"{safe_name} 起始值（>= {safe_name}，可选）",
                    }
                    parameters[f"{safe_name}_to"] = {
                        "type": "string",
                        "description": f"{safe_name} 结束值（<= {safe_name}，可选）",
                    }

        def make_handler(_schema, _table, _columns, _db):
            def handler(**kwargs):
                if not _db or not _db.is_connected:
                    return json.dumps({"error": "数据库未连接"}, ensure_ascii=False)
                filters = {}
                for k, v in kwargs.items():
                    if v is None or v == "":
                        continue
                    if k.endswith("_from"):
                        col = k[:-5]
                        if re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", col):
                            filters[col] = (">=", v)
                    elif k.endswith("_to"):
                        col = k[:-3]
                        if re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", col):
                            filters[col] = ("<=", v)
                    else:
                        filters[k] = v
                # 多取 1 行检测是否真截断
                result = _db.execute_safe_select(
                    _schema, _table,
                    filters=filters if filters else None,
                    limit=5001,
                )
                rows = result.get("rows", [])
                if len(rows) > 5000:
                    rows = rows[:5000]
                    result["rows"] = rows
                    result["row_count"] = 5000
                    result["warning"] = (
                        "数据已截断至 5000 行，结果不完整。"
                        "请添加过滤条件（如日期范围、产品类型）缩小范围后重新查询，或使用 run_statistics 做聚合统计。"
                    )
                # 清理内部 truncated 标记，用 warning 替代
                if "truncated" in result:
                    del result["truncated"]
                return json.dumps(result, ensure_ascii=False, default=str)
            return handler

        self.register(
            name=tool_name,
            description=description,
            parameters=parameters,
            handler=make_handler(schema, table, columns, _db),
            required=[],  # 所有列过滤均为可选
        )

    def get_tool_definitions(self) -> list[dict]:
        """获取所有 Tool 的 OpenAI 格式定义"""
        return list(self._tools.values())

    def get_tool_definitions_for_agent(self, agent_type: str) -> list[dict]:
        """返回指定 Agent 类型的工具子集。

        Agent 类型: analyze_reporter / quality_supervision / document_search / general
        general 返回全部工具。
        """
        if agent_type == "general":
            return self.get_tool_definitions()

        # Agent → 工具名模式
        FILTER_MAP = {
            "analyze_reporter": {"query_", "detect_anomaly", "generate_report", "generate_chart", "run_statistics", "deep_research_collect"},
            "quality_supervision": {"query_", "generate_chart"},
            "document_search": {"search_documents", "index_documents", "generate_chart"},
        }

        patterns = FILTER_MAP.get(agent_type, set())
        if not patterns:
            return self.get_tool_definitions()

        result = []
        for name, info in self._tools.items():
            for pat in patterns:
                if name.startswith(pat) or name == pat:
                    result.append(info)
                    break
        return result

    def get_tool_count(self) -> int:
        """返回已注册工具总数"""
        return len(self._tools)

    def execute(self, tool_name: str, arguments: dict) -> str:
        """执行指定的 Tool"""
        handler = self._handlers.get(tool_name)
        if not handler:
            return json.dumps({"error": f"未知工具: {tool_name}"}, ensure_ascii=False)

        # EAR Guard Rails: 前置校验
        validation = self.guard_rails.validate_tool_call(tool_name, arguments)
        if validation.blocked:
            return json.dumps(
                {"error": validation.reason, "suggestion": validation.suggestion},
                ensure_ascii=False,
            )

        try:
            return handler(**arguments)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def list_tools(self) -> list[dict]:
        """列出所有已注册工具（用于 /tools 命令）"""
        return [
            {
                "name": name,
                "description": info["function"]["description"][:100],
            }
            for name, info in self._tools.items()
        ]

    def get_schema_summary(self) -> dict:
        """按 数据库→schema 两层归类表/视图/物化视图，供侧边栏展示。
        返回: {db_name: {schema: {"tables":[], "views":[], "matviews":[]}}}"""
        result = {}  # db_name → {schema: {...}}

        for name, info in self._tools.items():
            if not name.startswith("query_"):
                continue

            meta = self._tool_meta.get(name)
            if not meta:
                continue
            db_name = meta.get("db_name", "")
            schema = meta["schema"]
            table = meta["table"]
            obj_type = meta["obj_type"]

            if db_name not in result:
                result[db_name] = {}
            if schema not in result[db_name]:
                result[db_name][schema] = {"tables": [], "views": [], "matviews": []}

            if obj_type == "materialized view":
                result[db_name][schema]["matviews"].append(table)
            elif obj_type == "view":
                result[db_name][schema]["views"].append(table)
            else:
                result[db_name][schema]["tables"].append(table)

        return result

    def register_skill_scripts(self):
        """注册 SKILL Python 脚本为 Tool"""
        if not self.skill_registry:
            return
        for skill_name, skill_info in self.skill_registry.get_executable_scripts():
            for script_path in skill_info["python_scripts"]:
                def make_skill_handler(_path, _name):
                    def handler(args: str = "{}"):
                        import subprocess
                        try:
                            parsed_args = json.loads(args)
                            arg_list = [f"--{k}={v}" for k, v in parsed_args.items()]
                            result = subprocess.run(
                                ["python3", str(_path)] + arg_list,
                                capture_output=True, text=True, timeout=30,
                            )
                            return json.dumps({
                                "stdout": result.stdout,
                                "stderr": result.stderr,
                                "returncode": result.returncode,
                            }, ensure_ascii=False)
                        except subprocess.TimeoutExpired:
                            return json.dumps({"error": "脚本执行超时 (30s)"}, ensure_ascii=False)
                        except Exception as e:
                            return json.dumps({"error": str(e)}, ensure_ascii=False)
                    return handler

                script_name = f"run_skill_{skill_name}_{script_path.stem}"
                self.register(
                    name=script_name,
                    description=f"执行 SKILL '{skill_name}' 的脚本 {script_path.name}。args 参数为 JSON 字符串。",
                    parameters={
                        "args": {
                            "type": "string",
                            "description": "传递给脚本的参数，JSON 格式",
                        }
                    },
                    handler=make_skill_handler(script_path, skill_name),
                )

    def register_deep_research(self):
        """注册 deep_research_collect 工具"""
        from vaxport.deep_research import DeepResearchCollector, DeepResearchPlan, build_research_summary

        def handler(plan_json: str = "{}") -> str:
            """执行 Deep Research 聚合采集"""
            if not self.db or not self.db.is_connected:
                return json.dumps({"error": "数据库未连接"}, ensure_ascii=False)

            plan = DeepResearchPlan.from_json(plan_json)
            if not plan or not plan.tables_needed:
                return json.dumps({"error": "计划解析失败或无表需要采集"}, ensure_ascii=False)

            collector = DeepResearchCollector(self.db)
            results = collector.collect(plan)
            if not results:
                return json.dumps({"error": "采集无结果"}, ensure_ascii=False)

            summary = build_research_summary(results)
            # 也返回结构化数据供 Agent 按需回查
            detail = {k: v.to_dict() for k, v in results.items()}
            return json.dumps({
                "summary": summary,
                "detail": detail,
                "task_id": plan.task_id,
            }, ensure_ascii=False, default=str)

        self.register(
            name="deep_research_collect",
            description="Deep Research 聚合采集工具。按结构化数据定位计划并发执行聚合SQL，返回统计摘要而非原始数据。适用于复杂多维度分析任务（APQR、偏差调查等）。参数 plan_json 为 JSON 字符串，包含 tables_needed（schema/table/key_filter/aggregate_hint）。",
            parameters={
                "plan_json": {
                    "type": "string",
                    "description": "结构化数据定位计划 JSON，格式: {\"tables_needed\": [{\"schema\":\"...\",\"table\":\"...\",\"why\":\"...\",\"key_filter\":\"...\",\"aggregate_hint\":\"...\"}], \"output_sections\": [...], \"task_id\": \"...\"}",
                },
            },
            handler=handler,
            required=["plan_json"],
        )