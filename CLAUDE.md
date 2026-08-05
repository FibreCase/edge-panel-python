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

There are no tests, linter, or formatter configured.

## Architecture

**ASGI composition**: `app/main.py` creates a `socketio.ASGIApp` that wraps the FastAPI app. Socket.IO and HTTP routes coexist on the same port. The Socket.IO server uses `async_mode="asgi"`.

**Message service** (`app/message_service.py`): SQLite database at `app/.cache/messages.db`. Messages support three types: `text`, `image`, `notify`. Deleting a message soft-deletes it into a `deleted_messages` table (capped at 20 entries). Image files are physically moved between `uploads/` and `deleted_uploads/` directories on delete/restore.

**Weather service** (`app/weather_service.py`): Calls QWeather API using Ed25519 JWT authentication — the JWT is signed by shelling out to `openssl pkeyutl`. Three data sources (current weather, minutely precipitation, air quality) each have independent file-based caches in `app/.cache/` with different TTLs. Precipitation cache uses a longer TTL when there's no rain. `get_weather_summary()` aggregates all three into a single socket payload. Reads `QWEATHER_KID`, `QWEATHER_PROJECT_ID`, `QWEATHER_LOCATION` from environment.

**Event service** (`app/event_service.py`): Placeholder module with `get_event_summary()` returning hardcoded event data. TODO: replace with a real data source.

**Web frontend** (`web/`): Static HTML pages using Tailwind CSS via CDN. `index.html` is the message display/send page; `manage.html` is the message management page. Both are PWA-enabled with a service worker (`sw.js`). Mounted at `/` as a catch-all static route.

**Image uploads**: Uploaded via `/api/messages/upload-image`. HEIC/HEIF images are converted to JPEG using Pillow. Files are stored in `app/.cache/uploads/` and served at `/uploads/{filename}`. The base URL for image links respects `PUBLIC_BASE_URL` env var, falling back to `LOCAL_BASE_URL`.

**Socket.IO events**: Clients emit `request_weather` / `request_event`; server pushes `weather_data` / `event_data` back to the requesting client. On any message change, server broadcasts `messages_updated` to all connected clients.

## Environment Variables

Docker-compose maps host-side `LOCATION`, `KID`, `PROJECT_ID` to container env vars `QWEATHER_LOCATION`, `QWEATHER_KID`, `QWEATHER_PROJECT_ID`. When running locally (without Docker), set the `QWEATHER_*` variants directly.

Required for weather: `QWEATHER_KID`, `QWEATHER_PROJECT_ID`, `QWEATHER_LOCATION` (lon,lat format).

Optional: `QWEATHER_PRIVATE_KEY_FILE` (default: `app/secrets/ed25519-private.pem`), `PUBLIC_BASE_URL` (public image URL prefix), `LOCAL_BASE_URL` (default: `http://127.0.0.1:5000`).

## Key Patterns

- All message mutations in `main.py` emit a Socket.IO `messages_updated` event after the database write.
- The weather service uses `_is_cache_valid()` with per-endpoint TTL constants — check these when modifying cache behavior.
- Python 3.14 is required (see `pyproject.toml` and `.python-version`).
