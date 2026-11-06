"""Rule expansion.

``expand_rules()`` takes a ``Program`` and replaces every ``RuleCall`` with
the body of its definition (a plain WHERE expression). Expansion happens
before planning, so backends never see rules.
"""

from __future__ import annotations
from typing import Any, Set

from qryndel.ast_nodes import (
    AndCondition,
    Condition,
    DeleteStatement,
    FieldPath,
    InsertStatement,
    NotCondition,
    NullPredicate,
    OrCondition,
    Program,
    Query,
    RuleCall,
    RuleDefinition,
    TransactionStatement,
    UpdateStatement,
    WhereExpr,
)
from qryndel.errors import UdlError


class SemanticError(UdlError):
    """Raised when rule validation or expansion fails."""

    def __init__(self, message: str):
        super().__init__(message, code="UDL_SEMANTIC_ERROR")


def _substitute(existing: FieldPath) -> FieldPath:
    """
    Copy a FieldPath into a new FieldPath preserving its structure.
    """
    return FieldPath(
        parts=list(existing.parts),
        qualifier=existing.qualifier,
    )


def _substitute_parameter(expr: WhereExpr, param: str, arg: FieldPath) -> WhereExpr:
    """
    Walk the expression tree and replace the parameter prefix in every
    ``FieldPath`` whose first part matches *param* with the call argument.

    For example, if the rule body contains ``x.age`` and the call argument
    is ``u``, the result is ``u.age``. If the call argument were ``u.profile``,
    the result would be ``u.profile.age``.
    """

    def _replace_prefix(field: FieldPath) -> FieldPath:
        if field.parts[0] == param:
            return FieldPath(
                parts=arg.parts + field.parts[1:],
                qualifier=arg.qualifier,
            )
        return _substitute(field)

    def walk(node: WhereExpr) -> WhereExpr:
        if isinstance(node, Condition):
            return Condition(
                field=_replace_prefix(node.field),
                operator=node.operator,
                value=node.value,
            )

        if isinstance(node, NullPredicate):
            return NullPredicate(
                field=_replace_prefix(node.field),
                predicate=node.predicate,
            )

        if isinstance(node, AndCondition):
            return AndCondition(left=walk(node.left), right=walk(node.right))

        if isinstance(node, OrCondition):
            return OrCondition(left=walk(node.left), right=walk(node.right))

        if isinstance(node, NotCondition):
            return NotCondition(child=walk(node.child))

        if isinstance(node, RuleCall):
            # Rule calls in rule bodies are also expanded (they reference
            # other rules); substitution still applies to their argument.
            new_arg = _replace_prefix(node.argument)
            return RuleCall(name=node.name, argument=new_arg)

        raise SemanticError(f"Unexpected expression type: {type(node).__name__}")

    return walk(expr)


def _find_rule(defs: list[RuleDefinition], name: str) -> RuleDefinition | None:
    """Find a rule definition by name, or return None."""
    for d in defs:
        if d.name == name:
            return d
    return None


def expand_rules(
    program: Program,
    schema: Any = None,
    _expanding: Set[str] | None = None,
) -> Query | UpdateStatement | DeleteStatement | TransactionStatement:
    """
    Expand all rule calls in *program* and return the expanded statement.

    Steps:
      1. Check for duplicate rule names.
      2. Replace every ``RuleCall`` in the statement's WHERE clause with the
         rule body (substituting the parameter with the call argument).
      3. Detect and reject recursive rule expansions.
      4. Return the expanded ``Query``, ``UpdateStatement`` or
         ``DeleteStatement``.

    ``schema`` is accepted for API compatibility and is not used during
    expansion.
    """
    if _expanding is None:
        _expanding = set()

    rules = program.rules
    statement = program.statement

    # --- Collect rule definitions, detect duplicates ---
    rule_map: dict[str, RuleDefinition] = {}
    for rule in rules:
        if rule.name in rule_map:
            raise SemanticError(f"Duplicate rule definition: '{rule.name}'")
        rule_map[rule.name] = rule

    # --- Quick return if no rules ---
    if not rules:
        return statement

    if isinstance(statement, TransactionStatement):
        return TransactionStatement(
            statements=[
                statement_item
                if isinstance(statement_item, InsertStatement)
                else expand_rules(
                    Program(types=program.types, rules=rules, statement=statement_item),
                    _expanding=_expanding,
                )
                for statement_item in statement.statements
            ],
            action=statement.action,
        )

    if not isinstance(statement, (Query, UpdateStatement, DeleteStatement)):
        raise SemanticError(
            "RULE definitions cannot be applied to INSERT statements because "
            "INSERT has no WHERE expression."
        )

    # --- Expand all rule calls in the statement's WHERE clause ---
    expanded = _expand_where(
        where=statement.where if hasattr(statement, "where") else None,
        rule_map=rule_map,
        _expanding=_expanding,
    )

    # --- Return a new statement with the expanded WHERE ---
    if isinstance(statement, Query):
        return Query(
            source=statement.source,
            joins=statement.joins,
            fields=statement.fields,
            where=expanded,
            order=statement.order,
            limit=statement.limit,
            distinct=statement.distinct,
            group_by=statement.group_by,
            having=statement.having,
        )
    elif isinstance(statement, UpdateStatement):
        return UpdateStatement(
            table=statement.table,
            assignments=statement.assignments,
            where=expanded,
            alias=statement.alias,
        )
    elif isinstance(statement, DeleteStatement):
        return DeleteStatement(
            table=statement.table,
            where=expanded,
            alias=statement.alias,
        )
    else:
        raise SemanticError(f"Unknown statement type: {type(statement).__name__}")


def _expand_where(
    where: WhereExpr | None,
    rule_map: dict[str, RuleDefinition],
    _expanding: Set[str],
) -> WhereExpr | None:
    """Recursively expand rule calls in a WHERE expression."""
    if where is None:
        return None

    if isinstance(where, RuleCall):
        rule = rule_map.get(where.name)
        if rule is None:
            raise SemanticError(f"Unknown rule: '{where.name}'")

        if where.name in _expanding:
            raise SemanticError(f"Recursive rule '{where.name}' is not supported.")

        _expanding.add(where.name)
        # Expand any rule calls within the rule body first
        expanded_body = _expand_where(rule.expression, rule_map, _expanding)
        _expanding.remove(where.name)

        if expanded_body is None:
            raise SemanticError(f"Rule '{where.name}' has empty body")

        # Substitute the parameter with the call argument
        result = _substitute_parameter(expanded_body, rule.parameter, where.argument)
        return result

    if isinstance(where, AndCondition):
        return AndCondition(
            left=_expand_where(where.left, rule_map, _expanding),
            right=_expand_where(where.right, rule_map, _expanding),
        )

    if isinstance(where, OrCondition):
        return OrCondition(
            left=_expand_where(where.left, rule_map, _expanding),
            right=_expand_where(where.right, rule_map, _expanding),
        )

    if isinstance(where, NotCondition):
        return NotCondition(
            child=_expand_where(where.child, rule_map, _expanding),
        )

    # Condition — no sub-rules
    return where
