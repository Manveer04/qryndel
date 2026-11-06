"""End-to-end tests for behaviour added or fixed in the v0.2.0 public release."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from qryndel import BudgetExceededError, Client, QueryBudget, SafetyError, UdlError
from qryndel.backends.sqlite import ParameterError
from qryndel.executor import ExecutionError
from qryndel.parser import ParseError, parse
from qryndel.semantic import SemanticError

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "shop.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, age INTEGER, city TEXT, nickname TEXT);
        CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER, amount REAL);
        INSERT INTO users VALUES
            (1, 'Alice', 30, 'Singapore', 'al'),
            (2, 'Bob', 17, 'Jakarta', NULL),
            (3, 'Cara', 22, 'Singapore', NULL),
            (4, 'Dana', 45, 'Kuala Lumpur', 'dd');
        INSERT INTO orders VALUES
            (1, 1, 20.0), (2, 1, 50.0), (3, 3, 10.0), (4, 4, 99.0), (5, 4, 1.0), (6, 4, 5.0);
        """
    )
    connection.commit()
    connection.close()
    return path


def rows(db, query, **kwargs):
    return Client(str(db)).execute(query, **kwargs).rows


# --- Read queries --------------------------------------------------------

def test_order_by_limit_distinct(db):
    assert rows(db, "FROM users RETURN name ORDER BY age DESC LIMIT 2") == [
        {"name": "Dana"}, {"name": "Alice"},
    ]
    assert rows(db, "FROM users RETURN DISTINCT city ORDER BY city") == [
        {"city": "Jakarta"}, {"city": "Kuala Lumpur"}, {"city": "Singapore"},
    ]


def test_join_with_aliases(db):
    result = rows(
        db,
        "FROM users AS u JOIN orders AS o ON u.id = o.user_id "
        "WHERE o.amount > 40 RETURN u.name, o.amount ORDER BY o.amount",
    )
    assert result == [{"u.name": "Alice", "o.amount": 50.0}, {"u.name": "Dana", "o.amount": 99.0}]


def test_left_join_keeps_unmatched_rows(db):
    result = rows(
        db,
        "FROM users AS u LEFT JOIN orders AS o ON u.id = o.user_id "
        "WHERE o.id IS NULL RETURN u.name",
    )
    assert result == [{"u.name": "Bob"}]


def test_group_by_having_and_aggregate_ordering(db):
    result = rows(
        db,
        "FROM orders RETURN user_id, COUNT(*), SUM(amount) "
        "GROUP BY user_id HAVING COUNT(*) >= 2 ORDER BY COUNT(*) DESC",
    )
    assert result == [
        {"user_id": 4, "COUNT(*)": 3, "SUM(amount)": 105.0},
        {"user_id": 1, "COUNT(*)": 2, "SUM(amount)": 70.0},
    ]


def test_order_by_aggregate_with_schema(db):
    """Regression: v38 crashed resolving an aggregate ORDER BY against a schema."""
    client = Client(str(db), schema={"orders": ["id", "user_id", "amount"]})
    result = client.execute(
        "FROM orders RETURN user_id, MAX(amount) GROUP BY user_id ORDER BY MAX(amount) DESC LIMIT 1"
    )
    assert result.rows == [{"user_id": 4, "MAX(amount)": 99.0}]


def test_having_requires_grouping_or_aggregate(db):
    with pytest.raises(SemanticError, match="HAVING requires"):
        Client(str(db)).compile("FROM orders RETURN user_id HAVING user_id > 1")


def test_comments_are_ignored(db):
    query = "-- adults only\nFROM users\nWHERE age >= 18 -- inclusive\nRETURN name ORDER BY name LIMIT 1"
    assert rows(db, query) == [{"name": "Alice"}]


def test_negative_limit_is_a_parse_error():
    with pytest.raises(ParseError):
        parse("FROM users RETURN name LIMIT -1")


# --- RULE and TYPE -------------------------------------------------------

def test_rule_with_null_predicate(db):
    """Regression: v38 could not expand IS NULL / IS NOT NULL inside a RULE body."""
    query = (
        "RULE has_nickname(x) = x.nickname IS NOT NULL\n"
        "FROM users AS u WHERE has_nickname(u) RETURN name ORDER BY name"
    )
    assert rows(db, query) == [{"name": "Alice"}, {"name": "Dana"}]


def test_type_declarations_are_enforced_by_the_sdk(db):
    """Regression: v38's Client ignored TYPE declarations in the program."""
    declared = "TYPE users {\n  id: ID\n  name: Text required\n  age: Number\n}\n"
    with pytest.raises(SemanticError, match="Unknown field 'city'"):
        Client(str(db)).compile(declared + "FROM users RETURN city")
    with pytest.raises(SemanticError, match="missing required field"):
        Client(str(db)).compile(declared + "INSERT INTO users { age: 3 }")
    assert rows(db, declared + "FROM users WHERE age > 40 RETURN name") == [{"name": "Dana"}]


def test_rules_apply_inside_transactions(db):
    query = (
        "RULE minor(x) = x.age < 18\n"
        "BEGIN\n"
        "UPDATE users AS u SET city = 'Minor' WHERE minor(u);\n"
        "DELETE FROM users AS u WHERE minor(u) AND u.city = 'Nowhere';\n"
        "COMMIT"
    )
    result = Client(str(db)).execute(query)
    assert result.transaction == "COMMIT"
    assert result.rows_affected == 1
    assert rows(db, "FROM users WHERE city = 'Minor' RETURN name") == [{"name": "Bob"}]


# --- Writes and safety ---------------------------------------------------

def test_insert_update_delete_results(db):
    client = Client(str(db))
    inserted = client.execute("INSERT INTO users { name: $name, age: 50, city: 'Bangkok' }", params={"name": "Eve"})
    assert inserted.is_write and inserted.rows_affected == 1 and inserted.last_insert_id == 5
    assert client.execute("UPDATE users SET age = 51 WHERE name = 'Eve'").rows_affected == 1
    assert client.execute("DELETE FROM users WHERE name = 'Eve'").rows_affected == 1
    assert inserted.rows == []


@pytest.mark.parametrize("query", ["UPDATE users SET age = 1", "DELETE FROM users"])
def test_unbounded_writes_are_rejected(db, query):
    with pytest.raises(SemanticError, match="without WHERE"):
        Client(str(db)).execute(query)


def test_read_only_client_blocks_writes(db):
    client = Client(str(db), read_only=True)
    assert client.execute("FROM users WHERE id = 1 RETURN name").rows == [{"name": "Alice"}]
    with pytest.raises(SafetyError, match="read-only"):
        client.execute("DELETE FROM users WHERE id = 1")
    assert rows(db, "FROM users WHERE id = 1 RETURN name") == [{"name": "Alice"}]


def test_allowed_tables_limit_writes(db):
    client = Client(str(db), allowed_tables=["orders"])
    assert client.execute("DELETE FROM orders WHERE id = 6").rows_affected == 1
    with pytest.raises(SafetyError, match="not permitted: users"):
        client.execute("DELETE FROM users WHERE id = 1")


def test_rollback_transaction_leaves_data_unchanged(db):
    result = Client(str(db)).execute(
        "BEGIN; UPDATE users SET age = 0 WHERE id = 1; DELETE FROM orders WHERE user_id = 1; ROLLBACK"
    )
    assert result.transaction == "ROLLBACK"
    assert result.rows_affected == 3
    assert rows(db, "FROM users WHERE id = 1 RETURN age") == [{"age": 30}]
    assert len(rows(db, "FROM orders WHERE user_id = 1 RETURN id")) == 2


def test_failed_transaction_rolls_back_every_statement(db):
    with pytest.raises(ExecutionError, match="no such column"):
        Client(str(db)).execute(
            "BEGIN; UPDATE users SET age = 99 WHERE id = 1; UPDATE users SET missing = 1 WHERE id = 2; COMMIT"
        )
    assert rows(db, "FROM users WHERE id = 1 RETURN age") == [{"age": 30}]


def test_affected_row_budget_rolls_back(db):
    with pytest.raises(BudgetExceededError, match="max_affected_rows=1"):
        Client(str(db)).execute(
            "UPDATE users SET city = 'X' WHERE age > 18", budget={"max_affected_rows": 1}
        )
    assert rows(db, "FROM users WHERE city = 'X' RETURN name") == []


def test_result_row_budget(db):
    with pytest.raises(BudgetExceededError, match="max_result_rows=2"):
        Client(str(db)).execute("FROM users RETURN name", budget=QueryBudget(max_result_rows=2))
    with pytest.raises(ValueError, match="unknown budget keys"):
        Client(str(db)).execute("FROM users RETURN name", budget={"rows": 2})


def test_preview_does_not_change_data(db):
    report = Client(str(db)).preview("DELETE FROM orders WHERE amount < $limit", params={"limit": 15})
    assert (report.operation, report.estimated_rows, report.mutates_data) == ("DELETE", 3, False)
    assert len(rows(db, "FROM orders RETURN id")) == 6


# --- Diagnostics ---------------------------------------------------------

def test_explain_redacts_parameter_values(db):
    report = Client(str(db)).explain("FROM users WHERE name = $secret RETURN id", params={"secret": "hunter2"})
    assert report.safety == "read-only"
    assert report.compiled["params"] == ["<redacted>"]
    assert "hunter2" not in json.dumps(report.to_dict())


def test_explain_works_without_parameter_values(db):
    report = Client(str(db)).explain("UPDATE users SET age = $age WHERE id = $id")
    assert report.safety == "mutation"
    assert report.compiled["sql"].startswith("UPDATE users")


def test_missing_parameter_is_structured(db):
    with pytest.raises(ParameterError) as info:
        Client(str(db)).compile("FROM users WHERE age > $minimum RETURN name")
    assert isinstance(info.value, UdlError) and isinstance(info.value, ValueError)
    assert info.value.code == "UDL_PARAMETER_ERROR"


def test_missing_database_file_is_not_created(tmp_path):
    missing = tmp_path / "nope.db"
    with pytest.raises(ExecutionError, match="unable to open"):
        Client(str(missing)).execute("FROM users RETURN name")
    assert not missing.exists()


def test_execution_metrics_are_reported(db):
    result = Client(str(db)).execute("FROM users RETURN name")
    assert result.metrics.rows_returned == 4
    assert result.metrics.rows_affected == 0


# --- CLI -----------------------------------------------------------------

def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "qryndel.cli", *args],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )


def test_cli_json_output(db):
    completed = run_cli("--db", str(db), "--json", "FROM users WHERE age < 20 RETURN name")
    assert completed.returncode == 0
    assert json.loads(completed.stdout) == [{"name": "Bob"}]


def test_cli_explain_and_preview(db):
    explained = run_cli("--db", str(db), "--explain", "DELETE FROM users WHERE age < $n", "--param", "$n=20")
    assert "Safety: mutation" in explained.stdout and "<redacted>" in explained.stdout
    previewed = run_cli("--db", str(db), "--preview", "DELETE FROM users WHERE age < 20")
    assert "1 rows would be affected" in previewed.stdout
    assert rows(db, "FROM users WHERE age < 20 RETURN name") == [{"name": "Bob"}]


def test_cli_errors_are_readable(db):
    completed = run_cli("--db", str(db), "--read-only", "DELETE FROM users WHERE id = 1")
    assert completed.returncode == 1
    assert completed.stderr.startswith("error[UDL_SAFETY_BLOCKED]")
    assert "Traceback" not in completed.stderr


def test_cli_budget_flags(db):
    completed = run_cli("--db", str(db), "--max-rows", "1", "FROM users RETURN name")
    assert completed.returncode == 1
    assert "UDL_BUDGET_EXCEEDED" in completed.stderr


def test_cli_runs_transaction_file(db, tmp_path):
    script = tmp_path / "move.qryndel"
    script.write_text("BEGIN\nUPDATE users SET city = 'Hanoi' WHERE id = 2;\nDELETE FROM orders WHERE user_id = 2;\nCOMMIT\n")
    completed = run_cli("--db", str(db), str(script))
    assert completed.stdout.strip() == "Transaction COMMIT: 1 rows affected"


# --- Shipped examples ----------------------------------------------------

EXAMPLES = sorted((PROJECT_ROOT / "examples").glob("*.qryndel"))


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda path: path.name)
def test_shipped_examples_compile(example):
    from setup_db import DB_PATH

    query = example.read_text(encoding="utf-8")
    params = {"minimum": 18} if "$minimum" in query else None
    Client(str(DB_PATH)).compile(query, params=params)
