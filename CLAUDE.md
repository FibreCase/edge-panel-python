# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Backend service for `flutter_desk_panel` — a desktop/mobile panel that displays weather, events, and messages. Built with FastAPI + Socket.IO, serves static web pages, and stores messages in SQLite.

## Commands

```bash
# Install dependencies
uv sync

# Run locally (serves on http://0.0.0.0:5000)
uv run python app/main.py

# Docker
docker compose up -d
```

Tests use the standard-library `unittest` runner:

```bash
uv run python -m unittest discover -s tests -v
```

There is no separately configured linter or formatter.

## Architecture

**ASGI composition**: `app/main.py` creates a `socketio.ASGIApp` that wraps the FastAPI app. Socket.IO and HTTP routes coexist on the same port. The Socket.IO server uses `async_mode="asgi"`.

**Message service** (`app/message_service.py`): SQLite database at `app/.cache/messages.db`. Messages support three types: `text`, `image`, `notify`. Deleting a message soft-deletes it into a `deleted_messages` table (capped at 20 entries). Image files are physically moved between `uploads/` and `deleted_uploads/` directories on delete/restore.

**Weather service** (`app/weather_service.py`): Calls QWeather API using Ed25519 JWT authentication — the JWT is signed by shelling out to `openssl pkeyutl`. Three data sources (current weather, minutely precipitation, air quality) each have independent file-based caches in `app/.cache/` with different TTLs. Precipitation cache uses a longer TTL when there's no rain. `get_weather_summary()` aggregates all three into a single socket payload. Reads `QWEATHER_KID`, `QWEATHER_PROJECT_ID`, `QWEATHER_LOCATION` from environment.

**Event service** (`app/event_service.py`): Placeholder module with `get_event_summary()` returning hardcoded event data. TODO: replace with a real data source.

**Web frontend** (`web/`): Static HTML pages using Tailwind CSS via CDN. `index.html` is the message send page and links to management; it does not expose a clear-all action. `manage.html` first presents a four-digit password form and only loads management data after login. Both are PWA-enabled with a service worker (`sw.js`). Mounted at `/` as a catch-all static route.

**Management authentication**: `POST /api/manage/login` compares the submitted four-digit password against `MANAGE_PASSWORD` and returns a random Bearer token. Tokens are held in the process-local `manage_sessions` dictionary for eight hours, so they do not survive a server restart. The management page stores its token in `sessionStorage`. `GET /api/messages/deleted`, `DELETE /api/messages/{message_id}`, `POST /api/messages/deleted/{message_id}/restore`, and `POST /api/messages/clear` use the `require_manage_session` dependency. `GET /api/messages` remains public because message-display clients consume it.

**Image uploads**: Uploaded via `/api/messages/upload-image`. HEIC/HEIF images are converted to JPEG using Pillow. Files are stored in `app/.cache/uploads/` and served at `/uploads/{filename}`. The base URL for image links respects `PUBLIC_BASE_URL` env var, falling back to `LOCAL_BASE_URL`.

**Socket.IO events**: Clients emit `request_weather` / `request_event`; server pushes `weather_data` / `event_data` back to the requesting client. On any message change, server broadcasts `messages_updated` to all connected clients.

## Environment Variables

Docker Compose passes through `QWEATHER_LOCATION`, `QWEATHER_KID`, `QWEATHER_PROJECT_ID`, and `MANAGE_PASSWORD` from the host environment. Local runs use the same variable names.

Required for weather: `QWEATHER_KID`, `QWEATHER_PROJECT_ID`, `QWEATHER_LOCATION` (lon,lat format).

Optional: `QWEATHER_PRIVATE_KEY_FILE` (default: `app/secrets/ed25519-private.pem`), `PUBLIC_BASE_URL` (public image URL prefix), `LOCAL_BASE_URL` (default: `http://127.0.0.1:5000`), and `MANAGE_PASSWORD` (exactly four ASCII digits; default: `1234`). Always override the default management password in deployments.

## Key Patterns

- All message mutations in `main.py` emit a Socket.IO `messages_updated` event after the database write.
- Management endpoints that expose history or mutate existing messages must retain the `require_manage_session` dependency. Creating messages and reading the active message feed remain public for panel clients.
- The weather service uses `_is_cache_valid()` with per-endpoint TTL constants — check these when modifying cache behavior.
- Python 3.14 is required (see `pyproject.toml` and `.python-version`).
