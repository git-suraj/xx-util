from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading

from xx.colors import ColorConfig, colorize, should_use_color
from xx.types import CommandExecutionResult


class ExecutionError(RuntimeError):
    """Raised when command execution cannot be started."""


def execute_command(
    command: str,
    shell: str,
    colors: ColorConfig | None = None,
) -> CommandExecutionResult:
    resolved_shell = _resolve_shell(shell)
    try:
        completed = _run_command(command, resolved_shell, colors)
    except OSError as exc:
        fallback_shell = "/bin/sh"
        if resolved_shell != fallback_shell:
            try:
                completed = _run_command(command, fallback_shell, colors)
            except OSError as fallback_exc:
                raise ExecutionError(
                    f"Unable to execute command via {resolved_shell} or {fallback_shell}: {fallback_exc}"
                ) from fallback_exc
        else:
            raise ExecutionError(f"Unable to execute command via {resolved_shell}: {exc}") from exc
    return completed


def _resolve_shell(shell: str) -> str:
    candidate = (shell or "").strip()
    if not candidate:
        return "/bin/sh"
    if os.path.isabs(candidate) and os.access(candidate, os.X_OK):
        return candidate
    resolved = shutil.which(candidate)
    if resolved:
        return resolved
    basename = os.path.basename(candidate)
    resolved = shutil.which(basename)
    if resolved:
        return resolved
    return "/bin/sh"


def _run_command(
    command: str,
    shell: str,
    colors: ColorConfig | None = None,
) -> CommandExecutionResult:
    wrapped_command = _wrap_with_pipefail(command, shell)
    colors = colors or ColorConfig()
    use_color = should_use_color(colors.enabled)
    process = subprocess.Popen(
        wrapped_command,
        shell=True,
        executable=shell,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    def reader(stream, sink, collector) -> None:
        try:
            for chunk in iter(stream.readline, ""):
                if sink is sys.stdout and use_color:
                    sink.write(colorize(chunk, colors.output, enabled=colors.enabled))
                else:
                    sink.write(chunk)
                sink.flush()
                collector.append(chunk)
        finally:
            stream.close()

    stdout_thread = threading.Thread(
        target=reader, args=(process.stdout, sys.stdout, stdout_chunks), daemon=True
    )
    stderr_thread = threading.Thread(
        target=reader, args=(process.stderr, sys.stderr, stderr_chunks), daemon=True
    )
    stdout_thread.start()
    stderr_thread.start()
    exit_code = process.wait()
    stdout_thread.join()
    stderr_thread.join()
    return CommandExecutionResult(
        exit_code=int(exit_code),
        stdout="".join(stdout_chunks),
        stderr="".join(stderr_chunks),
    )


def _wrap_with_pipefail(command: str, shell: str) -> str:
    if "|" not in command:
        return command

    shell_name = os.path.basename(shell or "")
    if shell_name in {"bash", "zsh"}:
        return f"set -o pipefail; {command}"
    return command
