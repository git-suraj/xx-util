from __future__ import annotations

import hashlib
import json
import os
import platform
import time
from pathlib import Path

from xx.types import MachineContext


CACHE_PATH = Path("~/.cache/xx/commands.json").expanduser()
CACHE_TTL_SECONDS = 300


def discover_machine_context(*, cache_enabled: bool = True) -> MachineContext:
    path_entries = os.environ.get("PATH", "").split(os.pathsep)
    path_hash = hashlib.sha256(os.environ.get("PATH", "").encode()).hexdigest()
    shell = os.environ.get("SHELL", "/bin/sh")
    cwd = Path.cwd()

    commands: list[str]
    if cache_enabled:
        cached = _read_cache(path_hash)
        if cached is not None:
            commands = cached
        else:
            commands = _scan_path(path_entries)
            _write_cache(path_hash, commands)
    else:
        commands = _scan_path(path_entries)

    return MachineContext(
        os_name=platform.system(),
        shell=shell,
        cwd=cwd,
        path_entries=path_entries,
        path_hash=path_hash,
        available_commands=commands,
    )


def _scan_path(path_entries: list[str]) -> list[str]:
    found: set[str] = set()
    for entry in path_entries:
        if not entry:
            continue
        path = Path(entry)
        if not path.exists() or not path.is_dir():
            continue
        try:
            for child in path.iterdir():
                try:
                    if child.is_file() and os.access(child, os.X_OK):
                        found.add(child.name)
                except OSError:
                    continue
        except OSError:
            continue
    return sorted(found)


def _read_cache(path_hash: str) -> list[str] | None:
    try:
        with CACHE_PATH.open() as handle:
            payload = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None

    if payload.get("path_hash") != path_hash:
        return None
    if time.time() - float(payload.get("created_at", 0)) > CACHE_TTL_SECONDS:
        return None
    commands = payload.get("commands")
    if not isinstance(commands, list):
        return None
    return [str(item) for item in commands]


def _write_cache(path_hash: str, commands: list[str]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "path_hash": path_hash,
        "created_at": time.time(),
        "commands": commands,
    }
    with CACHE_PATH.open("w") as handle:
        json.dump(payload, handle)
