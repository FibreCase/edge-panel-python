from __future__ import annotations

import base64
import gzip
import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

API_HOST = "https://kx3yfp4b97.re.qweatherapi.com"
DEFAULT_LOCATION = "116.41,39.92"
PRIVATE_KEY_FILE = Path(__file__).with_name("secrets") / "ed25519-private.pem"
CACHE_DIR = Path(__file__).with_name(".cache")

WEATHER_CACHE_FILE = CACHE_DIR / "weather.tmp"
PRECIPITATION_CACHE_FILE = CACHE_DIR / "precipitation.tmp"
AIRQUALITY_CACHE_FILE = CACHE_DIR / "airquality.tmp"

NO_RAIN_SUMMARY = "未来两小时无降水"
WEATHER_CACHE_TTL = 1800
PRECIPITATION_CACHE_TTL = 300
PRECIPITATION_NO_RAIN_CACHE_TTL = 3600
AIRQUALITY_CACHE_TTL = 1800


def _base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _load_private_key_path(private_key_path: str | Path | None = None) -> Path:
    path = Path(private_key_path or os.getenv("QWEATHER_PRIVATE_KEY_FILE") or PRIVATE_KEY_FILE)
    if not path.exists():
        raise ValueError(f"Missing QWeather private key file: {path}")
    return path


def _build_jwt(
    private_key_path: str | Path | None = None,
    kid: str | None = None,
    project_id: str | None = None,
    iat: float | None = None,
    exp: float | None = None,
) -> str:
    kid = kid or os.getenv("QWEATHER_KID")
    project_id = project_id or os.getenv("QWEATHER_PROJECT_ID")
    if not kid:
        raise ValueError("Missing QWeather kid. Set QWEATHER_KID or pass kid explicitly.")
    if not project_id:
        raise ValueError("Missing QWeather project id. Set QWEATHER_PROJECT_ID, or pass project_id explicitly.")

    private_key_path = _load_private_key_path(private_key_path)
    iat = int(iat if iat is not None else time.time()) - 30
    exp = int(exp if exp is not None else iat + 900)

    header = {"alg": "EdDSA", "kid": kid}
    payload = {"sub": project_id, "iat": iat, "exp": exp}
    header_payload = (
        f"{_base64url_encode(json.dumps(header, separators=(',', ':')).encode('utf-8'))}"
        f".{_base64url_encode(json.dumps(payload, separators=(',', ':')).encode('utf-8'))}"
    )

    with tempfile.NamedTemporaryFile(mode="wb", delete=False) as temp_file:
        temp_file.write(header_payload.encode("utf-8"))
        temp_file_path = Path(temp_file.name)

    try:
        try:
            sign_result = subprocess.run(
                [
                    "openssl",
                    "pkeyutl",
                    "-sign",
                    "-inkey",
                    str(private_key_path),
                    "-rawin",
                    "-in",
                    str(temp_file_path),
                ],
                check=True,
                capture_output=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("openssl is required to sign the QWeather JWT") from exc
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode("utf-8", errors="replace").strip() if exc.stderr else ""
            raise RuntimeError(f"Failed to sign QWeather JWT: {stderr or exc}") from exc
    finally:
        temp_file_path.unlink(missing_ok=True)

    return f"{header_payload}.{_base64url_encode(sign_result.stdout)}"


def _make_api_request(url: str, token: str) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept-Encoding": "gzip, deflate",
        },
    )

    try:
        with urlopen(request, timeout=10) as response:
            data = response.read()
            if response.headers.get("Content-Encoding") == "gzip":
                data = gzip.decompress(data)
            payload: dict[str, Any] = json.loads(data.decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"QWeather HTTP error: {exc.code} {exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"QWeather request failed: {exc.reason}") from exc

    if payload.get("code") != "200":
        raise RuntimeError(f"QWeather API error: {payload}")

    return payload


def _load_cache(file_path: Path) -> dict[str, Any] | None:
    try:
        if file_path.exists():
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError):
        pass
    return None


def _save_cache(file_path: Path, data: dict[str, Any]) -> None:
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except IOError as e:
        logger.warning("Failed to save cache: %s", e)


def _is_cache_valid(cache_data: dict[str, Any] | None, max_age_seconds: int = 1800) -> bool:
    if not cache_data or "cached_at" not in cache_data:
        return False

    try:
        cached_at = cache_data["cached_at"]
        return (time.time() - cached_at) < max_age_seconds
    except (ValueError, KeyError, TypeError):
        return False


def _request_current_weather(
    location: str = DEFAULT_LOCATION,
    token: str | None = None,
    private_key_path: str | Path | None = None,
    kid: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    token = token or _build_jwt(private_key_path=private_key_path, kid=kid, project_id=project_id)
    url = f"{API_HOST}/v7/grid-weather/now?{urlencode({'location': location, 'lang': 'en'})}"
    payload = _make_api_request(url, token)

    now = payload.get("now", {})
    return {
        "code": payload.get("code"),
        "updateTime": payload.get("updateTime"),
        "now": {
            "obsTime": now.get("obsTime"),
            "temp": now.get("temp"),
            "icon": now.get("icon"),
            "text": now.get("text"),
            "wind360": now.get("wind360"),
            "windDir": now.get("windDir"),
            "windScale": now.get("windScale"),
            "windSpeed": now.get("windSpeed"),
            "humidity": now.get("humidity"),
            "precip": now.get("precip"),
            "pressure": now.get("pressure"),
        },
        "refer": payload.get("refer", {}),
    }


def fetch_current_weather(
    location: str = DEFAULT_LOCATION,
    token: str | None = None,
    private_key_path: str | Path | None = None,
    kid: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    cached = _load_cache(WEATHER_CACHE_FILE)
    if cached and _is_cache_valid(cached, WEATHER_CACHE_TTL):
        return cached

    data = _request_current_weather(
        location=location, token=token,
        private_key_path=private_key_path, kid=kid, project_id=project_id,
    )
    data["cached_at"] = time.time()
    _save_cache(WEATHER_CACHE_FILE, data)
    return data


def _request_minutely_precipitation(
    location: str = DEFAULT_LOCATION,
    token: str | None = None,
    private_key_path: str | Path | None = None,
    kid: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    token = token or _build_jwt(private_key_path=private_key_path, kid=kid, project_id=project_id)
    url = f"{API_HOST}/v7/minutely/5m?{urlencode({'location': location, 'lang': 'zh-hans'})}"
    payload = _make_api_request(url, token)

    return {
        "code": payload.get("code"),
        "updateTime": payload.get("updateTime"),
        "summary": payload.get("summary"),
        "minutely": payload.get("minutely", []),
    }


def fetch_minutely_precipitation(
    location: str = DEFAULT_LOCATION,
    token: str | None = None,
    private_key_path: str | Path | None = None,
    kid: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    cached = _load_cache(PRECIPITATION_CACHE_FILE)

    if cached and cached.get("summary", "") != NO_RAIN_SUMMARY:
        max_age = PRECIPITATION_CACHE_TTL
    else:
        max_age = PRECIPITATION_NO_RAIN_CACHE_TTL

    if cached and _is_cache_valid(cached, max_age):
        return cached

    data = _request_minutely_precipitation(
        location=location, token=token,
        private_key_path=private_key_path, kid=kid, project_id=project_id,
    )
    data["cached_at"] = time.time()
    _save_cache(PRECIPITATION_CACHE_FILE, data)
    return data


def _request_air_quality(
    location: str = DEFAULT_LOCATION,
    token: str | None = None,
    private_key_path: str | Path | None = None,
    kid: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    token = token or _build_jwt(private_key_path=private_key_path, kid=kid, project_id=project_id)
    longitude, latitude = location.split(",")
    url = f"{API_HOST}/airquality/v1/current/{latitude}/{longitude}?lang=zh-hans"
    payload = _make_api_request(url, token)

    indexes = payload.get("indexes", [])
    if not indexes:
        raise RuntimeError("QWeather API error: No air quality data returned")

    index = indexes[0]
    return {
        "aqi": index.get("aqi"),
        "category": index.get("category"),
    }


def fetch_air_quality(
    location: str = DEFAULT_LOCATION,
    token: str | None = None,
    private_key_path: str | Path | None = None,
    kid: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    cached = _load_cache(AIRQUALITY_CACHE_FILE)
    if cached and _is_cache_valid(cached, AIRQUALITY_CACHE_TTL):
        return cached

    data = _request_air_quality(
        location=location, token=token,
        private_key_path=private_key_path, kid=kid, project_id=project_id,
    )
    data["cached_at"] = time.time()
    _save_cache(AIRQUALITY_CACHE_FILE, data)
    return data
