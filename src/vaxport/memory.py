"""跨会话反馈记忆 — 自动提取用户纠正并跨会话注入 system prompt"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

MEMORY_DIR = Path.home() / ".vaxport" / "memory"
FEEDBACK_FILE = MEMORY_DIR / "feedback.json"

FEEDBACK_EXTRACTION_PROMPT = """分析用户消息，判断是否包含对助手行为的纠正或反馈规则。

用户消息：{user_message}

如果是纠正/反馈，提取为一条简洁的行为规则（20字以内），格式：
RULE: <规则内容>

如果不是纠正/反馈（普通查询、追问补充等），回复：NO_FEEDBACK

纠正/反馈示例：
- "其他月的报警数是0,那就应该输出0这个数据" → RULE: 分组统计必须包含零值月份
- "冷链分析时你应该同时查运输表" → RULE: 冷链分析需同时查运输和偏差表
- "以后分析报告末尾给出放行结论" → RULE: 分析报告末尾需给出放行结论
- "查一下XX批次" → NO_FEEDBACK
- "把抗原数据也补充进去" → NO_FEEDBACK"""


class FeedbackMemory:
    """跨会话反馈记忆管理器"""

    def __init__(self):
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        self._items: list[dict] = []
        self._load()

    def _load(self):
        """从 JSON 文件加载反馈"""
        if FEEDBACK_FILE.exists():
            try:
                with open(FEEDBACK_FILE, encoding="utf-8") as f:
                    data = json.load(f)
                self._items = data.get("items", [])
            except (json.JSONDecodeError, KeyError):
                self._items = []

    def _save(self):
        """保存反馈到 JSON 文件"""
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "items": self._items,
            "updated_at": datetime.now().isoformat(),
        }
        with open(FEEDBACK_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def needs_extraction(self, user_message: str) -> bool:
        """检测用户消息是否可能包含纠正/反馈"""
        correction_keywords = [
            "不是", "不对", "错了", "应该是", "应该是这样",
            "记住", "以后", "下次", "上次",
            "你应该", "你要", "你需要", "不应该",
            "怎么没有", "为什么不", "为什么没",
        ]
        msg_lower = user_message.lower()
        return any(kw in msg_lower for kw in correction_keywords)

    def extract_and_store(self, user_message: str, llm_client) -> Optional[str]:
        """从用户消息中提取反馈规则并存储。

        使用轻量 LLM 调用提取规则，异步友好。
        返回提取的规则文本，或 None。
        """
        if not self.needs_extraction(user_message):
            return None

        try:
            prompt = FEEDBACK_EXTRACTION_PROMPT.format(user_message=user_message)
            resp = llm_client.chat_completion(
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_message},
                ],
                tools=None,
                stream=False,
            )
            llm_client.record_success()
            result = resp.choices[0].message.content or ""

            if result.startswith("RULE:") and "NO_FEEDBACK" not in result:
                rule = result.replace("RULE:", "").strip()
                # 去重
                existing_rules = {item["feedback"] for item in self._items}
                if rule not in existing_rules:
                    self._items.append({
                        "feedback": rule,
                        "source_query": user_message[:200],
                        "timestamp": datetime.now().isoformat(),
                    })
                    self._save()
                    return rule
        except Exception:
            llm_client.record_failure()

        return None

    def build_system_prompt_section(self) -> str:
        """构建要注入 system prompt 的反馈记忆段落"""
        if not self._items:
            return ""

        lines = ["\n## 用户历史反馈（请严格遵守）\n"]
        for item in self._items[-20:]:  # 最近 20 条
            lines.append(f"- {item['feedback']}")
        return "\n".join(lines)

    @property
    def item_count(self) -> int:
        return len(self._items)