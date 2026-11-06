"""Golden tests for the v28 language-semantics contract."""

import sqlite3

import pytest

from qryndel.ast_nodes import AndCondition, NotCondition, OrCondition
from qryndel.backends import get_backend
from qryndel.backends.sqlite import ParameterError
from qryndel.executor import execute
from qryndel.logical_plan import build_plan
from qryndel.parser import parse


def test_boolean_precedence_is_not_and_or():
    query = parse(
        "FROM users WHERE NOT age > 18 OR city = 'Singapore' AND age < 40 "
        "RETURN name"
    )

    expression = query.where
    assert isinstance(expression, OrCondition)
    assert isinstance(expression.left, NotCondition)
    assert isinstance(expression.right, AndCondition)


def test_parentheses_override_boolean_precedence():
    query = parse(
        "FROM users WHERE (age > 18 OR city = 'Singapore') AND active = TRUE "
        "RETURN name"
    )

    expression = query.where
    assert isinstance(expression, AndCondition)
    assert isinstance(expression.left, OrCondition)


def test_boolean_literals_are_typed_values():
    query = parse("FROM flags WHERE enabled = TRUE RETURN name")

    assert query.where.value is True


def test_missing_parameter_is_rejected_before_execution():
    plan = build_plan(parse("FROM users WHERE name = $name RETURN name"))

    with pytest.raises(ParameterError, match=r"missing parameter: \$name"):
        get_backend("sqlite").compile(plan)


def test_null_parameter_uses_sql_unknown_in_where(tmp_path):
    db_path = tmp_path / "nulls.db"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE values_table (id INTEGER, value TEXT)")
    connection.executemany(
        "INSERT INTO values_table VALUES (?, ?)",
        [(1, None), (2, "present")],
    )
    connection.commit()
    connection.close()

    plan = build_plan(parse(
        "FROM values_table WHERE value = $value RETURN id"
    ))
    compiled = get_backend("sqlite").compile(plan, parameters={"value": None})

    assert execute(compiled, str(db_path)) == []


def test_explicit_null_ordering_is_preserved_by_sqlite():
    plan = build_plan(parse(
        "FROM users RETURN name ORDER BY age ASC NULLS LAST"
    ))
    compiled = get_backend("sqlite").compile(plan)

    assert "ORDER BY age ASC NULLS LAST" in compiled.sql
