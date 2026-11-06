"""Tests for the Qryndel SQLite executor.

Requires a valid test.db (run setup_db.py first).
"""

import pytest
from pathlib import Path

from qryndel.executor import execute, ExecutionError

DB_PATH = str(Path(__file__).parent.parent / "test.db")


@pytest.fixture(scope="module")
def db():
    """Skip all tests if test.db does not exist."""
    db_file = Path(DB_PATH)
    if not db_file.exists():
        pytest.skip("test.db not found. Run python setup_db.py first.")
    return DB_PATH


class TestExecutorV01:
    """Basic queries execute correctly."""

    def test_select_all(self, db):
        rows = execute("SELECT name, email FROM users;", db)
        assert len(rows) == 6
        assert rows[0]["name"] == "Alice"

    def test_where_age(self, db):
        rows = execute("SELECT name FROM users WHERE age > 18;", db)
        assert len(rows) == 4

    def test_where_string(self, db):
        rows = execute("SELECT email FROM users WHERE name = 'Alice';", db)
        assert len(rows) == 1
        assert rows[0]["email"] == "alice@example.com"


class TestExecutorV02:
    """ORDER BY / LIMIT / DISTINCT queries execute correctly (requires test.db with city column)."""

    def test_and_condition(self, db):
        rows = execute(
            "SELECT name FROM users WHERE age > 18 AND city = 'Singapore';", db
        )
        assert len(rows) == 3
        names = {r["name"] for r in rows}
        assert names == {"Alice", "Charlie", "Frank"}

    def test_or_condition(self, db):
        rows = execute(
            "SELECT name FROM users WHERE age > 18 OR city = 'Singapore';", db
        )
        assert len(rows) == 4

    def test_order_by_desc(self, db):
        rows = execute("SELECT name, age FROM users ORDER BY age DESC;", db)
        assert rows[0]["name"] == "Diana"
        assert rows[0]["age"] == 45

    def test_order_by_asc(self, db):
        rows = execute("SELECT name, age FROM users ORDER BY age ASC;", db)
        assert rows[0]["name"] == "Eve"
        assert rows[0]["age"] == 15

    def test_limit(self, db):
        rows = execute("SELECT name FROM users ORDER BY age ASC LIMIT 3;", db)
        assert len(rows) == 3
        assert rows[0]["name"] == "Eve"

    def test_distinct_cities(self, db):
        rows = execute("SELECT DISTINCT city FROM users ORDER BY city;", db)
        cities = [r["city"] for r in rows]
        assert cities == ["Bangkok", "Jakarta", "Kuala Lumpur", "Singapore"]

    def test_nested_parentheses(self, db):
        rows = execute(
            "SELECT name FROM users WHERE age > 18 AND (city = 'Singapore' OR city = 'Kuala Lumpur');",
            db,
        )
        assert len(rows) == 4

    def test_error_on_bad_sql(self, db):
        with pytest.raises(ExecutionError):
            execute("SELECT FROM nonexistent;", db)


class TestExecutorV03:
    """JOIN queries execute correctly."""

    def test_basic_join(self, db):
        rows = execute(
            "SELECT users.name, orders.amount FROM users JOIN orders ON users.id = orders.user_id ORDER BY users.name;",
            db,
        )
        assert len(rows) == 10
        assert rows[0]["name"] == "Alice"

    def test_join_with_where(self, db):
        rows = execute(
            "SELECT users.name, orders.amount FROM users JOIN orders ON users.id = orders.user_id WHERE orders.amount > 100 ORDER BY orders.amount DESC;",
            db,
        )
        assert len(rows) == 2
        assert rows[0]["amount"] == 299.99
        assert rows[1]["amount"] == 199.99

    def test_left_join(self, db):
        rows = execute(
            "SELECT users.name, orders.amount FROM users LEFT JOIN orders ON users.id = orders.user_id ORDER BY users.name;",
            db,
        )
        assert len(rows) == 12
        bob_rows = [r for r in rows if r["name"] == "Bob"]
        assert len(bob_rows) == 1
        assert bob_rows[0]["amount"] is None

    def test_join_with_alias(self, db):
        rows = execute(
            "SELECT u.name, o.amount FROM users AS u JOIN orders AS o ON u.id = o.user_id ORDER BY u.name;",
            db,
        )
        assert len(rows) == 10


class TestExecutorV04:
    """nested/document field paths execute correctly (full pipeline)."""

    def _run_qryndel(self, qryndel_text: str, db_path: str):
        """Helper: run a Qryndel query end-to-end and return results."""
        from qryndel.parser import parse
        from qryndel.logical_plan import build_plan
        from qryndel.backends import get_backend
        ast = parse(qryndel_text)
        plan = build_plan(ast)
        compiler = get_backend("sqlite")
        compiled = compiler.compile(plan)
        return execute(compiled, db_path)

    def test_nested_return(self, db):
        rows = self._run_qryndel("FROM users RETURN name, profile.address.city", db)
        assert len(rows) == 6
        cities = {r["profile.address.city"] for r in rows}
        assert cities == {"Singapore", "Jakarta", "Kuala Lumpur", "Bangkok"}

    def test_nested_where_bool(self, db):
        rows = self._run_qryndel(
            "FROM users WHERE settings.notifications.email = true RETURN name", db
        )
        assert len(rows) == 4

    def test_nested_where_value(self, db):
        rows = self._run_qryndel(
            'FROM users WHERE profile.address.city = "Singapore" RETURN name', db
        )
        assert len(rows) == 3
        names = {r["name"] for r in rows}
        assert names == {"Alice", "Charlie", "Frank"}

    def test_nested_order_by(self, db):
        rows = self._run_qryndel(
            "FROM users RETURN name, profile.bio ORDER BY profile.bio", db
        )
        assert len(rows) == 6
        assert rows[0]["profile.bio"] == "Designer"

    def test_nested_with_alias(self, db):
        rows = self._run_qryndel(
            "FROM users AS u RETURN u.name, u.profile.address.city", db
        )
        assert len(rows) == 6
