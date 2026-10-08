from __future__ import annotations

from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import message_service


class MessageServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.patchers = [
            patch.object(message_service, "DB_PATH", self.root / "messages.db"),
            patch.object(message_service, "UPLOADS_DIR", self.root / "uploads"),
            patch.object(message_service, "DELETED_UPLOADS_DIR", self.root / "deleted_uploads"),
        ]
        for patcher in self.patchers:
            patcher.start()
        message_service.init_message_db()

    def tearDown(self) -> None:
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()

    def test_insert_and_list_messages_preserves_fields(self) -> None:
        first = message_service.insert_message(message_type="text", content="hello")
        second = message_service.insert_message(
            message_type="notify",
            content="build complete",
            sub_content="details",
            source_name="CI",
        )

        messages = message_service.list_messages()

        self.assertEqual([message["id"] for message in messages], [second["id"], first["id"]])
        self.assertEqual(messages[0]["type"], "notify")
        self.assertEqual(messages[0]["source_name"], "CI")
        self.assertEqual(messages[0]["sub_content"], "details")
        self.assertIsNotNone(messages[0]["created_at"])

    def test_delete_and_restore_image_moves_its_file(self) -> None:
        message_service.UPLOADS_DIR.mkdir()
        uploaded_file = message_service.UPLOADS_DIR / "photo.jpg"
        uploaded_file.write_bytes(b"image")
        message = message_service.insert_message(
            message_type="image",
            content="http://localhost/uploads/photo.jpg",
        )

        deleted = message_service.delete_message(message["id"])

        self.assertEqual(deleted["id"], message["id"])
        self.assertFalse(uploaded_file.exists())
        self.assertTrue((message_service.DELETED_UPLOADS_DIR / "photo.jpg").exists())
        self.assertEqual(message_service.list_messages(), [])
        self.assertEqual(message_service.list_deleted_messages()[0]["id"], message["id"])

        restored = message_service.restore_deleted_message(message["id"])

        self.assertEqual(restored["id"], message["id"])
        self.assertTrue(uploaded_file.exists())
        self.assertEqual(message_service.list_deleted_messages(), [])
        self.assertEqual(message_service.list_messages()[0]["id"], message["id"])

    def test_delete_and_restore_missing_message_returns_none(self) -> None:
        self.assertIsNone(message_service.delete_message(999))
        self.assertIsNone(message_service.restore_deleted_message(999))

    def test_delete_all_archives_messages(self) -> None:
        ids = [
            message_service.insert_message(message_type="text", content=f"message-{index}")["id"]
            for index in range(3)
        ]

        removed = message_service.delete_all_messages()

        self.assertEqual({message["id"] for message in removed}, set(ids))
        self.assertEqual(message_service.list_messages(), [])
        self.assertEqual(
            {message["id"] for message in message_service.list_deleted_messages()},
            set(ids),
        )

    def test_deleted_history_is_limited_to_twenty_newest_messages(self) -> None:
        for index in range(message_service.DELETED_MESSAGES_LIMIT + 2):
            message = message_service.insert_message(message_type="text", content=str(index))
            message_service.delete_message(message["id"])

        deleted = message_service.list_deleted_messages()

        self.assertEqual(len(deleted), message_service.DELETED_MESSAGES_LIMIT)
        self.assertEqual({message["id"] for message in deleted}, set(range(3, 23)))

    def test_init_migrates_legacy_message_schema(self) -> None:
        message_service.DB_PATH.unlink()
        with closing(sqlite3.connect(message_service.DB_PATH)) as conn, conn:
            conn.execute(
                """
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL CHECK (type IN ('text', 'image')),
                    content TEXT NOT NULL,
                    sub_content TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
                )
                """
            )
            conn.execute("INSERT INTO messages (type, content) VALUES ('text', 'legacy')")

        message_service.init_message_db()
        migrated = message_service.list_messages()
        notify = message_service.insert_message(
            message_type="notify",
            content="new",
            source_name="app",
        )

        self.assertEqual(migrated[0]["content"], "legacy")
        self.assertIsNone(migrated[0]["source_name"])
        self.assertEqual(notify["source_name"], "app")


if __name__ == "__main__":
    unittest.main()
