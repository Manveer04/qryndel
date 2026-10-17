"""SQLite execution for compiled v0.1.0 read queries."""

from __future__ import annotations

import sqlite3
from typing import Any

from qryndel.errors import UdlError
from qryndel.logical_plan import CompiledQuery


class ExecutionError(UdlError):
    code = "UDL_EXECUTION_ERROR"

    def __init__(self, message: str):
        super().__init__(message, code=self.code)


def execute(compiled: CompiledQuery | str, db_path: str) -> list[dict[str, Any]]:
    """Execute a compiled SELECT query and return dictionaries for its rows."""
    if isinstance(compiled, str):
        sql, params = compiled, []
    else:
        sql, params = compiled.sql, compiled.params
    if not sql.lstrip().upper().startswith("SELECT"):
        raise ExecutionError("v0.1.0 executes read queries only")
    connection = None
    try:
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(sql, params).fetchall()]
    except sqlite3.Error as exc:
        raise ExecutionError(f"SQLite error: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()
