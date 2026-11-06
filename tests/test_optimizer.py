"""Tests for safe logical-plan optimization."""

from qryndel.backends import get_backend
from qryndel.logical_plan import Filter, Join, Project, build_plan
from qryndel.optimizer import optimize_plan
from qryndel.parser import parse
from qryndel.semantic import validate


SCHEMA = {
    "users": {"id": "Number", "name": "Text"},
    "orders": {"user_id": "Number", "amount": "Number"},
}


def test_inner_join_filter_is_pushed_to_matching_branch():
    query = parse(
        "FROM users AS u JOIN orders AS o ON u.id = o.user_id "
        "WHERE o.amount > 50 RETURN u.name"
    )
    validate(query, schema=SCHEMA)
    optimized = optimize_plan(build_plan(query))
    assert isinstance(optimized, Project)
    assert isinstance(optimized.child, Join)
    assert isinstance(optimized.child.right, Filter)
    assert optimized.child.left.table_name == "users"


def test_outer_join_filter_is_not_pushed():
    query = parse(
        "FROM users AS u LEFT JOIN orders AS o ON u.id = o.user_id "
        "WHERE o.amount > 50 RETURN u.name"
    )
    validate(query, schema=SCHEMA)
    optimized = optimize_plan(build_plan(query))
    assert isinstance(optimized, Project)
    assert isinstance(optimized.child, Filter)
    assert isinstance(optimized.child.child, Join)


def test_optimized_join_compiles_with_pushed_predicate():
    query = parse(
        "FROM users AS u JOIN orders AS o ON u.id = o.user_id "
        "WHERE o.amount > 50 RETURN u.name"
    )
    validate(query, schema=SCHEMA)
    compiled = get_backend("sqlite").compile(optimize_plan(build_plan(query)))
    assert "WHERE o.amount > ?" in compiled.sql
    assert compiled.params == [50]
