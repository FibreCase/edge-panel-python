from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from app import sensor_service


class SensorServiceTests(unittest.TestCase):
    def fetch_payload(self, payload):
        with patch.object(sensor_service, "urlopen", return_value=io.BytesIO(json.dumps(payload).encode())):
            return sensor_service.fetch_sensor_data()

    def test_default_url_and_complete_snapshot(self):
        payload = {
            "status": "ok",
            "last_updated_unix_ms": 1791446400000,
            "reading": {
                "temperature_c": 23.5, "humidity_rh_pct": 42.0,
                "aqi": 2, "tvoc_ppb": 123, "eco2_ppm": 500,
                "validity": "normal", "new_data": True,
            },
            "error": None,
        }
        with (
            patch.dict(sensor_service.os.environ, {}, clear=True),
            patch.object(sensor_service, "urlopen", return_value=io.BytesIO(json.dumps(payload).encode())) as opener,
        ):
            self.assertEqual(sensor_service.fetch_sensor_data(), payload)
        self.assertEqual(opener.call_args.args[0].full_url, "http://localhost:38285/api/v1/sensor")
        self.assertEqual(opener.call_args.kwargs["timeout"], 5)

    def test_configured_url_and_warmup_preserved(self):
        with (
            patch.dict(sensor_service.os.environ, {"LOCAL_TEMP_SERVER": "http://sensor:8080/"}),
            patch.object(sensor_service, "urlopen", return_value=io.BytesIO(b'{"status":"ok","reading":{"validity":"warmup"}}')) as opener,
        ):
            self.assertEqual(sensor_service.fetch_sensor_data()["reading"]["validity"], "warmup")
        self.assertEqual(opener.call_args.args[0].full_url, "http://sensor:8080/api/v1/sensor")

    def test_request_failures(self):
        error = HTTPError("http://sensor", 503, "Service Unavailable", {}, None)
        self.addCleanup(error.close)
        for failure in (error, URLError("offline"), TimeoutError("timed out")):
            with self.subTest(failure=failure), patch.object(sensor_service, "urlopen", side_effect=failure):
                with self.assertRaises(RuntimeError):
                    sensor_service.fetch_sensor_data()

    def test_invalid_json(self):
        with patch.object(sensor_service, "urlopen", return_value=io.BytesIO(b"not json")):
            with self.assertRaisesRegex(RuntimeError, "invalid JSON"):
                sensor_service.fetch_sensor_data()

    def test_rejects_unavailable_or_missing_readings(self):
        for payload in (
            [], {"status": "starting", "reading": None},
            {"status": "error", "reading": {"temperature_c": 20}, "error": "I2C error"},
            {"status": "ok", "reading": None},
        ):
            with self.subTest(payload=payload), self.assertRaises(RuntimeError):
                self.fetch_payload(payload)


if __name__ == "__main__":
    unittest.main()
