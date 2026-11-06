"""Tests for v29 NULL, MISSING, and EXISTS predicates."""

import json
import sqlite3

from qryndel.backends import get_backend
from qryndel.logical_plan import (build_plan)
from qryndel.parser import parse


def _sqlite_ids(query: str, db_path):
    plan = build_plan(parse(query))
    compiled = get_backend("sqlite").compile(plan)
    return [row["id"] for row in _execute(compiled, db_path)]


def _execute(compiled, db_path):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(compiled.sql, compiled.params)]
    finally:
        connection.close()


def test_sqlite_distinguishes_json_null_from_missing(tmp_path):
    db_path = tmp_path / "documents.db"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE docs (id INTEGER, payload TEXT)")
    connection.executemany(
        "INSERT INTO docs VALUES (?, ?)",
        [
            (1, json.dumps({"email": None})),
            (2, json.dumps({})),
            (3, json.dumps({"email": "user@example.com"})),
        ],
    )
    connection.commit()
    connection.close()

    assert _sqlite_ids("FROM docs WHERE payload.email IS NULL RETURN id", db_path) == [1]
    assert _sqlite_ids("FROM docs WHERE payload.email IS MISSING RETURN id", db_path) == [2]
    assert _sqlite_ids("FROM docs WHERE payload.email EXISTS RETURN id", db_path) == [1, 3]
    assert _sqlite_ids("FROM docs WHERE payload.email IS NOT NULL RETURN id", db_path) == [3]
