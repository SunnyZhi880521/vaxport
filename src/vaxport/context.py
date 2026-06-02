"""上下文管理 — Token 计数 + 自动裁剪 + 自动压缩"""

from typing import Optional, Callable


# 模型上下文窗口配置表 (tokens) — 作为 API 获取失败时的兜底
# 支持前缀匹配
MODEL_CONTEXT_WINDOWS = {
    # 通义千问系列
    "qwen-max": 32768,
    "qwen3.7": 131072,
    "qwen3.6": 131072,
    "qwen3.5": 131072,
    "qwen-plus": 131072,
    "qwen-turbo": 1000000,
    "qwen-long": 1000000,
    "qwen-flash": 131072,
    "qwen-coder": 131072,
    "qwq-plus": 139264,
    "qwen": 32768,
    # DeepSeek 系列
    "deepseek-v4-pro": 131072,
    "deepseek-v4-flash": 131072,
    "deepseek-v4": 131072,
    "deepseek-v3": 131072,
    "deepseek-r1": 131072,
    "deepseek": 131072,
    # GLM 系列
    "glm-5": 131072,
    "glm-4": 131072,
    "glm": 32768,
    # 默认：现代模型普遍 128K+
    "default": 131072,
}

TRIM_THRESHOLD = 0.75  # 75% 触发裁剪
COMPRESS_THRESHOLD = 0.85  # 85% 触发自动压缩
TOOL_RESULT_MAX_TOKENS = 6000  # 单次 Tool 结果最大 token
MAX_ROUNDS = 100  # 安全上限（正常情况下上下文压缩会先触发）


def get_context_window(model: str) -> int:
    """获取模型的上下文窗口大小（支持前缀匹配）"""
    model_lower = model.lower()
    if model_lower in MODEL_CONTEXT_WINDOWS:
        return MODEL_CONTEXT_WINDOWS[model_lower]
    for key in sorted(MODEL_CONTEXT_WINDOWS, key=len, reverse=True):
        if key == "default":
            continue
        if model_lower.startswith(key):
            return MODEL_CONTEXT_WINDOWS[key]
    return MODEL_CONTEXT_WINDOWS["default"]


def estimate_tokens(text: str) -> int:
    """估算文本 token 数（中文 ~1 token/字，英文 ~0.25 token/字）"""
    chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other_chars = len(text) - chinese_chars
    return int(chinese_chars * 1.0 + other_chars * 0.25)


def count_tokens(messages: list) -> int:
    """计算消息列表的总 token 数"""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and "text" in part:
                    total += estimate_tokens(part["text"])
        total += 4  # role + overhead
    return total


def trim_context(messages: list, model: str, context_window: int = 0) -> tuple[list, bool]:
    """裁剪消息历史（保留最近轮次），返回 (裁剪后列表, 是否裁剪)

    Args:
        messages: 消息列表
        model: 模型名（用于查找上下文窗口）
        context_window: 显式指定上下文窗口（>0 时优先于 model 查找）
    """
    if context_window <= 0:
        context_window = get_context_window(model)
    current_tokens = count_tokens(messages)
    threshold = int(context_window * TRIM_THRESHOLD)

    if current_tokens <= threshold:
        return messages, False

    kept = []
    if messages and messages[0]["role"] == "system":
        kept.append(messages[0])
        start_idx = 1
    else:
        start_idx = 0

    # 保留最近 5 轮用户对话（避免过度裁剪）
    user_indices = [
        i for i in range(start_idx, len(messages))
        if messages[i]["role"] == "user"
    ]

    if len(user_indices) > 5:
        keep_from = user_indices[-6] + 1
    else:
        keep_from = start_idx

    kept.extend(messages[keep_from:])

    # 如果裁剪后仍然超标，只保留最近 3 轮
    if count_tokens(kept) > threshold:
        kept = [kept[0]] if kept and kept[0]["role"] == "system" else []
        if len(user_indices) > 3:
            keep_from = user_indices[-4] + 1
            kept.extend(messages[keep_from:])

    return kept, True


def build_compression_summary(messages: list, summarizer: Callable[[list], str]) -> str:
    """对历史消息进行摘要压缩

    Args:
        messages: 需要压缩的消息列表（不含 system prompt）
        summarizer: 摘要生成函数，接收消息列表，返回摘要文本

    Returns:
        压缩后的摘要文本，可注入为 system 消息
    """
    return summarizer(messages)


def truncate_tool_result(content: str, max_tokens: int = TOOL_RESULT_MAX_TOKENS) -> str:
    """截断过大的 Tool 结果"""
    tokens = estimate_tokens(content)
    if tokens <= max_tokens:
        return content

    char_limit = int(max_tokens / 1.0)
    truncated = content[:char_limit]
    return (
        truncated
        + f"\n\n⚠️ 结果已截断（原 {tokens} tokens → {max_tokens} tokens）"
    )