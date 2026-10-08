from __future__ import annotations

import base64
import gzip
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from app import weather_service


class _FakeResponse:
    def __init__(
        self,
        payload: dict[str, object] | None = None,
        *,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._data = data if data is not None else json.dumps(payload or {}).encode("utf-8")
        self.headers = headers or {}

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._data


def _decode_jwt_part(part: str) -> dict[str, object]:
    padded = part + "=" * (-len(part) % 4)
    return json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))


class QWeatherRequestTests(unittest.TestCase):
    def test_sends_bearer_token_and_accepts_legacy_success_code(self) -> None:
        response = _FakeResponse({"code": "200", "now": {"temp": "25"}})

        with patch.object(weather_service, "urlopen", return_value=response) as urlopen:
            payload = weather_service._make_api_request("https://example.test/weather", "token")

        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.test/weather")
        self.assertEqual(request.get_header("Authorization"), "Bearer token")
        self.assertEqual(request.get_header("Accept-encoding"), "gzip, deflate")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 10)
        self.assertEqual(payload["now"], {"temp": "25"})

    def test_accepts_numeric_legacy_success_code(self) -> None:
        with patch.object(weather_service, "urlopen", return_value=_FakeResponse({"code": 200})):
            payload = weather_service._make_api_request("https://example.test", "token")

        self.assertEqual(payload["code"], 200)

    def test_rejects_legacy_error_code(self) -> None:
        with patch.object(weather_service, "urlopen", return_value=_FakeResponse({"code": "401"})):
            with self.assertRaisesRegex(RuntimeError, "QWeather API error"):
                weather_service._make_api_request("https://example.test", "token")

    def test_decompresses_gzip_response(self) -> None:
        data = gzip.compress(json.dumps({"code": "200", "now": {"temp": "8"}}).encode())
        response = _FakeResponse(data=data, headers={"Content-Encoding": "gzip"})

        with patch.object(weather_service, "urlopen", return_value=response):
            payload = weather_service._make_api_request("https://example.test", "token")

        self.assertEqual(payload["now"]["temp"], "8")

    def test_wraps_http_error(self) -> None:
        error = HTTPError("https://example.test", 503, "Unavailable", {}, None)
        self.addCleanup(error.close)

        with patch.object(weather_service, "urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "503 Unavailable"):
                weather_service._make_api_request("https://example.test", "token")

    def test_wraps_network_error(self) -> None:
        with patch.object(weather_service, "urlopen", side_effect=URLError("offline")):
            with self.assertRaisesRegex(RuntimeError, "offline"):
                weather_service._make_api_request("https://example.test", "token")

    def test_build_jwt_contains_expected_claims_and_signature(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            key_path = Path(temp_dir) / "private.pem"
            key_path.write_text("test key", encoding="utf-8")
            completed = subprocess.CompletedProcess([], 0, stdout=b"signature", stderr=b"")

            with patch.object(weather_service.subprocess, "run", return_value=completed) as run:
                token = weather_service._build_jwt(
                    private_key_path=key_path,
                    kid="kid-1",
                    project_id="project-1",
                    iat=1_000,
                    exp=1_900,
                )

        header_part, payload_part, signature_part = token.split(".")
        self.assertEqual(_decode_jwt_part(header_part), {"alg": "EdDSA", "kid": "kid-1"})
        self.assertEqual(
            _decode_jwt_part(payload_part),
            {"sub": "project-1", "iat": 970, "exp": 1_900},
        )
        self.assertEqual(base64.urlsafe_b64decode(signature_part + "=="), b"signature")
        self.assertIn("-rawin", run.call_args.args[0])

    def test_build_jwt_requires_credentials(self) -> None:
        with patch.dict(weather_service.os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "kid"):
                weather_service._build_jwt(project_id="project")
            with self.assertRaisesRegex(ValueError, "project id"):
                weather_service._build_jwt(kid="kid")


class QWeatherDataTests(unittest.TestCase):
    def test_air_quality_v1_url_and_response_without_code(self) -> None:
        payload = {
            "metadata": {"tag": "test"},
            "indexes": [{"code": "cn-mee", "aqi": 62, "category": "良"}],
        }

        with patch.object(weather_service, "_make_api_request", return_value=payload) as request:
            air_quality = weather_service._request_air_quality(
                location="116.41,39.92",
                token="token",
            )

        self.assertEqual(
            request.call_args.args,
            (f"{weather_service.API_HOST}/airquality/v1/current/39.92/116.41?lang=zh-hans", "token"),
        )
        self.assertEqual(air_quality, {"aqi": 62, "category": "良"})

    def test_air_quality_requires_an_index(self) -> None:
        with patch.object(weather_service, "_make_api_request", return_value={"indexes": []}):
            with self.assertRaisesRegex(RuntimeError, "No air quality data"):
                weather_service._request_air_quality(token="token")

    def test_cache_validity_handles_fresh_stale_and_invalid_values(self) -> None:
        with patch.object(weather_service.time, "time", return_value=1_000):
            self.assertTrue(weather_service._is_cache_valid({"cached_at": 950}, 60))
            self.assertFalse(weather_service._is_cache_valid({"cached_at": 900}, 60))
            self.assertFalse(weather_service._is_cache_valid({"cached_at": "bad"}, 60))
            self.assertFalse(weather_service._is_cache_valid(None, 60))

    def test_current_weather_uses_valid_cache_without_request(self) -> None:
        cached = {"cached_at": 900, "now": {"temp": "20"}}

        with (
            patch.object(weather_service, "_load_cache", return_value=cached),
            patch.object(weather_service, "_is_cache_valid", return_value=True),
            patch.object(weather_service, "_request_current_weather") as request,
        ):
            result = weather_service.fetch_current_weather(token="token")

        self.assertIs(result, cached)
        request.assert_not_called()

    def test_current_weather_refreshes_and_saves_stale_cache(self) -> None:
        fresh = {"now": {"temp": "21"}}

        with (
            patch.object(weather_service, "_load_cache", return_value={"cached_at": 1}),
            patch.object(weather_service, "_is_cache_valid", return_value=False),
            patch.object(weather_service, "_request_current_weather", return_value=fresh),
            patch.object(weather_service, "_save_cache") as save,
            patch.object(weather_service.time, "time", return_value=2_000),
        ):
            result = weather_service.fetch_current_weather(location="1,2", token="token")

        self.assertEqual(result["cached_at"], 2_000)
        save.assert_called_once_with(weather_service.WEATHER_CACHE_FILE, fresh)

    def test_no_rain_cache_uses_longer_ttl(self) -> None:
        cached = {"cached_at": 1, "summary": weather_service.NO_RAIN_SUMMARY}

        with (
            patch.object(weather_service, "_load_cache", return_value=cached),
            patch.object(weather_service, "_is_cache_valid", return_value=True) as is_valid,
            patch.object(weather_service, "_request_minutely_precipitation") as request,
        ):
            result = weather_service.fetch_minutely_precipitation(token="token")

        self.assertIs(result, cached)
        is_valid.assert_called_once_with(cached, weather_service.PRECIPITATION_NO_RAIN_CACHE_TTL)
        request.assert_not_called()

    def test_rain_cache_uses_shorter_ttl(self) -> None:
        cached = {"cached_at": 1, "summary": "未来两小时有雨"}

        with (
            patch.object(weather_service, "_load_cache", return_value=cached),
            patch.object(weather_service, "_is_cache_valid", return_value=True) as is_valid,
        ):
            result = weather_service.fetch_minutely_precipitation(token="token")

        self.assertIs(result, cached)
        is_valid.assert_called_once_with(cached, weather_service.PRECIPITATION_CACHE_TTL)

    def test_weather_summary_combines_all_sources(self) -> None:
        with (
            patch.object(
                weather_service,
                "fetch_current_weather",
                return_value={"now": {"text": "Sunny", "temp": "25", "icon": "100"}},
            ),
            patch.object(
                weather_service,
                "fetch_minutely_precipitation",
                return_value={"summary": "无雨"},
            ),
            patch.object(
                weather_service,
                "fetch_air_quality",
                return_value={"aqi": 42, "category": "优"},
            ),
        ):
            summary = weather_service.get_weather_summary()

        self.assertEqual(
            summary,
            {
                "weather": "Sunny",
                "temperature": "25",
                "icon": "100",
                "rain_notification": "无雨",
                "aqi": 42,
                "aqi_category": "优",
            },
        )

    def test_cache_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "nested" / "weather.json"
            weather_service._save_cache(cache_file, {"weather": "晴"})

            self.assertEqual(weather_service._load_cache(cache_file), {"weather": "晴"})

    def test_invalid_cache_file_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_file = Path(temp_dir) / "weather.json"
            cache_file.write_text("not json", encoding="utf-8")

            self.assertIsNone(weather_service._load_cache(cache_file))


if __name__ == "__main__":
    unittest.main()
