# xx

`xx` turns a natural-language request into a shell command, shows the generated command, asks for confirmation, and only then executes it. It also stores an audit trail in SQLite and exposes a local reporting UI.

## Features

- provider-agnostic command generation for:
  - OpenAI
  - OpenAI-compatible endpoints
  - Anthropic
  - Gemini
  - Mistral
  - Ollama
- real executable discovery from `PATH`
- pipeline support
- high-risk command confirmation
- confirmation before execution
- SQLite-backed audit log
- reporting server with:
  - execution history
  - tokens by model
  - tokens by provider

## Prerequisites

- Python 3.11 or newer
- `pipx` installed if you want the recommended global CLI install
- a valid provider account or local Ollama setup
- a config file at `~/.config/xx/config.toml`

## Installation

Recommended for local CLI usage:

```bash
pipx install .
```

Development install from the repo root:

```bash
python3 -m pip install -e .
```

Or run directly during development without installing:

```bash
python3 -m xx --help
```

## Configuration

`xx` uses a config file only.

Default config path:

```text
~/.config/xx/config.toml
```

Example:

```toml
provider = "openai"
model = "gpt-4.1-mini"
api_key = "your-api-key"
repair_attempts = 3
# base_url = "https://api.openai.com/v1"

[reporting]
host = "127.0.0.1"
port = 10000
database_path = "~/.local/share/xx/xx.db"
retention_days = 90
default_report_days = 90
```

Accepted `provider` values:

- `openai`
- `openai_compatible`
- `anthropic`
- `gemini`
- `google`
  Maps internally to `gemini`.
- `mistral`
- `ollama`

Expected top-level config fields:

- `provider`
- `model`
- `api_key`
  Required for every provider except `ollama`.
- `base_url`
  Optional for `openai`, `openai_compatible`, `anthropic`, `mistral`, and `ollama`.
- `repair_attempts`
  Optional. Number of auto-repair attempts after a failed command. Defaults to `3`.

Provider-specific examples:

OpenAI:

```toml
provider = "openai"
model = "gpt-4.1-mini"
api_key = "your-openai-api-key"
# base_url = "https://api.openai.com/v1"
```

OpenAI-compatible:

```toml
provider = "openai_compatible"
model = "your-model-name"
api_key = "your-api-key"
base_url = "http://host:port/v1"
```

Anthropic:

```toml
provider = "anthropic"
model = "claude-3-5-sonnet-latest"
api_key = "your-anthropic-api-key"
# base_url = "https://api.anthropic.com/v1"
```

Gemini:

```toml
provider = "gemini"
model = "gemini-2.5-flash"
api_key = "your-gemini-api-key"
```

Google alias for Gemini:

```toml
provider = "google"
model = "gemini-2.5-flash"
api_key = "your-gemini-api-key"
```

Mistral:

```toml
provider = "mistral"
model = "mistral-small-latest"
api_key = "your-mistral-api-key"
# base_url = "https://api.mistral.ai/v1"
```

Ollama:

```toml
provider = "ollama"
model = "llama3.1"
# base_url = "http://localhost:11434"
```

Provider behavior:

- `openai`, `openai_compatible`, and `mistral` use `base_url + /chat/completions`
- `anthropic` uses `/v1/messages`
- `gemini` uses `generateContent`
- `google` is accepted as an alias for `gemini`
- `ollama` uses `/api/generate` and does not require `api_key`

## Usage

Generate and run a command:

```bash
xx "find me all files in this directory"
```

Generate only:

```bash
xx --print-only "show git branches"
```

Override provider or model:

```bash
xx --provider ollama --model llama3.1 "find json files larger than 1 MB"
```

Start the report server:

```bash
xx report serve
```

Show the effective local setup:

```bash
xx doctor
```

Normalize old UTC-style report timestamps into local time:

```bash
xx migrate timestamps
```

Then open:

```text
http://127.0.0.1:10000/report
```

JSON endpoints:

- `/api/executions?days=90`
- `/api/tokens/by-model?days=90`
- `/api/tokens/by-provider?days=90`

Execution report filtering and pagination:

- `/report?days=30`
- `/report?from=2026-01-01&to=2026-03-31`
- `/report?from=2026-01-01 09:00&to=2026-01-01 18:00`
- `/report?from=2026-01-01&to=2026-03-31&page=2&page_size=50`
- `/api/executions?from=2026-01-01 09:00&to=2026-01-01 18:00&page=1&page_size=50`

Rules:

- `from` and `to` are inclusive local date or date-time filters
- accepted formats are `YYYY-MM-DD`, `YYYY-MM-DD HH:MM`, and `YYYY-MM-DDTHH:MM`
- the HTML report expects 24-hour time input in `YYYY-MM-DD HH:MM` format
- if `from` or `to` is provided, that date range takes precedence over `days`
- execution pagination supports `page` and `page_size`
- the HTML report shows `from` and `to` with time selection at the top, and page size near the execution table
- token summary endpoints accept `days`, `from`, and `to`

## Reporting

Every invocation writes an audit row after a provider response is received. The stored fields include:

- invocation time
- user input
- generated command
- whether execution happened
- provider
- model
- prompt, completion, and total tokens
- risk level
- exit code
- working directory

Rows older than `retention_days` are pruned automatically. The report window defaults to `default_report_days`.
If a command fails, `xx` can ask the model for an amended command up to `repair_attempts` times. The default is `3`.
If a repaired command later succeeds, `xx` stores that successful repair in `memory_path`. On future similar requests, `xx` finds the closest remembered successful command with a lightweight semantic token match and passes it to the model as prior-successful-command context so the model can reuse it unchanged or adapt it minimally for the new request.
This is not a separate vector database. The semantic memory backend is a local JSON file with token-overlap matching, stored at `memory_path` and visible via `xx doctor`.
If you created rows before the switch to local timestamps, run `xx migrate timestamps` once to rewrite those legacy rows into local time.

The execution report groups retries into a single session row, showing the final successful command, the number of tries, and token totals summed across all attempts.

## Safety model

- all commands require confirmation before execution
- high-risk commands still require confirmation before execution
- pipelines are allowed
- operators such as `&&`, `||`, and `;` are treated as high-risk
- missing executables in the generated command are treated as high-risk
- if command startup fails, `xx` reports a clean execution error instead of a Python traceback

## Limitations

- aliases and shell functions are not part of command discovery
- provider APIs differ and may require extra tuning per model
- token accounting depends on what each provider returns
- the reporting server is intentionally local-only by default
