"""Tests for safe runtime query parameters."""

import pytest

from qryndel.backends import get_backend
from qryndel.backends.sqlite import ParameterError
from qryndel.executor import execute
from qryndel.logical_plan import (build_plan, build_insert_plan)
from qryndel.parser import parse, parse_program


def test_parameter_reference_is_parsed():
    query = parse("FROM users WHERE name = $username RETURN name")
    assert query.where.value.name == "username"


def test_sqlite_parameter_is_bound_and_executed():
    plan = build_plan(parse("FROM users WHERE name = $username RETURN name"))
    compiled = get_backend("sqlite").compile(plan, parameters={"username": "Alice"})
    assert compiled.sql.endswith("WHERE name = ?;")
    assert compiled.params == ["Alice"]
    rows = execute(compiled, "test.db")
    assert rows == [{"name": "Alice"}]


def test_missing_sqlite_parameter_is_rejected():
    plan = build_plan(parse("FROM users WHERE name = $username RETURN name"))
    with pytest.raises(ParameterError, match=r"missing parameter: \$username"):
        get_backend("sqlite").compile(plan)


def test_write_parameter_is_bound():
    program = parse_program("INSERT INTO users { name: $name, age: $age }")
    plan = build_insert_plan(program.statement)
    compiled = get_backend("sqlite").compile(
        plan, parameters={"name": "Aisha", "age": 21}
    )
    assert compiled.params == ["Aisha", 21]
    assert "'Aisha'" not in compiled.sql
