from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# 土地变化检测库默认白名单：统计智能体只查询这一张图斑表。
# 地类字典与业务口径（原/现土地类型、三调大类、编码映射）固化在 SQLAgent 提示词中，
# 不再作为可查询表暴露给 LLM（可通过 GEOAGENT_PG_WHITELIST 覆盖）。
DEFAULT_PG_WHITELIST: list[str] = ['data."2026_1_change_landuse"']


@dataclass(frozen=True)
class ModelProfile:
    """模型注册表中的一条模型（或兼容端点）配置。"""

    id: str
    provider: str = "openai"
    base_url: str | None = None
    api_key_env: str = "OPENAI_API_KEY"
    temperature: float | None = None
    max_tokens: int | None = None
    description: str = ""

    @property
    def api_key(self) -> str:
        return os.getenv(self.api_key_env, "")

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "available": self.available,
            "description": self.description,
        }


def default_model_registry() -> dict[str, ModelProfile]:
    """内置模型注册表。新增模型时在此追加一个 ModelProfile。"""
    return {
        "qwen3.7-flash": ModelProfile(
            id="qwen3.7-flash",
            provider="openai_compatible",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key_env="OPENAI_API_KEY",
            description="Alibaba Qwen3.7-Flash (DashScope)",
        ),
        "qwen3.7-plus": ModelProfile(
            id="qwen3.7-plus",
            provider="openai_compatible",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key_env="OPENAI_API_KEY",
            description="Alibaba Qwen3.7-Plus (DashScope)",
        ),
    }


class Settings:
    """应用配置。数据目录与默认模型可通过环境变量覆盖。"""

    def __init__(self) -> None:
        default_data_dir = Path(__file__).resolve().parent.parent / "data"
        self.data_dir = Path(os.getenv("GEOAGENT_DATA_DIR", str(default_data_dir)))
        self.default_model = os.getenv("GEOAGENT_DEFAULT_MODEL", "qwen3.7-plus")
        self.router_model = os.getenv("GEOAGENT_ROUTER_MODEL", "")
        default_skills_dir = Path(__file__).resolve().parent.parent.parent / "skills"
        self.skills_dir = Path(os.getenv("GEOAGENT_SKILLS_DIR", str(default_skills_dir)))
        self.model_registry = default_model_registry()
        # PostgreSQL/PostGIS 土地变化检测库（受控只读访问，连接串来自环境变量）。
        self.pg_dsn = os.getenv("GEOAGENT_PG_DSN", "").strip()
        raw_whitelist = os.getenv("GEOAGENT_PG_WHITELIST", "").strip()
        self.pg_whitelist = (
            [item.strip() for item in raw_whitelist.split(",") if item.strip()]
            if raw_whitelist
            else list(DEFAULT_PG_WHITELIST)
        )
        self.pg_max_rows = int(os.getenv("GEOAGENT_PG_MAX_ROWS", "200"))
        self.pg_timeout_s = float(os.getenv("GEOAGENT_PG_TIMEOUT_S", "10"))
        self.pg_audit_path = self.data_dir / "pg_audit.jsonl"

    def profile(self, model_id: str) -> ModelProfile:
        try:
            return self.model_registry[model_id]
        except KeyError:
            known = ", ".join(sorted(self.model_registry))
            raise KeyError(f"Unknown model '{model_id}'. Available: {known}") from None

    def list_profiles(self) -> list[dict[str, Any]]:
        return [p.to_dict() for p in self.model_registry.values()]
