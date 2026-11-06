"""Tests for write statements (INSERT / UPDATE / DELETE).

Covers:
  - Parser: parse INSERT, UPDATE, DELETE
  - Semantic: valid writes, unknown table/field, safety rules
  - Logical plan: InsertPlan, UpdatePlan, DeletePlan
  - SQLite compiler: compile INSERT, UPDATE, DELETE
  - Executor: write changes database
  - CLI: inline write commands work
"""

from __future__ import annotations
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

from qryndel.parser import parse_program, parse, ParseError
from qryndel.semantic import validate, ValidationError
from qryndel.logical_plan import (
    CompiledQuery,
    InsertPlan,
    UpdatePlan,
    DeletePlan,
    build_insert_plan,
    build_update_plan,
    build_delete_plan,
)
from qryndel.backends import get_backend
from qryndel.executor import execute, ExecutionError
from qryndel.sdk import Client
from qryndel.ast_nodes import (
    Assignment,
    Condition,
    DeleteStatement,
    FieldPath,
    InsertStatement,
    UpdateStatement,
)

PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = str(PROJECT_ROOT / "test.db")


# ---------------------------------------------------------------------------
# Schema used for validation
# ---------------------------------------------------------------------------

SCHEMA: Dict[str, Any] = {
    "users": {
        "id": "Number",
        "name": "Text",
        "age": "Number",
        "email": "Text",
        "city": "Text",
        "has_license": "Boolean",
    },
    "orders": {
        "id": "Number",
        "user_id": "Number",
        "product": "Text",
        "amount": "Number",
        "quantity": "Number",
    },
}


# ===========================================================================
# Parser tests
# ===========================================================================

class TestParseInsert:
    """Parse INSERT statements."""

    def test_basic_insert(self):
        program = parse_program(
            'INSERT INTO users { name: "Hana", age: 22, email: "hana@test.com", city: "Singapore" }'
        )
        stmt = program.statement
        assert isinstance(stmt, InsertStatement)
        assert stmt.table == "users"
        assert stmt.values["name"] == "Hana"
        assert stmt.values["age"] == 22
        assert stmt.values["email"] == "hana@test.com"
        assert stmt.values["city"] == "Singapore"

    def test_insert_numbers_strings(self):
        program = parse_program(
            "INSERT INTO orders { id: 1, user_id: 1, product: 'Widget', amount: 9.99, quantity: 5 }"
        )
        stmt = program.statement
        assert isinstance(stmt, InsertStatement)
        assert stmt.values["id"] == 1
        assert stmt.values["amount"] == 9.99
        assert stmt.values["product"] == "Widget"

    def test_insert_single_quote_strings(self):
        program = parse_program("INSERT INTO users { name: 'Hana', age: 22 }")
        stmt = program.statement
        assert stmt.values["name"] == "Hana"

    def test_invalid_insert_no_braces(self):
        with pytest.raises(ParseError):
            parse_program("INSERT INTO users name 'Hana'")

    def test_insert_empty(self):
        with pytest.raises(ParseError):
            parse_program("INSERT INTO users {}")


class TestParseUpdate:
    """Parse UPDATE statements."""

    def test_basic_update(self):
        program = parse_program(
            "UPDATE users SET age = 23 WHERE name = 'Hana'"
        )
        stmt = program.statement
        assert isinstance(stmt, UpdateStatement)
        assert stmt.table == "users"
        assert len(stmt.assignments) == 1
        assert stmt.assignments[0].field.parts == ["age"]
        assert stmt.assignments[0].value == 23
        assert isinstance(stmt.where, Condition)

    def test_update_multiple_assignments(self):
        program = parse_program(
            "UPDATE users SET age = 23, city = 'Jakarta' WHERE name = 'Hana'"
        )
        stmt = program.statement
        assert len(stmt.assignments) == 2

    def test_update_no_where_allowed(self):
        program = parse_program("UPDATE users SET age = 23")
        stmt = program.statement
        assert isinstance(stmt, UpdateStatement)
        assert stmt.where is None

    def test_invalid_update(self):
        with pytest.raises((ParseError, ValidationError)):
            program = parse_program("UPDATE users SET age = 23 WHERE")
            validate(program, schema=SCHEMA)


class TestParseDelete:
    """Parse DELETE statements."""

    def test_basic_delete(self):
        program = parse_program("DELETE FROM users WHERE name = 'Hana'")
        stmt = program.statement
        assert isinstance(stmt, DeleteStatement)
        assert stmt.table == "users"
        assert isinstance(stmt.where, Condition)

    def test_delete_no_where_allowed(self):
        program = parse_program("DELETE FROM users")
        stmt = program.statement
        assert isinstance(stmt, DeleteStatement)
        assert stmt.where is None

    def test_invalid_delete(self):
        with pytest.raises(ParseError):
            parse_program("DELETE FROM")


# ===========================================================================
# Semantic validation tests
# ===========================================================================

class TestSemanticInsert:
    """Semantic validation of INSERT statements."""

    def test_valid_insert(self):
        stmt = InsertStatement(
            table="users",
            values={"name": "Hana", "age": 22, "email": "h@t.com", "city": "SG"},
        )
        validate(stmt, schema=SCHEMA)

    def test_unknown_table(self):
        stmt = InsertStatement(table="nonexistent", values={"name": "X"})
        with pytest.raises(ValidationError, match="Unknown source"):
            validate(stmt, schema=SCHEMA)

    def test_unknown_field(self):
        stmt = InsertStatement(
            table="users", values={"name": "Hana", "bogus_field": 99}
        )
        with pytest.raises(ValidationError, match="Unknown field"):
            validate(stmt, schema=SCHEMA)

    def test_duplicate_field(self):
        with pytest.raises(ParseError, match="Duplicate field"):
            parse("INSERT INTO users { name: \"Hana\", name: \"Bob\" }")

    def test_empty_insert_rejected(self):
        stmt = InsertStatement(table="users", values={})
        with pytest.raises(ValidationError, match="requires at least one field"):
            validate(stmt, schema=SCHEMA)


class TestSemanticUpdate:
    """Semantic validation of UPDATE statements."""

    def test_valid_update(self):
        stmt = UpdateStatement(
            table="users",
            assignments=[Assignment(field=FieldPath(parts=["age"]), value=23)],
            where=Condition(field=FieldPath(parts=["name"]), operator="=", value="Hana"),
        )
        validate(stmt, schema=SCHEMA)

    def test_unknown_table(self):
        stmt = UpdateStatement(
            table="nonexistent",
            assignments=[Assignment(field=FieldPath(parts=["age"]), value=23)],
            where=Condition(field=FieldPath(parts=["name"]), operator="=", value="Hana"),
        )
        with pytest.raises(ValidationError, match="Unknown source"):
            validate(stmt, schema=SCHEMA)

    def test_unknown_field_in_set(self):
        stmt = UpdateStatement(
            table="users",
            assignments=[Assignment(field=FieldPath(parts=["bogus"]), value=23)],
            where=Condition(field=FieldPath(parts=["name"]), operator="=", value="Hana"),
        )
        with pytest.raises(ValidationError, match="Unknown field"):
            validate(stmt, schema=SCHEMA)

    def test_duplicate_assignment(self):
        stmt = UpdateStatement(
            table="users",
            assignments=[
                Assignment(field=FieldPath(parts=["age"]), value=23),
                Assignment(field=FieldPath(parts=["age"]), value=24),
            ],
            where=Condition(field=FieldPath(parts=["name"]), operator="=", value="Hana"),
        )
        with pytest.raises(ValidationError, match="Duplicate assignment"):
            validate(stmt, schema=SCHEMA)

    def test_update_without_where_rejected(self):
        stmt = UpdateStatement(
            table="users",
            assignments=[Assignment(field=FieldPath(parts=["age"]), value=23)],
            where=None,
        )
        with pytest.raises(ValidationError, match="UPDATE without WHERE"):
            validate(stmt, schema=SCHEMA)

    def test_update_no_schema_skips_validation(self):
        stmt = UpdateStatement(
            table="users",
            assignments=[Assignment(field=FieldPath(parts=["unknown"]), value=23)],
            where=None,
        )
        with pytest.raises(ValidationError, match="UPDATE without WHERE"):
            validate(stmt)


class TestSemanticDelete:
    """Semantic validation of DELETE statements."""

    def test_valid_delete(self):
        stmt = DeleteStatement(
            table="users",
            where=Condition(field=FieldPath(parts=["name"]), operator="=", value="Hana"),
        )
        validate(stmt, schema=SCHEMA)

    def test_unknown_table(self):
        stmt = DeleteStatement(
            table="nonexistent",
            where=Condition(field=FieldPath(parts=["name"]), operator="=", value="Hana"),
        )
        with pytest.raises(ValidationError, match="Unknown source"):
            validate(stmt, schema=SCHEMA)

    def test_delete_without_where_rejected(self):
        stmt = DeleteStatement(table="users", where=None)
        with pytest.raises(ValidationError, match="DELETE without WHERE"):
            validate(stmt, schema=SCHEMA)


# ===========================================================================
# Logical plan tests
# ===========================================================================

class TestInsertPlan:
    """Building InsertPlan from InsertStatement."""

    def test_insert_plan(self):
        stmt = InsertStatement(
            table="users", values={"name": "Hana", "age": 22}
        )
        plan = build_insert_plan(stmt)
        assert isinstance(plan, InsertPlan)
        assert plan.table == "users"
        assert plan.values == {"name": "Hana", "age": 22}


class TestUpdatePlan:
    """Building UpdatePlan from UpdateStatement."""

    def test_update_plan(self):
        stmt = UpdateStatement(
            table="users",
            assignments=[Assignment(field=FieldPath(parts=["age"]), value=23)],
            where=Condition(field=FieldPath(parts=["name"]), operator="=", value="Hana"),
        )
        plan = build_update_plan(stmt)
        assert isinstance(plan, UpdatePlan)
        assert plan.table == "users"
        assert len(plan.assignments) == 1
        assert plan.condition is not None

    def test_update_plan_no_where(self):
        stmt = UpdateStatement(
            table="users",
            assignments=[Assignment(field=FieldPath(parts=["age"]), value=23)],
            where=None,
        )
        plan = build_update_plan(stmt)
        assert plan.condition is None


class TestDeletePlan:
    """Building DeletePlan from DeleteStatement."""

    def test_delete_plan(self):
        stmt = DeleteStatement(
            table="users",
            where=Condition(field=FieldPath(parts=["name"]), operator="=", value="Hana"),
        )
        plan = build_delete_plan(stmt)
        assert isinstance(plan, DeletePlan)
        assert plan.table == "users"
        assert plan.condition is not None

    def test_delete_plan_no_where(self):
        stmt = DeleteStatement(table="users", where=None)
        plan = build_delete_plan(stmt)
        assert plan.condition is None


# ===========================================================================
# SQLite compiler tests
# ===========================================================================

class TestSqliteCompileInsert:
    """SQLite compilation of InsertPlan (parameterized)."""

    def test_compile_insert(self):
        plan = InsertPlan(
            table="users",
            values={"name": "Hana", "age": 22, "email": "hana@test.com", "city": "SG"},
        )
        compiler = get_backend("sqlite")
        cq = compiler.compile(plan)
        assert isinstance(cq, CompiledQuery)
        assert cq.is_write
        assert "INSERT INTO users" in cq.sql
        assert "name, age, email, city" in cq.sql or "name" in cq.sql
        assert "?" in cq.sql
        assert "'Hana'" not in cq.sql
        assert cq.params == ["Hana", 22, "hana@test.com", "SG"]


class TestSqliteCompileUpdate:
    """SQLite compilation of UpdatePlan (parameterized)."""

    def test_compile_update(self):
        plan = UpdatePlan(
            table="users",
            assignments=[Assignment(field=FieldPath(parts=["age"]), value=23)],
            condition=Condition(field=FieldPath(parts=["name"]), operator="=", value="Hana"),
        )
        compiler = get_backend("sqlite")
        cq = compiler.compile(plan)
        assert isinstance(cq, CompiledQuery)
        assert cq.is_write
        assert "UPDATE users" in cq.sql
        assert "SET age = ?" in cq.sql
        assert "WHERE name = ?" in cq.sql
        assert cq.params == [23, "Hana"]

    def test_compile_update_multiple(self):
        plan = UpdatePlan(
            table="users",
            assignments=[
                Assignment(field=FieldPath(parts=["age"]), value=23),
                Assignment(field=FieldPath(parts=["city"]), value="Jakarta"),
            ],
            condition=Condition(field=FieldPath(parts=["name"]), operator="=", value="Hana"),
        )
        compiler = get_backend("sqlite")
        cq = compiler.compile(plan)
        assert "age = ?" in cq.sql
        assert "city = ?" in cq.sql
        assert cq.params == [23, "Jakarta", "Hana"]

    def test_compile_update_alias(self):
        stmt = UpdateStatement(
            table="users",
            alias="u",
            assignments=[Assignment(field=FieldPath(parts=["u", "age"]), value=23)],
            where=Condition(field=FieldPath(parts=["u", "name"]), operator="=", value="Hana"),
        )
        cq = get_backend("sqlite").compile(build_update_plan(stmt))
        assert "UPDATE users AS u" in cq.sql
        assert "SET age = ?" in cq.sql
        assert "WHERE u.name = ?" in cq.sql


class TestSqliteCompileDelete:
    """SQLite compilation of DeletePlan (parameterized)."""

    def test_compile_delete(self):
        plan = DeletePlan(
            table="users",
            condition=Condition(field=FieldPath(parts=["name"]), operator="=", value="Hana"),
        )
        compiler = get_backend("sqlite")
        cq = compiler.compile(plan)
        assert isinstance(cq, CompiledQuery)
        assert cq.is_write
        assert "DELETE FROM users" in cq.sql
        assert "WHERE name = ?" in cq.sql
        assert cq.params == ["Hana"]


# ===========================================================================
# Executor tests
# ===========================================================================

class TestWriteExecutor:
    """Write operations execute correctly against SQLite through the SDK."""

    @pytest.fixture
    def client(self, tmp_path):
        path = tmp_path / "writes.db"
        shutil.copy(DB_PATH, path)
        return Client(str(path))

    def test_insert_update_delete_round_trip(self, client):
        inserted = client.execute(
            "INSERT INTO users { name: 'WriteTestUser', age: 30, email: 'wt@test.com', city: 'TestVille' }"
        )
        assert inserted.rows_affected == 1
        assert inserted.last_insert_id is not None
        assert client.execute(
            "FROM users WHERE name = 'WriteTestUser' RETURN name"
        ).rows == [{"name": "WriteTestUser"}]

        updated = client.execute("UPDATE users SET age = 31 WHERE name = 'WriteTestUser'")
        assert updated.rows_affected == 1
        assert client.execute(
            "FROM users WHERE name = 'WriteTestUser' RETURN age"
        ).rows == [{"age": 31}]

        deleted = client.execute("DELETE FROM users WHERE name = 'WriteTestUser'")
        assert deleted.rows_affected == 1
        assert client.execute("FROM users WHERE name = 'WriteTestUser' RETURN name").rows == []

    def test_raw_sql_writes_are_refused(self):
        with pytest.raises(ExecutionError, match="only be SELECT"):
            execute("DELETE FROM users WHERE 1 = 1;", DB_PATH)

    def test_select_still_works(self):
        rows = execute("SELECT name, email FROM users ORDER BY name LIMIT 1;", DB_PATH)
        assert isinstance(rows, list)
        assert len(rows) == 1


# ===========================================================================
# Backend rejection tests
# ===========================================================================

class TestBackendWrites:
    """The SQLite backend compiles write plans."""

    def test_sqlite_accepts_writes(self):
        compiler = get_backend("sqlite")
        plan = InsertPlan(table="users", values={"name": "Test"})
        cq = compiler.compile(plan)
        assert isinstance(cq, CompiledQuery)
        assert cq.is_write
        assert "INSERT INTO" in cq.sql
        assert cq.params == ["Test"]


# ===========================================================================
# CLI tests
# ===========================================================================

class TestCliWrites:
    """CLI handles write statements against a scratch copy of the fixture."""

    def _run_cli(self, db: Path, *args: str) -> str:
        result = subprocess.run(
            [sys.executable, "-m", "qryndel.cli", "--db", str(db), *args],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
        )
        return result.stdout + result.stderr

    def test_cli_insert_update_delete(self, tmp_path):
        db = tmp_path / "cli.db"
        shutil.copy(DB_PATH, db)
        assert self._run_cli(
            db, "INSERT INTO users { name: 'CliTest', age: 25, email: 'cli@test.com', city: 'CL' }"
        ).strip() == "Rows affected: 1"
        assert self._run_cli(
            db, "UPDATE users SET age = 26 WHERE name = 'CliTest'"
        ).strip() == "Rows affected: 1"
        assert self._run_cli(
            db, "DELETE FROM users WHERE name = 'CliTest'"
        ).strip() == "Rows affected: 1"

    def test_cli_rejects_unbounded_delete(self, tmp_path):
        db = tmp_path / "cli.db"
        shutil.copy(DB_PATH, db)
        output = self._run_cli(db, "DELETE FROM users")
        assert "error[UDL_SEMANTIC_ERROR]" in output
        assert "DELETE without WHERE" in output
