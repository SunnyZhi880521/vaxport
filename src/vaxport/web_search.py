"""外部搜索工具 — 文献/专利/法规更新检索

支持:
- 通用 web 搜索 (OpenAI web_search 或内置搜索)
- 专利检索 (中国专利/国际专利)
- 法规更新检索
- 科学文献检索

默认使用 OpenAI 的 web_search tool 能力，也可回退到结构化 URL 搜索。
"""

import json
from typing import Optional


def web_search(query: str, search_type: str = "general",
               options: str = "{}") -> dict:
    """外部搜索入口。

    Args:
        query: 搜索查询
        search_type: 搜索类型
            - "general": 通用搜索
            - "patent": 专利检索 (中国+国际)
            - "regulation": 法规更新检索 (NMPA/CDE/NIFDC)
            - "literature": 科学文献 (PubMed/CNKI)
            - "guideline": 指导原则检索 (ICH/WHO/NMPA)
        options: JSON {"max_results": 5, "language": "zh", "freshness": "year"}

    Returns:
        {"results": [...], "search_type": str, "query": str}
    """
    try:
        opts = json.loads(options) if isinstance(options, str) else options
    except (json.JSONDecodeError, TypeError):
        opts = {}

    max_results = opts.get("max_results", 5)
    language = opts.get("language", "zh")

    # 构建搜索策略
    search_configs = _get_search_configs(search_type, query, language)

    return {
        "search_type": search_type,
        "query": query,
        "max_results": max_results,
        "search_strategies": search_configs,
        "note": (
            "此工具返回结构化搜索策略供 LLM Agent 使用。"
            "Agent 应使用 search_documents (内部文档) 或调用外部搜索 API。"
            "对于法规/专利/文献检索，建议按以下结构化查询执行搜索。"
        ),
        "results": search_configs,
    }


def _get_search_configs(search_type: str, query: str,
                        language: str) -> list[dict]:
    """构建结构化搜索策略"""
    is_cn = language == "zh"

    configs = {
        "general": [
            {
                "source": "综合搜索",
                "query": query,
                "note": "使用通用搜索引擎搜索",
            },
        ],
        "patent": [
            {
                "source": "中国专利 (CNIPA)",
                "url": f"https://patents.google.com/?q={_urlencode(query)}&language=ZH",
                "query": query + (" 疫苗 专利" if is_cn else " vaccine patent"),
                "database": "Google Patents / CNIPA",
            },
            {
                "source": "国际专利 (WIPO)",
                "url": f"https://patentscope.wipo.int/search/en/search.jsf?query={_urlencode(query)}",
                "query": query + " vaccine",
                "database": "WIPO PATENTSCOPE",
            },
            {
                "source": "中国专利数据库 (CNKI)",
                "url": f"https://kns.cnki.net/kns8s/search?classid=SCDB&kw={_urlencode(query + ' 专利')}",
                "query": query + " 专利",
                "database": "CNKI 专利",
            } if is_cn else None,
        ],
        "regulation": [
            {
                "source": "NMPA 法规 (国家药监局)",
                "url": f"https://www.nmpa.gov.cn/so/s?qt={_urlencode(query)}",
                "query": query + " site:nmpa.gov.cn",
                "database": "NMPA 官网",
            },
            {
                "source": "CDE 指导原则 (药审中心)",
                "url": f"https://www.cde.org.cn/main/xxgk/listpage/bc67c7dbd9537c6a12e9a5a00927c9fd",
                "query": query + " site:cde.org.cn",
                "database": "CDE 药审中心",
            },
            {
                "source": "NIFDC 标准 (中检院)",
                "url": f"https://www.nifdc.org.cn/nifdc/bshff/ssxx/index.html",
                "query": query + " site:nifdc.org.cn",
                "database": "NIFDC 中检院",
            },
            {
                "source": "ICH Guidelines",
                "url": f"https://www.ich.org/search?search={_urlencode(query)}",
                "query": query + " ICH guideline",
                "database": "ICH",
            },
            {
                "source": "WHO Guidelines",
                "url": f"https://www.who.int/home/search?query={_urlencode(query)}",
                "query": query + " WHO guideline vaccine",
                "database": "WHO",
            },
        ] if is_cn else [
            {
                "source": "ICH / WHO / FDA",
                "query": query + " guideline vaccine",
            },
        ],
        "literature": [
            {
                "source": "PubMed",
                "url": f"https://pubmed.ncbi.nlm.nih.gov/?term={_urlencode(query)}",
                "query": query,
                "database": "PubMed/MEDLINE",
            },
            {
                "source": "CNKI (中国知网)",
                "url": f"https://kns.cnki.net/kns8s/search?classid=YSTT4HG0&kw={_urlencode(query)}",
                "query": query,
                "database": "CNKI",
            } if is_cn else None,
            {
                "source": "Google Scholar",
                "url": f"https://scholar.google.com/scholar?q={_urlencode(query)}",
                "query": query,
                "database": "Google Scholar",
            },
        ],
        "guideline": [
            {
                "source": "ICH Guidelines",
                "url": f"https://www.ich.org/page/search-index?search={_urlencode(query)}",
                "query": query + " ICH",
                "database": "ICH",
            },
            {
                "source": "中国药典",
                "url": f"https://ydz.chp.org.cn/",
                "query": query + " 中国药典",
                "database": "中国药典 2025",
            } if is_cn else None,
            {
                "source": "NMPA 指导原则",
                "query": query + " 指导原则 site:nmpa.gov.cn",
                "database": "NMPA",
            } if is_cn else None,
        ],
    }

    results = configs.get(search_type, configs["general"])
    # 过滤掉 None (条件性搜索结果)
    return [r for r in results if r is not None]


def _urlencode(s: str) -> str:
    """简单 URL 编码"""
    import urllib.parse
    return urllib.parse.quote(s)


def get_regulation_updates(category: str = "all") -> dict:
    """获取法规更新监控 URL 列表。

    Args:
        category: "nmpa" / "cde" / "nifdc" / "ich" / "who" / "all"

    Returns:
        各监管机构的法规更新页面 URL
    """
    sources = {
        "nmpa": {
            "name": "国家药品监督管理局",
            "url": "https://www.nmpa.gov.cn/yaopin/index.html",
            "update_frequency": "每日",
            "key_sections": ["法规文件", "公告通告", "政策解读"],
        },
        "cde": {
            "name": "药品审评中心",
            "url": "https://www.cde.org.cn/main/xxgk/listpage/bc67c7dbd9537c6a12e9a5a00927c9fd",
            "update_frequency": "每周",
            "key_sections": ["指导原则", "通知公告", "技术要求"],
        },
        "nifdc": {
            "name": "中国食品药品检定研究院",
            "url": "https://www.nifdc.org.cn/nifdc/bshff/ssxx/index.html",
            "update_frequency": "不定期",
            "key_sections": ["标准物质", "检验方法", "批签发公告"],
        },
        "ich": {
            "name": "ICH (国际人用药品注册技术协调会)",
            "url": "https://www.ich.org/page/ich-guidelines",
            "update_frequency": "不定期",
            "key_sections": ["Quality Guidelines (Q)", "Safety Guidelines (S)"],
        },
        "who": {
            "name": "WHO (世界卫生组织)",
            "url": "https://www.who.int/teams/health-product-and-policy-standards/standards-and-specifications/vaccines-quality",
            "update_frequency": "不定期",
            "key_sections": ["Vaccine Standards", "Technical Report Series"],
        },
    }

    if category == "all":
        return {"sources": list(sources.values()), "total": len(sources)}

    src = sources.get(category)
    if src:
        return {"source": src}
    return {"error": f"未知类别: {category}，支持: {list(sources.keys())}"}