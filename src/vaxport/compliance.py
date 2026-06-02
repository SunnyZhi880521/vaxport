"""合规审查工具 — 法规匹配 + RCA + 偏差分类 + CAPA 检查

为 ComplianceAgent 提供 4 个核心工具。
"""

import json
from typing import Optional

from vaxport.db import Database
from vaxport.regulations import REGULATION_SETS


def match_regulation(query: str, regulation_set: str = "all") -> dict:
    """法规条款匹配 — Jaccard 相似度排名。

    Args:
        query: 查询文本，如 "OOS偏差调查流程"
        regulation_set: "gmp" / "cnas" / "pharmacopoeia" / "batch_release" / "all"

    Returns:
        {"matches": [...], "query": str, "total_candidates": int}
    """
    articles = REGULATION_SETS.get(regulation_set, REGULATION_SETS["all"])
    if not articles:
        return {"error": f"未知法规集: {regulation_set}"}

    query_chars = set(query)
    results = []

    for art in articles:
        # Jaccard 相似度: |query ∩ keywords| / |query ∪ keywords|
        kw_set = set()
        for kw in art.get("keywords", []):
            kw_set.update(kw)

        # 同时检查 title 和 text 中的关键词命中
        title_text = art.get("title", "") + art.get("text", "")
        text_words = set(title_text)

        # 组合关键词集 + 文本词汇
        combined = kw_set | text_words

        intersection = len(query_chars & combined)
        union = len(query_chars | combined)
        score = intersection / union if union > 0 else 0

        # 额外加分: query 中的词直接出现在 keywords 中
        bonus = 0
        for kw in art.get("keywords", []):
            if kw in query:
                bonus += 0.1
        score = min(1.0, score + bonus)

        if score > 0.05:
            results.append({
                "clause": art["clause"],
                "standard": art.get("standard", "中国GMP 2010版"),
                "chapter": art.get("chapter", ""),
                "title": art["title"],
                "text": art["text"],
                "relevance": round(score, 3),
            })

    results.sort(key=lambda x: x["relevance"], reverse=True)
    top_k = results[:5]

    return {
        "matches": top_k,
        "query": query,
        "regulation_set": regulation_set,
        "total_candidates": len(articles),
        "total_matches": len(results),
    }


def root_cause_analysis(event_desc: str, event_type: str,
                        method: str = "5why") -> dict:
    """结构化根因分析 (RCA) 模板。

    Args:
        event_desc: 事件描述，如 "PEDV-2024-0016 批灭活后效价不合格"
        event_type: 事件类型，如 "OOS" / "偏差" / "设备故障" / "环境超标"
        method: 分析方法 — "5why" / "ishikawa" / "fta"

    Returns:
        结构化 RCA 框架
    """
    if method == "5why":
        return _rca_5why(event_desc, event_type)
    elif method == "ishikawa":
        return _rca_ishikawa(event_desc, event_type)
    elif method == "fta":
        return _rca_fta(event_desc, event_type)
    else:
        return {"error": f"未知RCA方法: {method}，支持: 5why/ishikawa/fta"}


def _rca_5why(event_desc: str, event_type: str) -> dict:
    """5-Why 分析模板"""
    return {
        "method": "5-Why",
        "event": event_desc,
        "event_type": event_type,
        "framework": {
            "why_1": {
                "question": f"为什么发生了'{event_desc}'？",
                "guidance": "直接原因 — 观察到的直接现象或即时触发因素",
                "placeholder": "【待调查填写】",
            },
            "why_2": {
                "question": "为什么会出现上述直接原因？",
                "guidance": "过程原因 — 导致直接原因的流程或操作因素",
                "placeholder": "【待调查填写】",
            },
            "why_3": {
                "question": "为什么该过程原因未被发现或控制？",
                "guidance": "系统原因 — 管理/培训/规程等系统性缺陷",
                "placeholder": "【待调查填写】",
            },
            "why_4": {
                "question": "为什么管理系统允许该系统原因存在？",
                "guidance": "组织原因 — QA oversight/质量文化/资源配置",
                "placeholder": "【待调查填写】",
            },
            "why_5": {
                "question": "为什么该组织原因长期未被纠正？",
                "guidance": "根因 — 质量体系层面的根本缺陷",
                "placeholder": "【待调查填写】",
            },
        },
        "suggested_investigation": [
            f"1. 调取{event_type}发生前后的全部操作记录和人员日志",
            "2. 检查相关设备的校准状态和维护记录",
            "3. 核对相关SOP的最新版本和培训记录",
            "4. 排查同期其他批次是否存在类似问题",
            "5. 必要时进行模拟实验复现",
        ],
        "note": "以上为结构化分析模板，需结合现场调查结果逐层填写。每层 Why 应基于证据而非假设。",
    }


def _rca_ishikawa(event_desc: str, event_type: str) -> dict:
    """Ishikawa 鱼骨图 (6M) 模板"""
    categories = {
        "人员 (Man)": ["操作失误", "培训不足", "人员变更", "疲劳/排班"],
        "设备 (Machine)": ["设备故障", "校准偏离", "维护不足", "老化/磨损"],
        "物料 (Material)": ["原料批次差异", "供应商变更", "储存不当", "污染"],
        "方法 (Method)": ["SOP不完善", "方法变更", "参数设置错误", "验证不足"],
        "测量 (Measurement)": ["仪器偏差", "取样代表性", "方法不适用", "计算错误"],
        "环境 (Mother Nature)": ["温湿度偏离", "洁净度不达标", "季节变化", "停电/波动"],
    }

    causes = {}
    for cat, items in categories.items():
        causes[cat] = [
            {"factor": item, "likelihood": "待评估", "evidence": "待收集"}
            for item in items
        ]

    return {
        "method": "Ishikawa (鱼骨图/6M)",
        "event": event_desc,
        "event_type": event_type,
        "categories": causes,
        "suggested_approach": [
            "1. 组织跨部门小组（生产/QC/QA/工程）进行头脑风暴",
            f"2. 对每个6M类别下的因素逐一评估可能性",
            "3. 对高可能性因素收集客观证据（记录/数据/访谈）",
            "4. 排除无证据支持的因素，聚焦根因",
            "5. 用5-Why对确认的可能原因进一步深挖",
        ],
        "note": "Ishikawa用于广泛识别可能原因，确定根因后建议用5-Why深挖。",
    }


def _rca_fta(event_desc: str, event_type: str) -> dict:
    """故障树分析 (FTA) 模板"""
    return {
        "method": "FTA (故障树分析)",
        "event": event_desc,
        "event_type": event_type,
        "top_event": event_desc,
        "logic_gate": "OR (任一中间事件发生即导致顶事件)",
        "branches": [
            {
                "branch": "原料/物料因素",
                "logic": "OR",
                "sub_events": ["原料批次不合格", "物料储存条件偏离",
                               "供应商变更未评估", "交叉污染"],
            },
            {
                "branch": "工艺/操作因素",
                "logic": "OR",
                "sub_events": ["工艺参数偏离设定值", "操作未按SOP执行",
                               "设备/器具清洁不彻底", "中间体存放超时"],
            },
            {
                "branch": "检测/放行因素",
                "logic": "AND (需同时发生)",
                "sub_events": ["检验方法产生错误结果", "OOS调查未发现根本原因"],
            },
            {
                "branch": "环境/设施因素",
                "logic": "OR",
                "sub_events": ["HVAC系统故障", "洁净区压差异常",
                               "温湿度超限", "虫鼠害入侵"],
            },
        ],
        "suggested_approach": [
            "1. 从顶事件向下逐层分析，直到基本事件",
            "2. 计算最小割集（导致顶事件的最小基本事件组合）",
            "3. 评估各基本事件的发生概率",
            "4. 优先调查概率最高的最小割集",
        ],
    }


def classify_deviation(devi_data: str) -> dict:
    """偏差分类 — 基于 GMP 第 250 条的等级判定。

    Args:
        devi_data: JSON 字符串
            {"description": "偏差描述", "affected_batch": "批号",
             "product_impact": "直接影响/间接影响/无影响",
             "gmp_impact": "影响产品放行/影响数据完整性/影响工艺验证/无影响"}

    Returns:
        {"level": "Critical/Major/Minor", "basis": str, "actions_required": [...]}
    """
    try:
        d = json.loads(devi_data) if isinstance(devi_data, str) else devi_data
    except (json.JSONDecodeError, TypeError):
        return {"error": "devi_data 必须是有效 JSON"}

    desc = d.get("description", "")
    product_impact = d.get("product_impact", "")
    gmp_impact = d.get("gmp_impact", "")

    # 判定逻辑
    critical_keywords = [
        "产品放行", "患者安全", "无菌", "热原", "效价不合格",
        "直接影响产品质量", "影响产品安全性", "影响产品有效性",
    ]
    major_keywords = [
        "工艺验证", "数据完整性", "SOP偏离", "设备故障",
        "间接影响", "参数超限", "环境超标",
    ]

    is_critical = any(kw in desc for kw in critical_keywords) or \
                  any(kw in product_impact for kw in ["直接影响", "产品放行"]) or \
                  any(kw in gmp_impact for kw in ["影响产品放行"])

    is_major = any(kw in desc for kw in major_keywords) or \
               any(kw in product_impact for kw in ["间接影响"]) or \
               any(kw in gmp_impact for kw in ["数据完整性", "工艺验证"])

    if is_critical:
        level = "Critical"
        basis = "中国GMP 第250条 — 偏差直接影响产品质量/患者安全"
        actions = [
            "立即停止相关操作，隔离受影响产品",
            "24小时内启动紧急调查（QA负责人牵头）",
            "72小时内完成I阶段调查并提交初步报告",
            "评估对其他批次的影响（追溯同期生产批次）",
            "如涉及已放行产品，启动召回评估程序",
        ]
    elif is_major:
        level = "Major"
        basis = "中国GMP 第250条 — 偏差涉及SOP/GMP合规性偏离"
        actions = [
            "记录偏差详情并通知QA主管",
            "5个工作日内完成调查和影响评估",
            "根据调查结果确定是否需要启动CAPA",
            "如涉及工艺验证状态，评估再验证必要性",
        ]
    else:
        level = "Minor"
        basis = "中国GMP 第250条 — 轻微偏离，无产品/数据影响"
        actions = [
            "记录偏差并在批记录中备注",
            "主管确认后归档，纳入趋势分析",
            "如同类Minor偏差频发(>3次/月)，升级为Major",
        ]

    return {
        "level": level,
        "basis": basis,
        "description": desc[:200],
        "actions_required": actions,
        "note": "此分类为规则引擎自动判定，最终等级需QA主管确认。",
    }


def check_capa_closure(db: Optional[Database] = None,
                        capa_id: str = "", filters: str = "{}") -> dict:
    """CAPA 闭环状态检查。

    Args:
        db: 数据库连接（CLI注入）
        capa_id: 可选，指定 CAPA 编号
        filters: JSON 过滤条件，如 {"status": "open"}

    Returns:
        CAPA 状态信息
    """
    try:
        f = json.loads(filters) if isinstance(filters, str) else filters
    except (json.JSONDecodeError, TypeError):
        f = {}

    if not db:
        return {
            "note": "未连接数据库，返回 CAPA 闭环检查框架",
            "capa_lifecycle": {
                "stages": [
                    {"stage": 1, "name": "CAPA 发起", "required": "偏差/OOS/投诉/审计发现 → CAPA申请",
                     "timeline": "5个工作日内"},
                    {"stage": 2, "name": "根因调查", "required": "5-Why/Ishikawa/FTA → 确定根因",
                     "timeline": "30日内"},
                    {"stage": 3, "name": "措施制定", "required": "纠正措施+预防措施+责任人+完成期限",
                     "timeline": "根因确认后15日内"},
                    {"stage": 4, "name": "措施执行", "required": "按计划执行 + 过程记录",
                     "timeline": "按计划"},
                    {"stage": 5, "name": "有效性验证", "required": "数据证明措施有效 + 趋势改善",
                     "timeline": "措施完成后30-90日"},
                    {"stage": 6, "name": "关闭批准", "required": "QA负责人审批 + 记录归档",
                     "timeline": "验证通过后15日内"},
                ],
            },
            "checklist": [
                "☐ 所有措施是否按期完成？",
                "☐ 有效性验证数据是否充分？",
                "☐ 是否有同类偏差再次发生？",
                "☐ CAPA 文件是否完整（含审批签名）？",
                "☐ 相关 SOP 是否已更新？",
                "☐ 相关人员是否已培训？",
            ],
        }

    # 尝试从数据库查询
    try:
        if capa_id:
            result = db.execute_safe_select(
                "analog_pedv", "production_batches",
                filters={"batch_id": capa_id},
                limit=1,
            )
            return {
                "capa_id": capa_id,
                "query_result": result,
                "note": "CAPA 状态查询结果 — 请根据实际表结构调整查询",
            }
        else:
            return {
                "status": "ready",
                "message": f"数据库已连接，请指定 capa_id 或提供更具体的查询条件",
            }
    except Exception as e:
        return {"error": str(e), "note": "CAPA 表可能尚未建立或表结构不匹配"}