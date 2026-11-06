import sqlite3

import pytest

from qryndel.budgets import BudgetExceededError, QueryBudget, enforce_result_budget
from qryndel.executor import execute
from qryndel.logical_plan import CompiledQuery


def test_result_row_budget_fails_closed(tmp_path):
    db_path = str(tmp_path / "budget.db")
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE users (id INTEGER)")
    connection.executemany("INSERT INTO users VALUES (?)", [(1,), (2,)])
    connection.commit()
    connection.close()

    with pytest.raises(BudgetExceededError, match="max_result_rows=1"):
        execute("SELECT id FROM users ORDER BY id", db_path, QueryBudget(max_result_rows=1))


def test_duration_budget_interrupts_long_query(tmp_path):
    db_path = str(tmp_path / "timeout.db")
    sqlite3.connect(db_path).close()

    with pytest.raises(BudgetExceededError, match="max_duration_ms=1"):
        execute(
            CompiledQuery(
                "WITH RECURSIVE counter(value) AS ("
                "SELECT 1 UNION ALL SELECT value + 1 FROM counter WHERE value < 1000000) "
                "SELECT value FROM counter"
            ),
            db_path,
            QueryBudget(max_duration_ms=1),
        )


def test_result_budget_helper_applies_to_native_adapter_results():
    with pytest.raises(BudgetExceededError, match="max_result_rows=1"):
        enforce_result_budget([{"id": 1}, {"id": 2}], QueryBudget(max_result_rows=1))
