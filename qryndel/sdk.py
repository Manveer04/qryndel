"""Python API for compiling and executing UDL against SQLite."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

from qryndel.backends import get_backend
from qryndel.budgets import QueryBudget
from qryndel.executor import execute_with_metrics
from qryndel.explain import ExplainReport, explain_plan
from qryndel.logical_plan import PlanNode, build_statement_plan
from qryndel.metrics import ExecutionMetrics
from qryndel.optimizer import optimize_plan
from qryndel.parser import parse_program
from qryndel.safety import MutationPolicy, PreviewReport, enforce_mutation_policy, preview_mutation
from qryndel.semantic import validate


@dataclass(frozen=True)
class QueryResult:
    """The outcome of one executed statement.

    Reads fill ``rows``. Writes leave ``rows`` empty and report
    ``rows_affected`` (and ``last_insert_id`` for a single INSERT).
    Transactions also set ``transaction`` to ``"COMMIT"`` or ``"ROLLBACK"``
    and list each statement's outcome in ``statements``.
    """

    rows: list[dict[str, Any]]
    backend: str = "sqlite"
    compiled: Any = None
    rows_affected: int | None = None
    last_insert_id: int | None = None
    transaction: str | None = None
    statements: tuple[dict[str, Any], ...] = ()
    metrics: ExecutionMetrics | None = None

    @property
    def is_write(self) -> bool:
        return self.rows_affected is not None


class _RedactedParameters(dict):
    """Binds every ``$name`` to ``None`` so a query can be explained unbound."""

    def __contains__(self, key: object) -> bool:
        return True

    def __getitem__(self, key: str) -> Any:
        return None


def _coerce_budget(budget: QueryBudget | Mapping[str, Any] | None) -> QueryBudget | None:
    if budget is None or isinstance(budget, QueryBudget):
        return budget
    allowed = {"max_duration_ms", "max_result_rows", "max_result_bytes", "max_affected_rows"}
    unknown = set(budget) - allowed
    if unknown:
        raise ValueError(f"unknown budget keys: {', '.join(sorted(unknown))}")
    return QueryBudget(**budget)


class Client:
    """Compile and execute UDL against one SQLite database file.

    ``schema`` optionally maps table names to field names (a list) or to a
    nested dict of fields; TYPE declarations in a program are merged on top.
    ``read_only=True`` rejects every write. ``allowed_tables`` limits which
    tables writes may touch.
    """

    def __init__(
        self,
        db_path: str | os.PathLike[str],
        *,
        schema: Optional[Mapping[str, Any]] = None,
        read_only: bool = False,
        allowed_tables: Optional[Iterable[str]] = None,
    ):
        self.db_path = db_path
        self.schema = dict(schema) if schema is not None else None
        self.policy = MutationPolicy(
            read_only=read_only,
            allowed_tables=frozenset(allowed_tables or ()),
        )
        self._compiler = get_backend("sqlite")

    @property
    def connection(self) -> str | os.PathLike[str]:
        """Backward-compatible alias for ``db_path`` (v0.1.0)."""
        return self.db_path

    def plan(self, query: str) -> PlanNode:
        """Parse, validate and optimize *query* into a logical plan."""
        statement = validate(parse_program(query), schema=self.schema)
        plan = optimize_plan(build_statement_plan(statement))
        self._compiler.preflight(plan)
        return plan

    def compile(self, query: str, *, params: Mapping[str, Any] | None = None) -> Any:
        """Compile *query* to parameterized SQL without touching the database."""
        return self._compiler.compile(self.plan(query), parameters=params)

    def explain(self, query: str, *, params: Mapping[str, Any] | None = None) -> ExplainReport:
        """Describe the plan and SQL for *query*. Parameter values are redacted."""
        plan = self.plan(query)
        compiled = self._compiler.compile(
            plan, parameters=params if params is not None else _RedactedParameters()
        )
        return explain_plan(plan, self._compiler, compiled)

    def preview(self, query: str, *, params: Mapping[str, Any] | None = None) -> PreviewReport:
        """Count the rows a write would affect, without changing any data."""
        plan = self.plan(query)
        self._compiler.compile(plan, parameters=params)  # surfaces missing parameters
        return preview_mutation(plan, self.db_path, params)

    def execute(
        self,
        query: str,
        *,
        params: Mapping[str, Any] | None = None,
        budget: QueryBudget | Mapping[str, Any] | None = None,
    ) -> QueryResult:
        """Compile and execute *query*, enforcing the client's mutation policy."""
        plan = self.plan(query)
        enforce_mutation_policy(plan, self.policy)
        compiled = self._compiler.compile(plan, parameters=params)
        result, metrics = execute_with_metrics(compiled, self.db_path, budget=_coerce_budget(budget))

        if isinstance(result, list):
            return QueryResult(rows=result, compiled=compiled, metrics=metrics)
        if "statements" in result:
            return QueryResult(
                rows=[],
                compiled=compiled,
                rows_affected=metrics.rows_affected,
                transaction=result["transaction"],
                statements=tuple(result["statements"]),
                metrics=metrics,
            )
        return QueryResult(
            rows=[],
            compiled=compiled,
            rows_affected=int(result["rows_affected"]),
            last_insert_id=result.get("last_insert_id"),
            metrics=metrics,
        )
