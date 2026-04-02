from __future__ import annotations

import os
import tomllib
from pathlib import Path

from xx.types import Config, ReportingConfig


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
    reporting_raw = raw.get("reporting", {})

    reporting = ReportingConfig(
        host=reporting_raw.get("host", "127.0.0.1"),
        port=int(reporting_raw.get("port", 10000)),
        database_path=Path(
            reporting_raw.get("database_path", "~/.local/share/xx/xx.db")
        ).expanduser(),
        retention_days=int(reporting_raw.get("retention_days", 90)),
        default_report_days=int(reporting_raw.get("default_report_days", 90)),
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
        print_only=print_only,
        debug=debug,
        cache_enabled=not no_cache,
        config_path=path,
        reporting=reporting,
    )
