from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class ReportingConfig:
    host: str = "127.0.0.1"
    port: int = 10000
    database_path: Path = Path("~/.local/share/xx/xx.db").expanduser()
    retention_days: int = 90
    default_report_days: int = 90


@dataclass(slots=True)
class Config:
    provider: str
    model: str
    api_key: str | None = None
    base_url: str | None = None
    print_only: bool = False
    debug: bool = False
    cache_enabled: bool = True
    force: bool = False
    config_path: Path | None = None
    reporting: ReportingConfig = field(default_factory=ReportingConfig)


@dataclass(slots=True)
class MachineContext:
    os_name: str
    shell: str
    cwd: Path
    path_entries: list[str]
    path_hash: str
    available_commands: list[str]


@dataclass(slots=True)
class TokenUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(slots=True)
class CommandProposal:
    command: str
    reason: str | None
    risk: str
    provider: str
    model: str
    token_usage: TokenUsage = field(default_factory=TokenUsage)


@dataclass(slots=True)
class SafetyAssessment:
    level: str
    flags: list[str]
    requires_confirmation: bool = True


@dataclass(slots=True)
class ExecutionRecord:
    invoked_at: str
    user_input: str
    generated_command: str
    executed: bool
    approved: bool
    provider: str
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    risk_level: str
    exit_code: int | None
    cwd: str
