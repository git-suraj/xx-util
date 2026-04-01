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
- high-risk command blocking unless `--force` is supplied
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

Allow a high-risk command:

```bash
xx --force "delete all build artifacts in this directory"
```

Start the report server:

```bash
xx report serve
```

Then open:

```text
http://127.0.0.1:10000/report
```

JSON endpoints:

- `/api/executions?days=90`
- `/api/tokens/by-model?days=90`
- `/api/tokens/by-provider?days=90`

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

## Safety model

- all commands require confirmation before execution
- high-risk commands are refused unless `--force` is present
- pipelines are allowed
- operators such as `&&`, `||`, and `;` are treated as high-risk
- missing executables in the generated command are treated as high-risk

## Limitations

- aliases and shell functions are not part of command discovery
- provider APIs differ and may require extra tuning per model
- token accounting depends on what each provider returns
- the reporting server is intentionally local-only by default
