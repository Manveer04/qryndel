"""Load a validation schema from an existing SQLite database.

``load_sqlite_schema(path)`` returns the schema shape the semantic checker
uses: ``{table: {column: type_name | open_document}}``. Columns declared as
TEXT, JSON, BLOB or with no type are marked as open JSON documents, so nested
paths such as ``profile.address.city`` stay valid while a typo in a column
name (``platfrom``) is still caught before any SQL runs.

Opening is read-only: the database file is never created or changed.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Any, Dict

from qryndel.executor import ExecutionError, _connect
from qryndel.semantic import OPEN_DOCUMENT

_DOCUMENT_TYPES = ("TEXT", "CHAR", "CLOB", "JSON", "BLOB", "VARCHAR")


def column_type(declared: str) -> str:
    """Map a SQLite declared type to a UDL type name (Number/Boolean/Text)."""
    upper = (declared or "").upper()
    if "BOOL" in upper:
        return "Boolean"
    if "INT" in upper or any(token in upper for token in ("REAL", "FLOA", "DOUB", "NUM", "DEC")):
        return "Number"
    return "Text"


def _is_document(declared: str) -> bool:
    upper = (declared or "").upper()
    return not upper or any(token in upper for token in _DOCUMENT_TYPES)


def load_sqlite_schema(db_path: str | os.PathLike[str]) -> Dict[str, Dict[str, Any]]:
    """Read every table and view's columns from the database at *db_path*."""
    try:
        connection = _connect(db_path)
    except sqlite3.Error as exc:
        raise ExecutionError(f"SQLite error: {exc}") from exc
    try:
        names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        schema: Dict[str, Dict[str, Any]] = {}
        for name in names:
            quoted = '"' + name.replace('"', '""') + '"'
            columns: Dict[str, Any] = {}
            for row in connection.execute(f"PRAGMA table_info({quoted})"):
                column, declared = row[1], row[2]
                if _is_document(declared):
                    columns[column] = {OPEN_DOCUMENT: True}
                else:
                    columns[column] = column_type(declared)
            schema[name] = columns
        return schema
    except sqlite3.Error as exc:
        raise ExecutionError(f"SQLite error: {exc}") from exc
    finally:
        connection.close()


def describe_schema(schema: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, str]]:
    """A plain ``{table: {column: type}}`` view of a loaded schema, for display."""
    return {
        table: {
            column: ("Text" if isinstance(value, dict) else str(value))
            for column, value in columns.items()
        }
        for table, columns in schema.items()
    }
