import sqlite3

import pytest

from qryndel.backends import get_backend
from qryndel.logical_plan import build_plan, build_update_plan
from qryndel.parser import parse, parse_program
from qryndel.sdk import Client
from qryndel.safety import (
    MutationPolicy,
    SafetyError,
    assess_mutation,
    enforce_mutation_policy,
    preview_mutation,
)


def test_read_only_policy_blocks_mutations():
    plan = build_update_plan(
        parse_program("UPDATE users SET age = 20 WHERE id = 1").statement
    )

    with pytest.raises(SafetyError, match="read-only"):
        enforce_mutation_policy(plan, MutationPolicy(read_only=True))


def test_policy_blocks_unbounded_mutations():
    plan = build_update_plan(parse_program("UPDATE users SET age = 20").statement)
    assessment = assess_mutation(plan, MutationPolicy(read_only=False))

    assert not assessment.allowed
    assert "requires WHERE" in assessment.reasons[0]


def test_preview_counts_without_modifying_database(tmp_path):
    db_path = str(tmp_path / "preview.db")
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE users (id INTEGER, age INTEGER)")
    connection.executemany("INSERT INTO users VALUES (?, ?)", [(1, 10), (2, 20)])
    connection.commit()
    connection.close()

    report = Client(db_path).preview("UPDATE users SET age = 99 WHERE age > 10")

    assert report.estimated_rows == 1
    assert report.operation == "UPDATE"
    connection = sqlite3.connect(db_path)
    assert connection.execute("SELECT age FROM users WHERE id = 2").fetchone()[0] == 20
    connection.close()


def test_preview_binds_literals_and_parameters_in_order(tmp_path):
    """Regression: v38 paired parameter names with compiled values by position."""
    db_path = str(tmp_path / "mixed-preview.db")
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE users (id INTEGER, age INTEGER, name TEXT)")
    connection.executemany(
        "INSERT INTO users VALUES (?, ?, ?)", [(1, 10, "a"), (2, 20, "b"), (3, 30, "b")]
    )
    connection.commit()
    connection.close()

    report = Client(db_path).preview(
        "DELETE FROM users WHERE age > 15 AND name = $name", params={"name": "b"}
    )

    assert report.estimated_rows == 2


def test_preview_uses_structured_parameterized_plan(tmp_path):
    db_path = str(tmp_path / "parameter-preview.db")
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE users (id INTEGER, age INTEGER)")
    connection.executemany("INSERT INTO users VALUES (?, ?)", [(1, 10), (2, 20)])
    connection.commit()
    connection.close()

    statement = parse_program(
        "UPDATE users SET age = $new_age WHERE id = $user_id"
    ).statement
    plan = build_update_plan(statement)
    compiled = get_backend("sqlite").compile(
        plan,
        parameters={"new_age": 99, "user_id": 2},
    )

    report = preview_mutation(plan, db_path, {"new_age": 99, "user_id": 2})

    assert report.estimated_rows == 1

    plan = build_update_plan(
        parse_program("UPDATE users SET age = 99 WHERE age > 10").statement
    )
    compiler = get_backend("sqlite")
    report = preview_mutation(plan, db_path)

    assert report.estimated_rows == 1
    assert report.mutates_data is False
    connection = sqlite3.connect(db_path)
    assert connection.execute("SELECT age FROM users WHERE id = 2").fetchone()[0] == 20
    connection.close()
