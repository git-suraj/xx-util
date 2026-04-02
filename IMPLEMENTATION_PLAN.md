# `xx` Implementation Plan

## Locked Decisions

- v1 allows pipelines.
- risky commands require confirmation before execution.
- command discovery includes only real executables on `PATH`.
- implementation language is Python.
- configuration uses a persisted config file only.
- the model-provided `reason` is shown only under `--debug`.
- reporting is served by a separate command.
- data retention is configurable and defaults to 90 days.

## Goal

Build `xx` as a Python CLI that translates natural language into a shell command, prints the proposed command, asks for confirmation, and executes it only after approval.
It also exposes a reporting endpoint that serves 90 days of execution and token-usage data.

## Delivery Strategy

Implement `xx` in small vertical slices so the CLI becomes usable early and provider integrations remain isolated behind one interface.
Reporting should be built as a first-class subsystem because token metrics and execution audit data must be captured at command time.

## Phase 1. Project Foundation

- Create a Python package layout with modules for `cli`, `config`, `providers`, `discovery`, `prompt`, `safety`, `executor`, `reporting`, `storage`, and `types`.
- Add a `pyproject.toml` with a console script entrypoint for `xx`.
- Use `argparse` for the first version unless a richer CLI framework is needed later.
- Define the core internal models:
  - `Config`
  - `MachineContext`
  - `CommandProposal`
  - `SafetyAssessment`
  - `ExecutionRecord`
  - `TokenUsageRecord`

## Phase 2. CLI and Configuration

- Implement `xx "<request>"` argument parsing.
- Add v1 flags:
  - `--provider`
  - `--model`
  - `--print-only`
  - `--debug`
  - `--no-cache`
- Support a persisted config file only for provider and model configuration.
- Recommended config file path:
  - `~/.config/xx/config.toml`
- Accept these provider values in config:
  - `openai`
  - `openai_compatible`
  - `anthropic`
  - `gemini`
  - `google`
    Normalize to `gemini`.
  - `mistral`
  - `ollama`
- Config precedence:
  1. CLI flags
  2. config file
  3. built-in defaults
- Fail fast when required provider credentials or model settings are missing from the config file.
- Add reporting configuration:
  - endpoint host
  - endpoint port
  - database path
  - retention window
  - default report window

## Phase 3. Machine Discovery

- Implement PATH scanning to collect real executables only.
- Capture:
  - OS
  - shell
  - current working directory
  - PATH entries
  - PATH hash
  - available commands
- Add cache support for discovered commands.
- Recommended cache path:
  - `~/.cache/xx/commands.json`
- Invalidate cache on:
  - TTL expiry
  - PATH hash change
  - `--no-cache`

## Phase 4. Persistence and Audit Logging

- Add a lightweight local database for audit and reporting data.
- Recommended storage:
  - SQLite
- Recommended database path:
  - `~/.local/share/xx/xx.db`
- Persist one record for every `xx` invocation after a provider response is received.
- Capture:
  - invocation timestamp
  - user input
  - generated command
  - provider
  - model
  - token counts
  - risk level
  - whether execution was approved
  - whether execution actually ran
  - subprocess exit code if run
  - current working directory
- Add retention cleanup that deletes data older than 90 days.
- Make retention configurable via config file.
- Default retention window:
  - 90 days

## Phase 5. Prompt Contract

- Build one normalized prompt constructor shared by every provider adapter.
- Include:
  - user request
  - current working directory
  - shell
  - OS
  - discovered commands
  - safety rules
  - strict response schema
- Require structured JSON output from the model.

Expected normalized response:

```json
{
  "command": "ls -l",
  "reason": "Lists files with details.",
  "risk": "low"
}
```

Rules:

- `command` is mandatory.
- `reason` is optional for display and shown only in `--debug`.
- `risk` must normalize to `low`, `medium`, or `high`.

## Phase 6. Provider Abstraction

- Define one provider interface:

```text
generate_command(machine_context, request, config) -> CommandProposal
```

- Implement adapters in this order:
  1. OpenAI-compatible base adapter
  2. OpenAI
  3. Ollama
  4. Anthropic
  5. Gemini
  6. Mistral

- Normalize:
  - model selection
  - API key lookup
  - base URL handling
  - request construction
  - response parsing
  - provider error mapping
  - token usage extraction

Token usage normalization should produce a common shape:

```text
prompt_tokens
completion_tokens
total_tokens
```

If a provider does not return token usage directly, the adapter should either compute it or mark the value unavailable in a consistent way.

## Phase 7. Response Validation

- Reject malformed JSON.
- Reject empty commands.
- Normalize unknown risk labels if possible, otherwise fail.
- Verify referenced binaries against discovered commands where feasible.
- Allow pipelines.
- Treat `&&`, `||`, and `;` as high-risk operators.

## Phase 8. Safety Layer

- Build static checks for:
  - `sudo`
  - `rm`, `chmod`, `chown`
  - package install commands
  - remote script execution
  - command substitution
  - shell chaining
- Policy:
  - always require confirmation
  - if risk is high, still require confirmation
- Pipelines alone are allowed and should not be treated as invalid.

## Phase 9. Execution Flow

- Print the generated command as:

```text
>>> <command>
```

- Ask for confirmation before execution.
- Execute through the current shell using `shell -c`.
- Stream stdout and stderr live.
- Return the subprocess exit code.
- Write the final execution outcome back to the audit record.

## Phase 10. Reporting Endpoint

- Expose a local HTTP endpoint for reports through a separate command.
- Recommended command shape:
  - `xx report serve`
- Recommended implementation:
  - FastAPI or Flask
- Recommended default bind:
  - `127.0.0.1`
- Recommended default port:
  - `10000`

Required report views:

1. Execution report table for the last 90 days with:
  - execution datetime
  - user input
  - command returned by the LLM
  - whether the command was executed
  - provider/model called
  - tokens consumed
2. Token-consumption summary grouped by model for the selected time window.
3. Token-consumption summary grouped by provider for the selected time window.

Recommended endpoints:

- `GET /report`
  - returns an HTML report page
- `GET /api/executions?days=90`
  - returns JSON rows for the execution table
- `GET /api/tokens/by-model?days=90`
  - returns JSON aggregates grouped by model
- `GET /api/tokens/by-provider?days=90`
  - returns JSON aggregates grouped by provider

Recommended UI behavior:

- default to the configured report window, which defaults to 90 days
- sortable execution table
- readable datetime formatting
- token totals visible at a glance
- simple filtering by provider or model if cheap to add

## Phase 11. UX and Debugging

- Implement `--print-only` to skip confirmation and execution.
- Implement `--debug` to show:
  - selected provider and model
  - cache hit or miss
  - parsed proposal
  - safety assessment
  - provider `reason`
- Keep normal mode terse and focused on the command and confirmation prompt.

## Phase 12. Testing

### Unit tests

- config precedence
- provider selection
- config file loading
- command discovery
- cache behavior
- audit record persistence
- retention cleanup
- token normalization
- provider-level token aggregation
- prompt construction
- response parsing
- safety classification

### Integration tests

- confirmation flow
- `--print-only`
- refusal path for risky commands
- refusal path for malformed provider responses
- mocked provider adapters
- reporting endpoint JSON responses
- reporting endpoint HTML rendering
- 90-day retention behavior
- configurable retention behavior
- provider token summary behavior

### Fixture tests

- list files
- search for text
- find large files
- inspect git status
- pretty-print JSON

## Phase 13. Packaging and Docs

- Finalize `pyproject.toml`
- expose `xx` as a console script
- document config file format
- document reporting endpoint usage
- document database location and retention policy
- document the separate reporting command
- add usage examples and safety behavior to `README.md`

## Recommended Implementation Order

1. Scaffold package and CLI entrypoint.
2. Implement config loading from the config file.
3. Implement SQLite persistence and audit schema.
4. Implement PATH discovery and caching.
5. Add prompt builder and response schema validation.
6. Implement OpenAI-compatible provider first.
7. Capture and normalize token usage.
8. Add safety checks and confirmation flow.
9. Add command execution and output streaming.
10. Add the reporting endpoint and report page.
11. Add `--print-only` and `--debug`.
12. Add remaining providers.
13. Add tests and packaging polish.

## Initial Milestone Breakdown

### Milestone 1

- package scaffold
- CLI argument parsing
- config resolution
- SQLite schema
- machine discovery

### Milestone 2

- prompt builder
- OpenAI-compatible adapter
- token extraction
- response validation

### Milestone 3

- safety engine
- confirmation flow
- execution engine
- audit logging

### Milestone 4

- reporting endpoint
- 90-day retention
- additional providers
- cache and debug support
- tests
- docs
