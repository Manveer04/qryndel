import sqlite3

import pytest

from qryndel.budgets import BudgetExceededError, QueryBudget
from qryndel.sdk import Client


def test_write_budget_rolls_back_oversized_update(tmp_path):
    path = str(tmp_path / "writes.db")
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE users (age INTEGER)")
    connection.executemany("INSERT INTO users VALUES (?)", [(1,), (2,)])
    connection.commit()
    connection.close()

    with pytest.raises(BudgetExceededError, match="max_affected_rows"):
        Client(path).execute(
            "UPDATE users SET age = 3 WHERE age > 0",
            budget=QueryBudget(max_affected_rows=1),
        )

    connection = sqlite3.connect(path)
    assert connection.execute("SELECT age FROM users ORDER BY age").fetchall() == [(1,), (2,)]
    connection.close()
