"""v32 fault-injection coverage for rollback and adapter failures."""

import sqlite3

import pytest

from qryndel.backends import get_backend
from qryndel.budgets import QueryBudget
from qryndel.executor import ExecutionError, execute
from qryndel.logical_plan import build_transaction_plan
from qryndel.parser import parse_program


def test_constraint_failure_rolls_back_prior_transaction_writes(tmp_path):
    path = str(tmp_path / "constraint.db")
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE accounts (id INTEGER PRIMARY KEY, balance INTEGER)")
    connection.execute("INSERT INTO accounts VALUES (1, 100)")
    connection.commit()
    connection.close()

    program = parse_program(
        "BEGIN UPDATE accounts SET balance = 90 WHERE id = 1; "
        "INSERT INTO accounts { id: 1, balance: 50 }; COMMIT"
    )
    compiled = get_backend("sqlite").compile(build_transaction_plan(program.statement))

    with pytest.raises(ExecutionError, match="SQLite error"):
        execute(compiled, path)

    connection = sqlite3.connect(path)
    assert connection.execute("SELECT * FROM accounts").fetchall() == [(1, 100)]
    connection.close()


def test_budget_failure_rolls_back_transaction(tmp_path):
    path = str(tmp_path / "budget.db")
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE accounts (id INTEGER PRIMARY KEY, balance INTEGER)")
    connection.executemany("INSERT INTO accounts VALUES (?, ?)", [(1, 100), (2, 50)])
    connection.commit()
    connection.close()

    program = parse_program(
        "BEGIN UPDATE accounts SET balance = 90 WHERE id = 1; "
        "UPDATE accounts SET balance = 60 WHERE id = 2; COMMIT"
    )
    compiled = get_backend("sqlite").compile(build_transaction_plan(program.statement))

    with pytest.raises(Exception, match="max_affected_rows"):
        execute(compiled, path, budget=QueryBudget(max_affected_rows=1))

    connection = sqlite3.connect(path)
    assert connection.execute("SELECT balance FROM accounts ORDER BY id").fetchall() == [(100,), (50,)]
    connection.close()
