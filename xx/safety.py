from __future__ import annotations

import re
import shlex

from xx.types import MachineContext, SafetyAssessment


HIGH_RISK_PATTERNS = [
    (re.compile(r"(^|\s)sudo(\s|$)"), "uses sudo"),
    (re.compile(r"(^|\s)rm\s+-[^\n]*r"), "recursive delete"),
    (re.compile(r"(^|\s)(chmod|chown)(\s|$)"), "permission or ownership change"),
    (re.compile(r"curl\s+.*\|\s*(sh|bash|zsh)"), "remote script execution"),
    (re.compile(r"(^|\s)(brew|apt|apt-get|yum|dnf|pacman|pip|pip3|npm)\s+(install|remove|uninstall)\b"), "package manager mutation"),
    (re.compile(r"(&&|;|\|\|)"), "command chaining"),
    (re.compile(r"\$\("), "command substitution"),
]


def assess_command(command: str, machine: MachineContext) -> SafetyAssessment:
    flags: list[str] = []
    level = "low"

    if "|" in command:
        flags.append("uses pipeline")

    for pattern, label in HIGH_RISK_PATTERNS:
        if pattern.search(command):
            flags.append(label)
            level = "high"

    if level != "high":
        if re.search(r"(^|\s)(mv|cp|tee)\b", command):
            flags.append("writes or copies files")
            level = "medium"

    unavailable = _find_unavailable_commands(command, machine)
    if unavailable:
        flags.append(f"references unavailable commands: {', '.join(unavailable)}")
        level = "high"

    return SafetyAssessment(level=level, flags=flags, requires_confirmation=True)


def _find_unavailable_commands(command: str, machine: MachineContext) -> list[str]:
    installed = set(machine.available_commands)
    missing: list[str] = []
    for segment in command.split("|"):
        try:
            parts = shlex.split(segment.strip())
        except ValueError:
            return ["unparseable command"]
        if not parts:
            continue
        executable = parts[0]
        if executable in {"cd", "echo", "test", "[", "true", "false", "export"}:
            continue
        if executable not in installed:
            missing.append(executable)
    return missing
