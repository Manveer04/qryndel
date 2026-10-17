"""Parameterized SQLite compiler for the public v0.1.0 language."""

from __future__ import annotations

import re
from typing import Any, Mapping

from qryndel.ast_nodes import AndCondition, Condition, FieldPath, NotCondition, NullPredicate, OrCondition, ParameterRef
from qryndel.backends.base import BackendCompiler
from qryndel.logical_plan import CompiledQuery, Filter, Project, Scan


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"Unsafe SQL identifier: {value!r}")
    return value


class ParameterError(ValueError):
    pass


class SqliteBackend(BackendCompiler):
    name = "sqlite"
    capabilities = frozenset({"from", "where", "return", "and", "or", "not", "parameters", "null_predicates"})

    def compile(self, plan: Project, parameters: Mapping[str, Any] | None = None) -> CompiledQuery:
        params = dict(parameters or {})
        source = plan.child.child if isinstance(plan.child, Filter) else plan.child
        assert isinstance(source, Scan)
        table = _identifier(source.table_name)
        from_sql = table
        if source.alias:
            from_sql += f" AS {_identifier(source.alias)}"
        columns = ", ".join(_field_sql(field, source.alias) for field in plan.columns)
        sql = f"SELECT {columns}\nFROM {from_sql}"
        values: list[Any] = []
        if isinstance(plan.child, Filter):
            where_sql, where_values = _expression_sql(plan.child.predicate, source.alias, params)
            sql += f"\nWHERE {where_sql}"
            values.extend(where_values)
        sql += ";"
        return CompiledQuery(sql=sql, params=values)

    def format_output(self, compiled: CompiledQuery) -> str:
        return f"SQL: {compiled.sql}\nParams: {compiled.params}"


def _field_sql(field: FieldPath, alias: str | None) -> str:
    if len(field.parts) != 1:
        raise ValueError("nested fields are not part of v0.1.0")
    name = _identifier(field.name)
    return f"{_identifier(alias)}.{name}" if alias else name


def _expression_sql(expression: Any, alias: str | None, parameters: Mapping[str, Any]):
    if isinstance(expression, Condition):
        field = _field_sql(expression.field, alias)
        value = expression.value
        if isinstance(value, ParameterRef):
            if value.name not in parameters:
                raise ParameterError(f"missing parameter: ${value.name}")
            return f"{field} {expression.operator} ?", [parameters[value.name]]
        if value is None:
            if expression.operator == "=":
                return f"{field} IS NULL", []
            if expression.operator == "!=":
                return f"{field} IS NOT NULL", []
        return f"{field} {expression.operator} ?", [value]
    if isinstance(expression, NullPredicate):
        return f"{_field_sql(expression.field, alias)} {'IS NULL' if expression.predicate == 'IS_NULL' else 'IS NOT NULL'}", []
    if isinstance(expression, AndCondition):
        left, left_values = _expression_sql(expression.left, alias, parameters)
        right, right_values = _expression_sql(expression.right, alias, parameters)
        return f"({left} AND {right})", left_values + right_values
    if isinstance(expression, OrCondition):
        left, left_values = _expression_sql(expression.left, alias, parameters)
        right, right_values = _expression_sql(expression.right, alias, parameters)
        return f"({left} OR {right})", left_values + right_values
    if isinstance(expression, NotCondition):
        child, values = _expression_sql(expression.child, alias, parameters)
        return f"(NOT ({child}))", values
    raise ValueError(f"unsupported expression: {type(expression).__name__}")
