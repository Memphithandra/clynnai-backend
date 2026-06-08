# ClynnAI Backend

ClynnAI Backend is a portable FastAPI service for the ClynnAI Android app. It provides OpenAI-compatible chat and image proxy endpoints, a stateful conversation API, an internal LangGraph-based agent runtime, Firecrawl web tools, upload handling, ASR forwarding, and a small admin configuration page.

## Features

- **FastAPI service** with health, version, admin, model, chat, image, upload, ASR, and conversation endpoints.
- **OpenAI-compatible upstream proxy** for `/api/chat/completions` and `/api/images/generations`.
- **Agent runtime** built around LangGraph with tool calls, streaming events, image result extraction, and phone-action interruption support.
- **Built-in tools** for Firecrawl search/scrape, image generation, and phone action requests.
- **Conversation storage** using SQLite plus local upload storage.
- **Admin WebUI** at `/admin` for editing provider config without exposing secret values in server-info responses.

## Repository Layout

```text
clynnai_backend/
  main.py                 # FastAPI app and API routes
  config.py               # Settings, env aliases, provider config loading
  agent/                  # Agent runtime, prompts, schemas, tools, LLM client
 data/
  provider-config.example.json
 tests/                   # pytest coverage for API, config, agent runtime
 pyproject.toml
 requirements.lock
```

Runtime-only files are intentionally ignored by Git:

- `.env`
- `data/provider-config.json`
- `.venv/`
- caches, logs, local databases, and build artifacts

## Requirements

- Python 3.11+
- A virtual environment tool such as `uv`, `python -m venv`, or equivalent
- An OpenAI-compatible upstream API for chat/image/ASR features
- Optional: Firecrawl API key for web search/scrape tools

## Quick Start

```bash
git clone https://github.com/Memphithandra/clynnai-backend.git
cd clynnai-backend
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
cp data/provider-config.example.json data/provider-config.json
python -m clynnai_backend
```

The service listens on `0.0.0.0:8088` by default.

Open:

- Health check: `http://127.0.0.1:8088/health`
- Version: `http://127.0.0.1:8088/version`
- Admin page: `http://127.0.0.1:8088/admin`

## Configuration

Configuration is loaded from three places:

1. Environment variables and `.env`
2. `data/provider-config.json`
3. Built-in defaults in `clynnai_backend/config.py`

Environment variables override the provider config file.

Common settings:

| Setting | Env aliases | Default |
| --- | --- | --- |
| `host` | `HOST`, `CLYNN_AI_HOST` | `0.0.0.0` |
| `port` | `PORT`, `CLYNN_AI_PORT` | `8088` |
| `public_base_url` | `PUBLIC_BASE_URL`, `CLYNN_PUBLIC_BASE_URL` | `http://127.0.0.1:8088` |
| `upstream_base_url` | `UPSTREAM_BASE_URL`, `CLYNN_UPSTREAM_BASE_URL` | OpenAI-compatible base URL |
| `upstream_api_key` | `UPSTREAM_API_KEY`, `CLYNN_UPSTREAM_API_KEY` | empty |
| `default_model` | `DEFAULT_MODEL`, `CLYNN_DEFAULT_MODEL` | empty |
| `image_model` | `IMAGE_MODEL`, `CLYNN_IMAGE_MODEL` | empty |
| `asr_model` | `ASR_MODEL`, `CLYNN_ASR_MODEL` | `whisper-1` |
| `firecrawl_api_key` | `FIRECRAWL_API_KEY`, `CLYNN_FIRECRAWL_API_KEY` | empty |
| `firecrawl_base_url` | `FIRECRAWL_BASE_URL`, `CLYNN_FIRECRAWL_BASE_URL` | `https://api.firecrawl.dev` |
| `agent_max_steps` | `AGENT_MAX_STEPS`, `CLYNN_AGENT_MAX_STEPS` | `6` |
| `agent_use_native_tool_calls` | `AGENT_USE_NATIVE_TOOL_CALLS`, `CLYNN_AGENT_USE_NATIVE_TOOL_CALLS` | `true` |

The provider config path defaults to `./data/provider-config.json`. Override it with:

```bash
export CLYNN_CONFIG_PATH=/path/to/provider-config.json
```

## Provider Config Example

```json
{
  "upstream_base_url": "https://api.example.com/v1",
  "upstream_model": "example-model",
  "upstream_api_key": "",
  "admin_token": "change-me",
  "app_token": "change-me",
  "firecrawl_api_key": "",
  "asr_model": "whisper-1"
}
```

Do not commit real API keys. Keep real values in `.env` or `data/provider-config.json`.

## API Overview

### Service and Admin

- `GET /health` — service health.
- `GET /version` — backend version.
- `GET /admin` — lightweight provider configuration page.
- `GET /api/admin/provider-config` — redacted provider config.
- `PUT /api/admin/provider-config` — update provider config fields.
- `GET /api/server-info` — safe server info for the app.
- `GET /api/models` — list upstream models when available.

### OpenAI-Compatible Proxy

- `POST /api/chat/completions` — forward chat completions to the configured upstream.
- `POST /api/images/generations` — forward image generation requests.

### ClynnAI Conversation API

- `POST /api/conversations/message` — send a message and receive a structured response.
- `POST /api/conversations/message/stream` — stream agent/model events.
- `GET /api/conversations` — list recent conversations.
- `GET /api/conversations/{session_id}/messages` — list messages for a session.
- `POST /api/conversations/{session_id}/messages/{message_id}/edit` — edit and rerun from a message.
- `DELETE /api/conversations/{session_id}` — delete a conversation.
- `POST /api/conversations/{session_id}/phone-actions/{action_id}/result` — report phone-action result.

### Files and ASR

- `POST /api/uploads` — upload a file.
- `GET /api/uploads/{file_id}` — retrieve an uploaded file.
- `POST /api/asr/transcribe` — forward an audio file to the configured ASR model.

### Personas

- `GET /api/personas` — list personas.
- `PUT /api/personas/{name}` — create or update a persona.
- `DELETE /api/personas/{name}` — delete a persona.

## Development

Install test dependencies and run tests:

```bash
. .venv/bin/activate
pip install -e '.[test]'
pytest -q
```

Current expected result:

```text
14 passed
```

## Security Notes

- Never commit `.env` or `data/provider-config.json`.
- `server-info` and admin config responses expose only `_set` booleans for secret fields.
- Use strong `admin_token` and `app_token` values before exposing the service to a network.
- Prefer a reverse proxy with HTTPS for public deployments.
- Keep `public_base_url` aligned with the URL used by the Android app.

## License

No license file is included yet. Add one before distributing this project publicly.
