from __future__ import annotations

import os
import shutil
import subprocess


class ExecutionError(RuntimeError):
    """Raised when command execution cannot be started."""


def execute_command(command: str, shell: str) -> int:
    resolved_shell = _resolve_shell(shell)
    try:
        completed = subprocess.run(command, shell=True, executable=resolved_shell, check=False)
    except OSError as exc:
        fallback_shell = "/bin/sh"
        if resolved_shell != fallback_shell:
            try:
                completed = subprocess.run(command, shell=True, executable=fallback_shell, check=False)
            except OSError as fallback_exc:
                raise ExecutionError(
                    f"Unable to execute command via {resolved_shell} or {fallback_shell}: {fallback_exc}"
                ) from fallback_exc
        else:
            raise ExecutionError(f"Unable to execute command via {resolved_shell}: {exc}") from exc
    return int(completed.returncode)


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
