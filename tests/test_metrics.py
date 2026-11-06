import sqlite3
import pytest

from qryndel.budgets import BudgetExceededError, QueryBudget
from qryndel.executor import execute_with_metrics
from qryndel.metrics import ExecutionMetrics


def test_sqlite_execution_metrics_do_not_capture_rows(tmp_path):
    path = str(tmp_path / "metrics.db")
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE users (name TEXT)")
    connection.executemany("INSERT INTO users VALUES (?)", [("Alice",), ("Bob",)])
    connection.commit()
    connection.close()

    rows, metrics = execute_with_metrics("SELECT name FROM users", path)

    assert rows == [{"name": "Alice"}, {"name": "Bob"}]
    assert isinstance(metrics, ExecutionMetrics)
    assert metrics.backend == "sqlite"
    assert metrics.rows_returned == 2
    assert metrics.rows_affected == 0
    assert "Alice" not in metrics.to_dict().__repr__()


def test_result_bytes_budget_rejects_oversized_transfer(tmp_path):
    path = str(tmp_path / "budget.db")
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE users (name TEXT)")
    connection.execute("INSERT INTO users VALUES ('Alice')")
    connection.commit()
    connection.close()

    with pytest.raises(BudgetExceededError, match="max_result_bytes"):
        execute_with_metrics("SELECT name FROM users", path, QueryBudget(max_result_bytes=5))
