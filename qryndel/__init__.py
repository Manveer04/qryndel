"""Qryndel, an experimental SQLite implementation of Unified Data Language."""

__version__ = "0.2.0"

from qryndel.budgets import BudgetExceededError, QueryBudget
from qryndel.errors import SourceSpan, UdlError
from qryndel.explain import ExplainReport
from qryndel.metrics import ExecutionMetrics
from qryndel.safety import PreviewReport, SafetyError
from qryndel.sdk import Client, QueryResult

__all__ = [
    "BudgetExceededError",
    "Client",
    "ExecutionMetrics",
    "ExplainReport",
    "PreviewReport",
    "QueryBudget",
    "QueryResult",
    "SafetyError",
    "SourceSpan",
    "UdlError",
    "__version__",
]
