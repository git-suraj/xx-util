from __future__ import annotations

import subprocess


def execute_command(command: str, shell: str) -> int:
    completed = subprocess.run([shell, "-c", command], check=False)
    return int(completed.returncode)
