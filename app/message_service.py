from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Any


DB_PATH = Path(__file__).with_name(".cache") / "messages.db"
UPLOADS_DIR = DB_PATH.parent / "uploads"
DELETED_UPLOADS_DIR = DB_PATH.parent / "deleted_uploads"
DELETED_MESSAGES_LIMIT = 20


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


def _create_deleted_messages_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS deleted_messages (
            original_id INTEGER PRIMARY KEY,
            type TEXT NOT NULL CHECK (type IN ('text', 'image', 'notify')),
            content TEXT NOT NULL,
            sub_content TEXT,
            source_name TEXT,
            created_at TEXT NOT NULL,
            deleted_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
        """
    )


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] for row in rows}


def _ensure_messages_schema(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table' AND name = 'messages'
        """
    ).fetchone()
    columns = _table_columns(conn, "messages") if row is not None else set()

    if row is None:
        _create_messages_table(conn)
        return
    else:
        table_sql = (row["sql"] or "").lower()
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


def _ensure_deleted_messages_schema(conn: sqlite3.Connection) -> None:
    _create_deleted_messages_table(conn)


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_message_db() -> None:
    with _get_connection() as conn:
        _ensure_messages_schema(conn)
        _ensure_deleted_messages_schema(conn)
        conn.commit()


def _extract_uploaded_filename(content: str) -> str | None:
    if "/uploads/" not in content:
        return None

    filename = content.split("/uploads/", 1)[1].strip()
    return filename or None


def _cleanup_file(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass


def _move_image_to_deleted_uploads(content: str) -> str | None:
    filename = _extract_uploaded_filename(content)
    if not filename:
        return None

    source_path = UPLOADS_DIR / filename
    deleted_path = DELETED_UPLOADS_DIR / filename
    DELETED_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

    if source_path.exists():
        shutil.move(str(source_path), str(deleted_path))
    elif deleted_path.exists():
        return str(deleted_path)

    return str(deleted_path) if deleted_path.exists() else None


def _restore_image_from_deleted_uploads(content: str) -> None:
    filename = _extract_uploaded_filename(content)
    if not filename:
        return

    deleted_path = DELETED_UPLOADS_DIR / filename
    restored_path = UPLOADS_DIR / filename
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

    if deleted_path.exists():
        shutil.move(str(deleted_path), str(restored_path))


def _archive_deleted_message(conn: sqlite3.Connection, row: sqlite3.Row) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO deleted_messages (
            original_id, type, content, sub_content, source_name, created_at, deleted_at
        )
        VALUES (?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
        """,
        (
            row["id"],
            row["type"],
            row["content"],
            row["sub_content"],
            row["source_name"],
            row["created_at"],
        ),
    )


def _trim_deleted_history(conn: sqlite3.Connection) -> None:
    rows_to_remove = conn.execute(
        """
        SELECT original_id, type, content
        FROM deleted_messages
        WHERE original_id NOT IN (
            SELECT original_id
            FROM deleted_messages
            ORDER BY datetime(deleted_at) DESC, original_id DESC
            LIMIT ?
        )
        """,
        (DELETED_MESSAGES_LIMIT,),
    ).fetchall()

    if not rows_to_remove:
        return

    for row in rows_to_remove:
        if row["type"] == "image":
            filename = _extract_uploaded_filename(row["content"])
            if filename:
                _cleanup_file(DELETED_UPLOADS_DIR / filename)

    conn.execute(
        """
        DELETE FROM deleted_messages
        WHERE original_id NOT IN (
            SELECT original_id
            FROM deleted_messages
            ORDER BY datetime(deleted_at) DESC, original_id DESC
            LIMIT ?
        )
        """,
        (DELETED_MESSAGES_LIMIT,),
    )


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


def list_deleted_messages() -> list[dict[str, Any]]:
    with _get_connection() as conn:
        rows = conn.execute(
            """
            SELECT original_id, type, content, sub_content, source_name, created_at, deleted_at
            FROM deleted_messages
            ORDER BY datetime(deleted_at) DESC, original_id DESC
            """
        ).fetchall()

    return [_deleted_row_to_dict(row) for row in rows]


def restore_deleted_message(message_id: int) -> dict[str, Any] | None:
    with _get_connection() as conn:
        row = conn.execute(
            """
            SELECT original_id, type, content, sub_content, source_name, created_at, deleted_at
            FROM deleted_messages
            WHERE original_id = ?
            """,
            (message_id,),
        ).fetchone()

        if row is None:
            return None

        conn.execute(
            """
            INSERT INTO messages (id, type, content, sub_content, source_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                row["original_id"],
                row["type"],
                row["content"],
                row["sub_content"],
                row["source_name"],
                row["created_at"],
            ),
        )
        if row["type"] == "image":
            _restore_image_from_deleted_uploads(row["content"])
        conn.execute("DELETE FROM deleted_messages WHERE original_id = ?", (message_id,))
        conn.commit()

    return _deleted_row_to_dict(row)


def delete_all_messages() -> list[dict[str, Any]]:
    with _get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, type, content, sub_content, source_name, created_at
            FROM messages
            """
        ).fetchall()

        messages = [_row_to_dict(row) for row in rows]
        for row in rows:
            _archive_deleted_message(conn, row)
            if row["type"] == "image":
                _move_image_to_deleted_uploads(row["content"])
        conn.execute("DELETE FROM messages")
        _trim_deleted_history(conn)
        conn.commit()

    return messages


def delete_message(message_id: int) -> dict[str, Any] | None:
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
            return None

        message = _row_to_dict(row)
        _archive_deleted_message(conn, row)
        if row["type"] == "image":
            _move_image_to_deleted_uploads(row["content"])
        conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
        _trim_deleted_history(conn)
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


def _deleted_row_to_dict(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {}

    return {
        "id": row["original_id"],
        "type": row["type"],
        "content": row["content"],
        "sub_content": row["sub_content"],
        "source_name": row["source_name"],
        "created_at": row["created_at"],
        "deleted_at": row["deleted_at"],
    }
