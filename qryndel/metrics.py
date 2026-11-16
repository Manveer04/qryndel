"""Opt-in execution metrics with no query-value or row-content capture."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExecutionMetrics:
    backend: str
    elapsed_ms: float
    rows_returned: int = 0
    rows_affected: int = 0
    result_bytes: int | None = None  # measured only under a max_result_bytes budget
    budget_applied: bool = False
    budget_exceeded: bool = False
    result_complete: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "elapsed_ms": self.elapsed_ms,
            "rows_returned": self.rows_returned,
            "rows_affected": self.rows_affected,
            "result_bytes": self.result_bytes,
            "budget_applied": self.budget_applied,
            "budget_exceeded": self.budget_exceeded,
            "result_complete": self.result_complete,
        }
