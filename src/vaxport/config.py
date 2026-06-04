"""配置管理 — YAML 配置文件读写 + 首次运行引导"""

import os
import sys
import yaml
from pathlib import Path
from typing import Optional

DEFAULT_CONFIG = {
    "api": {
        "aliyun_key": "",
        "aliyun_model": "",
        "aliyun_base_url": "",
    },
    "local": {
        "ollama_url": "",
        "ollama_model": "",
    },
    "pg": {
        "host": "",
        "port": 5432,
        "database": "",
        "user": "",
        "exclude_schemas": [],
        "databases": [],
        "ssh_tunnel": {
            "enabled": False,
            "jump_host": "",
            "jump_port": 22,
            "db_host": "",
            "db_port": 5432,
            "local_port": 5433,
        },
    },
    "agent": {
        "max_tool_rounds": 100,
        "total_timeout": 600,
        "session_dir": "~/.vaxport/sessions",
        "primary_backend": "aliyun",
        "export_dir": "",
        "auto_plan": True,
        "plan_confirm": False,
        "auto_review": True,
        "agent_temperatures": {},
        "agent_models": {},
    },
}

CONFIG_DIR = Path.home() / ".vaxport"
CONFIG_PATH = CONFIG_DIR / "config.yaml"


class Config:
    """配置管理类，支持 YAML 文件 + 环境变量 fallback"""

    def __init__(self, config_path: Optional[Path] = None):
        self._path = config_path or CONFIG_PATH
        self._data = DEFAULT_CONFIG.copy()
        if self._path.exists():
            self._load()

    def _load(self):
        with open(self._path) as f:
            data = yaml.safe_load(f) or {}
        self._merge(self._data, data)

    def _merge(self, base: dict, override: dict):
        for k, v in override.items():
            if isinstance(v, dict) and k in base:
                self._merge(base[k], v)
            else:
                base[k] = v

    @property
    def aliyun_api_key(self) -> str:
        """阿里云 API Key，优先环境变量"""
        return os.getenv("DASHSCOPE_API_KEY") or self._data["api"]["aliyun_key"]

    @property
    def aliyun_model(self) -> str:
        return self._data["api"]["aliyun_model"]

    @property
    def aliyun_base_url(self) -> str:
        return self._data["api"]["aliyun_base_url"]

    @property
    def ollama_url(self) -> str:
        return self._data["local"]["ollama_url"]

    @property
    def ollama_model(self) -> str:
        return self._data["local"]["ollama_model"]

    @property
    def pg_host(self) -> str:
        return os.getenv("PG_HOST") or self._data["pg"]["host"]

    @property
    def pg_port(self) -> int:
        return int(os.getenv("PG_PORT") or self._data["pg"]["port"])

    @property
    def pg_database(self) -> str:
        return os.getenv("PG_DATABASE") or self._data["pg"]["database"]

    @property
    def pg_user(self) -> str:
        return os.getenv("PG_USER") or self._data["pg"]["user"]

    @property
    def pg_password(self) -> str:
        return os.getenv("PG_PASSWORD") or self._data["pg"].get("password", "")

    @property
    def ssh_tunnel_enabled(self) -> bool:
        return self._data["pg"].get("ssh_tunnel", {}).get("enabled", False)

    @property
    def ssh_tunnel_jump_host(self) -> str:
        return self._data["pg"].get("ssh_tunnel", {}).get("jump_host", "")

    @property
    def ssh_tunnel_jump_port(self) -> int:
        return self._data["pg"].get("ssh_tunnel", {}).get("jump_port", 22)

    @property
    def ssh_tunnel_db_host(self) -> str:
        return self._data["pg"].get("ssh_tunnel", {}).get("db_host", self.pg_host)

    @property
    def ssh_tunnel_db_port(self) -> int:
        return self._data["pg"].get("ssh_tunnel", {}).get("db_port", self.pg_port)

    @property
    def ssh_tunnel_local_port(self) -> int:
        return self._data["pg"].get("ssh_tunnel", {}).get("local_port", 5433)

    @property
    def pg_exclude_schemas(self) -> list:
        return self._data["pg"].get("exclude_schemas", [])

    @property
    def db_configs(self) -> list[dict]:
        """返回所有数据库配置列表，每项包含 name, database, host, port, user, password"""
        dbs = self._data["pg"].get("databases", [])
        if not dbs:
            # 向后兼容：无 databases 配置时使用单库
            return [{
                "name": self._data["pg"]["database"],
                "database": self._data["pg"]["database"],
                "host": self.pg_host,
                "port": self.pg_port,
                "user": self.pg_user,
                "password": self.pg_password,
            }]
        # 填充默认值
        result = []
        for db in dbs:
            result.append({
                "name": db.get("name", db["database"]),
                "database": db["database"],
                "host": db.get("host", self.pg_host),
                "port": db.get("port", self.pg_port),
                "user": db.get("user", self.pg_user),
                "password": db.get("password", self.pg_password),
            })
        return result

    @property
    def max_tool_rounds(self) -> int:
        return self._data["agent"]["max_tool_rounds"]

    @property
    def total_timeout(self) -> int:
        return self._data["agent"].get("total_timeout", 0)

    @property
    def session_dir(self) -> Path:
        return Path(self._data["agent"]["session_dir"]).expanduser()

    @property
    def primary_backend(self) -> str:
        return self._data["agent"]["primary_backend"]

    @property
    def export_dir(self) -> Path:
        custom = self._data["agent"].get("export_dir", "")
        if custom:
            return Path(custom).expanduser()
        return Path.home() / "Downloads" / "vaxport_exports"

    @property
    def auto_plan(self) -> bool:
        return self._data["agent"].get("auto_plan", True)

    @property
    def plan_confirm(self) -> bool:
        return self._data["agent"].get("plan_confirm", True)

    @property
    def auto_review(self) -> bool:
        return self._data["agent"].get("auto_review", True)

    @property
    def agent_temperatures(self) -> dict:
        return self._data["agent"].get("agent_temperatures", {})

    def get_agent_temperature(self, agent_name: str) -> float:
        """获取指定 Agent 的 temperature，未配置返回全局默认 0.1"""
        return self.agent_temperatures.get(agent_name, 0.1)

    def set_agent_temperature(self, agent_name: str, temperature: float):
        """设置 Agent 的 temperature"""
        if "agent_temperatures" not in self._data["agent"]:
            self._data["agent"]["agent_temperatures"] = {}
        self._data["agent"]["agent_temperatures"][agent_name] = temperature
        self.save()

    @property
    def agent_models(self) -> dict:
        return self._data["agent"].get("agent_models", {})

    def get_agent_model(self, agent_name: str) -> str | None:
        """获取指定 Agent 的偏好模型，未配置返回 None（继承全局）"""
        return self.agent_models.get(agent_name)

    def set_agent_model(self, agent_name: str, model_id: str | None):
        """设置 Agent 偏好模型。model_id 为 None 时删除配置（恢复继承全局）"""
        if "agent_models" not in self._data["agent"]:
            self._data["agent"]["agent_models"] = {}
        if model_id is None:
            self._data["agent"]["agent_models"].pop(agent_name, None)
        else:
            self._data["agent"]["agent_models"][agent_name] = model_id
        self.save()

    @property
    def pg_dsn(self) -> str:
        """构建 PostgreSQL 连接字符串（key=value 格式供 psycopg2 使用）"""
        # psycopg2 不使用 dsn 字符串，直接传 key=value
        return (
            f"host={self.pg_host} port={self.pg_port} "
            f"dbname={self.pg_database} user={self.pg_user}"
        )

    @property
    def is_configured(self) -> bool:
        """检查是否已完成配置"""
        return self._path.exists()

    def save(self):
        """保存配置到 YAML 文件"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w") as f:
            yaml.dump(self._data, f, allow_unicode=True, default_flow_style=False)

    def set(self, section: str, key: str, value):
        """运行时修改配置值"""
        self._data[section][key] = value

    def get(self, section: str, key: str, default=None):
        return self._data.get(section, {}).get(key, default)


def run_setup_wizard(config: Config) -> Config:
    """首次运行配置引导"""

    print("首次运行 vaxport，正在配置...\n")

    api_key = input("API Key (阿里云百炼): ")
    if api_key:
        config.set("api", "aliyun_key", api_key)

    pg_host = input(f"PG 主机 [{config.pg_host}]: ")
    if pg_host:
        config.set("pg", "host", pg_host)

    pg_port_str = input(f"PG 端口 [{config.pg_port}]: ")
    if pg_port_str:
        config.set("pg", "port", int(pg_port_str))

    pg_db = input(f"PG 数据库 [{config.pg_database}]: ")
    if pg_db:
        config.set("pg", "database", pg_db)

    pg_user = input(f"PG 用户 [{config.pg_user}]: ")
    if pg_user:
        config.set("pg", "user", pg_user)

    ollama_url = input(f"Ollama URL [{config.ollama_url}]: ")
    if ollama_url:
        config.set("local", "ollama_url", ollama_url)

    ollama_model = input("本地模型名称 (如 qwen3:14b, 留空跳过): ")
    if ollama_model:
        config.set("local", "ollama_model", ollama_model)

    # API Key 安全提示
    if api_key:
        print(
            "\n⚠️  API Key 已保存到配置文件。建议改用环境变量: export DASHSCOPE_API_KEY=sk-xxx"
        )

    if not ollama_model:
        print(
            "\n💡 本地大模型未配置。如需离线备用，可后续执行 ollama pull <模型名>，"
            "然后在 ~/.vaxport/config.yaml 中设置 local.ollama_model"
        )

    config.save()
    print(f"\n配置已保存到 {config._path}")
    return config


def load_config() -> Config:
    """加载配置，首次运行自动引导"""
    config = Config()
    if not config.is_configured:
        if sys.stdin.isatty():
            config = run_setup_wizard(config)
        else:
            # headless 模式（如 Tauri sidecar），创建空配置文件
            config.save()
    return config