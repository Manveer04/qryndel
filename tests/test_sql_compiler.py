"""Tests for the parameterized SQLite compiler."""

import pytest
from qryndel.parser import parse
from qryndel.logical_plan import build_plan, CompiledQuery
from qryndel.backends import get_backend


def compile_to_sql(plan):
    return get_backend("sqlite").compile(plan).sql
from qryndel.backends import get_backend
from qryndel.semantic import validate
from qryndel.ast_nodes import FieldPath
from qryndel.logical_plan import Project, Scan


def _compile(qryndel_text: str) -> str:
    """Helper: parse, build plan, compile to SQL (no semantic validation)."""
    ast = parse(qryndel_text)
    plan = build_plan(ast)
    return compile_to_sql(plan)


def _compiled(qryndel_text: str) -> CompiledQuery:
    """Helper: parse, build plan, return CompiledQuery."""
    ast = parse(qryndel_text)
    plan = build_plan(ast)
    compiler = get_backend("sqlite")
    return compiler.compile(plan)


class TestSqlCompilerV01:
    """Basic SQL output still correct (parameterized)."""

    def test_simple_select(self):
        sql = _compile("FROM users RETURN name, email")
        assert sql == "SELECT name, email\nFROM users;"

    def test_where(self):
        sql = _compile("FROM users WHERE age > 18 RETURN name")
        assert sql == "SELECT name\nFROM users\nWHERE age > ?;"

    def test_where_string(self):
        sql = _compile('FROM users WHERE name = "Alice" RETURN email')
        assert sql == "SELECT email\nFROM users\nWHERE name = ?;"

    def test_where_not_equal(self):
        sql = _compile("FROM users WHERE age != 30 RETURN name")
        assert "age != ?" in sql


class TestSqlCompilerV02:
    """ORDER BY / LIMIT / DISTINCT SQL output (parameterized)."""

    def test_and_condition(self):
        sql = _compile('FROM users WHERE age > 18 AND city = "Singapore" RETURN name')
        assert "WHERE (age > ? AND city = ?)" in sql

    def test_or_condition(self):
        sql = _compile('FROM users WHERE age > 18 OR city = "Singapore" RETURN name')
        assert "WHERE (age > ? OR city = ?)" in sql

    def test_not_condition(self):
        sql = _compile('FROM users WHERE NOT city = "Singapore" RETURN name')
        assert "WHERE (NOT city = ?)" in sql

    def test_nested_parentheses(self):
        sql = _compile(
            'FROM users WHERE age > 18 AND (city = "Singapore" OR city = "Kuala Lumpur") RETURN name'
        )
        assert (
            "WHERE (age > ? AND (city = ? OR city = ?))" in sql
        )

    def test_order_by_asc(self):
        sql = _compile("FROM users RETURN name ORDER BY age ASC")
        assert "ORDER BY age ASC" in sql

    def test_order_by_desc(self):
        sql = _compile("FROM users RETURN name ORDER BY age DESC")
        assert "ORDER BY age DESC" in sql

    def test_order_by_multiple(self):
        sql = _compile("FROM users RETURN name ORDER BY age DESC, name ASC")
        assert "ORDER BY age DESC, name ASC" in sql

    def test_limit(self):
        sql = _compile("FROM users RETURN name LIMIT 10")
        assert "LIMIT 10" in sql

    def test_distinct(self):
        sql = _compile("FROM users RETURN DISTINCT city")
        assert "SELECT DISTINCT city" in sql

    def test_order_by_and_limit(self):
        sql = _compile("FROM users WHERE age > 18 RETURN name ORDER BY age DESC LIMIT 5")
        expected = "SELECT name\nFROM users\nWHERE age > ?\nORDER BY age DESC\nLIMIT 5;"
        assert sql == expected

    def test_full_feature(self):
        sql = _compile(
            'FROM users WHERE age > 18 AND city = "Singapore" RETURN DISTINCT name ORDER BY name ASC LIMIT 3'
        )
        assert "SELECT DISTINCT name" in sql
        assert "WHERE (age > ? AND city = ?)" in sql
        assert "ORDER BY name ASC" in sql
        assert "LIMIT 3" in sql


class TestSqlCompilerV03:
    """JOIN SQL output (parameterized)."""

    def test_basic_join(self):
        sql = _compile(
            "FROM users JOIN orders ON users.id = orders.user_id RETURN users.name, orders.amount"
        )
        assert "FROM users INNER JOIN orders ON users.id = orders.user_id" in sql

    def test_join_with_where(self):
        sql = _compile(
            "FROM users JOIN orders ON users.id = orders.user_id WHERE orders.amount > 100 RETURN users.name"
        )
        assert "WHERE orders.amount > ?" in sql

    def test_join_with_order_by(self):
        sql = _compile(
            "FROM users JOIN orders ON users.id = orders.user_id RETURN users.name ORDER BY orders.amount DESC"
        )
        assert "ORDER BY orders.amount DESC" in sql

    def test_join_with_limit(self):
        sql = _compile(
            "FROM users JOIN orders ON users.id = orders.user_id RETURN users.name LIMIT 5"
        )
        assert "LIMIT 5" in sql

    def test_join_with_distinct(self):
        sql = _compile(
            "FROM users JOIN orders ON users.id = orders.user_id RETURN DISTINCT users.name"
        )
        assert "SELECT DISTINCT" in sql

    def test_join_with_alias(self):
        sql = _compile(
            "FROM users AS u JOIN orders AS o ON u.id = o.user_id RETURN u.name, o.amount"
        )
        assert "FROM users AS u INNER JOIN orders AS o ON u.id = o.user_id" in sql

    def test_inner_join(self):
        sql = _compile(
            "FROM users INNER JOIN orders ON users.id = orders.user_id RETURN users.name"
        )
        assert "INNER JOIN" in sql

    def test_left_join(self):
        sql = _compile(
            "FROM users LEFT JOIN orders ON users.id = orders.user_id RETURN users.name"
        )
        assert "LEFT JOIN" in sql

    def test_join_full_feature(self):
        sql = _compile(
            "FROM users JOIN orders ON users.id = orders.user_id WHERE orders.amount > 50 RETURN users.name, orders.amount ORDER BY orders.amount DESC LIMIT 3"
        )
        assert "JOIN" in sql
        assert "WHERE orders.amount > ?" in sql
        assert "ORDER BY orders.amount DESC" in sql
        assert "LIMIT 3" in sql


class TestSqlCompilerV04:
    """nested field SQL output (parameterized)."""

    def test_nested_return(self):
        sql = _compile(
            'FROM users WHERE profile.address.city = "Singapore" RETURN name, profile.address.city'
        )
        assert "SELECT name, json_extract(profile, '$.address.city') AS \"profile.address.city\"" in sql
        assert "WHERE json_extract(profile, '$.address.city') = ?" in sql

    def test_nested_return_only(self):
        sql = _compile(
            "FROM users RETURN name, profile.address.city"
        )
        assert "json_extract(profile, '$.address.city') AS \"profile.address.city\"" in sql
        assert "SELECT name," in sql

    def test_nested_order_by(self):
        sql = _compile(
            "FROM users RETURN name, profile.address.city ORDER BY profile.address.city ASC"
        )
        assert "ORDER BY json_extract(profile, '$.address.city') ASC" in sql

    def test_nested_where_only(self):
        sql = _compile(
            'FROM users WHERE profile.address.city = "Singapore" RETURN name'
        )
        assert "WHERE json_extract(profile, '$.address.city') = ?" in sql

    def test_deeply_nested(self):
        sql = _compile(
            "FROM users WHERE settings.notifications.email = true RETURN name, settings.notifications.email"
        )
        assert "json_extract(settings, '$.notifications.email')" in sql


# ===========================================================================
# Parameterization tests
# ===========================================================================

class TestParameterizedQueries:
    """CompiledQuery contains correct SQL and params."""

    def test_where_number_param(self):
        cq = _compiled("FROM users WHERE age > 18 RETURN name")
        assert cq.sql == "SELECT name\nFROM users\nWHERE age > ?;"
        assert cq.params == [18]
        assert not cq.is_write

    def test_where_string_param(self):
        cq = _compiled('FROM users WHERE name = "Alice" RETURN email')
        assert cq.sql == "SELECT email\nFROM users\nWHERE name = ?;"
        assert cq.params == ["Alice"]

    def test_where_multiple_params(self):
        cq = _compiled('FROM users WHERE age > 18 AND city = "Singapore" RETURN name')
        assert cq.params == [18, "Singapore"]

    def test_where_not_param(self):
        cq = _compiled('FROM users WHERE NOT city = "Singapore" RETURN name')
        assert cq.params == ["Singapore"]

    def test_nested_where_param(self):
        cq = _compiled('FROM users WHERE profile.address.city = "Singapore" RETURN name')
        assert "json_extract(profile, '$.address.city') = ?" in cq.sql
        assert cq.params == ["Singapore"]

    def test_bool_param(self):
        cq = _compiled("FROM users WHERE has_license = true RETURN name")
        assert cq.params == [True]

    def test_join_where_param(self):
        cq = _compiled(
            "FROM users JOIN orders ON users.id = orders.user_id WHERE orders.amount > 100 RETURN users.name"
        )
        assert "WHERE orders.amount > ?" in cq.sql
        assert cq.params == [100]

    def test_qualified_fields_after_semantic_validation(self):
        ast = parse(
            "FROM users AS u JOIN orders AS o ON u.id = o.user_id "
            "RETURN u.name, o.amount"
        )
        validate(ast, schema={
            "users": {"id": "Number", "name": "Text"},
            "orders": {"user_id": "Number", "amount": "Number"},
        })
        cq = get_backend("sqlite").compile(build_plan(ast))
        assert 'SELECT u.name AS "u.name", o.amount AS "o.amount"' in cq.sql
        assert "u.u" not in cq.sql
        assert "o.o" not in cq.sql

    def test_order_by_and_limit_no_params(self):
        cq = _compiled("FROM users RETURN name ORDER BY age DESC LIMIT 5")
        assert cq.params == []

    def test_is_write_false_for_select(self):
        cq = _compiled("FROM users RETURN name")
        assert not cq.is_write

    def test_sql_injection_harmless(self):
        """With parameterization, a malicious string is just a value, not SQL."""
        cq = _compiled(
            '''FROM users WHERE name = "Robert'); DROP TABLE users; --" RETURN name'''
        )
        assert "DROP TABLE" not in cq.sql
        assert cq.sql == "SELECT name\nFROM users\nWHERE name = ?;"
        assert cq.params == ["Robert'); DROP TABLE users; --"]


class TestLegacyCompileToSql:
    """The legacy compile_to_sql function still works (returns parameterized SQL)."""

    def test_compile_to_sql_returns_string(self):
        sql = compile_to_sql(build_plan(parse("FROM users RETURN name")))
        assert isinstance(sql, str)
        assert sql == "SELECT name\nFROM users;"


def test_direct_ast_identifier_injection_is_rejected():
    plan = Project(
        columns=[FieldPath(parts=["name; DROP TABLE users;--"])],
        child=Scan("users"),
    )
    with pytest.raises(ValueError, match="Unsafe SQL identifier"):
        get_backend("sqlite").compile(plan)
