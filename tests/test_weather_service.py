from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from app import weather_service


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._data = json.dumps(payload).encode("utf-8")
        self.headers: dict[str, str] = {}

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._data


class QWeatherResponseTests(unittest.TestCase):
    def test_accepts_legacy_v7_success_code(self) -> None:
        response = _FakeResponse({"code": "200", "now": {"temp": "25"}})

        with patch.object(weather_service, "urlopen", return_value=response):
            payload = weather_service._make_api_request("https://example.test", "token")

        self.assertEqual(payload["now"], {"temp": "25"})

    def test_rejects_legacy_v7_error_code(self) -> None:
        response = _FakeResponse({"code": "401"})

        with patch.object(weather_service, "urlopen", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "QWeather API error"):
                weather_service._make_api_request("https://example.test", "token")

    def test_accepts_and_parses_air_quality_v1_response_without_code(self) -> None:
        response = _FakeResponse(
            {
                "metadata": {"tag": "test"},
                "indexes": [
                    {
                        "code": "cn-mee",
                        "aqi": 62,
                        "category": "良",
                    }
                ],
                "pollutants": [],
                "stations": [],
            }
        )

        with patch.object(weather_service, "urlopen", return_value=response):
            air_quality = weather_service._request_air_quality(
                location="116.41,39.92",
                token="token",
            )

        self.assertEqual(air_quality, {"aqi": 62, "category": "良"})


if __name__ == "__main__":
    unittest.main()
