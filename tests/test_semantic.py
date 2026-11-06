"""Tests for the Qryndel semantic analyzer."""

import pytest
from qryndel.parser import parse
from qryndel.semantic import SemanticError, validate


# Nested (document-style) schema format
SCHEMA = {
    "users": {
        "id": "Number",
        "name": "Text",
        "age": "Number",
        "email": "Text",
        "city": "Text",
        "profile": {
            "address": {
                "city": "Text",
                "country": "Text",
            },
            "bio": "Text",
        },
        "settings": {
            "notifications": {
                "email": "Boolean",
                "sms": "Boolean",
            },
        },
    },
    "orders": {
        "id": "Number",
        "user_id": "Number",
        "product": "Text",
        "amount": "Number",
        "quantity": "Number",
    },
}


class TestSemanticV01:
    """Basic validation still works."""

    def test_valid_query_no_schema(self):
        q = parse("FROM users RETURN name, email")
        validate(q, schema=None)  # should not raise

    def test_valid_query_with_schema(self):
        q = parse("FROM users WHERE age > 18 RETURN name, email")
        validate(q, schema=SCHEMA)

    def test_unknown_return_field(self):
        q = parse("FROM users RETURN nonexistent")
        with pytest.raises(SemanticError, match="Unknown field.*nonexistent"):
            validate(q, schema=SCHEMA)

    def test_unknown_where_field(self):
        q = parse("FROM users WHERE bogus > 18 RETURN name")
        with pytest.raises(SemanticError, match="Unknown field.*bogus"):
            validate(q, schema=SCHEMA)


class TestSemanticV02:
    """ORDER BY / LIMIT / DISTINCT validation of new features."""

    def test_and_condition_known_fields(self):
        q = parse('FROM users WHERE age > 18 AND city = "Singapore" RETURN name')
        validate(q, schema=SCHEMA)

    def test_or_condition_known_fields(self):
        q = parse('FROM users WHERE age > 18 OR city = "Singapore" RETURN name')
        validate(q, schema=SCHEMA)

    def test_not_condition_known_fields(self):
        q = parse('FROM users WHERE NOT city = "Singapore" RETURN name')
        validate(q, schema=SCHEMA)

    def test_nested_parentheses_known_fields(self):
        q = parse(
            'FROM users WHERE age > 18 AND (city = "Singapore" OR city = "Kuala Lumpur") RETURN name'
        )
        validate(q, schema=SCHEMA)

    def test_unknown_field_in_and(self):
        q = parse('FROM users WHERE age > 18 AND bogus = "x" RETURN name')
        with pytest.raises(SemanticError, match="Unknown field.*bogus"):
            validate(q, schema=SCHEMA)

    def test_unknown_field_in_or(self):
        q = parse('FROM users WHERE age > 18 OR bogus = "x" RETURN name')
        with pytest.raises(SemanticError, match="Unknown field.*bogus"):
            validate(q, schema=SCHEMA)

    def test_unknown_field_in_parentheses(self):
        q = parse(
            'FROM users WHERE age > 18 AND (city = "Singapore" OR bogus = "x") RETURN name'
        )
        with pytest.raises(SemanticError, match="Unknown field.*bogus"):
            validate(q, schema=SCHEMA)

    def test_unknown_field_in_order_by(self):
        q = parse("FROM users RETURN name ORDER BY bogus ASC")
        with pytest.raises(SemanticError, match="Unknown field.*bogus"):
            validate(q, schema=SCHEMA)

    def test_known_field_in_order_by(self):
        q = parse("FROM users RETURN name ORDER BY age DESC")
        validate(q, schema=SCHEMA)

    def test_limit_no_validation_needed(self):
        q = parse("FROM users RETURN name LIMIT 5")
        validate(q, schema=SCHEMA)

    def test_distinct_known_field(self):
        q = parse("FROM users RETURN DISTINCT city")
        validate(q, schema=SCHEMA)


class TestSemanticV03:
    """JOIN validation."""

    def test_basic_join_valid(self):
        q = parse(
            "FROM users JOIN orders ON users.id = orders.user_id RETURN users.name, orders.amount"
        )
        validate(q, schema=SCHEMA)

    def test_join_with_alias_valid(self):
        q = parse(
            "FROM users AS u JOIN orders AS o ON u.id = o.user_id RETURN u.name, o.amount"
        )
        validate(q, schema=SCHEMA)

    def test_join_unknown_table(self):
        q = parse(
            "FROM users JOIN nonexistent ON users.id = nonexistent.id RETURN users.name"
        )
        with pytest.raises(SemanticError, match="Unknown source.*nonexistent"):
            validate(q, schema=SCHEMA)

    def test_join_unknown_alias(self):
        q = parse(
            "FROM users JOIN orders ON x.id = orders.user_id RETURN users.name"
        )
        with pytest.raises(SemanticError, match="Unknown qualifier.*x"):
            validate(q, schema=SCHEMA)

    def test_join_unknown_qualified_field(self):
        q = parse(
            "FROM users JOIN orders ON users.id = orders.user_id RETURN users.nonexistent"
        )
        with pytest.raises(SemanticError, match="Unknown field.*nonexistent"):
            validate(q, schema=SCHEMA)

    def test_join_ambiguous_unqualified_field(self):
        q = parse(
            "FROM users JOIN orders ON users.id = orders.user_id RETURN id"
        )
        with pytest.raises(SemanticError, match="Ambiguous field.*id"):
            validate(q, schema=SCHEMA)

    def test_join_unqualified_field_ok_when_unique(self):
        q = parse(
            "FROM users JOIN orders ON users.id = orders.user_id RETURN name"
        )
        validate(q, schema=SCHEMA)

    def test_non_join_query_still_valid(self):
        q = parse("FROM users WHERE age > 18 RETURN name, email")
        validate(q, schema=SCHEMA)


class TestSemanticV04:
    """nested/document-style field validation."""

    def test_valid_nested_return(self):
        q = parse(
            'FROM users WHERE profile.address.city = "Singapore" RETURN name, profile.address.city'
        )
        validate(q, schema=SCHEMA)

    def test_valid_nested_with_alias(self):
        q = parse(
            "FROM users AS u WHERE u.profile.address.city = \"Singapore\" RETURN u.name, u.profile.address.city"
        )
        validate(q, schema=SCHEMA)

    def test_valid_nested_settings(self):
        q = parse(
            "FROM users WHERE settings.notifications.email = true RETURN name, settings.notifications.email"
        )
        validate(q, schema=SCHEMA)

    def test_valid_nested_order_by(self):
        q = parse(
            "FROM users RETURN name, profile.address.city ORDER BY profile.address.city ASC"
        )
        validate(q, schema=SCHEMA)

    def test_valid_nested_join_qualified(self):
        q = parse(
            "FROM users JOIN orders ON users.id = orders.user_id WHERE users.profile.address.city = \"Singapore\" RETURN users.name"
        )
        validate(q, schema=SCHEMA)

    def test_unknown_nested_field(self):
        q = parse(
            'FROM users WHERE profile.address.zipcode = "12345" RETURN name'
        )
        with pytest.raises(SemanticError, match="Unknown nested field"):
            validate(q, schema=SCHEMA)

    def test_invalid_nesting_into_scalar(self):
        q = parse(
            "FROM users WHERE name.first = \"John\" RETURN name"
        )
        with pytest.raises(SemanticError, match="not a nested|is not a nested|Unknown nested"):
            validate(q, schema=SCHEMA)

    def test_unknown_nested_root_column(self):
        q = parse(
            "FROM users RETURN bogus.nested.field"
        )
        with pytest.raises(SemanticError, match="Unknown qualifier.*bogus"):
            validate(q, schema=SCHEMA)

    def test_nested_with_unknown_alias(self):
        q = parse(
            "FROM users AS u WHERE x.profile.address.city = \"Singapore\" RETURN u.name"
        )
        with pytest.raises(SemanticError, match="Unknown qualifier.*x"):
            validate(q, schema=SCHEMA)

    def test_non_join_query_still_valid(self):
        q = parse("FROM users WHERE age > 18 RETURN name, email")
        validate(q, schema=SCHEMA)

    def test_flat_fields_still_work(self):
        q = parse("FROM users WHERE age > 18 AND city = \"Singapore\" RETURN name")
        validate(q, schema=SCHEMA)
        q2 = parse("FROM users RETURN name, age, email ORDER BY age DESC LIMIT 5")
        validate(q2, schema=SCHEMA)

    def test_nested_join_condition(self):
        """Qualified nested field in JOIN ON condition."""
        q = parse(
            "FROM users JOIN orders ON users.id = orders.user_id RETURN users.name"
        )
        validate(q, schema=SCHEMA)
