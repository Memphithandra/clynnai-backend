from functools import lru_cache
import json
import os
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROVIDER_CONFIG_FIELDS = {
    "public_base_url",
    "upstream_base_url",
    "upstream_api_key",
    "default_model",
    "image_model",
    "asr_model",
    "firecrawl_api_key",
    "firecrawl_base_url",
    "agent_max_steps",
    "agent_use_native_tool_calls",
}

PROVIDER_CONFIG_ENV_ALIASES = {
    "public_base_url": ("PUBLIC_BASE_URL", "CLYNN_PUBLIC_BASE_URL"),
    "upstream_base_url": ("UPSTREAM_BASE_URL", "CLYNN_UPSTREAM_BASE_URL"),
    "upstream_api_key": ("UPSTREAM_API_KEY", "CLYNN_UPSTREAM_API_KEY"),
    "default_model": ("DEFAULT_MODEL", "CLYNN_DEFAULT_MODEL"),
    "image_model": ("IMAGE_MODEL", "CLYNN_IMAGE_MODEL"),
    "asr_model": ("ASR_MODEL", "CLYNN_ASR_MODEL"),
    "firecrawl_api_key": ("FIRECRAWL_API_KEY", "CLYNN_FIRECRAWL_API_KEY"),
    "firecrawl_base_url": ("FIRECRAWL_BASE_URL", "CLYNN_FIRECRAWL_BASE_URL"),
    "agent_max_steps": ("AGENT_MAX_STEPS", "CLYNN_AGENT_MAX_STEPS"),
    "agent_use_native_tool_calls": ("AGENT_USE_NATIVE_TOOL_CALLS", "CLYNN_AGENT_USE_NATIVE_TOOL_CALLS"),
}


class Settings(BaseSettings):
    host: str = Field("0.0.0.0", validation_alias=AliasChoices("HOST", "CLYNN_AI_HOST"))
    port: int = Field(8088, validation_alias=AliasChoices("PORT", "CLYNN_AI_PORT"))
    public_base_url: str = "http://127.0.0.1:8088"
    upstream_base_url: str = "http://154.64.230.33:3000/v1"
    upstream_api_key: str = ""
    default_model: str = ""
    image_model: str = ""
    asr_model: str = "whisper-1"
    database_url: str = "sqlite:////data/clynnai/clynnai.db"
    storage_dir: Path = Path("/data/clynnai/uploads")
    admin_token: str = "change-me"
    app_token: str = "change-me"
    firecrawl_api_key: str = ""
    firecrawl_base_url: str = "https://api.firecrawl.dev"
    agent_max_steps: int = 6
    agent_use_native_tool_calls: bool = True

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
    )


def provider_config_path() -> Path:
    return Path(os.environ.get("CLYNN_CONFIG_PATH") or "./data/provider-config.json")


def load_provider_config() -> dict[str, Any]:
    path = provider_config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {key: value for key, value in data.items() if key in PROVIDER_CONFIG_FIELDS}


def save_provider_config(updates: dict[str, Any]) -> dict[str, Any]:
    current = load_provider_config()
    for key, value in updates.items():
        if key not in PROVIDER_CONFIG_FIELDS:
            continue
        if value is None:
            continue
        current[key] = value
    path = provider_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    get_settings.cache_clear()
    return current


def redacted_provider_config(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    return {
        "public_base_url": settings.public_base_url,
        "upstream_base_url": settings.upstream_base_url,
        "upstream_api_key_set": bool(settings.upstream_api_key),
        "default_model": settings.default_model,
        "image_model": settings.image_model,
        "asr_model": settings.asr_model,
        "firecrawl_api_key_set": bool(settings.firecrawl_api_key),
        "firecrawl_base_url": settings.firecrawl_base_url,
        "agent_max_steps": settings.agent_max_steps,
        "agent_use_native_tool_calls": settings.agent_use_native_tool_calls,
    }


def _env_value_for_config_key(key: str) -> str | None:
    for env_name in PROVIDER_CONFIG_ENV_ALIASES.get(key, (key.upper(),)):
        if env_name in os.environ:
            return os.environ[env_name]
    return None


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    data = load_provider_config()
    for key, value in data.items():
        if _env_value_for_config_key(key) is not None:
            continue
        setattr(settings, key, value)
    return settings
