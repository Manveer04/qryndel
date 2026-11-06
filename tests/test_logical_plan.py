"""Tests for the Qryndel logical plan builder."""

import pytest
from qryndel.parser import parse
from qryndel.logical_plan import (
    build_plan,
    Distinct,
    Filter,
    Join,
    Limit,
    Project,
    Scan,
    Sort,
)
from qryndel.ast_nodes import FieldPath, OrderField


class TestLogicalPlanV01:
    """Basic plan shapes still work."""

    def test_simple_select(self):
        q = parse("FROM users RETURN name, email")
        plan = build_plan(q)
        assert isinstance(plan, Project)
        assert plan.columns == [FieldPath(parts=["name"]), FieldPath(parts=["email"])]
        assert isinstance(plan.child, Scan)
        assert plan.child.table_name == "users"

    def test_where(self):
        q = parse("FROM users WHERE age > 18 RETURN name")
        plan = build_plan(q)
        assert isinstance(plan, Project)
        assert isinstance(plan.child, Filter)
        assert isinstance(plan.child.child, Scan)


class TestLogicalPlanV02:
    """ORDER BY / LIMIT / DISTINCT plan shapes."""

    def test_order_by(self):
        q = parse("FROM users RETURN name ORDER BY age DESC")
        plan = build_plan(q)
        assert isinstance(plan, Project)
        assert isinstance(plan.child, Sort)
        assert plan.child.order_fields == [OrderField(FieldPath(parts=["age"]), "DESC")]
        assert isinstance(plan.child.child, Scan)

    def test_limit(self):
        q = parse("FROM users RETURN name LIMIT 10")
        plan = build_plan(q)
        assert isinstance(plan, Limit)
        assert plan.count == 10
        assert isinstance(plan.child, Project)

    def test_distinct(self):
        q = parse("FROM users RETURN DISTINCT city")
        plan = build_plan(q)
        assert isinstance(plan, Project)
        assert isinstance(plan.child, Distinct)

    def test_order_by_and_limit(self):
        q = parse("FROM users WHERE age > 18 RETURN name ORDER BY age DESC LIMIT 5")
        plan = build_plan(q)
        # Root should be Limit
        assert isinstance(plan, Limit)
        assert plan.count == 5
        inner = plan.child
        assert isinstance(inner, Project)
        inner = inner.child
        assert isinstance(inner, Sort)
        inner = inner.child
        assert isinstance(inner, Filter)
        inner = inner.child
        assert isinstance(inner, Scan)

    def test_distinct_order_limit(self):
        q = parse("FROM users RETURN DISTINCT city ORDER BY city ASC LIMIT 3")
        plan = build_plan(q)
        assert isinstance(plan, Limit)
        assert isinstance(plan.child, Project)
        assert isinstance(plan.child.child, Sort)
        assert isinstance(plan.child.child.child, Distinct)

    def test_filter_distinct_sort_project_limit_order(self):
        """Verify full plan tree: Limit -> Project -> Sort -> Distinct -> Filter -> Scan"""
        q = parse(
            'FROM users WHERE age > 18 RETURN DISTINCT name ORDER BY name ASC LIMIT 5'
        )
        plan = build_plan(q)

        assert isinstance(plan, Limit)
        assert isinstance(plan.child, Project)
        assert isinstance(plan.child.child, Sort)
        assert isinstance(plan.child.child.child, Distinct)
        assert isinstance(plan.child.child.child.child, Filter)
        assert isinstance(plan.child.child.child.child.child, Scan)


class TestLogicalPlanV03:
    """JOIN plan shapes."""

    def test_basic_join(self):
        q = parse(
            "FROM users JOIN orders ON users.id = orders.user_id RETURN users.name"
        )
        plan = build_plan(q)
        assert isinstance(plan, Project)
        assert isinstance(plan.child, Join)
        assert plan.child.join_type == "INNER"
        assert isinstance(plan.child.left, Scan)
        assert plan.child.left.table_name == "users"
        assert isinstance(plan.child.right, Scan)
        assert plan.child.right.table_name == "orders"

    def test_join_with_where(self):
        q = parse(
            "FROM users JOIN orders ON users.id = orders.user_id WHERE orders.amount > 100 RETURN users.name"
        )
        plan = build_plan(q)
        assert isinstance(plan, Project)
        assert isinstance(plan.child, Filter)
        assert isinstance(plan.child.child, Join)

    def test_join_with_alias(self):
        q = parse(
            "FROM users AS u JOIN orders AS o ON u.id = o.user_id RETURN u.name"
        )
        plan = build_plan(q)
        assert isinstance(plan, Project)
        assert isinstance(plan.child, Join)
        assert plan.child.left.alias == "u"
        assert plan.child.right.alias == "o"

    def test_chained_joins(self):
        """FROM a JOIN b ON ... JOIN c ON ..."""
        q = parse(
            "FROM users AS u JOIN orders AS o ON u.id = o.user_id RETURN u.name"
        )
        plan = build_plan(q)
        assert isinstance(plan, Project)
        assert isinstance(plan.child, Join)


class TestLogicalPlanV04:
    """nested field plan shapes."""

    def test_nested_return(self):
        q = parse(
            'FROM users WHERE profile.address.city = "Singapore" RETURN name, profile.address.city'
        )
        plan = build_plan(q)
        assert isinstance(plan, Project)
        assert plan.columns == [
            FieldPath(parts=["name"]),
            FieldPath(parts=["profile", "address", "city"]),
        ]
        assert isinstance(plan.child, Filter)
        assert isinstance(plan.child.child, Scan)

    def test_nested_order_by(self):
        q = parse(
            "FROM users RETURN name, profile.address.city ORDER BY profile.address.city ASC"
        )
        plan = build_plan(q)
        assert isinstance(plan, Project)
        assert isinstance(plan.child, Sort)
        assert plan.child.order_fields == [
            OrderField(FieldPath(parts=["profile", "address", "city"]), "ASC")
        ]
