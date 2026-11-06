"""Backend capability declarations and logical-plan preflight checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet, Set

from qryndel.ast_nodes import (
    AggregateField,
    AndCondition,
    Condition,
    FieldPath,
    NotCondition,
    NullPredicate,
    OrCondition,
)
from qryndel.logical_plan import (
    Filter,
    GroupBy,
    Having,
    Join,
    PlanNode,
    Project,
    TransactionPlan,
)
from qryndel.errors import UdlError


@dataclass(frozen=True)
class Capabilities:
    """Features an adapter can support for a particular plan family."""

    backend: str
    select: bool = False
    nested_paths: bool = False
    inner_join: bool = False
    left_join: bool = False
    right_join: bool = False
    full_join: bool = False
    group_by: bool = False
    having: bool = False
    aggregate_functions: FrozenSet[str] = field(default_factory=frozenset)
    writes: bool = False
    transactions: bool = False
    parameter_binding: bool = False
    native_execution: bool = False
    null_ordering: bool = False

    def supports(self, feature: str) -> bool:
        if feature.startswith("aggregate:"):
            return feature.split(":", 1)[1] in self.aggregate_functions
        return bool(getattr(self, feature, False))


class CapabilityError(UdlError):
    """Raised when a backend cannot support a logical plan."""

    code = "UDL_CAPABILITY_UNSUPPORTED"

    def __init__(self, message: str, backend: str, capability: str):
        super().__init__(
            message,
            code=self.code,
            backend=backend,
            capability=capability,
            suggested_action="Choose a capable backend or rewrite the query.",
        )


def required_capabilities(plan: PlanNode) -> Set[str]:
    """Discover capability requirements without consulting a backend."""
    required: Set[str] = {"select"}

    def walk(node: PlanNode) -> None:
        def inspect_field(field: object) -> None:
            if isinstance(field, FieldPath) and len(field.parts) >= 3:
                required.add("nested_paths")
            if isinstance(field, AggregateField) and field.field is not None:
                inspect_field(field.field)

        def inspect_expression(expression: object) -> None:
            if isinstance(expression, Condition):
                inspect_field(expression.field)
            elif isinstance(expression, NullPredicate):
                inspect_field(expression.field)
            elif isinstance(expression, (AndCondition, OrCondition)):
                inspect_expression(expression.left)
                inspect_expression(expression.right)
            elif isinstance(expression, NotCondition):
                inspect_expression(expression.child)

        if isinstance(node, Join):
            required.add(f"{node.join_type.lower()}_join")
            walk(node.left)
            walk(node.right)
            return
        if isinstance(node, TransactionPlan):
            required.add("transactions")
            required.add("writes")
            return
        if node.__class__.__name__ in {"InsertPlan", "UpdatePlan", "DeletePlan"}:
            required.add("writes")
        if isinstance(node, GroupBy):
            required.add("group_by")
            for field in node.fields:
                inspect_field(field)
        if isinstance(node, Having):
            required.add("having")
            inspect_expression(node.predicate)
        if isinstance(node, Project):
            for column in node.columns:
                if isinstance(column, AggregateField):
                    required.add(f"aggregate:{column.function}")
                inspect_field(column)
        if isinstance(node, Filter):
            inspect_expression(node.predicate)
        if hasattr(node, "order_fields"):
            for order_field in node.order_fields:
                if order_field.nulls:
                    required.add("null_ordering")
                if isinstance(order_field.field, AggregateField):
                    if order_field.field.field is not None:
                        inspect_field(order_field.field.field)
                else:
                    inspect_field(order_field.field)
        if hasattr(node, "child"):
            walk(node.child)

    walk(plan)
    return required


def preflight(plan: PlanNode, capabilities: Capabilities) -> None:
    """Fail closed when a plan requires an undeclared backend feature."""
    for feature in sorted(required_capabilities(plan)):
        if not capabilities.supports(feature):
            if feature.endswith("_join"):
                join_type = feature[: -len("_join")].upper()
                message = (
                    f"Backend '{capabilities.backend}' does not support "
                    f"{join_type} JOIN (capability '{feature}')."
                )
            else:
                description = feature.replace("_", " ")
                message = (
                    f"Backend '{capabilities.backend}' does not support {description} "
                    f"(capability '{feature}')."
                )
            raise CapabilityError(message, backend=capabilities.backend, capability=feature)
