from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


STOPWORDS = {
    "a",
    "an",
    "and",
    "any",
    "for",
    "following",
    "given",
    "i",
    "it",
    "me",
    "of",
    "please",
    "print",
    "show",
    "that",
    "the",
    "this",
    "those",
    "to",
    "these",
}
MIN_SHARED_TOKENS = 2
MIN_SIMILARITY_SCORE = 0.5
SEMANTIC_MEMORY_BACKEND = "json-file token-similarity"


def lookup_repaired_command(memory_path: Path, *, user_request: str) -> dict[str, Any] | None:
    payload = _load_memory(memory_path)
    normalized_request = _normalize_request(user_request)
    request_tokens = _semantic_tokens(user_request)
    best_entry: dict[str, Any] | None = None
    best_score = 0.0
    for entry in payload.get("entries", []):
        if not isinstance(entry, dict):
            continue
        command = entry.get("successful_command")
        if not isinstance(command, str) or not command.strip():
            continue
        if entry.get("request_key") == normalized_request:
            return {
                "request_key": entry.get("request_key"),
                "original_request": entry.get("original_request"),
                "failed_command": entry.get("failed_command"),
                "successful_command": command.strip(),
                "similarity_score": 1.0,
            }
        score = _semantic_similarity(
            request_tokens,
            _semantic_tokens(str(entry.get("original_request") or entry.get("request_key") or "")),
        )
        if score > best_score:
            best_score = score
            best_entry = entry
    if best_score >= MIN_SIMILARITY_SCORE:
        return {
            "request_key": best_entry.get("request_key"),
            "original_request": best_entry.get("original_request"),
            "failed_command": best_entry.get("failed_command"),
            "successful_command": str(best_entry.get("successful_command", "")).strip(),
            "similarity_score": best_score,
        }
    return None


def remember_successful_repair(
    memory_path: Path,
    *,
    user_request: str,
    failed_command: str,
    successful_command: str,
) -> None:
    payload = _load_memory(memory_path)
    entries = payload.setdefault("entries", [])
    if not isinstance(entries, list):
        entries = []
        payload["entries"] = entries

    normalized_request = _normalize_request(user_request)
    replacement = {
        "request_key": normalized_request,
        "original_request": user_request,
        "failed_command": failed_command,
        "successful_command": successful_command,
    }

    updated = False
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        if entry.get("request_key") == normalized_request:
            entries[index] = replacement
            updated = True
            break
    if not updated:
        entries.append(replacement)

    _write_memory(memory_path, payload)


def describe_memory(memory_path: Path) -> dict[str, Any]:
    payload = _load_memory(memory_path)
    entries = payload.get("entries", [])
    valid_entries = 0
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            command = entry.get("successful_command")
            if isinstance(command, str) and command.strip():
                valid_entries += 1
    return {
        "backend": SEMANTIC_MEMORY_BACKEND,
        "path": str(memory_path),
        "entries": valid_entries,
    }


def _normalize_request(user_request: str) -> str:
    return " ".join(user_request.strip().lower().split())


def _semantic_tokens(user_request: str) -> set[str]:
    tokens: set[str] = set()
    for token in re.findall(r"[a-z0-9_+-]+", _normalize_request(user_request)):
        normalized = _normalize_token(token)
        if normalized and normalized not in STOPWORDS:
            tokens.add(normalized)
    return tokens


def _normalize_token(token: str) -> str:
    aliases = {
        "formatted": "pretty",
        "formatting": "pretty",
        "prettify": "pretty",
        "pretty-print": "pretty",
        "prettyprint": "pretty",
        "prettyprinting": "pretty",
        "prints": "pretty",
    }
    if token in aliases:
        return aliases[token]
    if token.endswith("ing") and len(token) > 5:
        token = token[:-3]
    if token.endswith("ed") and len(token) > 4:
        token = token[:-2]
    if token.endswith("s") and len(token) > 4:
        token = token[:-1]
    return aliases.get(token, token)


def _semantic_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    shared = left & right
    if len(shared) < MIN_SHARED_TOKENS:
        return 0.0
    return (2 * len(shared)) / (len(left) + len(right))


def _load_memory(memory_path: Path) -> dict[str, Any]:
    try:
        with memory_path.open() as handle:
            payload = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"entries": []}
    if not isinstance(payload, dict):
        return {"entries": []}
    return payload


def _write_memory(memory_path: Path, payload: dict[str, Any]) -> None:
    try:
        memory_path.parent.mkdir(parents=True, exist_ok=True)
        with memory_path.open("w") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
    except OSError:
        return
