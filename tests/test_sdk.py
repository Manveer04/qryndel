import sqlite3

import pytest

from qryndel.budgets import QueryBudget
from qryndel.sdk import Client, QueryResult


def _database(path):
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE users (id INTEGER, name TEXT, age INTEGER)")
    connection.executemany(
        "INSERT INTO users VALUES (?, ?, ?)",
        [(1, "Alice", 30), (2, "Bob", 17)],
    )
    connection.commit()
    connection.close()


def test_sdk_compiles_without_execution(tmp_path):
    client = Client(str(tmp_path / "unused.db"))

    compiled = client.compile("FROM users WHERE age > $minimum RETURN name", params={"minimum": 18})

    assert compiled.sql == "SELECT name\nFROM users\nWHERE age > ?;"
    assert compiled.params == [18]
