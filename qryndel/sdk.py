"""Small Python API for the SQLite-focused v0.1.0 release."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from qryndel.backends import get_backend
from qryndel.executor import execute
from qryndel.logical_plan import build_plan
from qryndel.parser import parse_program
from qryndel.semantic import validate


@dataclass(frozen=True)
class QueryResult:
    """Rows returned by a SQLite query."""

    rows: list[dict[str, Any]]
    backend: str = "sqlite"
    compiled: Any = None


class Client:
    """Compile and execute v0.1.0 UDL against a SQLite database file."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.connection = db_path

    def _compile(self, query: str, params: Mapping[str, Any] | None = None):
        statement = parse_program(query)
        validate(statement)
        plan = build_plan(statement)
        compiler = get_backend("sqlite")
        return compiler.compile(plan, parameters=params)

    def compile(self, query: str, *, params: Mapping[str, Any] | None = None):
        """Compile a supported query without executing it."""
        return self._compile(query, params)

    def execute(self, query: str, *, params: Mapping[str, Any] | None = None) -> QueryResult:
        """Compile and execute a supported query against SQLite."""
        compiled = self._compile(query, params)
        rows = execute(compiled, self.db_path)
        if not isinstance(rows, list):
            raise TypeError("v0.1.0 only executes read queries")
        return QueryResult(rows=rows, compiled=compiled)
