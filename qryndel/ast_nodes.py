"""Abstract syntax tree nodes for the Qryndel v0.1.0 language."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Union


@dataclass(frozen=True)
class Source:
    name: str
    alias: str | None = None


@dataclass(frozen=True)
class FieldPath:
    parts: list[str]

    @property
    def name(self) -> str:
        return self.parts[-1]


@dataclass(frozen=True)
class ParameterRef:
    name: str


@dataclass(frozen=True)
class Condition:
    field: FieldPath
    operator: str
    value: Any


@dataclass(frozen=True)
class NullPredicate:
    field: FieldPath
    predicate: str


@dataclass(frozen=True)
class AndCondition:
    left: "WhereExpr"
    right: "WhereExpr"


@dataclass(frozen=True)
class OrCondition:
    left: "WhereExpr"
    right: "WhereExpr"


@dataclass(frozen=True)
class NotCondition:
    child: "WhereExpr"


WhereExpr = Union[Condition, NullPredicate, AndCondition, OrCondition, NotCondition]


@dataclass(frozen=True)
class Query:
    source: Source
    fields: list[FieldPath]
    where: WhereExpr | None = None
