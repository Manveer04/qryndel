"""Abstract syntax tree nodes for the Qryndel v0.2.0 language."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union


# ---------------------------------------------------------------------------
# Core nodes (table queries)
# ---------------------------------------------------------------------------

@dataclass
class Source:
    """The data source (table name, optionally aliased)."""
    name: str
    alias: Optional[str] = None

    def __repr__(self) -> str:
        if self.alias:
            return f"Source({self.name} AS {self.alias})"
        return f"Source({self.name})"


@dataclass
class FieldPath:
    """A field/column reference supporting table qualifiers and nested paths.

    ``qualifier`` is set when the reference explicitly starts with a table name
    or alias (e.g. ``users.name`` or ``u.profile.address.city`` after
    semantic resolution).
    ``parts`` holds all dot-separated segments of the path.
    """
    parts: List[str]
    qualifier: Optional[str] = None

    @property
    def name(self) -> str:
        """The terminal field name (last segment)."""
        return self.parts[-1]

    def __repr__(self) -> str:
        dotted = ".".join(self.parts)
        if self.qualifier:
            return f"FieldPath({self.qualifier}.{dotted})"
        return f"FieldPath({dotted})"


@dataclass(frozen=True)
class AggregateField:
    """An aggregate projection such as ``COUNT(*)`` or ``SUM(amount)``."""
    function: str
    field: Optional[FieldPath] = None

    def __repr__(self) -> str:
        argument = "*" if self.field is None else self.field
        return f"AggregateField({self.function}({argument}))"


@dataclass(frozen=True)
class GroupByClause:
    fields: List[FieldPath]


@dataclass(frozen=True)
class HavingClause:
    expression: WhereExpr


# ---------------------------------------------------------------------------
# WHERE expression tree
# ---------------------------------------------------------------------------

@dataclass
class Condition:
    """A binary comparison: field op value."""
    field: Union[FieldPath, AggregateField]
    operator: str
    value: Any

    def __repr__(self) -> str:
        return f"Condition({self.field} {self.operator} {self.value!r})"


@dataclass
class NullPredicate:
    """A null or document-existence predicate on a field."""
    field: FieldPath
    predicate: str

    def __repr__(self) -> str:
        return f"NullPredicate({self.field} {self.predicate})"


@dataclass(frozen=True)
class ParameterRef:
    """A named runtime value, such as ``$username``."""
    name: str

    def __repr__(self) -> str:
        return f"ParameterRef(${self.name})"


@dataclass
class AndCondition:
    """Boolean AND of two sub-expressions."""
    left: WhereExpr
    right: WhereExpr

    def __repr__(self) -> str:
        return f"({self.left} AND {self.right})"


@dataclass
class OrCondition:
    """Boolean OR of two sub-expressions."""
    left: WhereExpr
    right: WhereExpr

    def __repr__(self) -> str:
        return f"({self.left} OR {self.right})"


@dataclass
class NotCondition:
    """Boolean NOT of a sub-expression."""
    child: WhereExpr

    def __repr__(self) -> str:
        return f"(NOT {self.child})"


# ---------------------------------------------------------------------------
# Rule call
# ---------------------------------------------------------------------------

@dataclass
class RuleCall:
    """A call to a defined rule, e.g. ``is_adult(u)``."""
    name: str
    argument: FieldPath

    def __repr__(self) -> str:
        return f"RuleCall({self.name}({self.argument}))"


WhereExpr = Union[
    Condition,
    NullPredicate,
    AndCondition,
    OrCondition,
    NotCondition,
    RuleCall,
]


# ---------------------------------------------------------------------------
# ORDER BY
# ---------------------------------------------------------------------------

@dataclass
class OrderField:
    """A single field in an ORDER BY clause with optional direction."""
    field: FieldPath
    direction: str = "ASC"
    nulls: Optional[str] = None

    def __repr__(self) -> str:
        suffix = f" NULLS {self.nulls}" if self.nulls else ""
        return f"{self.field} {self.direction}{suffix}".strip()


# ---------------------------------------------------------------------------
# JOIN support
# ---------------------------------------------------------------------------

@dataclass
class JoinCondition:
    """A binary comparison of two fields: left op right."""
    left: FieldPath
    operator: str
    right: FieldPath

    def __repr__(self) -> str:
        return f"JoinCondition({self.left} {self.operator} {self.right})"


@dataclass
class Join:
    """A single JOIN clause (right side)."""
    join_type: str  # "INNER", "LEFT", "RIGHT", "FULL"
    right: Source
    alias: Optional[str] = None
    condition: Optional[JoinCondition] = None

    def __repr__(self) -> str:
        alias_s = f" AS {self.alias}" if self.alias else ""
        return f"{self.join_type} JOIN {self.right.name}{alias_s} ON {self.condition}"


# ---------------------------------------------------------------------------
# Top-level Query (table)
# ---------------------------------------------------------------------------

@dataclass
class Query:
    """Top-level AST node for a complete Qryndel FROM query."""
    source: Source
    fields: List[Union[FieldPath, AggregateField]]
    where: Optional[WhereExpr] = None
    order: Optional[List[OrderField]] = None
    limit: Optional[int] = None
    distinct: bool = False
    joins: List[Join] = field(default_factory=list)
    group_by: Optional[List[FieldPath]] = None
    having: Optional[WhereExpr] = None

    def __repr__(self) -> str:
        parts = [
            f"Query(\n"
            f"  source={self.source},\n"
            f"  fields={self.fields},\n"
            f"  where={self.where},\n"
            f"  order={self.order},\n"
            f"  limit={self.limit},\n"
            f"  distinct={self.distinct},\n"
            f"  group_by={self.group_by},\n"
            f"  having={self.having},\n"
            f"  joins={self.joins}\n"
            f")"
        ]
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Type definitions, rule definitions, and Program
# ---------------------------------------------------------------------------

@dataclass
class TypeField:
    """A field declared by a language-level TYPE definition."""
    name: str
    type_name: str
    required: bool = False


@dataclass
class TypeDefinition:
    """A named UDL schema declaration, such as ``TYPE Student { ... }``."""
    name: str
    fields: List[TypeField]
    allow_extra: bool = False

@dataclass
class RuleDefinition:
    """A single rule definition, e.g. ``RULE is_adult(x) = x.age >= 18``."""
    name: str
    parameter: str
    expression: WhereExpr

    def __repr__(self) -> str:
        return f"RuleDefinition({self.name}({self.parameter}) = {self.expression})"


@dataclass
class Assignment:
    """A single field = value assignment (for UPDATE)."""
    field: FieldPath
    value: Any

    def __repr__(self) -> str:
        return f"Assignment({self.field} = {self.value!r})"


@dataclass
class InsertStatement:
    """INSERT INTO table { field: value, ... }."""
    table: str
    values: Dict[str, Any]
    alias: Optional[str] = None

    def __repr__(self) -> str:
        return f"InsertStatement({self.table}, {self.values})"


@dataclass
class UpdateStatement:
    """UPDATE table SET field = value, ... WHERE ..."""
    table: str
    assignments: List[Assignment]
    where: Optional[WhereExpr] = None
    alias: Optional[str] = None

    def __repr__(self) -> str:
        return f"UpdateStatement({self.table}, {self.assignments}, where={self.where})"


@dataclass
class DeleteStatement:
    """DELETE FROM table WHERE ..."""
    table: str
    where: Optional[WhereExpr] = None
    alias: Optional[str] = None

    def __repr__(self) -> str:
        return f"DeleteStatement({self.table}, where={self.where})"


@dataclass
class TransactionStatement:
    """A local transaction containing multiple write statements."""
    statements: List[Union[InsertStatement, UpdateStatement, DeleteStatement]]
    action: str = "COMMIT"


Statement = Union[
    Query,
    InsertStatement,
    UpdateStatement,
    DeleteStatement,
    TransactionStatement,
]


@dataclass
class Program:
    """A complete program: optional TYPE/RULE declarations and one statement."""
    types: List[TypeDefinition]
    rules: List[RuleDefinition]
    statement: Statement

    @property
    def query(self) -> Query:
        """Return the statement when it is a table ``Query``."""
        if isinstance(self.statement, Query):
            return self.statement
        raise TypeError(f"Program.statement is not a query: {type(self.statement).__name__}")

    def __repr__(self) -> str:
        return f"Program(types={self.types}, rules={self.rules}, statement={self.statement!r})"
