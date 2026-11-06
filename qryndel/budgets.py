"""Execution budgets shared by runtime adapters."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any
from qryndel.errors import UdlError


class BudgetExceededError(UdlError):
    code = "UDL_BUDGET_EXCEEDED"


@dataclass(frozen=True)
class QueryBudget:
    max_duration_ms: int | None = None
    max_result_rows: int | None = None
    max_result_bytes: int | None = None
    max_affected_rows: int | None = None

    def __post_init__(self) -> None:
        if self.max_duration_ms is not None and self.max_duration_ms <= 0:
            raise ValueError("max_duration_ms must be positive")
        if self.max_result_rows is not None and self.max_result_rows < 0:
            raise ValueError("max_result_rows must not be negative")
        if self.max_result_bytes is not None and self.max_result_bytes <= 0:
            raise ValueError("max_result_bytes must be positive")
        if self.max_affected_rows is not None and self.max_affected_rows < 0:
            raise ValueError("max_affected_rows must not be negative")


def enforce_result_budget(result: Any, budget: QueryBudget | None) -> None:
    """Enforce result limits after an adapter returns data."""
    if budget is None:
        return
    if isinstance(result, list):
        if budget.max_result_rows is not None and len(result) > budget.max_result_rows:
            raise BudgetExceededError(
                f"result exceeds max_result_rows={budget.max_result_rows}"
            )
    if budget.max_result_bytes is not None:
        encoded = json.dumps(result, ensure_ascii=False, default=str, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > budget.max_result_bytes:
            raise BudgetExceededError(
                f"result exceeds max_result_bytes={budget.max_result_bytes}"
            )
