"""Tests for local SQLite transactions."""

import sqlite3

import pytest

from qryndel.backends import get_backend
from qryndel.executor import ExecutionError, execute
from qryndel.logical_plan import build_transaction_plan
from qryndel.parser import parse_program
from qryndel.semantic import validate


def _database(path):
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE accounts (id INTEGER PRIMARY KEY, balance INTEGER)")
    connection.execute("INSERT INTO accounts VALUES (1, 100)")
    connection.execute("INSERT INTO accounts VALUES (2, 50)")
    connection.commit()
    connection.close()


def test_commit_transaction_executes_all_statements(tmp_path):
    db_path = str(tmp_path / "transaction.db")
    _database(db_path)
    text = (
        "BEGIN UPDATE accounts SET balance = 90 WHERE id = 1; "
        "UPDATE accounts SET balance = 60 WHERE id = 2; COMMIT"
    )
    program = parse_program(text)
    assert program.statement.action == "COMMIT"
    validate(program, schema={"accounts": {"id": "Number", "balance": "Number"}})
    compiled = get_backend("sqlite").compile(build_transaction_plan(program.statement))
    result = execute(compiled, db_path)
    assert result["transaction"] == "COMMIT"
    connection = sqlite3.connect(db_path)
    assert connection.execute("SELECT balance FROM accounts ORDER BY id").fetchall() == [(90,), (60,)]
    connection.close()


def test_rollback_transaction_discards_changes(tmp_path):
    db_path = str(tmp_path / "transaction.db")
    _database(db_path)
    text = (
        "BEGIN UPDATE accounts SET balance = 90 WHERE id = 1; "
        "UPDATE accounts SET balance = 60 WHERE id = 2; ROLLBACK"
    )
    program = parse_program(text)
    compiled = get_backend("sqlite").compile(build_transaction_plan(program.statement))
    result = execute(compiled, db_path)
    assert result["transaction"] == "ROLLBACK"
    connection = sqlite3.connect(db_path)
    assert connection.execute("SELECT balance FROM accounts ORDER BY id").fetchall() == [(100,), (50,)]
    connection.close()


def test_failed_transaction_is_atomic(tmp_path):
    db_path = str(tmp_path / "transaction.db")
    _database(db_path)
    text = (
        "BEGIN UPDATE accounts SET balance = 90 WHERE id = 1; "
        "UPDATE missing SET balance = 60 WHERE id = 2; COMMIT"
    )
    program = parse_program(text)
    compiled = get_backend("sqlite").compile(build_transaction_plan(program.statement))
    with pytest.raises(ExecutionError):
        execute(compiled, db_path)
    connection = sqlite3.connect(db_path)
    assert connection.execute("SELECT balance FROM accounts WHERE id = 1").fetchone() == (100,)
    connection.close()
