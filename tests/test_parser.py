"""Tests for the Qryndel parser."""

import pytest
from qryndel.parser import ParseError, parse
from qryndel.ast_nodes import (
    AndCondition,
    Condition,
    FieldPath,
    Join,
    JoinCondition,
    NotCondition,
    OrderField,
    OrCondition,
    Query,
    Source,
)


class TestParseV01:
    """Basic queries must still parse correctly."""

    def test_simple_select(self):
        q = parse("FROM users RETURN name, email")
        assert isinstance(q, Query)
        assert q.source == Source("users")
        assert q.fields == [FieldPath(parts=["name"]), FieldPath(parts=["email"])]
        assert q.where is None
        assert q.order is None
        assert q.limit is None
        assert q.distinct is False

    def test_where_comparison(self):
        q = parse("FROM users WHERE age > 18 RETURN name, email")
        assert q.source == Source("users")
        assert q.fields == [FieldPath(parts=["name"]), FieldPath(parts=["email"])]
        assert isinstance(q.where, Condition)
        assert q.where.field == FieldPath(parts=["age"])
        assert q.where.operator == ">"
        assert q.where.value == 18

    def test_where_with_string(self):
        q = parse('FROM users WHERE name = "Alice" RETURN email')
        assert isinstance(q.where, Condition)
        assert q.where.field == FieldPath(parts=["name"])
        assert q.where.operator == "="
        assert q.where.value == "Alice"

    def test_boolean_literals_are_case_insensitive(self):
        q = parse("FROM users WHERE has_license = TRUE RETURN name")
        assert q.where.value is True

    def test_all_operators(self):
        for op in ["=", "!=", ">", "<", ">=", "<="]:
            q = parse(f"FROM users WHERE age {op} 10 RETURN name")
            assert q.where.operator == op

    def test_single_field_return(self):
        q = parse("FROM users RETURN name")
        assert q.fields == [FieldPath(parts=["name"])]


class TestParseV02:
    """ORDER BY, LIMIT, DISTINCT and boolean logic."""

    def test_and_condition(self):
        q = parse('FROM users WHERE age > 18 AND city = "Singapore" RETURN name')
        assert isinstance(q.where, AndCondition)
        assert isinstance(q.where.left, Condition)
        assert isinstance(q.where.right, Condition)

    def test_or_condition(self):
        q = parse('FROM users WHERE age > 18 OR city = "Singapore" RETURN name')
        assert isinstance(q.where, OrCondition)

    def test_parenthesized_group(self):
        q = parse(
            'FROM users WHERE age > 18 AND (city = "Singapore" OR city = "Kuala Lumpur") RETURN name'
        )
        assert isinstance(q.where, AndCondition)
        assert isinstance(q.where.left, Condition)
        assert isinstance(q.where.right, OrCondition)

    def test_not_condition(self):
        q = parse('FROM users WHERE NOT city = "Singapore" RETURN name')
        assert isinstance(q.where, NotCondition)

    def test_order_by_asc(self):
        q = parse("FROM users RETURN name ORDER BY age ASC")
        assert q.order is not None
        assert len(q.order) == 1
        assert q.order[0].field == FieldPath(parts=["age"])
        assert q.order[0].direction == "ASC"

    def test_order_by_desc(self):
        q = parse("FROM users RETURN name ORDER BY age DESC")
        assert q.order[0].direction == "DESC"

    def test_order_by_default_asc(self):
        q = parse("FROM users RETURN name ORDER BY age")
        assert q.order[0].direction == "ASC"

    def test_order_by_multiple(self):
        q = parse("FROM users RETURN name ORDER BY age DESC, name ASC")
        assert len(q.order) == 2
        assert q.order[0].field == FieldPath(parts=["age"])
        assert q.order[0].direction == "DESC"
        assert q.order[1].field == FieldPath(parts=["name"])
        assert q.order[1].direction == "ASC"

    def test_limit(self):
        q = parse("FROM users RETURN name LIMIT 10")
        assert q.limit == 10

    def test_distinct(self):
        q = parse("FROM users RETURN DISTINCT city")
        assert q.distinct is True
        assert q.fields == [FieldPath(parts=["city"])]

    def test_order_by_and_limit(self):
        q = parse("FROM users WHERE age > 18 RETURN name, email ORDER BY age DESC LIMIT 5")
        assert q.limit == 5
        assert len(q.order) == 1
        assert q.order[0].field == FieldPath(parts=["age"])
        assert q.order[0].direction == "DESC"
        assert isinstance(q.where, Condition)

    def test_full_feature_query(self):
        q = parse(
            'FROM users WHERE age > 18 AND city = "Singapore" RETURN DISTINCT name ORDER BY name ASC LIMIT 3'
        )
        assert q.distinct is True
        assert q.limit == 3
        assert q.order[0].field == FieldPath(parts=["name"])
        assert isinstance(q.where, AndCondition)


class TestParseV03:
    """JOIN features."""

    def test_basic_join(self):
        q = parse(
            "FROM users JOIN orders ON users.id = orders.user_id RETURN users.name, orders.amount"
        )
        assert len(q.joins) == 1
        assert q.joins[0].join_type == "INNER"
        assert q.joins[0].right == Source("orders")
        assert isinstance(q.joins[0].condition, JoinCondition)
        assert q.joins[0].condition.left == FieldPath(parts=["users", "id"])
        assert q.joins[0].condition.right == FieldPath(parts=["orders", "user_id"])

    def test_join_with_where(self):
        q = parse(
            "FROM users JOIN orders ON users.id = orders.user_id WHERE orders.amount > 100 RETURN users.name"
        )
        assert len(q.joins) == 1
        assert isinstance(q.where, Condition)

    def test_join_with_order_by(self):
        q = parse(
            "FROM users JOIN orders ON users.id = orders.user_id RETURN users.name ORDER BY orders.amount DESC"
        )
        assert q.order[0].field == FieldPath(parts=["orders", "amount"])

    def test_join_with_limit(self):
        q = parse(
            "FROM users JOIN orders ON users.id = orders.user_id RETURN users.name LIMIT 5"
        )
        assert q.limit == 5

    def test_join_with_distinct(self):
        q = parse(
            "FROM users JOIN orders ON users.id = orders.user_id RETURN DISTINCT users.name"
        )
        assert q.distinct is True

    def test_join_with_alias(self):
        q = parse(
            "FROM users AS u JOIN orders AS o ON u.id = o.user_id RETURN u.name, o.amount"
        )
        assert q.source.alias == "u"
        assert len(q.joins) == 1
        assert q.joins[0].alias == "o"
        assert q.joins[0].condition.left == FieldPath(parts=["u", "id"])

    def test_inner_join(self):
        q = parse(
            "FROM users INNER JOIN orders ON users.id = orders.user_id RETURN users.name"
        )
        assert q.joins[0].join_type == "INNER"

    def test_left_join(self):
        q = parse(
            "FROM users LEFT JOIN orders ON users.id = orders.user_id RETURN users.name"
        )
        assert q.joins[0].join_type == "LEFT"

    def test_qualified_field_in_return(self):
        q = parse(
            "FROM users JOIN orders ON users.id = orders.user_id RETURN users.name, orders.amount"
        )
        assert q.fields[0] == FieldPath(parts=["users", "name"])
        assert q.fields[1] == FieldPath(parts=["orders", "amount"])

    def test_qualified_field_in_where(self):
        q = parse(
            "FROM users JOIN orders ON users.id = orders.user_id WHERE orders.amount > 50 RETURN users.name"
        )
        assert q.where.field == FieldPath(parts=["orders", "amount"])


class TestParseV04:
    """nested field paths."""

    def test_nested_return(self):
        q = parse(
            'FROM users WHERE profile.address.city = "Singapore" RETURN name, profile.address.city'
        )
        assert q.fields[0] == FieldPath(parts=["name"])
        assert q.fields[1] == FieldPath(parts=["profile", "address", "city"])
        assert q.where.field == FieldPath(parts=["profile", "address", "city"])

    def test_nested_with_alias(self):
        q = parse(
            "FROM users AS u WHERE u.profile.address.city = \"Singapore\" RETURN u.name, u.profile.address.city"
        )
        assert q.fields[0] == FieldPath(parts=["u", "name"])
        assert q.fields[1] == FieldPath(parts=["u", "profile", "address", "city"])

    def test_deeply_nested(self):
        q = parse(
            "FROM users WHERE settings.notifications.email = true RETURN name, settings.notifications.email"
        )
        assert q.fields[1] == FieldPath(parts=["settings", "notifications", "email"])
        assert q.where.field == FieldPath(parts=["settings", "notifications", "email"])

    def test_nested_order_by(self):
        q = parse(
            "FROM users RETURN name, profile.address.city ORDER BY profile.address.city ASC"
        )
        assert q.order[0].field == FieldPath(parts=["profile", "address", "city"])

    def test_nested_with_join(self):
        q = parse(
            "FROM users JOIN orders ON users.id = orders.user_id WHERE users.profile.address.city = \"Singapore\" RETURN users.name"
        )
        assert q.where.field == FieldPath(parts=["users", "profile", "address", "city"])


class TestParseErrors:
    """Parser must reject invalid syntax."""

    def test_no_from(self):
        with pytest.raises(ParseError):
            parse("RETURN name")

    def test_no_return(self):
        with pytest.raises(ParseError):
            parse("FROM users")

    def test_gibberish(self):
        with pytest.raises(ParseError):
            parse("hello world")

    def test_bad_operator(self):
        with pytest.raises(ParseError):
            parse("FROM users WHERE age INVALID 10 RETURN name")

    def test_unclosed_string(self):
        with pytest.raises(ParseError):
            parse('FROM users WHERE name = "Alice RETURN email')
