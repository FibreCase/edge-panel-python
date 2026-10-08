from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_LOCAL_TEMP_SERVER = "http://localhost:38285"


def fetch_sensor_data() -> dict[str, Any]:
    """Fetch the latest AHT21/ENS160 snapshot from the local sensor server.

    Preserve ``status``, ``last_updated_unix_ms``, ``reading`` and ``error``.
    The reading contains temperature_c, humidity_rh_pct, aqi, tvoc_ppb,
    eco2_ppm, validity and new_data. Callers should check validity before
    using ENS160 readings: warmup/startup readings are not yet normal.
    Unavailable sensors or malformed responses raise RuntimeError.
    """
    base_url = os.getenv("LOCAL_TEMP_SERVER", DEFAULT_LOCAL_TEMP_SERVER).rstrip("/")
    request = Request(f"{base_url}/api/v1/sensor", headers={"Accept": "application/json"})

    try:
        with urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"Local sensor HTTP error: {exc.code} {exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Local sensor request failed: {exc.reason}") from exc
    except (TimeoutError, OSError) as exc:
        raise RuntimeError(f"Local sensor request failed: {exc}") from exc
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError("Local sensor returned invalid JSON") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("Local sensor returned an invalid snapshot")
    if payload.get("status") != "ok":
        raise RuntimeError(f"Local sensor is unavailable: {payload.get('error') or payload.get('status')}")
    if not isinstance(payload.get("reading"), dict):
        raise RuntimeError("Local sensor returned no reading")

    return payload
