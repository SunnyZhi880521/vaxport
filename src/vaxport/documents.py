"""文档检索工具 — pgvector + 百炼 DashScope embeddings RAG

为 DocumentSearchAgent 提供语义搜索能力。
延迟索引策略：首次查询时检查索引状态。

依赖: pgvector (PostgreSQL 扩展), dashscope + openai SDK
Embedding: 阿里百炼 text-embedding-v4 (OpenAI 兼容接口), 1536d
"""

import json
import os
from typing import Optional

from vaxport.db import Database

# 百炼 DashScope embedding 配置
EMBEDDING_DIM = 1536
EMBEDDING_MODEL = "text-embedding-v4"
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def _get_embedding(text: str) -> Optional[list[float]]:
    """调用百炼 DashScope embeddings API 生成向量。

    优先使用 DASHSCOPE_API_KEY，回退到 OPENAI_API_KEY。
    """
    from openai import OpenAI

    api_key = os.environ.get("DASHSCOPE_API_KEY", "")
    if api_key:
        # 百炼 DashScope (OpenAI 兼容接口)
        client = OpenAI(api_key=api_key, base_url=DASHSCOPE_BASE_URL)
        model = EMBEDDING_MODEL
    else:
        # 回退 OpenAI
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            return None
        client = OpenAI(api_key=api_key)
        model = "text-embedding-3-small"

    try:
        resp = client.embeddings.create(
            model=model, input=text,
            dimensions=EMBEDDING_DIM,  # 显式指定 1536d
        )
        return resp.data[0].embedding
    except Exception:
        # 百炼失败时尝试 OpenAI 回退
        openai_key = os.environ.get("OPENAI_API_KEY", "")
        if openai_key and openai_key != api_key:
            try:
                client2 = OpenAI(api_key=openai_key)
                resp = client2.embeddings.create(
                    model="text-embedding-3-small", input=text,
                )
                return resp.data[0].embedding
            except Exception:
                pass
        return None


def _ensure_rag_schema(db: Database) -> dict:
    """确保 pgvector schema 存在。"""
    setup_sql = """
    CREATE EXTENSION IF NOT EXISTS vector;
    CREATE SCHEMA IF NOT EXISTS vaxport_rag;
    CREATE TABLE IF NOT EXISTS vaxport_rag.documents (
        id SERIAL PRIMARY KEY,
        doc_type VARCHAR(50) NOT NULL,
        title VARCHAR(500),
        content TEXT NOT NULL,
        chunk_index INT DEFAULT 0,
        embedding VECTOR(1536),
        metadata JSONB DEFAULT '{}',
        created_at TIMESTAMPTZ DEFAULT NOW()
    );
    """
    try:
        db.execute_simple(setup_sql)
        # 尝试创建索引（可能因无数据而跳过）
        try:
            db.execute_simple(
                "CREATE INDEX IF NOT EXISTS idx_documents_embedding "
                "ON vaxport_rag.documents "
                "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
            )
        except Exception:
            pass  # IVFFlat 需要数据才能创建，后续可手动创建
        return {"status": "ready", "schema": "vaxport_rag.documents"}
    except Exception as e:
        return {"error": f"RAG schema 创建失败: {e}"}


def index_documents(db: Optional[Database], source_table: str,
                    text_columns: str, doc_type: str = "general",
                    title_column: str = "") -> dict:
    """从数据库表索引文档到 pgvector。

    Args:
        db: 数据库连接
        source_table: schema.table 格式的源表
        text_columns: 逗号分隔的文本列名
        doc_type: 文档类型标签 (sop/regulation/deviation/literature/batch_history)
        title_column: 用作标题的列名

    Returns:
        {"indexed_chunks": N, "source": "schema.table"}
    """
    if not db or not db.is_connected:
        return {"error": "数据库未连接"}

    # 确保 schema
    schema_result = _ensure_rag_schema(db)
    if "error" in schema_result:
        return schema_result

    # 解析表名
    parts = source_table.split(".", 1)
    if len(parts) != 2:
        return {"error": "source_table 格式需为 schema.table"}
    schema, table = parts

    cols = [c.strip() for c in text_columns.split(",") if c.strip()]
    if not cols:
        return {"error": "text_columns 不能为空"}

    # 查询数据
    try:
        result = db.execute_safe_select(schema, table, limit=5000)
        rows = result.get("rows", [])
    except Exception as e:
        return {"error": f"查询源表失败: {e}"}

    if not rows:
        return {"indexed_chunks": 0, "source": source_table, "note": "源表无数据"}

    # 分块和 embedding
    import tiktoken
    enc = tiktoken.get_encoding("cl100k_base")  # 近似分块（百炼 text-embedding-v4 兼容）

    chunks = []
    for i, row in enumerate(rows):
        title = str(row.get(title_column, "")) if title_column else f"{table}_{i}"
        text = " ".join(str(row.get(c, "")) for c in cols)
        if not text.strip():
            continue

        # 按 ~512 tokens 分块
        tokens = enc.encode(text)
        chunk_size = 500
        for j in range(0, len(tokens), chunk_size):
            chunk_tokens = tokens[j:j + chunk_size]
            chunk_text = enc.decode(chunk_tokens)
            if chunk_text.strip():
                chunks.append({
                    "doc_type": doc_type,
                    "title": title[:500],
                    "content": chunk_text,
                    "chunk_index": j // chunk_size,
                    "metadata": json.dumps({"source_row": i}, ensure_ascii=False),
                })

    if not chunks:
        return {"indexed_chunks": 0, "source": source_table, "note": "无可索引的文本内容"}

    # 生成 embeddings 并批量插入
    indexed = 0
    batch_size = 20

    for batch_start in range(0, len(chunks), batch_size):
        batch = chunks[batch_start:batch_start + batch_size]
        texts = [c["content"] for c in batch]

        embeddings = []
        for text in texts:
            emb = _get_embedding(text)
            if emb:
                embeddings.append(emb)
            else:
                embeddings.append(None)

        # INSERT
        for c, emb in zip(batch, embeddings):
            if emb is None:
                continue
            try:
                emb_str = "[" + ",".join(str(v) for v in emb) + "]"
                sql = (
                    f"INSERT INTO vaxport_rag.documents "
                    f"(doc_type, title, content, chunk_index, embedding, metadata) "
                    f"VALUES (%s, %s, %s, %s, %s::vector, %s::jsonb)"
                )
                db.execute_simple(sql, (
                    c["doc_type"], c["title"], c["content"],
                    c["chunk_index"], emb_str, c["metadata"],
                ))
                indexed += 1
            except Exception:
                continue

    return {
        "indexed_chunks": indexed,
        "total_chunks": len(chunks),
        "source": source_table,
        "doc_type": doc_type,
    }


def search_documents(db: Optional[Database], query: str,
                     doc_type: str = "all", top_k: int = 5) -> dict:
    """RAG 向量检索。

    Args:
        db: 数据库连接
        query: 搜索查询文本
        doc_type: 文档类型过滤 ("sop"/"regulation"/"deviation"/"literature"/"all")
        top_k: 返回结果数

    Returns:
        {"results": [...], "query": str, "method": "vector"|"keyword"}
    """
    if not db or not db.is_connected:
        return {"error": "数据库未连接"}

    # 生成查询向量
    query_embedding = _get_embedding(query)
    if query_embedding is None:
        # 回退到关键词搜索
        return _keyword_search(db, query, doc_type, top_k)

    emb_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

    # pgvector cosine distance 搜索
    type_filter = ""
    params = [emb_str, top_k]
    if doc_type != "all":
        type_filter = "AND doc_type = %s"
        params.insert(1, doc_type)

    sql = f"""
        SELECT id, doc_type, title, content, metadata, created_at,
               1 - (embedding <=> %s::vector) AS similarity
        FROM vaxport_rag.documents
        WHERE embedding IS NOT NULL {type_filter}
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """

    try:
        result = db.execute_simple(sql, tuple(params))
        rows = result.get("rows", [])
    except Exception as e:
        # pgvector 不可用时回退关键词搜索
        return _keyword_search(db, query, doc_type, top_k)

    if not rows:
        return {
            "results": [],
            "query": query,
            "method": "vector",
            "note": "未找到匹配文档，请先使用 index_documents 索引数据",
        }

    return {
        "results": [
            {
                "title": r.get("title", ""),
                "content": r.get("content", "")[:500],
                "doc_type": r.get("doc_type", ""),
                "similarity": round(r.get("similarity", 0), 4),
                "source_id": r.get("id"),
            }
            for r in rows
        ],
        "query": query,
        "top_k": top_k,
        "method": "vector",
    }


def _keyword_search(db: Database, query: str, doc_type: str,
                    top_k: int) -> dict:
    """回退方案: PostgreSQL ILIKE 关键词搜索。"""
    type_filter = ""
    params = []
    if doc_type != "all":
        type_filter = "AND doc_type = %s"
        params.append(doc_type)

    like_pattern = "%" + query.replace("%", "\\%") + "%"
    params.extend([like_pattern, top_k])

    sql = f"""
        SELECT id, doc_type, title, content, metadata, created_at
        FROM vaxport_rag.documents
        WHERE (content ILIKE %s OR title ILIKE %s) {type_filter}
        ORDER BY created_at DESC
        LIMIT %s
    """

    try:
        result = db.execute_simple(sql, tuple(params))
        rows = result.get("rows", [])
    except Exception as e:
        # 表不存在
        return {
            "results": [],
            "query": query,
            "method": "keyword",
            "note": f"文档搜索不可用: {e}。请先运行 index_documents 索引文档。",
        }

    return {
        "results": [
            {
                "title": r.get("title", ""),
                "content": r.get("content", "")[:500],
                "doc_type": r.get("doc_type", ""),
                "similarity": None,
                "source_id": r.get("id"),
            }
            for r in rows
        ],
        "query": query,
        "top_k": top_k,
        "method": "keyword",
    }