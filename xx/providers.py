from __future__ import annotations

import json
import urllib.error
import urllib.request

from xx.prompt import build_prompt, build_repair_prompt
from xx.types import CommandProposal, Config, MachineContext, TokenUsage


class ProviderError(RuntimeError):
    """Raised when provider interaction fails."""


def generate_command(
    config: Config,
    machine: MachineContext,
    user_request: str,
    prior_successful_command: dict | None = None,
) -> CommandProposal:
    prompt_text = build_prompt(user_request, machine, prior_successful_command)
    return _generate_from_prompt(config, prompt_text)


def generate_repaired_command(
    config: Config,
    machine: MachineContext,
    user_request: str,
    failed_command: str,
    exit_code: int,
    stdout: str,
    stderr: str,
    prior_successful_command: dict | None = None,
) -> CommandProposal:
    prompt_text = build_repair_prompt(
        user_request,
        machine,
        failed_command,
        exit_code,
        stdout,
        stderr,
        prior_successful_command,
    )
    return _generate_from_prompt(config, prompt_text)


def _generate_from_prompt(config: Config, prompt_text: str) -> CommandProposal:
    provider = config.provider
    if provider in {"openai", "openai_compatible", "mistral"}:
        return _openai_like_generate(config, prompt_text)
    if provider == "anthropic":
        return _anthropic_generate(config, prompt_text)
    if provider == "gemini":
        return _gemini_generate(config, prompt_text)
    if provider == "ollama":
        return _ollama_generate(config, prompt_text)
    raise ProviderError(f"Unsupported provider: {provider}")


def _openai_like_generate(config: Config, prompt_text: str) -> CommandProposal:
    base_url = config.base_url or _default_base_url(config.provider)
    payload = {
        "model": config.model,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "You generate exactly one shell command in JSON."},
            {"role": "user", "content": prompt_text},
        ],
    }
    body = _post_json(
        f"{base_url}/chat/completions",
        payload,
        headers={"Authorization": f"Bearer {config.api_key}"},
    )
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError("Malformed OpenAI-compatible response") from exc
    usage = body.get("usage", {})
    return _proposal_from_content(
        content,
        provider=config.provider,
        model=config.model,
        token_usage=TokenUsage(
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
        ),
    )


def _anthropic_generate(config: Config, prompt_text: str) -> CommandProposal:
    base_url = config.base_url or "https://api.anthropic.com/v1"
    payload = {
        "model": config.model,
        "max_tokens": 512,
        "system": "Return JSON only with command, reason, risk.",
        "messages": [{"role": "user", "content": prompt_text}],
    }
    body = _post_json(
        f"{base_url}/messages",
        payload,
        headers={
            "x-api-key": config.api_key or "",
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        content = body["content"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError("Malformed Anthropic response") from exc
    usage = body.get("usage", {})
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    total_tokens = None
    if input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    return _proposal_from_content(
        content,
        provider=config.provider,
        model=config.model,
        token_usage=TokenUsage(
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            total_tokens=total_tokens,
        ),
    )


def _gemini_generate(config: Config, prompt_text: str) -> CommandProposal:
    base_url = config.base_url or "https://generativelanguage.googleapis.com/v1beta/models"
    payload = {
        "contents": [{"parts": [{"text": prompt_text}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    body = _post_json(
        f"{base_url}/{config.model}:generateContent?key={config.api_key}",
        payload,
        headers={},
    )
    try:
        content = body["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError("Malformed Gemini response") from exc
    usage = body.get("usageMetadata", {})
    return _proposal_from_content(
        content,
        provider=config.provider,
        model=config.model,
        token_usage=TokenUsage(
            prompt_tokens=usage.get("promptTokenCount"),
            completion_tokens=usage.get("candidatesTokenCount"),
            total_tokens=usage.get("totalTokenCount"),
        ),
    )


def _ollama_generate(config: Config, prompt_text: str) -> CommandProposal:
    base_url = config.base_url or "http://localhost:11434"
    payload = {
        "model": config.model,
        "prompt": prompt_text,
        "stream": False,
        "format": "json",
    }
    body = _post_json(f"{base_url}/api/generate", payload, headers={})
    try:
        content = body["response"]
    except KeyError as exc:
        raise ProviderError("Malformed Ollama response") from exc
    prompt_tokens = body.get("prompt_eval_count")
    completion_tokens = body.get("eval_count")
    total_tokens = None
    if prompt_tokens is not None and completion_tokens is not None:
        total_tokens = prompt_tokens + completion_tokens
    return _proposal_from_content(
        content,
        provider=config.provider,
        model=config.model,
        token_usage=TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        ),
    )


def _post_json(url: str, payload: dict, headers: dict[str, str]) -> dict:
    data = json.dumps(payload).encode()
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise ProviderError(f"Provider HTTP error {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise ProviderError(f"Provider connection error: {exc.reason}") from exc


def _proposal_from_content(
    content: str, *, provider: str, model: str, token_usage: TokenUsage
) -> CommandProposal:
    parsed = _extract_json_object(content)
    command = str(parsed.get("command", "")).strip()
    reason = parsed.get("reason")
    risk = str(parsed.get("risk", "medium")).strip().lower()
    if risk not in {"low", "medium", "high"}:
        risk = "medium"
    if not command:
        raise ProviderError("Provider returned an empty command")
    return CommandProposal(
        command=command,
        reason=str(reason) if reason is not None else None,
        risk=risk,
        provider=provider,
        model=model,
        token_usage=token_usage,
    )


def _extract_json_object(content: str) -> dict:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if "\n" in stripped:
            stripped = stripped.split("\n", 1)[1]
        if stripped.endswith("```"):
            stripped = stripped[:-3]
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ProviderError("Provider did not return valid JSON")
        return json.loads(stripped[start : end + 1])


def _default_base_url(provider: str) -> str:
    if provider == "mistral":
        return "https://api.mistral.ai/v1"
    return "https://api.openai.com/v1"
