"""SQLite execution for compiled UDL statements.

Reads return a list of row dictionaries. Single writes return
``{"rows_affected": n}`` (plus ``"last_insert_id"`` for INSERT). Transactions
return ``{"transaction": "COMMIT" | "ROLLBACK", "statements": [...]}``.

Every write runs inside an explicit transaction. Budget violations and
database errors roll back before the error is raised, so a failed write never
leaves partial changes behind.
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


class ExecutionError(UdlError):
    """Raised when SQLite cannot execute a compiled statement."""

    code = "UDL_EXECUTION_ERROR"

    def __init__(self, message: str):
        super().__init__(message, code=self.code)


Compiled = Union[str, CompiledQuery, CompiledTransaction]
ExecutionResult = Union[List[Dict[str, Any]], Dict[str, Any]]


def _connect(db_path: str | os.PathLike[str]) -> sqlite3.Connection:
    """Open an existing database file; never create one as a side effect."""
    uri = Path(db_path).resolve().as_uri() + "?mode=rw"
    connection = sqlite3.connect(uri, uri=True, isolation_level=None)
    connection.row_factory = sqlite3.Row
    return connection


def _rollback(connection: sqlite3.Connection) -> None:
    if connection.in_transaction:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass


def _result_size_bytes(result: Any) -> int:
    return len(
        json.dumps(result, ensure_ascii=False, default=str, separators=(",", ":")).encode("utf-8")
    )


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


def _run_read(connection: sqlite3.Connection, sql: str, params: list[Any], budget: QueryBudget | None):
    cursor = connection.execute(sql, params)
    if budget and budget.max_result_rows is not None:
        rows = cursor.fetchmany(budget.max_result_rows + 1)
        if len(rows) > budget.max_result_rows:
            raise BudgetExceededError(f"result exceeds max_result_rows={budget.max_result_rows}")
    else:
        rows = cursor.fetchall()
    result = [dict(row) for row in rows]
    if budget and budget.max_result_bytes is not None:
        if _result_size_bytes(result) > budget.max_result_bytes:
            raise BudgetExceededError(f"result exceeds max_result_bytes={budget.max_result_bytes}")
    return result


def execute(
    compiled: Compiled,
    db_path: str | os.PathLike[str],
    budget: QueryBudget | None = None,
) -> ExecutionResult:
    """Execute a compiled statement against the SQLite database at *db_path*.

    A raw SQL string is accepted for read-only ``SELECT`` statements only;
    writes must come from the UDL compiler so that safety checks apply.
    """
    if isinstance(compiled, str):
        if not compiled.lstrip().upper().startswith("SELECT"):
            raise ExecutionError("raw SQL strings may only be SELECT statements")
        compiled = CompiledQuery(sql=compiled, params=[])

    connection = None
    timed_out = False
    in_transaction = False
    try:
        connection = _connect(db_path)
        if budget and budget.max_duration_ms is not None:
            deadline = time.monotonic() + budget.max_duration_ms / 1000

            def progress() -> int:
                nonlocal timed_out
                timed_out = time.monotonic() >= deadline
                return 1 if timed_out else 0

            connection.set_progress_handler(progress, 1000)

        if isinstance(compiled, CompiledTransaction):
            connection.execute("BEGIN")
            in_transaction = True
            results = [_run_write(connection, statement) for statement in compiled.statements]
            _check_affected(sum(int(item["rows_affected"]) for item in results), budget)
            connection.execute("COMMIT" if compiled.action == "COMMIT" else "ROLLBACK")
            in_transaction = False
            return {"transaction": compiled.action, "statements": results}

        if compiled.is_write:
            connection.execute("BEGIN")
            in_transaction = True
            result = _run_write(connection, compiled)
            _check_affected(int(result["rows_affected"]), budget)
            connection.execute("COMMIT")
            in_transaction = False
            return result

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
        raise ExecutionError(f"SQLite error: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()


def execute_with_metrics(
    compiled: Compiled,
    db_path: str | os.PathLike[str],
    budget: QueryBudget | None = None,
) -> tuple[ExecutionResult, ExecutionMetrics]:
    """Execute and also return aggregate metrics (no row contents captured)."""
    started = time.monotonic()
    result = execute(compiled, db_path, budget=budget)
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
        result_bytes=_result_size_bytes(result),
        budget_applied=budget is not None,
    )
