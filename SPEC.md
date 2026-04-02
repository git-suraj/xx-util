# `xx` Specification

## 1. Overview

`xx` is a CLI utility that converts natural-language intent into a shell command, shows the generated command to the user, asks for confirmation, and only then executes it.

Example:

```bash
$ xx "find me all files in this directory"
>>> ls -l
Execute this command? (Y/n): Y
total 0
```

Core goals:

- Accept plain-English task descriptions.
- Generate shell commands that work on the current machine.
- Support multiple LLM providers behind one interface.
- Be aware of available local commands and common utilities installed on the machine.
- Require explicit confirmation before execution.

Non-goals for v1:

- Full autonomous multi-step task execution.
- Long-running agent workflows.
- Executing commands without user confirmation.
- Managing provider credentials beyond reading a local config file.

## 2. User Experience

### 2.1 Primary command

```bash
xx "<natural language request>"
```

Behavior:

1. Read user intent from CLI args.
2. Detect shell, OS, current working directory, and available commands.
3. Send structured context to the selected LLM.
4. Receive a proposed shell command.
5. Print the command prefixed with `>>>`.
6. Ask for confirmation.
7. Execute only if confirmed.
8. Stream command output to stdout/stderr.
9. Return the executed command's exit code.

### 2.2 Reporting command

```bash
xx report serve
```

Behavior:

1. Start a local HTTP server for reporting.
2. Read execution and token-usage data from the local database.
3. Serve an HTML report plus JSON APIs.
4. Default to the configured reporting window, which is 90 days unless overridden in config.

### 2.3 Confirmation flow

Default prompt:

```text
>>> ls -l
Execute this command? (Y/n):
```

Rules:

- `Enter` means yes.
- `Y` or `y` means yes.
- `N` or `n` means no.
- On no, `xx` exits without executing anything.

### 2.4 Error cases

- If the model cannot produce a safe command, `xx` prints an explanation and exits non-zero.
- If provider credentials are missing, `xx` prints the missing config fields and exits non-zero.
- If the generated command references an unavailable binary, `xx` should either:
  - ask the model for a fallback using installed tools, or
  - refuse execution and explain why.

## 3. Functional Requirements

### 3.1 Input handling

- Accept natural-language input as positional arguments.
- Preserve the full input string exactly as entered after shell parsing.
- If no input is provided, show help text and exit.

### 3.2 Command generation

The system must generate exactly one shell command string per request for v1.
That command string may include pipelines.

The generated command should:

- Prefer installed utilities when helpful, such as `fzf`, `bat`, `fd`, `rg`, `jq`, etc.
- Respect the current shell environment.
- Target the current working directory unless the user specifies otherwise.
- Avoid interactive programs unless the user explicitly asks for one.
- Avoid destructive operations by default.
- Allow pipelines when they are the clearest way to satisfy the request.

### 3.3 Execution

- Show the proposed command before running it.
- Ask for confirmation every time in v1.
- Execute the command in the user's current shell context where practical.
- Stream stdout and stderr live.
- Return the subprocess exit code.

### 3.4 Provider abstraction

The utility must be provider-agnostic.

Supported provider families for v1:

- OpenAI
- Anthropic
- Google Gemini
- Mistral
- Ollama
- Generic self-hosted OpenAI-compatible endpoint

The user chooses the provider via configuration.

Accepted config values for `provider`:

- `openai`
- `openai_compatible`
- `anthropic`
- `gemini`
- `google`
  Alias for `gemini`.
- `mistral`
- `ollama`

The provider layer must normalize:

- model selection
- API key lookup
- base URL if applicable
- request/response handling
- error mapping
- token usage extraction

### 3.5 Local command awareness

`xx` must discover what commands are available on the current machine.

Minimum discovery behavior:

- Inspect `PATH`
- Build a set of executable command names available in the current environment
- Detect current shell and operating system

V1 discovery scope:

- Include only real executables available via `PATH`
- Exclude shell aliases and shell functions

Recommended enrichments:

- Include versions for key tools when cheap to obtain
- Detect common enhanced tools and prefer them when installed
- Cache discovery results with TTL

### 3.6 Safety constraints

Before execution, `xx` must evaluate whether the generated command appears risky.

Risk categories:

- destructive file operations: `rm`, `mv` across paths, `chmod`, `chown`
- system changes: package installs, service restarts
- network operations: `curl | sh`, remote scripts
- privilege escalation: `sudo`
- multi-command chains: `&&`, `;`, `||`, pipes

V1 policy:

- Always require confirmation.
- Display a risk notice for high-risk commands.
- Require confirmation before executing high-risk commands.

### 3.7 Reporting and audit requirements

`xx` must persist execution and token-usage records locally and expose them through a separate reporting command.

Required persisted fields per invocation:

- invocation datetime
- user input
- command generated by the LLM
- whether the command was executed
- provider
- model
- token counts
- risk level
- subprocess exit code if execution occurred
- current working directory

Required reporting capabilities:

- execution history table for a configurable time window
- token totals grouped by model
- token totals grouped by provider

Retention policy:

- reporting data retention must be configurable
- default retention must be 90 days

## 4. Configuration

### 4.1 Provider selection

Primary configuration file:

```text
~/.config/xx/config.toml
```

Required provider configuration fields:

OpenAI:

```toml
provider = "openai"
model = "gpt-4.1-mini"
api_key = "..."
base_url = "..." # optional
```

Anthropic:

```toml
provider = "anthropic"
model = "claude-..."
api_key = "..."
base_url = "..." # optional
```

Gemini:

```toml
provider = "gemini"
model = "gemini-..."
api_key = "..."
```

Google alias for Gemini:

```toml
provider = "google"
model = "gemini-..."
api_key = "..."
```

Mistral:

```toml
provider = "mistral"
model = "mistral-..."
api_key = "..."
```

Ollama:

```toml
provider = "ollama"
model = "llama3.1"
base_url = "http://localhost:11434" # optional if default is assumed
```

OpenAI-compatible self-hosted:

```toml
provider = "openai_compatible"
model = "..."
api_key = "..."
base_url = "http://host:port/v1"
```

### 4.2 Config file structure

Recommended config file:

```text
~/.config/xx/config.toml
```

Config precedence:

1. CLI flags
2. Config file
3. Built-in defaults

Recommended additional config sections:

```toml
[reporting]
host = "127.0.0.1"
port = 10000
database_path = "~/.local/share/xx/xx.db"
retention_days = 90
default_report_days = 90
```

### 4.3 Proposed CLI flags

```bash
xx "list files"
xx --provider ollama --model llama3.1 "find large files"
xx --print-only "show me all git branches"
xx --no-cache "search for duplicate files"
xx report serve
```

Recommended flags for v1:

- `--provider`
- `--model`
- `--print-only`
- `--debug`
- `--no-cache`

## 5. Prompting Contract

The model should receive structured context, not only the raw user sentence.

Minimum prompt payload:

- user request
- OS name
- shell type
- current directory
- available command names
- safety rules
- response format requirements

Example system prompt intent:

- You are generating one shell command for the user's machine.
- Use only commands from the provided installed-command set when possible.
- Prefer concise non-interactive commands.
- Do not explain.
- Return JSON with fields `command`, `reason`, `risk`.

Expected model response:

```json
{
  "command": "ls -l",
  "reason": "Lists files in the current directory with details.",
  "risk": "low"
}
```

The implementation should validate this schema before display or execution.

## 6. Command Discovery

### 6.1 Required behavior

Build a machine-context snapshot:

- `os`: macOS, Linux, etc.
- `shell`: bash, zsh, fish, etc.
- `cwd`
- `path_entries`
- `available_commands`

Possible discovery approaches:

- `compgen -c` for bash
- `whence -pm '*'` or equivalent for zsh
- scanning each directory in `PATH`

Preferred implementation approach:

- scan `PATH` directories directly and collect executable basenames
- de-duplicate names
- avoid shell-specific behavior where possible

### 6.2 Tool ranking

The implementation should optionally maintain a preference map for modern tools:

- prefer `rg` over `grep`
- prefer `fd` over `find` for simple filename search
- prefer `bat` over `cat` when formatted output is useful
- prefer `jq` for JSON filtering

This should be a hint, not a hard rule. The model still decides from available tools.

### 6.3 Caching

Discovery can be cached to avoid rescanning `PATH` on every invocation.

Suggested cache file:

```text
~/.cache/xx/commands.json
```

Suggested invalidation:

- TTL expiry, e.g. 5 minutes
- optional `--no-cache`
- shell/OS change
- `PATH` change hash

## 7. Architecture

### 7.1 Modules

Recommended modules:

- `cmd/xx` or equivalent CLI entrypoint
- `config`
- `providers`
- `prompt`
- `discovery`
- `safety`
- `executor`
- `storage`
- `reporting`
- `types`

### 7.2 Provider interface

Example conceptual interface:

```text
GenerateCommand(ctx, request) -> CommandProposal
```

Where `CommandProposal` includes:

- `command`
- `reason`
- `risk`
- `provider`
- `model`
- `token_usage`

### 7.3 Execution pipeline

1. Parse CLI input.
2. Load config.
3. Discover machine context.
4. Select provider client.
5. Build structured prompt.
6. Request proposal from LLM.
7. Validate response.
8. Run safety analysis.
9. Persist the proposal and token-usage audit record.
10. Print proposal and confirmation prompt.
11. Execute command if approved.
12. Update the audit record with execution outcome.
13. Return exit code.

### 7.4 Reporting pipeline

1. Load reporting config.
2. Open the local database.
3. Apply retention cleanup for expired records.
4. Serve HTML and JSON report endpoints.
5. Query execution history for the selected time window.
6. Query token totals grouped by model.
7. Query token totals grouped by provider.

## 8. Data Structures

Suggested internal types:

```text
Config
- provider
- model
- api_key
- base_url
- print_only
- debug
- cache_enabled
- report_host
- report_port
- report_database_path
- retention_days
- default_report_days

MachineContext
- os
- shell
- cwd
- path_entries
- available_commands[]

CommandProposal
- command
- reason
- risk
- provider
- model
- prompt_tokens
- completion_tokens
- total_tokens

SafetyAssessment
- level
- flags[]
- requires_confirmation

ExecutionRecord
- invoked_at
- user_input
- generated_command
- executed
- provider
- model
- prompt_tokens
- completion_tokens
- total_tokens
- risk_level
- exit_code
- cwd
```

## 9. Safety Model

### 9.1 Baseline checks

Run static checks on the generated string before execution:

- contains forbidden prefixes
- contains shell chaining operators
- contains `sudo`
- contains wildcard deletes
- contains command substitution
- references binaries not in discovered set

Notes for v1:

- pipelines are allowed and should not be treated as invalid by themselves
- chaining operators such as `&&`, `;`, and `||` should remain high-risk

### 9.2 Risk levels

- `low`: read-only listing or search commands
- `medium`: file writes in local project
- `high`: deletes, installs, system changes, network-executed scripts

### 9.3 Confirmation enhancements

For `high` risk commands, recommended prompt:

```text
>>> rm -rf build
Risk: high (destructive file operation)
Execute this command? (y/N):
```

In v1, high-risk commands should still require explicit confirmation.

## 10. Failure Handling

The utility should fail clearly in these cases:

- unknown provider
- missing credentials
- provider API error
- malformed model response
- empty command
- unsafe command rejected by policy
- subprocess spawn failure

Recommended behavior:

- print a short error message to stderr
- include actionable detail in `--debug` mode
- exit non-zero

## 11. Reporting

### 11.1 Storage

Reporting data should be stored in a local SQLite database.

Suggested database path:

```text
~/.local/share/xx/xx.db
```

The database should retain only the configured retention window.
Default retention must be 90 days.

### 11.2 Reporting command

Recommended command:

```bash
xx report serve
```

This command should start a local-only HTTP server by default.

Recommended defaults:

- host: `127.0.0.1`
- port: `10000`

### 11.3 Required endpoints

- `GET /report`
  - serves an HTML report page
- `GET /api/executions?days=90`
  - returns execution rows for the selected time window
- `GET /api/tokens/by-model?days=90`
  - returns token aggregates grouped by model
- `GET /api/tokens/by-provider?days=90`
  - returns token aggregates grouped by provider

### 11.4 Required report content

Execution table:

- execution datetime
- user input
- command returned by the LLM
- whether the command was executed
- provider
- model
- tokens consumed

Token summaries:

- totals grouped by model
- totals grouped by provider

The default report window must come from config and default to 90 days.

## 12. Observability

Recommended v1 debug output behind `--debug`:

- selected provider and model
- command-discovery source and cache usage
- prompt size metrics
- parsed model response
- safety assessment
- token usage values

Do not log secrets.

## 13. Testing

### 13.1 Unit tests

- config resolution precedence
- provider selection
- config file validation
- command discovery
- audit record persistence
- retention cleanup
- token normalization
- provider token aggregation
- prompt construction
- response parsing
- safety classification

### 13.2 Integration tests

- mock each provider adapter
- verify confirmation prompt behavior
- verify command execution only after confirmation
- verify refusal path on malformed or risky responses
- verify report endpoints
- verify configurable retention behavior

### 13.3 Fixture-based tests

Use canned provider responses for prompts such as:

- list files
- search for text
- find large files
- inspect git status
- pretty-print JSON

## 14. Suggested V1 Scope

Recommended v1 deliverables:

- CLI accepting natural-language input
- provider abstraction
- support for OpenAI, Anthropic, Gemini, Mistral, Ollama, and OpenAI-compatible endpoints
- PATH-based local command discovery
- JSON response contract from model
- confirmation before execution
- safety classification and warnings
- `--print-only`, `--provider`, `--model`, `--debug`
- audit logging to SQLite
- `xx report serve`
- token summaries by model and provider
- Python implementation

## 15. Deferred Features

Out of scope for v1 but worth planning for:

- multi-command plans
- shell history awareness
- command explanation mode
- auto-repair on command failure
- interactive selection between multiple candidate commands
- command memory / personalization
- per-project config
- offline model ranking

## 16. Open Questions

1. Should the command run in a shell with aliases/functions loaded, or strictly as a subprocess using discovered binaries?

## 17. Recommended Decisions

If you want the fastest clean v1, I recommend:

- one command only, with pipelines allowed
- require confirmation for high-risk commands
- use a config file as the primary configuration source
- execute through the current shell with `-c`, but validate aggressively first
- show `reason` only in `--debug`
- discover real executables only for v1
- implement in Python first for speed of iteration
