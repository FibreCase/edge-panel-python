from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


DB_PATH = Path(__file__).with_name(".cache") / "messages.db"


def _create_messages_table(conn: sqlite3.Connection) -> None:
	conn.execute(
		"""
		CREATE TABLE IF NOT EXISTS messages (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			type TEXT NOT NULL CHECK (type IN ('text', 'image', 'notify')),
			content TEXT NOT NULL,
			sub_content TEXT,
			source_name TEXT,
			created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
		)
		"""
	)


def _table_columns(conn: sqlite3.Connection) -> set[str]:
	rows = conn.execute("PRAGMA table_info(messages)").fetchall()
	return {row["name"] for row in rows}


def _ensure_messages_schema(conn: sqlite3.Connection) -> None:
	row = conn.execute(
		"""
		SELECT sql
		FROM sqlite_master
		WHERE type = 'table' AND name = 'messages'
		"""
	).fetchone()

	if row is None:
		_create_messages_table(conn)
		return

	table_sql = (row["sql"] or "").lower()
	columns = _table_columns(conn)
	if "notify" not in table_sql:
		existing_columns = [
			column
			for column in ("id", "type", "content", "sub_content", "created_at")
			if column in columns
		]
		conn.execute("ALTER TABLE messages RENAME TO messages_old")
		_create_messages_table(conn)
		column_list = ", ".join(existing_columns)
		conn.execute(
			f"INSERT INTO messages ({column_list}) SELECT {column_list} FROM messages_old"
		)
		conn.execute("DROP TABLE messages_old")
		return

	if "source_name" not in columns:
		conn.execute("ALTER TABLE messages ADD COLUMN source_name TEXT")


def _get_connection() -> sqlite3.Connection:
	conn = sqlite3.connect(DB_PATH)
	conn.row_factory = sqlite3.Row
	return conn


def init_message_db() -> None:
	with _get_connection() as conn:
		_ensure_messages_schema(conn)
		conn.commit()


def insert_message(
	*,
	message_type: str,
	content: str,
	sub_content: str | None = None,
	source_name: str | None = None,
) -> dict[str, Any]:
	with _get_connection() as conn:
		cursor = conn.execute(
			"""
			INSERT INTO messages (type, content, sub_content, source_name)
			VALUES (?, ?, ?, ?)
			""",
			(message_type, content, sub_content, source_name),
		)
		conn.commit()
		row = conn.execute(
			"""
			SELECT id, type, content, sub_content, source_name, created_at
			FROM messages
			WHERE id = ?
			""",
			(cursor.lastrowid,),
		).fetchone()

	return _row_to_dict(row)


def list_messages() -> list[dict[str, Any]]:
	with _get_connection() as conn:
		rows = conn.execute(
			"""
			SELECT id, type, content, sub_content, source_name, created_at
			FROM messages
			ORDER BY datetime(created_at) DESC, id DESC
			"""
		).fetchall()

	return [_row_to_dict(row) for row in rows]


def delete_all_messages() -> list[dict[str, Any]]:
	with _get_connection() as conn:
		rows = conn.execute(
			"""
			SELECT id, type, content, sub_content, source_name, created_at
			FROM messages
			"""
		).fetchall()

		messages = [_row_to_dict(row) for row in rows]
		conn.execute("DELETE FROM messages")
		conn.commit()

	return messages


def delete_message(message_id: int) -> dict[str, Any]:
	with _get_connection() as conn:
		row = conn.execute(
			"""
			SELECT id, type, content, sub_content, source_name, created_at
			FROM messages
			WHERE id = ?
			""",
			(message_id,),
		).fetchone()

		if row is None:
			return {}

		message = _row_to_dict(row)
		conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
		conn.commit()

	return message


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any]:
	if row is None:
		return {}

	return {
		"id": row["id"],
		"type": row["type"],
		"content": row["content"],
		"sub_content": row["sub_content"],
		"source_name": row["source_name"],
		"created_at": row["created_at"],
	}
