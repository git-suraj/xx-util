from __future__ import annotations

import os
import tomllib
from pathlib import Path

from xx.colors import ColorConfig
from xx.types import ChatConfig, Config, ReportingConfig


class ConfigError(RuntimeError):
    """Raised when config is missing or invalid."""


PROVIDER_ALIASES = {
    "google": "gemini",
}


def default_config_path() -> Path:
    return Path(os.environ.get("XX_CONFIG", "~/.config/xx/config.toml")).expanduser()


def load_config(
    *,
    config_path: Path | None = None,
    provider_override: str | None = None,
    model_override: str | None = None,
    print_only: bool = False,
    debug: bool = False,
    no_cache: bool = False,
    require_provider: bool = True,
) -> Config:
    path = (config_path or default_config_path()).expanduser()
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    with path.open("rb") as handle:
        raw = tomllib.load(handle)

    provider = provider_override or raw.get("provider")
    model = model_override or raw.get("model")
    api_key = raw.get("api_key")
    base_url = raw.get("base_url")
    repair_attempts = int(raw.get("repair_attempts", 3))
    memory_path = Path(raw.get("memory_path", "~/.local/share/xx/repair-memory.json")).expanduser()
    colors_raw = raw.get("colors", {})
    if not isinstance(colors_raw, dict):
        raise ConfigError("Config field [colors] must be a table")
    reporting_raw = raw.get("reporting", {})
    if not isinstance(reporting_raw, dict):
        raise ConfigError("Config field [reporting] must be a table")
    chat_raw = raw.get("chat", {})
    if not isinstance(chat_raw, dict):
        raise ConfigError("Config field [chat] must be a table")

    reporting = ReportingConfig(
        host=reporting_raw.get("host", "127.0.0.1"),
        port=int(reporting_raw.get("port", 10000)),
        database_path=Path(
            reporting_raw.get("database_path", "~/.local/share/xx/xx.db")
        ).expanduser(),
        retention_days=int(reporting_raw.get("retention_days", 90)),
        default_report_days=int(reporting_raw.get("default_report_days", 90)),
    )
    colors = ColorConfig(
        enabled=_read_bool(colors_raw.get("enabled", True)),
        preview=str(colors_raw.get("preview", "green")).strip().lower(),
        output=str(colors_raw.get("output", "yellow")).strip().lower(),
        chat_prompt=str(colors_raw.get("chat_prompt", "cyan")).strip().lower(),
        error=str(colors_raw.get("error", "red")).strip().lower(),
        system=str(colors_raw.get("system", "bright_black")).strip().lower(),
    )
    chat = ChatConfig(
        include_command_output=_read_bool(chat_raw.get("include_command_output", False)),
        max_output_context_chars=max(0, int(chat_raw.get("max_output_context_chars", 12000))),
    )

    normalized = ""
    if require_provider:
        if not provider:
            raise ConfigError("Missing required config field: provider")
        if not model:
            raise ConfigError("Missing required config field: model")

        normalized = provider.strip().lower()
        normalized = PROVIDER_ALIASES.get(normalized, normalized)
        if normalized not in {
            "openai",
            "openai_compatible",
            "anthropic",
            "gemini",
            "mistral",
            "ollama",
        }:
            raise ConfigError(f"Unsupported provider: {provider}")

        if normalized != "ollama" and not api_key:
            raise ConfigError("Missing required config field: api_key")
    elif provider:
        normalized = provider.strip().lower()
        normalized = PROVIDER_ALIASES.get(normalized, normalized)

    return Config(
        provider=normalized,
        model=model or "",
        api_key=api_key,
        base_url=base_url,
        repair_attempts=max(0, repair_attempts),
        memory_path=memory_path,
        colors=colors,
        print_only=print_only,
        debug=debug,
        cache_enabled=not no_cache,
        config_path=path,
        reporting=reporting,
        chat=chat,
    )


def _read_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)
