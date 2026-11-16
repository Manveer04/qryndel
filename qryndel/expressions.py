"""Helpers for value expressions (fields, literals, aggregates, arithmetic)."""

from __future__ import annotations

from typing import Any, Callable, Iterator

from qryndel.ast_nodes import AggregateField, BinaryOp, Convert, FieldPath, Negate, ParameterRef, ProjectionItem

_PRECEDENCE = {"+": 1, "-": 1, "*": 2, "/": 2}


def iter_fields(expr: Any) -> Iterator[FieldPath]:
    """Yield every FieldPath in *expr*, including inside aggregates."""
    if isinstance(expr, FieldPath):
        yield expr
    elif isinstance(expr, AggregateField):
        if expr.field is not None:
            yield from iter_fields(expr.field)
    elif isinstance(expr, BinaryOp):
        yield from iter_fields(expr.left)
        yield from iter_fields(expr.right)
    elif isinstance(expr, (Negate, Convert)):
        yield from iter_fields(expr.operand)
    elif isinstance(expr, ProjectionItem):
        yield from iter_fields(expr.expression)


def iter_aggregates(expr: Any) -> Iterator[AggregateField]:
    """Yield every aggregate in *expr* (outermost first)."""
    if isinstance(expr, AggregateField):
        yield expr
        if expr.field is not None:
            yield from iter_aggregates(expr.field)
    elif isinstance(expr, BinaryOp):
        yield from iter_aggregates(expr.left)
        yield from iter_aggregates(expr.right)
    elif isinstance(expr, (Negate, Convert)):
        yield from iter_aggregates(expr.operand)
    elif isinstance(expr, ProjectionItem):
        yield from iter_aggregates(expr.expression)


def contains_aggregate(expr: Any) -> bool:
    return next(iter_aggregates(expr), None) is not None


def is_computed(expr: Any) -> bool:
    """True when *expr* uses arithmetic (anywhere, including in aggregates)."""
    if isinstance(expr, (BinaryOp, Negate, Convert)):
        return True
    if isinstance(expr, AggregateField):
        return expr.field is not None and is_computed(expr.field)
    if isinstance(expr, ProjectionItem):
        return is_computed(expr.expression)
    return False


def map_fields(expr: Any, transform: Callable[[FieldPath], FieldPath]) -> Any:
    """Return a copy of *expr* with every FieldPath replaced by transform(field)."""
    if isinstance(expr, FieldPath):
        return transform(expr)
    if isinstance(expr, AggregateField):
        return AggregateField(
            function=expr.function,
            field=None if expr.field is None else map_fields(expr.field, transform),
        )
    if isinstance(expr, BinaryOp):
        return BinaryOp(op=expr.op, left=map_fields(expr.left, transform), right=map_fields(expr.right, transform))
    if isinstance(expr, Negate):
        return Negate(operand=map_fields(expr.operand, transform))
    if isinstance(expr, Convert):
        return Convert(target=expr.target, operand=map_fields(expr.operand, transform))
    return expr


def to_text(expr: Any) -> str:
    """Render *expr* back to UDL text; used as the default result-column name."""
    if isinstance(expr, ProjectionItem):
        return expr.alias or to_text(expr.expression)
    if isinstance(expr, FieldPath):
        return ".".join(expr.parts)
    if isinstance(expr, AggregateField):
        return f"{expr.function}({'*' if expr.field is None else to_text(expr.field)})"
    if isinstance(expr, ParameterRef):
        return f"${expr.name}"
    if isinstance(expr, Convert):
        return f"{expr.target}({to_text(expr.operand)})"
    if isinstance(expr, Negate):
        inner = to_text(expr.operand)
        return f"-({inner})" if isinstance(expr.operand, BinaryOp) else f"-{inner}"
    if isinstance(expr, BinaryOp):
        level = _PRECEDENCE[expr.op]
        left = to_text(expr.left)
        right = to_text(expr.right)
        if isinstance(expr.left, BinaryOp) and _PRECEDENCE[expr.left.op] < level:
            left = f"({left})"
        if isinstance(expr.right, BinaryOp) and (
            _PRECEDENCE[expr.right.op] < level
            or (_PRECEDENCE[expr.right.op] == level and expr.op in "-/")
        ):
            right = f"({right})"
        return f"{left} {expr.op} {right}"
    if expr is None:
        return "NULL"
    if isinstance(expr, bool):
        return "TRUE" if expr else "FALSE"
    if isinstance(expr, str):
        return '"' + expr + '"' if "'" in expr else "'" + expr + "'"
    return str(expr)


def result_name(item: Any) -> str:
    """The key a RETURN item gets in result rows."""
    if isinstance(item, ProjectionItem) and item.alias:
        return item.alias
    return to_text(item)
