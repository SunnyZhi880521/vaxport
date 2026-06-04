"""LLM 后端适配层 — OpenAI 兼容协议统一接口"""

import time
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import httpx
from openai import OpenAI, APIStatusError

from vaxport.config import Config


class Backend(Enum):
    ALIYUN = "aliyun"
    OLLAMA = "ollama"


@dataclass
class BackendState:
    """后端状态追踪"""

    name: str
    model: str
    base_url: str
    api_key: str
    consecutive_failures: int = 0
    is_active: bool = True


@dataclass
class LLMClient:
    """统一的 LLM 客户端，基于 OpenAI 兼容协议"""

    config: Config
    _clients: dict[str, OpenAI] = field(default_factory=dict, init=False)
    _states: dict[str, BackendState] = field(default_factory=dict, init=False)
    _active_backend: str = field(default="", init=False)
    _recovery_timer: Optional[threading.Timer] = field(default=None, init=False)
    _model_max_tokens: dict[str, int] = field(default_factory=dict, init=False)

    MAX_FAILURES = 3
    RECOVERY_INTERVAL = 300  # 5 分钟

    def __post_init__(self):
        self._register_backends()
        if self._states:
            primary = self.config.primary_backend
            self._active_backend = primary if primary in self._states else next(iter(self._states))
        else:
            self._active_backend = ""

    def _register_backends(self):
        """注册所有可用后端"""
        # 使用干净的 httpx 客户端，忽略系统代理环境变量
        http_client = httpx.Client(proxy=None, trust_env=False, timeout=httpx.Timeout(600.0, connect=30.0))

        # DashScope (阿里云百炼)
        aliyun_key = self.config.aliyun_api_key
        if aliyun_key:
            self._clients["aliyun"] = OpenAI(
                base_url=self.config.aliyun_base_url,
                api_key=aliyun_key,
                http_client=http_client,
            )
            self._states["aliyun"] = BackendState(
                name="aliyun",
                model=self.config.aliyun_model,
                base_url=self.config.aliyun_base_url,
                api_key=aliyun_key,
            )

        # Ollama (本地) — 仅当配置了模型名称时才注册
        ollama_model = self.config.ollama_model
        if ollama_model:
            self._clients["ollama"] = OpenAI(
                base_url=f"{self.config.ollama_url}/v1",
                api_key="ollama",
                http_client=http_client,
            )
            self._states["ollama"] = BackendState(
                name="ollama",
                model=ollama_model,
                base_url=f"{self.config.ollama_url}/v1",
                api_key="ollama",
            )

    @property
    def active_backend(self) -> str:
        return self._active_backend

    @property
    def active_model(self) -> str:
        if not self._active_backend or self._active_backend not in self._states:
            return ""
        return self._states[self._active_backend].model

    @property
    def active_client(self) -> OpenAI:
        if not self._active_backend or self._active_backend not in self._clients:
            raise RuntimeError("没有可用的 LLM 后端，请先配置 API Key 或本地模型")
        return self._clients[self._active_backend]

    @property
    def available_backends(self) -> list[str]:
        return list(self._clients.keys())

    def switch_backend(self, name: str) -> bool:
        """手动切换后端"""
        if name not in self._clients:
            return False
        self._active_backend = name
        self._states[name].is_active = True
        return True

    def list_models(self, backend_name: str) -> list[str]:
        """调用 /v1/models 获取该后端所有可用模型 ID 列表"""
        if backend_name not in self._clients:
            return []
        client = self._clients[backend_name]
        resp = client.models.list()
        return sorted([m.id for m in resp.data])

    def set_model(self, backend_name: str, model_name: str) -> bool:
        """动态切换后端+模型"""
        if backend_name not in self._clients:
            return False
        self._active_backend = backend_name
        self._states[backend_name].model = model_name
        self._states[backend_name].is_active = True
        return True

    def record_failure(self):
        """记录当前后端失败"""
        if not self._active_backend or self._active_backend not in self._states:
            return
        state = self._states[self._active_backend]
        state.consecutive_failures += 1
        if state.consecutive_failures >= self.MAX_FAILURES:
            self._failover()

    def record_success(self):
        """记录当前后端成功"""
        state = self._states[self._active_backend]
        state.consecutive_failures = 0

    def _failover(self):
        """熔断：切换到备用后端"""
        current = self._active_backend
        state = self._states[current]
        state.is_active = False

        # 找第一个可用的备用后端
        for name, s in self._states.items():
            if name != current and s.is_active:
                self._active_backend = name
                break

        # 启动定时恢复
        self._schedule_recovery(current)

    def _schedule_recovery(self, backend_name: str):
        """定时尝试恢复主后端"""
        if self._recovery_timer:
            self._recovery_timer.cancel()

        def _try_recover():
            state = self._states[backend_name]
            state.is_active = True
            state.consecutive_failures = 0

        self._recovery_timer = threading.Timer(self.RECOVERY_INTERVAL, _try_recover)
        self._recovery_timer.daemon = True
        self._recovery_timer.start()

    def get_status(self) -> dict:
        """获取所有后端状态"""
        result = {}
        for name, state in self._states.items():
            marker = " ← 当前" if name == self._active_backend else ""
            result[f"{name}{marker}"] = {
                "model": state.model,
                "failures": state.consecutive_failures,
                "active": state.is_active,
            }
        return result

    def chat_completion(self, messages: list, tools: Optional[list] = None, stream: bool = False,
                        model: str | None = None, temperature: float | None = None):
        """统一的 chat completion 调用，含 429 指数退避重试"""
        kwargs = {
            "model": model or self.active_model,
            "messages": messages,
            "temperature": temperature if temperature is not None else 0.1,
        }
        if tools:
            kwargs["tools"] = tools
        if stream:
            kwargs["stream"] = True

        max_retries = 3
        base_delay = 1.0
        for attempt in range(max_retries + 1):
            try:
                return self.active_client.chat.completions.create(**kwargs)
            except APIStatusError as e:
                if e.status_code == 429 and attempt < max_retries:
                    delay = base_delay * (2 ** attempt)
                    time.sleep(delay)
                    continue
                raise

    def fetch_model_max_tokens(self, model_name: str) -> int:
        """从 API 获取模型上下文窗口大小（max_tokens 字段）"""
        try:
            if not self._active_backend or self._active_backend not in self._clients:
                return 0
            # 用当前活跃后端的裸 http_client 调用 /v1/models/{name}
            client = self._clients[self._active_backend]
            # OpenAI SDK 屏蔽了直接访问，用 httpx 调用
            import httpx
            resp = httpx.Client(proxy=None, trust_env=False).get(
                f"{self._states[self._active_backend].base_url}/models/{model_name}",
                headers={"Authorization": f"Bearer {self._states[self._active_backend].api_key}"},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                extra = data.get("extra_info", {})
                envs = extra.get("default_envs", {})
                max_tokens = envs.get("max_tokens")
                if max_tokens and isinstance(max_tokens, int) and max_tokens > 0:
                    self._model_max_tokens[model_name] = max_tokens
                    return max_tokens
        except Exception:
            pass
        return 0

    def get_model_max_tokens(self, model_name: str) -> int:
        """获取模型上下文窗口（缓存优先，未命中时查 API）"""
        if model_name in self._model_max_tokens:
            return self._model_max_tokens[model_name]
        # 尝试从 API 获取
        result = self.fetch_model_max_tokens(model_name)
        if result > 0:
            return result
        # 缓存 "0" 避免重复请求
        self._model_max_tokens[model_name] = 0
        return 0

    @property
    def active_model_max_tokens(self) -> int:
        """当前活跃模型的上下文窗口大小"""
        return self.get_model_max_tokens(self.active_model)


def create_llm_client(config: Config) -> LLMClient:
    """工厂函数：根据配置创建 LLM 客户端"""
    return LLMClient(config=config)