from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from app import main


class SensorSocketTests(unittest.IsolatedAsyncioTestCase):
    async def test_sensor_snapshot_is_sent_only_to_requesting_client(self):
        payload = {"status": "ok", "reading": {"temperature_c": 26.1}}
        with (
            patch.object(main, "fetch_sensor_data", return_value=payload),
            patch.object(main.socket_server, "emit", new=AsyncMock()) as emit,
        ):
            await main.request_sensor("client-1")
        emit.assert_awaited_once_with("sensor_data", payload, to="client-1")

    async def test_sensor_failure_returns_unavailable_snapshot(self):
        with (
            patch.object(main, "fetch_sensor_data", side_effect=RuntimeError("offline")),
            patch.object(main.logger, "exception"),
            patch.object(main.socket_server, "emit", new=AsyncMock()) as emit,
        ):
            await main.request_sensor("client-1")
        emit.assert_awaited_once_with(
            "sensor_data", {"status": "error", "reading": None}, to="client-1"
        )


class ManagementAuthenticationTests(unittest.TestCase):
    def setUp(self) -> None:
        main.manage_sessions.clear()

    def tearDown(self) -> None:
        main.manage_sessions.clear()

    def test_login_returns_session_token(self) -> None:
        with (
            patch.object(main, "MANAGE_PASSWORD", "7391"),
            patch.object(main.secrets, "token_urlsafe", return_value="session-token"),
            patch.object(main.time, "time", return_value=1_000),
        ):
            result = main.manage_login(main.ManageLoginRequest(password="7391"))

        self.assertEqual(
            result,
            {"token": "session-token", "expires_in": main.MANAGE_SESSION_TTL_SECONDS},
        )
        self.assertEqual(
            main.manage_sessions["session-token"],
            1_000 + main.MANAGE_SESSION_TTL_SECONDS,
        )

    def test_login_rejects_wrong_password(self) -> None:
        with patch.object(main, "MANAGE_PASSWORD", "7391"):
            with self.assertRaises(HTTPException) as raised:
                main.manage_login(main.ManageLoginRequest(password="0000"))

        self.assertEqual(raised.exception.status_code, 401)
        self.assertEqual(main.manage_sessions, {})

    def test_login_request_requires_exactly_four_ascii_digits(self) -> None:
        for password in ("123", "12345", "12ab", "１２３４"):
            with self.subTest(password=password):
                with self.assertRaises(ValidationError):
                    main.ManageLoginRequest(password=password)

    def test_valid_session_is_accepted(self) -> None:
        main.manage_sessions["valid"] = 2_000

        with patch.object(main.time, "time", return_value=1_000):
            self.assertIsNone(main.require_manage_session("Bearer valid"))

    def test_missing_unknown_and_expired_sessions_are_rejected(self) -> None:
        main.manage_sessions["expired"] = 900

        with patch.object(main.time, "time", return_value=1_000):
            for authorization in (None, "Basic value", "Bearer unknown", "Bearer expired"):
                with self.subTest(authorization=authorization):
                    with self.assertRaises(HTTPException) as raised:
                        main.require_manage_session(authorization)
                    self.assertEqual(raised.exception.status_code, 401)

        self.assertNotIn("expired", main.manage_sessions)

    def test_all_management_routes_require_session_dependency(self) -> None:
        protected_routes = {
            ("/api/messages/deleted", "GET"),
            ("/api/messages/{message_id}", "DELETE"),
            ("/api/messages/deleted/{message_id}/restore", "POST"),
            ("/api/messages/clear", "POST"),
        }

        for path, method in protected_routes:
            with self.subTest(path=path, method=method):
                route = next(
                    route
                    for route in main.app.routes
                    if getattr(route, "path", None) == path and method in getattr(route, "methods", set())
                )
                dependencies = [dependency.call for dependency in route.dependant.dependencies]
                self.assertIn(main.require_manage_session, dependencies)


class MessageApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_message_validates_type_specific_fields(self) -> None:
        invalid_requests = (
            main.MessageCreateRequest(type="image", content="url", sub_content="subtitle"),
            main.MessageCreateRequest(type="text", content="text", sub_content="subtitle"),
            main.MessageCreateRequest(type="notify", content="notice"),
        )

        for request in invalid_requests:
            with self.subTest(request=request):
                with self.assertRaises(HTTPException) as raised:
                    await main.create_message(request)
                self.assertEqual(raised.exception.status_code, 400)

    async def test_create_message_saves_and_emits_update(self) -> None:
        saved = {"id": 7, "type": "notify", "content": "done", "source_name": "CI"}
        request = main.MessageCreateRequest(
            type="notify",
            content="done",
            source_name="CI",
        )

        with (
            patch.object(main, "insert_message", return_value=saved) as insert,
            patch.object(main.socket_server, "emit", new=AsyncMock()) as emit,
        ):
            result = await main.create_message(request)

        insert.assert_called_once_with(
            message_type="notify",
            content="done",
            sub_content=None,
            source_name="CI",
        )
        emit.assert_awaited_once_with("messages_updated", {"message": saved})
        self.assertEqual(result, {"message": saved})

    async def test_delete_missing_message_returns_404_without_emitting(self) -> None:
        with (
            patch.object(main, "delete_message", return_value=None),
            patch.object(main.socket_server, "emit", new=AsyncMock()) as emit,
        ):
            with self.assertRaises(HTTPException) as raised:
                await main.delete_single_message(99)

        self.assertEqual(raised.exception.status_code, 404)
        emit.assert_not_awaited()

    async def test_clear_messages_returns_count_and_emits(self) -> None:
        with (
            patch.object(main, "delete_all_messages", return_value=[{"id": 1}, {"id": 2}]),
            patch.object(main.socket_server, "emit", new=AsyncMock()) as emit,
        ):
            result = await main.clear_messages()

        self.assertEqual(result, {"deleted_count": 2})
        emit.assert_awaited_once_with("messages_updated", {"cleared": True})

    async def test_upload_rejects_non_image_and_empty_files(self) -> None:
        non_image = Mock(content_type="text/plain")
        with self.assertRaises(HTTPException) as raised:
            await main.upload_image_message(non_image)
        self.assertEqual(raised.exception.status_code, 400)

        empty_image = Mock(content_type="image/png")
        empty_image.read = AsyncMock(return_value=b"")
        with self.assertRaises(HTTPException) as raised:
            await main.upload_image_message(empty_image)
        self.assertEqual(raised.exception.status_code, 400)

    async def test_upload_saves_image_and_message(self) -> None:
        upload = Mock(
            content_type="image/png",
            filename="photo.png",
        )
        upload.read = AsyncMock(return_value=b"image-data")
        saved = {"id": 8, "type": "image"}

        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(main, "UPLOADS_DIR", Path(temp_dir)),
                patch.object(main, "PUBLIC_BASE_URL", "https://panel.example"),
                patch.object(main, "_normalize_uploaded_image", return_value=(b"normalized", ".jpg")),
                patch.object(main, "uuid4", return_value=Mock(hex="fixed-id")),
                patch.object(main, "insert_message", return_value=saved) as insert,
                patch.object(main.socket_server, "emit", new=AsyncMock()) as emit,
            ):
                result = await main.upload_image_message(upload)
                written = (Path(temp_dir) / "fixed-id.jpg").read_bytes()

        self.assertEqual(written, b"normalized")
        self.assertEqual(result["url"], "https://panel.example/uploads/fixed-id.jpg")
        insert.assert_called_once_with(
            message_type="image",
            content="https://panel.example/uploads/fixed-id.jpg",
        )
        emit.assert_awaited_once_with("messages_updated", {"message": saved})


if __name__ == "__main__":
    unittest.main()
