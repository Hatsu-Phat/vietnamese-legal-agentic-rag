"""Application configuration loader using pydantic-settings and PyYAML.

Loads environment variables from .env and static pipeline parameters
from configs/dev.yaml without hardcoding secrets.
"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
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
    gemini_api_key: str | None = Field(default=None, alias="GEMINI_API_KEY")
    gemini_model_id: str = Field(default="gemini-2.5-flash", alias="GEMINI_MODEL_ID")
    llm_api_key: str | None = Field(default=None, alias="LLM_API_KEY")
    llm_model: str = Field(default="gemini-2.5-flash", alias="LLM_MODEL")
    llm_temperature: float = Field(default=0.0, alias="LLM_TEMPERATURE")
    llm_timeout_seconds: int = Field(default=30, alias="LLM_TIMEOUT_SECONDS")

    # Embeddings & Retrieval
    embedding_model_name: str = Field(default="BAAI/bge-m3", alias="EMBEDDING_MODEL_NAME")
    embedding_device: str = Field(default="cpu", alias="EMBEDDING_DEVICE")
    chroma_persist_directory: str = Field(
        default="./data/indexes/chroma", alias="CHROMA_PERSIST_DIRECTORY"
    )
    metadata_db_url: str = Field(default="sqlite:///./data/metadata.db", alias="METADATA_DB_URL")

    # Agent Bounds
    max_retrieval_rounds: int = Field(default=2, alias="MAX_RETRIEVAL_ROUNDS")
    tool_timeout_seconds: int = Field(default=15, alias="TOOL_TIMEOUT_SECONDS")
    strict_citation_verification: bool = Field(default=True, alias="STRICT_CITATION_VERIFICATION")


def load_yaml_config(config_path: str = "configs/dev.yaml") -> dict[str, Any]:
    """Load static pipeline YAML configuration file safely.

    Args:
        config_path: Relative or absolute path to the YAML file.

    Returns:
        dict containing parsed configuration parameters.
    """
    path = Path(config_path)
    if not path.is_file():
        return {}
    with open(path, encoding="utf-8") as f:
        content: Any = yaml.safe_load(f)
        if isinstance(content, dict):
            return content
        return {}


# Global settings singleton
settings = AppSettings()
