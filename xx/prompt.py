from __future__ import annotations

import json

from xx.types import ChatTurn, MachineContext


def _shared_rules() -> list[str]:
    return [
        "Prefer installed commands from the available_commands list.",
        "Prefer modern installed utilities when they fit: rg over grep, fd over find, bat over cat, jq for JSON, fzf for interactive preview.",
        "If a modern utility is installed and clearly better for the request, use it instead of a more primitive fallback.",
        "Avoid interactive commands unless explicitly requested.",
        "Avoid destructive commands unless the user clearly asked for them.",
        "If you use python -c, the code must be valid as a single command-line argument.",
        "Do not place compound Python statements such as def, class, if, for, while, try, or with after a semicolon in python -c code.",
        "For non-trivial Python, prefer expressions, comprehensions, lambdas, or a shell-safe multi-line form instead of inline compound statements.",
        "Return JSON only with keys command, reason, risk.",
        "risk must be one of low, medium, high.",
    ]


def build_prompt(
    user_request: str,
    machine: MachineContext,
    prior_successful_command: dict | None = None,
) -> str:
    payload = {
        "request": user_request,
        "os": machine.os_name,
        "shell": machine.shell,
        "cwd": str(machine.cwd),
        "available_commands": machine.available_commands,
        "prior_successful_command": prior_successful_command,
        "rules": [
            "Return exactly one shell command.",
            "Pipelines are allowed when useful.",
            "If prior_successful_command is present, reuse it when it still satisfies the request, otherwise adapt it minimally to satisfy the new request.",
            *_shared_rules(),
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
    prior_successful_command: dict | None = None,
) -> str:
    payload = {
        "request": user_request,
        "os": machine.os_name,
        "shell": machine.shell,
        "cwd": str(machine.cwd),
        "available_commands": machine.available_commands,
        "prior_successful_command": prior_successful_command,
        "failed_command": failed_command,
        "exit_code": exit_code,
        "stdout_tail": stdout[-4000:],
        "stderr_tail": stderr[-4000:],
        "rules": [
            "The previous command failed.",
            "Return one amended shell command only.",
            "Keep the command as close as possible to the original intent.",
            "If prior_successful_command is present, prefer adapting that known-good command over inventing a different approach when it still fits the request.",
            "Use null-safe path handling when piping file paths between tools.",
            "Pipelines are allowed when useful.",
            "Use the stderr and exit_code to avoid repeating the same syntax or quoting mistake.",
            *_shared_rules(),
        ],
        "response_schema": {
            "command": "string",
            "reason": "string",
            "risk": "low|medium|high",
        },
    }
    return json.dumps(payload, indent=2)


def build_chat_prompt(
    user_message: str,
    machine: MachineContext,
    turns: list[ChatTurn],
    *,
    include_command_output: bool,
    max_output_context_chars: int,
) -> str:
    payload = {
        "mode": "chat",
        "current_user_message": user_message,
        "os": machine.os_name,
        "shell": machine.shell,
        "cwd": str(machine.cwd),
        "available_commands": machine.available_commands,
        "previous_turns": [
            _chat_turn_payload(
                turn,
                include_command_output=include_command_output,
                max_output_context_chars=max_output_context_chars,
            )
            for turn in turns
        ],
        "rules": [
            "Return exactly one shell command for the current user message.",
            "Use previous turns as conversational context for refinement.",
            "If command output is not included, do not claim to have read exact previous output values.",
            "When the user asks to refine a previous result, generate a command that reproduces the refined result from the filesystem or environment.",
            "Pipelines are allowed when useful.",
            *_shared_rules(),
        ],
        "response_schema": {
            "command": "string",
            "reason": "string",
            "risk": "low|medium|high",
        },
    }
    return json.dumps(payload, indent=2)


def _chat_turn_payload(
    turn: ChatTurn,
    *,
    include_command_output: bool,
    max_output_context_chars: int,
) -> dict:
    payload = {
        "user_message": turn.user_message,
        "command": turn.command,
        "approved": turn.approved,
        "executed": turn.executed,
        "risk_level": turn.risk_level,
        "exit_code": turn.exit_code,
        "stdout_included": False,
        "stderr_included": False,
    }
    if include_command_output:
        payload["stdout_tail"] = _tail(turn.stdout, max_output_context_chars)
        payload["stderr_tail"] = _tail(turn.stderr, max_output_context_chars)
        payload["stdout_included"] = bool(payload["stdout_tail"])
        payload["stderr_included"] = bool(payload["stderr_tail"])
    return payload


def _tail(value: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    return value[-max_chars:]
