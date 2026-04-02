from __future__ import annotations

import json

from xx.types import MachineContext


def build_prompt(user_request: str, machine: MachineContext) -> str:
    payload = {
        "request": user_request,
        "os": machine.os_name,
        "shell": machine.shell,
        "cwd": str(machine.cwd),
        "available_commands": machine.available_commands,
        "rules": [
            "Return exactly one shell command.",
            "Pipelines are allowed when useful.",
            "Prefer installed commands from the available_commands list.",
            "Prefer modern installed utilities when they fit: rg over grep, fd over find, bat over cat, jq for JSON, fzf for interactive preview.",
            "If a modern utility is installed and clearly better for the request, use it instead of a more primitive fallback.",
            "Avoid interactive commands unless explicitly requested.",
            "Avoid destructive commands unless the user clearly asked for them.",
            "Return JSON only with keys command, reason, risk.",
            "risk must be one of low, medium, high.",
        ],
        "response_schema": {
            "command": "string",
            "reason": "string",
            "risk": "low|medium|high",
        },
    }
    return json.dumps(payload, indent=2)


def build_repair_prompt(
    user_request: str,
    machine: MachineContext,
    failed_command: str,
    exit_code: int,
    stdout: str,
    stderr: str,
) -> str:
    payload = {
        "request": user_request,
        "os": machine.os_name,
        "shell": machine.shell,
        "cwd": str(machine.cwd),
        "available_commands": machine.available_commands,
        "failed_command": failed_command,
        "exit_code": exit_code,
        "stdout_tail": stdout[-4000:],
        "stderr_tail": stderr[-4000:],
        "rules": [
            "The previous command failed.",
            "Return one amended shell command only.",
            "Keep the command as close as possible to the original intent.",
            "Prefer installed commands from the available_commands list.",
            "Use null-safe path handling when piping file paths between tools.",
            "Pipelines are allowed when useful.",
            "Return JSON only with keys command, reason, risk.",
            "risk must be one of low, medium, high.",
        ],
        "response_schema": {
            "command": "string",
            "reason": "string",
            "risk": "low|medium|high",
        },
    }
    return json.dumps(payload, indent=2)
