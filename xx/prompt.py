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
