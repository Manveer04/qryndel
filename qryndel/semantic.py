"""Semantic validation for public v0.1.0 queries."""

from __future__ import annotations

from typing import Any, Mapping

from qryndel.ast_nodes import AndCondition, Condition, FieldPath, NotCondition, NullPredicate, OrCondition, Query
from qryndel.errors import UdlError


class SemanticError(UdlError):
    code = "UDL_SEMANTIC_ERROR"

    def __init__(self, message: str):
        super().__init__(message, code=self.code)


ValidationError = SemanticError


def _fields(expression: Any):
    if isinstance(expression, (Condition, NullPredicate)):
        yield expression.field
    elif isinstance(expression, (AndCondition, OrCondition)):
        yield from _fields(expression.left)
        yield from _fields(expression.right)
    elif isinstance(expression, NotCondition):
        yield from _fields(expression.child)


def validate(query: Query, schema: Mapping[str, Any] | None = None) -> None:
    if not isinstance(query, Query):
        raise SemanticError("Only table queries are supported in v0.1.0")
    if not query.source.name:
        raise SemanticError("A source table is required")
    fields = list(query.fields)
    if not fields:
        raise SemanticError("RETURN requires at least one field")
    if schema is None:
        return
    table_schema = schema.get(query.source.name)
    if table_schema is None:
        raise SemanticError(f"Unknown source: '{query.source.name}'")
    available = set(table_schema) if isinstance(table_schema, (dict, list, tuple, set)) else set()
    for field in fields + list(_fields(query.where)):
        if field.name not in available:
            raise SemanticError(f"Unknown field '{field.name}' in source '{query.source.name}'")
