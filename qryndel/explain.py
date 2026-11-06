"""Explain reports for compiled plans. Parameter values are always redacted."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from qryndel.capabilities import required_capabilities
from qryndel.logical_plan import CompiledQuery, CompiledTransaction, PlanNode


REDACTED = "<redacted>"


@dataclass(frozen=True)
class ExplainReport:
    """What a query will do, produced without executing it."""

    backend: str
    logical_plan: str
    required_capabilities: tuple[str, ...]
    compiled: Any
    safety: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "safety": self.safety,
            "required_capabilities": list(self.required_capabilities),
            "logical_plan": self.logical_plan,
            "compiled": self.compiled,
        }


def _redacted_compiled(compiled: Any) -> Any:
    if isinstance(compiled, CompiledQuery):
        return {
            "sql": compiled.sql,
            "params": [REDACTED for _ in compiled.params],
            "is_write": compiled.is_write,
        }
    if isinstance(compiled, CompiledTransaction):
        return {
            "action": compiled.action,
            "statements": [_redacted_compiled(statement) for statement in compiled.statements],
        }
    return REDACTED


def explain_plan(plan: PlanNode, compiler: Any, compiled: Any) -> ExplainReport:
    """Build an explain report without executing the compiled plan."""
    if isinstance(compiled, CompiledTransaction):
        safety = f"transaction ({compiled.action})"
    elif isinstance(compiled, CompiledQuery) and compiled.is_write:
        safety = "mutation"
    else:
        safety = "read-only"
    return ExplainReport(
        backend=compiler.name,
        logical_plan=repr(plan),
        required_capabilities=tuple(sorted(required_capabilities(plan))),
        compiled=_redacted_compiled(compiled),
        safety=safety,
    )


def format_explain(report: ExplainReport) -> str:
    """Render an explain report as text without exposing parameter values."""
    lines = [
        f"EXPLAIN ({report.backend})",
        f"Safety: {report.safety}",
        f"Required capabilities: {', '.join(report.required_capabilities) or '(none)'}",
        f"Logical plan: {report.logical_plan}",
    ]
    compiled = report.compiled
    statements = compiled.get("statements") if isinstance(compiled, dict) else None
    if statements is not None:
        lines.append(f"Transaction: {compiled['action']}")
        for index, statement in enumerate(statements, 1):
            lines.append(f"Statement {index}: {statement['sql']}")
            lines.append(f"Params {index}: {statement['params']}")
    elif isinstance(compiled, dict):
        lines.append(f"SQL: {compiled['sql']}")
        lines.append(f"Params: {compiled['params']}")
    return "\n".join(lines)
