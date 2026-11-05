"""Application configuration loader using pydantic-settings and PyYAML.

Loads environment variables from .env and static pipeline parameters
from configs/dev.yaml without hardcoding secrets.
"""

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.core.paths import DataPaths

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "dev.yaml"


def resolve_project_path(path: str | Path) -> Path:
    """Resolve relative paths against the checkout, regardless of working directory."""
    expanded = Path(path).expanduser()
    return expanded if expanded.is_absolute() else PROJECT_ROOT / expanded


def load_yaml_config(config_path: str | Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Read pipeline YAML from an absolute or project-relative path."""
    path = resolve_project_path(config_path)
    with path.open(encoding="utf-8") as stream:
        content: Any = yaml.safe_load(stream)
    if not isinstance(content, dict):
        raise ValueError(f"Pipeline config must contain a mapping: {path}")
    return content


def load_data_paths(config: dict[str, Any] | None = None) -> DataPaths:
    """Validate the shared layout and resolve its directories to absolute paths."""
    cfg = load_yaml_config() if config is None else config
    paths = DataPaths.model_validate(cfg.get("paths", {}))
    return DataPaths.model_validate(
        {name: resolve_project_path(value) for name, value in paths.model_dump().items()}
    )


class AppSettings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application Environment
    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")

    # LLM Settings
    llm_provider: str = Field(default="gemini", alias="LLM_PROVIDER")
    gemini_api_key: SecretStr | None = Field(default=None, alias="GEMINI_API_KEY", repr=False)
    gemini_model_id: str = Field(default="gemini-2.5-flash", alias="GEMINI_MODEL_ID")
    llm_api_key: SecretStr | None = Field(default=None, alias="LLM_API_KEY", repr=False)
    llm_model: str = Field(default="gemini-2.5-flash", alias="LLM_MODEL")
    llm_temperature: float = Field(default=0.0, alias="LLM_TEMPERATURE")
    llm_timeout_seconds: int = Field(default=30, alias="LLM_TIMEOUT_SECONDS")

    # Embeddings & Retrieval
    embedding_model_name: str = Field(default="BAAI/bge-m3", alias="EMBEDDING_MODEL_NAME")
    embedding_device: str = Field(default="cpu", alias="EMBEDDING_DEVICE")
    chroma_persist_directory: str = Field(
        default=str(load_data_paths({}).indexes_dir / "chroma"), alias="CHROMA_PERSIST_DIRECTORY"
    )
    metadata_db_url: str = Field(
        default=f"sqlite:///{load_data_paths({}).processed_dir / 'metadata.db'}",
        alias="METADATA_DB_URL",
    )

    # Cloud credentials stay in the environment, never YAML or index manifests.
    qdrant_mode: Literal["local", "server", "memory"] | None = Field(
        default=None, alias="QDRANT_MODE"
    )
    qdrant_url: str | None = Field(default=None, alias="QDRANT_URL")
    qdrant_api_key: SecretStr | None = Field(default=None, alias="QDRANT_API_KEY", repr=False)

    # Role credentials (P2): one API key per role; empty keys are disabled, never matched.
    auth_api_key_employee: SecretStr | None = Field(
        default=None, alias="AUTH_API_KEY_EMPLOYEE", repr=False
    )
    auth_api_key_manager: SecretStr | None = Field(
        default=None, alias="AUTH_API_KEY_MANAGER", repr=False
    )
    auth_api_key_legal: SecretStr | None = Field(
        default=None, alias="AUTH_API_KEY_LEGAL", repr=False
    )

    # Agent Bounds
    max_retrieval_rounds: int = Field(default=2, alias="MAX_RETRIEVAL_ROUNDS")
    tool_timeout_seconds: int = Field(default=15, alias="TOOL_TIMEOUT_SECONDS")
    strict_citation_verification: bool = Field(default=True, alias="STRICT_CITATION_VERIFICATION")


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """One settings instance per process; injectable explicitly in tests/startup."""
    return AppSettings()


# Compatibility for existing CLI imports; new startup code uses get_settings().
settings = get_settings()
