"""Qryndel, an experimental SQLite implementation of Unified Data Language."""

__version__ = "0.3.0"

from qryndel.budgets import BudgetExceededError, QueryBudget
from qryndel.contracts import (
    Compare,
    ContractViolation,
    NoMissing,
    NonEmpty,
    NonNull,
    ResultContract,
    RowCount,
    Unique,
)
from qryndel.errors import SourceSpan, UdlError
from qryndel.executor import DatabaseLockedError, ExecutionError
from qryndel.explain import ExplainReport
from qryndel.metrics import ExecutionMetrics
from qryndel.safety import PreviewReport, SafetyError
from qryndel.schema import load_sqlite_schema
from qryndel.schema_drift import SchemaDriftReport, detect_type_drift
from qryndel.sdk import Client, QueryResult

__all__ = [
    "BudgetExceededError",
    "Client",
    "Compare",
    "ContractViolation",
    "DatabaseLockedError",
    "ExecutionError",
    "ExecutionMetrics",
    "ExplainReport",
    "NoMissing",
    "NonEmpty",
    "NonNull",
    "PreviewReport",
    "QueryBudget",
    "QueryResult",
    "ResultContract",
    "RowCount",
    "SafetyError",
    "SchemaDriftReport",
    "SourceSpan",
    "UdlError",
    "Unique",
    "__version__",
    "detect_type_drift",
    "load_sqlite_schema",
]
