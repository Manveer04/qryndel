"""Safe logical-plan rewrites for the Qryndel prototype.

The first optimizer pass deliberately performs only transformations whose
semantics are independent of storage statistics: adjacent filters are fused,
and qualified predicates are pushed into either side of an INNER JOIN.
"""

from __future__ import annotations

from typing import Optional, Set

from qryndel.ast_nodes import (
    AndCondition,
    Condition,
    FieldPath,
    NotCondition,
    OrCondition,
    WhereExpr,
)
from qryndel.logical_plan import Filter, Join, PlanNode, Scan


def _tables(node: PlanNode) -> Set[str]:
    if isinstance(node, Scan):
        return {node.table_name, node.alias} if node.alias else {node.table_name}
    if isinstance(node, Join):
        return _tables(node.left) | _tables(node.right)
    if hasattr(node, "child"):
        return _tables(node.child)
    return set()


def _references(expr: WhereExpr) -> Optional[Set[str]]:
    """Return referenced qualifiers, or None when a predicate is unqualified."""
    if isinstance(expr, Condition):
        field = expr.field
        if not isinstance(field, FieldPath) or field.qualifier is None:
            return None
        return {field.qualifier}
    if isinstance(expr, (AndCondition, OrCondition)):
        left = _references(expr.left)
        right = _references(expr.right)
        if left is None or right is None:
            return None
        return left | right
    if isinstance(expr, NotCondition):
        return _references(expr.child)
    return None


def _fuse(left: WhereExpr, right: WhereExpr) -> AndCondition:
    return AndCondition(left=left, right=right)


def optimize_plan(plan: PlanNode) -> PlanNode:
    """Return an optimized plan without changing query results."""
    if isinstance(plan, Filter):
        child = optimize_plan(plan.child)
        if isinstance(child, Filter):
            return optimize_plan(Filter(
                predicate=_fuse(plan.predicate, child.predicate), child=child.child
            ))
        if isinstance(child, Join) and child.join_type == "INNER":
            refs = _references(plan.predicate)
            left_tables = _tables(child.left)
            right_tables = _tables(child.right)
            if refs and refs <= left_tables:
                return Join(
                    join_type=child.join_type,
                    left=optimize_plan(Filter(plan.predicate, child.left)),
                    right=child.right,
                    condition=child.condition,
                )
            if refs and refs <= right_tables:
                return Join(
                    join_type=child.join_type,
                    left=child.left,
                    right=optimize_plan(Filter(plan.predicate, child.right)),
                    condition=child.condition,
                )
        return Filter(predicate=plan.predicate, child=child)

    if isinstance(plan, Join):
        return Join(
            join_type=plan.join_type,
            left=optimize_plan(plan.left),
            right=optimize_plan(plan.right),
            condition=plan.condition,
        )

    if hasattr(plan, "child"):
        from dataclasses import replace
        return replace(plan, child=optimize_plan(plan.child))

    return plan
