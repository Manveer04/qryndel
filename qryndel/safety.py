"""Fail-closed mutation policies and read-only mutation previews."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from typing import Any, FrozenSet, Mapping

from qryndel.backends.sqlite import _compile_where_expr_params, _safe_identifier
from qryndel.errors import UdlError
from qryndel.executor import ExecutionError, _connect
from qryndel.logical_plan import DeletePlan, InsertPlan, PlanNode, TransactionPlan, UpdatePlan


class SafetyError(UdlError):
    """Raised when a mutation is blocked by policy."""

    code = "UDL_SAFETY_BLOCKED"


@dataclass(frozen=True)
class MutationPolicy:
    """Rules checked before a mutation is allowed to run.

    ``allowed_tables`` empty means every table is allowed.
    """

    read_only: bool = False
    allowed_tables: FrozenSet[str] = field(default_factory=frozenset)
    require_where: bool = True


@dataclass(frozen=True)
class SafetyAssessment:
    allowed: bool
    reasons: tuple[str, ...] = ()
    tables: tuple[str, ...] = ()
    is_mutation: bool = False


@dataclass(frozen=True)
class PreviewReport:
    """Rows a mutation would touch, computed without changing data."""

    operation: str
    tables: tuple[str, ...]
    estimated_rows: int
    mutates_data: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "tables": list(self.tables),
            "estimated_rows": self.estimated_rows,
            "mutates_data": self.mutates_data,
        }


WritePlan = InsertPlan | UpdatePlan | DeletePlan


def _writes(plan: PlanNode) -> list[WritePlan]:
    if isinstance(plan, (InsertPlan, UpdatePlan, DeletePlan)):
        return [plan]
    if isinstance(plan, TransactionPlan):
        return list(plan.statements)
    return []


def is_mutation(plan: PlanNode) -> bool:
    return bool(_writes(plan))


def assess_mutation(plan: PlanNode, policy: MutationPolicy) -> SafetyAssessment:
    writes = _writes(plan)
    if not writes:
        return SafetyAssessment(True, tables=(), is_mutation=False)

    reasons: list[str] = []
    tables = tuple(dict.fromkeys(statement.table for statement in writes))
    if policy.read_only:
        reasons.append("client is read-only")
    if policy.allowed_tables:
        denied = sorted(set(tables) - set(policy.allowed_tables))
        if denied:
            reasons.append(f"tables are not permitted: {', '.join(denied)}")
    if policy.require_where:
        unbounded = [
            statement.table
            for statement in writes
            if isinstance(statement, (UpdatePlan, DeletePlan)) and statement.condition is None
        ]
        if unbounded:
            reasons.append(f"mutation requires WHERE: {', '.join(unbounded)}")
    return SafetyAssessment(not reasons, tuple(reasons), tables, True)


def enforce_mutation_policy(plan: PlanNode, policy: MutationPolicy) -> None:
    assessment = assess_mutation(plan, policy)
    if not assessment.allowed:
        raise SafetyError("mutation blocked: " + "; ".join(assessment.reasons))


def _count_sql(statement: WritePlan, parameters: Mapping[str, Any] | None) -> tuple[str, list[Any]]:
    table = _safe_identifier(statement.table)
    alias = _safe_identifier(statement.alias) if statement.alias else None
    from_sql = f"{table} AS {alias}" if alias else table
    if statement.condition is None:
        return f"SELECT COUNT(*) FROM {from_sql}", []
    params: list[Any] = []
    known_tables = {statement.table, *([statement.alias] if statement.alias else [])}
    where_sql = _compile_where_expr_params(
        statement.condition, known_tables=known_tables, params=params, parameters=parameters
    )
    return f"SELECT COUNT(*) FROM {from_sql} WHERE {where_sql}", params


def preview_mutation(
    plan: PlanNode,
    db_path: str | os.PathLike[str],
    parameters: Mapping[str, Any] | None = None,
) -> PreviewReport:
    """Count the rows each write in *plan* would affect, without writing."""
    writes = _writes(plan)
    if not writes:
        raise SafetyError("only mutations can be previewed")
    total = 0
    operations: list[str] = []
    connection = None
    try:
        connection = _connect(db_path)
        for statement in writes:
            if isinstance(statement, InsertPlan):
                operations.append("INSERT")
                total += 1
                continue
            operations.append("UPDATE" if isinstance(statement, UpdatePlan) else "DELETE")
            sql, params = _count_sql(statement, parameters)
            total += int(connection.execute(sql, params).fetchone()[0])
    except sqlite3.Error as exc:
        raise ExecutionError(f"SQLite error: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()
    return PreviewReport(
        operation="/".join(operations),
        tables=tuple(dict.fromkeys(statement.table for statement in writes)),
        estimated_rows=total,
    )
