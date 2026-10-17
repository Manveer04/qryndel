"""Logical plan and SQLite compilation result for v0.1.0."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from qryndel.ast_nodes import FieldPath, Query, WhereExpr


@dataclass(frozen=True)
class Scan:
    table_name: str
    alias: str | None = None


@dataclass(frozen=True)
class Filter:
    predicate: WhereExpr
    child: Scan


@dataclass(frozen=True)
class Project:
    columns: list[FieldPath]
    child: Filter | Scan


PlanNode = Project


@dataclass(frozen=True)
class CompiledQuery:
    sql: str
    params: list[Any]


def build_plan(query: Query) -> Project:
    source = Scan(query.source.name, query.source.alias)
    child: Filter | Scan = Filter(query.where, source) if query.where else source
    return Project(query.fields, child)
