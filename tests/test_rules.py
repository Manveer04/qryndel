"""
Tests for RULE definitions and expansion.
"""

from __future__ import annotations
import pytest
from typing import Any, Dict

from qryndel.ast_nodes import (AndCondition, Condition, FieldPath, NotCondition, OrCondition, Program, Query, RuleCall, RuleDefinition)
from qryndel.parser import ParseError, parse, parse_program
from qryndel.semantic import SemanticError, validate
from qryndel.rules import SemanticError as RuleSemanticError, expand_rules
from qryndel.backends import get_backend
from qryndel.logical_plan import (build_plan)


# ---------------------------------------------------------------------------
# Schemas for rule tests
# ---------------------------------------------------------------------------

TABLE_SCHEMA: Dict[str, Any] = {
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
    },
}

GRAPH_SCHEMA: Dict[str, Any] = {
    "nodes": {
        "Person": {
            "name": "Text",
            "age": "Number",
            "city": "Text",
        },
        "friend": {
            "name": "Text",
            "age": "Number",
        },
    },
    "relationships": {
        "KNOWS": {"from": "Person", "to": "Person"},
        "FRIEND_OF": {"from": "Person", "to": "Person"},
    },
}


# ===========================================================================
# 1. Parsing rules (10 tests)
# ===========================================================================

class TestParseRules:
    """Test that RULE definitions are parsed correctly."""

    def test_parse_single_rule(self):
        text = "RULE is_adult(x) = x.age >= 18\nFROM users AS u WHERE is_adult(u) RETURN name"
        program = parse_program(text)
        assert len(program.rules) == 1
        rule = program.rules[0]
        assert rule.name == "is_adult"
        assert rule.parameter == "x"
        assert isinstance(rule.expression, Condition)
        assert rule.expression.operator == ">="

    def test_parse_multiple_rules(self):
        text = (
            "RULE is_adult(x) = x.age >= 18\n"
            "RULE has_email(x) = x.email != ''\n"
            "FROM users AS u WHERE is_adult(u) RETURN name"
        )
        program = parse_program(text)
        assert len(program.rules) == 2

    def test_parse_rule_after_query(self):
        """Rules must come before the query — grammar enforces order."""
        with pytest.raises(ParseError):
            parse_program("FROM users RETURN name\nRULE is_adult(x) = x.age >= 18")

    def test_parse_rule_and_or(self):
        text = "RULE big_spender(x) = x.amount > 1000 AND x.quantity >= 5\nFROM orders AS o WHERE big_spender(o) RETURN product"
        program = parse_program(text)
        assert isinstance(program.rules[0].expression, AndCondition)

    def test_parse_rule_or(self):
        text = "RULE special(x) = x.city = 'NYC' OR x.city = 'LA'\nFROM users AS u WHERE special(u) RETURN name"
        program = parse_program(text)
        assert isinstance(program.rules[0].expression, OrCondition)

    def test_parse_rule_not(self):
        text = "RULE not_adult(x) = NOT x.age >= 18\nFROM users AS u WHERE not_adult(u) RETURN name"
        program = parse_program(text)
        assert isinstance(program.rules[0].expression, NotCondition)

    def test_parse_rule_return_query(self):
        """parse() still returns Query for backward compat."""
        text = "RULE is_adult(x) = x.age >= 18\nFROM users AS u WHERE is_adult(u) RETURN name"
        result = parse(text)
        assert isinstance(result, Query)
        assert result.source.name == "users"


    def test_parse_rule_no_rules(self):
        """parse_program with no rules returns empty rules list."""
        program = parse_program("FROM users RETURN name")
        assert len(program.rules) == 0
        assert isinstance(program.query, Query)

    def test_parse_rule_multiple_params_not_supported(self):
        """Grammar only allows single parameter, so multi-param should fail."""
        with pytest.raises(ParseError):
            parse_program("RULE test(x, y) = x.age >= 18\nFROM users AS u RETURN name")


# ===========================================================================
# 2. Duplicate names (2 tests)
# ===========================================================================

class TestDuplicateRules:
    """Test that duplicate rule names are detected."""

    def test_duplicate_rule_name_raises(self):
        text = (
            "RULE is_adult(x) = x.age >= 18\n"
            "RULE is_adult(y) = y.age >= 21\n"
            "FROM users AS u WHERE is_adult(u) RETURN name"
        )
        program = parse_program(text)
        with pytest.raises(RuleSemanticError, match="Duplicate rule definition"):
            expand_rules(program)

    def test_duplicate_via_validate_raises(self):
        text = (
            "RULE is_adult(x) = x.age >= 18\n"
            "RULE is_adult(y) = y.age >= 21\n"
            "FROM users AS u WHERE is_adult(u) RETURN name"
        )
        program = parse_program(text)
        with pytest.raises(SemanticError, match="Duplicate rule definition"):
            validate(program, schema=TABLE_SCHEMA)


# ===========================================================================
# 3. Unknown rule calls (2 tests)
# ===========================================================================

class TestUnknownRuleCalls:
    """Test that calls to undefined rules are rejected."""

    def test_unknown_rule_raises(self):
        text = "FROM users AS u WHERE undefined_rule(u) RETURN name"
        program = parse_program(text)
        with pytest.raises(SemanticError, match="Unexpanded rule call"):
            validate(program, schema=TABLE_SCHEMA)

    def test_unknown_rule_in_nested_rule_raises(self):
        text = (
            "RULE my_rule(x) = not_defined(x)\n"
            "FROM users AS u WHERE my_rule(u) RETURN name"
        )
        program = parse_program(text)
        with pytest.raises(SemanticError, match="Unknown rule"):
            validate(program, schema=TABLE_SCHEMA)


# ===========================================================================
# 4. Recursive rejection (2 tests)
# ===========================================================================

class TestRecursiveRules:
    """Test that recursive rules are detected and rejected."""

    def test_direct_recursion_raises(self):
        text = (
            "RULE recursive(x) = recursive(x)\n"
            "FROM users AS u WHERE recursive(u) RETURN name"
        )
        program = parse_program(text)
        with pytest.raises(SemanticError, match="Recursive rule"):
            validate(program, schema=TABLE_SCHEMA)

    def test_indirect_recursion_raises(self):
        text = (
            "RULE a(x) = b(x)\n"
            "RULE b(x) = a(x)\n"
            "FROM users AS u WHERE a(u) RETURN name"
        )
        program = parse_program(text)
        with pytest.raises(SemanticError, match="Recursive rule"):
            validate(program, schema=TABLE_SCHEMA)


# ===========================================================================
# 5. Basic expansion (6 tests)
# ===========================================================================

class TestBasicExpansion:
    """Test basic rule expansion into a flat WHERE condition."""

    def test_simple_rule_expansion(self):
        text = "RULE is_adult(x) = x.age >= 18\nFROM users AS u WHERE is_adult(u) RETURN name"
        program = parse_program(text)
        expanded = expand_rules(program)
        assert isinstance(expanded, Query)
        assert expanded.where is not None
        assert isinstance(expanded.where, Condition)
        # The expanded condition should use 'u.age' not 'x.age'
        assert expanded.where.field.parts == ["u", "age"]
        assert expanded.where.operator == ">="
        assert expanded.where.value == 18

    def test_rule_expansion_with_alias(self):
        text = "RULE is_adult(x) = x.age >= 18\nFROM users AS u WHERE is_adult(u) RETURN name, age"
        program = parse_program(text)
        expanded = expand_rules(program)
        assert expanded.where.field.parts[0] == "u"

    def test_rule_expansion_preserves_query(self):
        text = "RULE is_adult(x) = x.age >= 18\nFROM users AS u WHERE is_adult(u) RETURN name"
        program = parse_program(text)
        expanded = expand_rules(program)
        assert expanded.source.name == "users"
        assert len(expanded.fields) == 1
        assert expanded.fields[0].parts == ["name"]

    def test_rule_with_value_comparison(self):
        text = "RULE is_minor(x) = x.age < 18\nFROM users AS u WHERE is_minor(u) RETURN name"
        program = parse_program(text)
        expanded = expand_rules(program)
        assert isinstance(expanded.where, Condition)
        assert expanded.where.operator == "<"
        assert expanded.where.value == 18

    def test_rule_with_string_value(self):
        text = "RULE from_nyc(x) = x.city = 'NYC'\nFROM users AS u WHERE from_nyc(u) RETURN name"
        program = parse_program(text)
        expanded = expand_rules(program)
        assert isinstance(expanded.where, Condition)
        assert expanded.where.value == "NYC"

    def test_rule_non_parameter_field_left_alone(self):
        """Fields in the rule body that don't start with the parameter stay as-is."""
        text = "RULE special(x) = x.status = 'active' AND x.age >= 18\nFROM users AS u WHERE special(u) RETURN name"
        program = parse_program(text)
        expanded = expand_rules(program)
        assert isinstance(expanded.where, AndCondition)
        # Both conditions should have 'u' prefix
        assert isinstance(expanded.where.left, Condition)
        assert expanded.where.left.field.parts == ["u", "status"]
        assert isinstance(expanded.where.right, Condition)
        assert expanded.where.right.field.parts == ["u", "age"]


# ===========================================================================
# 6. Nested/chained rules (6 tests)
# ===========================================================================

class TestChainedRules:
    """Test expansion of rules that call other rules."""

    def test_chained_rules_expand(self):
        text = (
            "RULE is_adult(x) = x.age >= 18\n"
            "RULE can_drive(x) = is_adult(x) AND x.has_license = true\n"
            "FROM users AS u WHERE can_drive(u) RETURN name"
        )
        program = parse_program(text)
        expanded = expand_rules(program)
        assert isinstance(expanded.where, AndCondition)
        # The first condition should be the expanded is_adult -> u.age >= 18
        assert isinstance(expanded.where.left, Condition)
        assert expanded.where.left.field.parts == ["u", "age"]
        assert expanded.where.left.operator == ">="
        # The second condition should be the inline one
        assert isinstance(expanded.where.right, Condition)
        assert expanded.where.right.field.parts == ["u", "has_license"]

    def test_deeply_chained_rules(self):
        text = (
            "RULE a(x) = x.v > 1\n"
            "RULE b(x) = a(x) AND x.w < 10\n"
            "RULE c(x) = b(x) OR x.y = 0\n"
            "FROM users AS u WHERE c(u) RETURN name"
        )
        program = parse_program(text)
        expanded = expand_rules(program)
        assert isinstance(expanded.where, OrCondition)
        assert isinstance(expanded.where.left, AndCondition)
        assert isinstance(expanded.where.left.left, Condition)
        assert expanded.where.left.left.field.parts == ["u", "v"]

    def test_rule_calling_same_param_name_different_arg(self):
        """Test two calls to the same rule with different arguments."""
        text = (
            "RULE is_adult(x) = x.age >= 18\n"
            "FROM users AS u INNER JOIN orders AS o ON u.id = o.user_id\n"
            "WHERE is_adult(u) AND is_adult(o)\n"
            "RETURN u.name"
        )
        program = parse_program(text)
        expanded = expand_rules(program)
        assert isinstance(expanded.where, AndCondition)
        assert isinstance(expanded.where.left, Condition)
        assert expanded.where.left.field.parts == ["u", "age"]
        assert isinstance(expanded.where.right, Condition)
        assert expanded.where.right.field.parts == ["o", "age"]

    def test_chained_through_validate(self):
        """Test end-to-end validation with chained rules."""
        text = (
            "RULE is_adult(x) = x.age >= 18\n"
            "RULE can_drive(x) = is_adult(x) AND x.has_license = true\n"
            "FROM users AS u WHERE can_drive(u) RETURN name"
        )
        program = parse_program(text)
        validate(program, schema=TABLE_SCHEMA)  # should not raise

    def test_chained_unknown_field_in_rule_rejected(self):
        """Rule body references field not in schema — rejected at validation."""
        text = (
            "RULE bad(x) = x.nonexistent > 0\n"
            "FROM users AS u WHERE bad(u) RETURN name"
        )
        program = parse_program(text)
        with pytest.raises(SemanticError, match="Unknown field|Unknown qualifier"):
            validate(program, schema=TABLE_SCHEMA)

    def test_rule_multiple_calls_in_or(self):
        text = (
            "RULE is_adult(x) = x.age >= 18\n"
            "FROM users AS u WHERE is_adult(u) OR is_adult(u) RETURN name"
        )
        program = parse_program(text)
        expanded = expand_rules(program)
        assert isinstance(expanded.where, OrCondition)


# ===========================================================================
# 7. AND/OR/NOT with rules (6 tests)
# ===========================================================================

class TestBooleanWithRules:
    """Test boolean logic combining rule calls and conditions."""

    def test_rule_in_and(self):
        text = (
            "RULE is_adult(x) = x.age >= 18\n"
            "FROM users AS u WHERE is_adult(u) AND u.city = 'NYC' RETURN name"
        )
        program = parse_program(text)
        expanded = expand_rules(program)
        assert isinstance(expanded.where, AndCondition)
        assert isinstance(expanded.where.left, Condition)

    def test_rule_in_or(self):
        text = (
            "RULE is_adult(x) = x.age >= 18\n"
            "FROM users AS u WHERE is_adult(u) OR u.city = 'LA' RETURN name"
        )
        program = parse_program(text)
        expanded = expand_rules(program)
        assert isinstance(expanded.where, OrCondition)

    def test_rule_with_not(self):
        text = (
            "RULE is_adult(x) = x.age >= 18\n"
            "FROM users AS u WHERE NOT is_adult(u) RETURN name"
        )
        program = parse_program(text)
        expanded = expand_rules(program)
        assert isinstance(expanded.where, NotCondition)
        assert isinstance(expanded.where.child, Condition)

    def test_rule_in_parenthesized_group(self):
        text = (
            "RULE is_adult(x) = x.age >= 18\n"
            "FROM users AS u WHERE (is_adult(u) AND u.city = 'NYC') OR u.city = 'LA' RETURN name"
        )
        program = parse_program(text)
        expanded = expand_rules(program)
        # Should be Or(And(expanded, Condition), Condition)
        assert isinstance(expanded.where, OrCondition)
        assert isinstance(expanded.where.left, AndCondition)

    def test_two_rules_in_and(self):
        text = (
            "RULE is_adult(x) = x.age >= 18\n"
            "RULE from_nyc(x) = x.city = 'NYC'\n"
            "FROM users AS u WHERE is_adult(u) AND from_nyc(u) RETURN name"
        )
        program = parse_program(text)
        expanded = expand_rules(program)
        assert isinstance(expanded.where, AndCondition)

    def test_rule_and_inline_condition(self):
        text = (
            "RULE is_adult(x) = x.age >= 18\n"
            "FROM users AS u WHERE is_adult(u) AND u.email != '' RETURN name"
        )
        program = parse_program(text)
        expanded = expand_rules(program)
        assert isinstance(expanded.where, AndCondition)
        # Find the email condition
        right = expanded.where.right
        assert isinstance(right, Condition)
        assert "email" in right.field.parts


# ===========================================================================
# 9. SQLite compilation with rules (6 tests)
# ===========================================================================

class TestSqliteWithRules:
    """Test that SQLite compilation works after rule expansion."""

    def test_sqlite_rule_expanded(self):
        text = (
            "RULE is_adult(x) = x.age >= 18\n"
            "FROM users AS u WHERE is_adult(u) RETURN u.name"
        )
        program = parse_program(text)
        expanded = expand_rules(program, schema=TABLE_SCHEMA)
        plan = build_plan(expanded)
        backend = get_backend("sqlite")
        sql = backend.format_output(backend.compile(plan))
        assert "WHERE" in sql
        assert "u.age" in sql
        assert ">=" in sql

    def test_sqlite_chained_rules(self):
        text = (
            "RULE is_adult(x) = x.age >= 18\n"
            "RULE can_drive(x) = is_adult(x) AND x.has_license = true\n"
            "FROM users AS u WHERE can_drive(u) RETURN u.name"
        )
        program = parse_program(text)
        expanded = expand_rules(program, schema=TABLE_SCHEMA)
        plan = build_plan(expanded)
        backend = get_backend("sqlite")
        sql = backend.format_output(backend.compile(plan))
        assert "AND" in sql
        assert "u.age >= ?" in sql

    def test_sqlite_rule_with_value(self):
        text = (
            "RULE from_nyc(x) = x.city = 'NYC'\n"
            "FROM users AS u WHERE from_nyc(u) RETURN u.name"
        )
        program = parse_program(text)
        expanded = expand_rules(program, schema=TABLE_SCHEMA)
        plan = build_plan(expanded)
        backend = get_backend("sqlite")
        sql = backend.format_output(backend.compile(plan))
        assert "u.city" in sql

    def test_sqlite_rule_distinct(self):
        text = (
            "RULE is_adult(x) = x.age >= 18\n"
            "FROM users AS u WHERE is_adult(u) RETURN DISTINCT u.city"
        )
        program = parse_program(text)
        expanded = expand_rules(program, schema=TABLE_SCHEMA)
        plan = build_plan(expanded)
        backend = get_backend("sqlite")
        sql = backend.format_output(backend.compile(plan))
        assert "DISTINCT" in sql

    def test_sqlite_rule_limit(self):
        text = (
            "RULE is_adult(x) = x.age >= 18\n"
            "FROM users AS u WHERE is_adult(u) RETURN u.name LIMIT 5"
        )
        program = parse_program(text)
        expanded = expand_rules(program, schema=TABLE_SCHEMA)
        plan = build_plan(expanded)
        backend = get_backend("sqlite")
        sql = backend.format_output(backend.compile(plan))
        assert "LIMIT" in sql

    def test_sqlite_rule_no_rules(self):
        """No rules should still compile fine."""
        program = parse_program("FROM users AS u RETURN u.name")
        expanded = expand_rules(program, schema=TABLE_SCHEMA)
        plan = build_plan(expanded)
        backend = get_backend("sqlite")
        sql = backend.format_output(backend.compile(plan))
        assert "SELECT" in sql
