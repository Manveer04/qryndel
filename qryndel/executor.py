"""SQLite execution for compiled UDL statements.

Reads return a list of row dictionaries. Single writes return
``{"rows_affected": n}`` (plus ``"last_insert_id"`` for INSERT). Transactions
return ``{"transaction": "COMMIT" | "ROLLBACK", "statements": [...]}``.

Every write runs inside ``BEGIN IMMEDIATE``, so the write lock is taken up
front and two writers can never deadlock. Budget violations and database
errors roll back before the error is raised, so a failed write never leaves
partial changes behind.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Union

from qryndel.budgets import BudgetExceededError, QueryBudget
from qryndel.errors import UdlError
from qryndel.logical_plan import CompiledQuery, CompiledTransaction
from qryndel.metrics import ExecutionMetrics

#: How long to wait for another connection's lock before giving up.
DEFAULT_BUSY_TIMEOUT_MS = 5000

_FETCH_CHUNK = 1000


class ExecutionError(UdlError):
    """Raised when SQLite cannot execute a compiled statement."""

    code = "UDL_EXECUTION_ERROR"

    def __init__(self, message: str, *, code: str | None = None, suggested_action: str | None = None):
        super().__init__(message, code=code or self.code, suggested_action=suggested_action)


class DatabaseLockedError(ExecutionError):
    """Another connection held the lock for longer than we were willing to wait."""

    code = "UDL_DATABASE_LOCKED"


Compiled = Union[str, CompiledQuery, CompiledTransaction]
ExecutionResult = Union[List[Dict[str, Any]], Dict[str, Any]]


def _connect(
    db_path: str | os.PathLike[str], busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS
) -> sqlite3.Connection:
    """Open an existing database file; never create one as a side effect."""
    uri = Path(db_path).resolve().as_uri() + "?mode=rw"
    connection = sqlite3.connect(
        uri, uri=True, isolation_level=None, timeout=max(busy_timeout_ms, 0) / 1000
    )
    connection.row_factory = sqlite3.Row
    return connection


def _rollback(connection: sqlite3.Connection) -> None:
    if connection.in_transaction:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass


def _encoded_size(value: Any) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":")).encode("utf-8")
    )


def _result_size_bytes(result: Any) -> int:
    return _encoded_size(result)


def _is_lock_error(exc: sqlite3.Error) -> bool:
    text = str(exc).lower()
    return "database is locked" in text or "database is busy" in text or "database table is locked" in text


def _run_write(connection: sqlite3.Connection, statement: CompiledQuery) -> Dict[str, Any]:
    cursor = connection.execute(statement.sql, statement.params)
    result: Dict[str, Any] = {"rows_affected": cursor.rowcount}
    if statement.sql.lstrip().upper().startswith("INSERT"):
        result["last_insert_id"] = cursor.lastrowid
    return result


def _check_affected(affected: int, budget: QueryBudget | None) -> None:
    if budget and budget.max_affected_rows is not None and affected > budget.max_affected_rows:
        raise BudgetExceededError(
            f"write affects {affected} rows, exceeds max_affected_rows={budget.max_affected_rows}"
        )


def _run_read(
    connection: sqlite3.Connection, sql: str, params: list[Any], budget: QueryBudget | None
) -> tuple[list[dict[str, Any]], int | None]:
    """Fetch rows, stopping as soon as a row or byte budget is exceeded.

    Returns the rows and their encoded size (``None`` unless a byte budget
    asked for it: measuring is not free on large results).
    """
    cursor = connection.execute(sql, params)
    max_rows = budget.max_result_rows if budget else None
    max_bytes = budget.max_result_bytes if budget else None
    if max_rows is None and max_bytes is None:
        return [dict(row) for row in cursor.fetchall()], None

    rows: list[dict[str, Any]] = []
    size = 2  # the surrounding [ ]
    while True:
        chunk = cursor.fetchmany(_FETCH_CHUNK)
        if not chunk:
            break
        converted = [dict(row) for row in chunk]
        separator = 1 if rows else 0  # the comma between chunks
        rows.extend(converted)
        if max_rows is not None and len(rows) > max_rows:
            raise BudgetExceededError(f"result exceeds max_result_rows={max_rows}")
        if max_bytes is not None:
            size += _encoded_size(converted) - 2 + separator
            if size > max_bytes:
                raise BudgetExceededError(f"result exceeds max_result_bytes={max_bytes}")
    return rows, (size if max_bytes is not None else None)


def execute(
    compiled: Compiled,
    db_path: str | os.PathLike[str],
    budget: QueryBudget | None = None,
    *,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> ExecutionResult:
    """Execute a compiled statement against the SQLite database at *db_path*.

    A raw SQL string is accepted for read-only ``SELECT`` statements only;
    writes must come from the UDL compiler so that safety checks apply.
    ``busy_timeout_ms`` bounds how long to wait for another connection's
    lock; ``budget.max_duration_ms`` also caps it.
    """
    result, _ = _execute(compiled, db_path, budget, busy_timeout_ms)
    return result


def _execute(compiled, db_path, budget, busy_timeout_ms):
    if isinstance(compiled, str):
        if not compiled.lstrip().upper().startswith("SELECT"):
            raise ExecutionError("raw SQL strings may only be SELECT statements")
        compiled = CompiledQuery(sql=compiled, params=[])

    lock_wait_ms = busy_timeout_ms
    if budget and budget.max_duration_ms is not None:
        lock_wait_ms = min(lock_wait_ms, budget.max_duration_ms)

    connection = None
    timed_out = False
    in_transaction = False
    try:
        try:
            connection = _connect(db_path, lock_wait_ms)
        except sqlite3.Error as exc:
            raise ExecutionError(f"SQLite error: {exc}") from exc
        if budget and budget.max_duration_ms is not None:
            deadline = time.monotonic() + budget.max_duration_ms / 1000

            def progress() -> int:
                nonlocal timed_out
                timed_out = time.monotonic() >= deadline
                return 1 if timed_out else 0

            connection.set_progress_handler(progress, 1000)

        if isinstance(compiled, CompiledTransaction):
            connection.execute("BEGIN IMMEDIATE")
            in_transaction = True
            results = [_run_write(connection, statement) for statement in compiled.statements]
            _check_affected(sum(int(item["rows_affected"]) for item in results), budget)
            connection.execute("COMMIT" if compiled.action == "COMMIT" else "ROLLBACK")
            in_transaction = False
            return {"transaction": compiled.action, "statements": results}, None

        if compiled.is_write:
            connection.execute("BEGIN IMMEDIATE")
            in_transaction = True
            result = _run_write(connection, compiled)
            _check_affected(int(result["rows_affected"]), budget)
            connection.execute("COMMIT")
            in_transaction = False
            return result, None

        return _run_read(connection, compiled.sql, compiled.params, budget)

    except BudgetExceededError:
        if connection is not None and in_transaction:
            _rollback(connection)
        raise
    except sqlite3.Error as exc:
        if connection is not None and in_transaction:
            _rollback(connection)
        if budget and budget.max_duration_ms is not None and timed_out:
            raise BudgetExceededError(
                f"query exceeded max_duration_ms={budget.max_duration_ms}"
            ) from exc
        if _is_lock_error(exc):
            raise DatabaseLockedError(
                f"database is locked by another connection (waited {lock_wait_ms} ms)",
                suggested_action="Retry later, keep other transactions short, or enable WAL mode "
                "(PRAGMA journal_mode=WAL) so readers never wait for writers.",
            ) from exc
        raise ExecutionError(f"SQLite error: {exc}") from exc
    except OverflowError as exc:
        if connection is not None and in_transaction:
            _rollback(connection)
        raise ExecutionError(f"value out of range for SQLite: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()


def execute_with_metrics(
    compiled: Compiled,
    db_path: str | os.PathLike[str],
    budget: QueryBudget | None = None,
    *,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> tuple[ExecutionResult, ExecutionMetrics]:
    """Execute and also return aggregate metrics (no row contents captured).

    ``result_bytes`` is only measured when a ``max_result_bytes`` budget is
    set; otherwise it is ``None`` (encoding a million rows just to count bytes
    roughly doubled the cost of large reads).
    """
    started = time.monotonic()
    result, size = _execute(compiled, db_path, budget, busy_timeout_ms)
    rows_affected = 0
    if isinstance(result, dict):
        if "statements" in result:
            rows_affected = sum(int(item.get("rows_affected", 0)) for item in result["statements"])
        else:
            rows_affected = int(result.get("rows_affected", 0))
    return result, ExecutionMetrics(
        backend="sqlite",
        elapsed_ms=(time.monotonic() - started) * 1000,
        rows_returned=len(result) if isinstance(result, list) else 0,
        rows_affected=rows_affected,
        result_bytes=size,
        budget_applied=budget is not None,
    )
