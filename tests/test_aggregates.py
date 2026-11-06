"""Tests for aggregate projections."""

import pytest

from qryndel.ast_nodes import AggregateField
from qryndel.backends import get_backend
from qryndel.logical_plan import (build_plan)
from qryndel.parser import parse
from qryndel.semantic import SemanticError, validate


def test_parse_aggregate_projections():
    query = parse("FROM users RETURN COUNT(*), SUM(age), AVG(age), MIN(age), MAX(age)")
    assert [field.function for field in query.fields] == [
        "COUNT", "SUM", "AVG", "MIN", "MAX"
    ]
    assert all(isinstance(field, AggregateField) for field in query.fields)
    assert query.fields[0].field is None


def test_sqlite_compiles_and_executes_aggregates():
    plan = build_plan(parse("FROM users RETURN COUNT(*), AVG(age), MAX(age)"))
    compiled = get_backend("sqlite").compile(plan)
    assert "SELECT COUNT(*), AVG(age), MAX(age)" in compiled.sql

    from qryndel.executor import execute
    rows = execute(compiled, "test.db")
    assert rows[0]["COUNT(*)"] == 6
    assert rows[0]["MAX(age)"] == 45


def test_aggregate_field_is_semantically_validated():
    query = parse("FROM users RETURN SUM(age)")
    validate(query, schema={"users": {"age": "Number"}})

    invalid = parse("FROM users RETURN SUM(missing)")
    with pytest.raises(SemanticError, match="Unknown field.*missing"):
        validate(invalid, schema={"users": {"age": "Number"}})


def test_group_by_and_having_execute_on_sqlite():
    query = parse(
        "FROM users RETURN city, COUNT(*) GROUP BY city "
        "HAVING COUNT(*) > 1 ORDER BY city"
    )
    assert query.group_by[0].parts == ["city"]
    assert query.having is not None
    validate(query, schema={"users": {"city": "Text", "age": "Number"}})
    from qryndel.executor import execute
    rows = execute(get_backend("sqlite").compile(build_plan(query)), "test.db")
    assert rows == [{"city": "Singapore", "COUNT(*)": 3}]
