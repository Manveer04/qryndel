"""Logical plans and compiled-query containers.

Read plan nodes:   Scan, Join, Filter, GroupBy, Having, Distinct, Sort,
                   Project, Limit
Write plan nodes:  InsertPlan, UpdatePlan, DeletePlan, TransactionPlan
Compiled output:   CompiledQuery (SQL with ``?`` placeholders plus params)
                   and CompiledTransaction.

Read plans are built bottom-up:
    Scan -> [Join] -> [Filter] -> [GroupBy] -> [Having] -> [Distinct]
         -> [Sort] -> Project -> [Limit]
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Union

from qryndel.ast_nodes import (
    Assignment,
    Condition,
    DeleteStatement,
    FieldPath,
    InsertStatement,
    JoinCondition,
    Join as JoinAst,
    OrderField,
    Query,
    Source,
    Statement,
    UpdateStatement,
    TransactionStatement,
    WhereExpr,
)


# ===================================================================
# Table plan node types
# ===================================================================

@dataclass
class Scan:
    """Read all rows from `table_name`."""
    table_name: str
    alias: Optional[str] = None

    def __repr__(self) -> str:
        if self.alias:
            return f"Scan({self.table_name} AS {self.alias})"
        return f"Scan({self.table_name})"


@dataclass
class Join:
    """Join two sub-plans on a condition."""
    join_type: str
    left: PlanNode
    right: PlanNode
    condition: Optional[JoinCondition] = None

    def __repr__(self) -> str:
        return f"Join({self.join_type}, {self.left}, {self.right}, {self.condition})"


@dataclass
class Filter:
    """Keep rows that satisfy `predicate`."""
    predicate: WhereExpr
    child: PlanNode

    def __repr__(self) -> str:
        return f"Filter({self.predicate}, {self.child})"


@dataclass
class GroupBy:
    fields: List[FieldPath]
    child: PlanNode


@dataclass
class Having:
    predicate: WhereExpr
    child: PlanNode


@dataclass
class Distinct:
    """Remove duplicate rows."""
    child: PlanNode

    def __repr__(self) -> str:
        return f"Distinct({self.child})"


@dataclass
class Sort:
    """Sort rows by the given fields."""
    order_fields: List[OrderField]
    child: PlanNode

    def __repr__(self) -> str:
        return f"Sort({self.order_fields}, {self.child})"


@dataclass
class Project:
    """Keep only the listed columns."""
    columns: List[FieldPath]
    child: PlanNode

    def __repr__(self) -> str:
        return f"Project({self.columns}, {self.child})"


@dataclass
class Limit:
    """Keep only `count` rows."""
    count: int
    child: PlanNode

    def __repr__(self) -> str:
        return f"Limit({self.count}, {self.child})"


TablePlanNode = Union[Scan, Join, Filter, GroupBy, Having, Distinct, Sort, Project, Limit]


# ===================================================================
# Write plan node types
# ===================================================================

@dataclass
class InsertPlan:
    """Insert rows into *table* with the given *values*."""
    table: str
    values: Dict[str, Any]

    def __repr__(self) -> str:
        return f"InsertPlan({self.table}, {self.values})"


@dataclass
class UpdatePlan:
    """Update rows in *table* matching *condition* with *assignments*."""
    table: str
    assignments: List[Assignment]
    condition: Optional[WhereExpr] = None
    alias: Optional[str] = None

    def __repr__(self) -> str:
        return f"UpdatePlan({self.table}, {self.assignments}, where={self.condition})"


@dataclass
class DeletePlan:
    """Delete rows from *table* matching *condition*."""
    table: str
    condition: Optional[WhereExpr] = None
    alias: Optional[str] = None

    def __repr__(self) -> str:
        return f"DeletePlan({self.table}, where={self.condition})"


WritePlanNode = Union[InsertPlan, UpdatePlan, DeletePlan]


@dataclass
class TransactionPlan:
    """A sequence of write plans executed atomically by a backend."""
    statements: List[WritePlanNode]
    action: str = "COMMIT"


PlanNode = Union[TablePlanNode, WritePlanNode, TransactionPlan]


# ===================================================================
# Plan builders
# ===================================================================

def _build_from(source: Source, joins: List[JoinAst]) -> PlanNode:
    """Build the FROM clause plan tree (Scan + optional Joins)."""
    node: PlanNode = Scan(table_name=source.name, alias=source.alias)
    for join in joins:
        right_scan = Scan(table_name=join.right.name, alias=join.alias)
        node = Join(
            join_type=join.join_type,
            left=node,
            right=right_scan,
            condition=join.condition,
        )
    return node


def build_plan(query: Query) -> PlanNode:
    """Build a table logical plan from a parsed and validated FROM query.

        Scan -> [Join] -> Filter -> GroupBy -> Having -> Distinct
             -> Sort -> Project -> Limit
    """
    node: PlanNode = _build_from(query.source, query.joins)

    if query.where is not None:
        node = Filter(predicate=query.where, child=node)

    if query.group_by is not None:
        node = GroupBy(fields=query.group_by, child=node)

    if query.having is not None:
        node = Having(predicate=query.having, child=node)

    if query.distinct:
        node = Distinct(child=node)

    if query.order is not None and len(query.order) > 0:
        node = Sort(order_fields=query.order, child=node)

    node = Project(columns=query.fields, child=node)

    if query.limit is not None:
        node = Limit(count=query.limit, child=node)

    return node


# ===================================================================
# Write plan builders
# ===================================================================

def build_insert_plan(stmt: InsertStatement) -> InsertPlan:
    """Build an InsertPlan from a validated InsertStatement."""
    return InsertPlan(table=stmt.table, values=stmt.values)


def build_update_plan(stmt: UpdateStatement) -> UpdatePlan:
    """Build an UpdatePlan from a validated UpdateStatement."""
    return UpdatePlan(
        table=stmt.table,
        assignments=stmt.assignments,
        condition=stmt.where,
        alias=stmt.alias,
    )


def build_delete_plan(stmt: DeleteStatement) -> DeletePlan:
    """Build a DeletePlan from a validated DeleteStatement."""
    return DeletePlan(table=stmt.table, condition=stmt.where, alias=stmt.alias)


def build_transaction_plan(stmt: TransactionStatement) -> TransactionPlan:
    """Build a transaction plan from validated write statements."""
    plans: List[WritePlanNode] = []
    for statement in stmt.statements:
        if isinstance(statement, InsertStatement):
            plans.append(build_insert_plan(statement))
        elif isinstance(statement, UpdateStatement):
            plans.append(build_update_plan(statement))
        elif isinstance(statement, DeleteStatement):
            plans.append(build_delete_plan(statement))
        else:
            raise TypeError(f"Transactions only support writes, got {type(statement).__name__}")
    return TransactionPlan(statements=plans, action=stmt.action)


# ===================================================================
# Statement dispatch and compiled output
# ===================================================================

def build_statement_plan(statement: Statement) -> PlanNode:
    """Build the logical plan for any validated statement."""
    if isinstance(statement, Query):
        return build_plan(statement)
    if isinstance(statement, InsertStatement):
        return build_insert_plan(statement)
    if isinstance(statement, UpdateStatement):
        return build_update_plan(statement)
    if isinstance(statement, DeleteStatement):
        return build_delete_plan(statement)
    if isinstance(statement, TransactionStatement):
        return build_transaction_plan(statement)
    raise TypeError(f"Cannot plan statement of type {type(statement).__name__}")


@dataclass
class CompiledQuery:
    """Parameterized SQL output from backend compilation.

    ``sql`` contains ``?`` placeholders for all literal values.
    ``params`` holds the corresponding values in order.
    ``is_write`` is ``True`` for INSERT / UPDATE / DELETE.
    """
    sql: str
    params: List[Any] = field(default_factory=list)
    is_write: bool = False

    def __repr__(self) -> str:
        return f"CompiledQuery(sql={self.sql!r}, params={self.params})"


@dataclass
class CompiledTransaction:
    """Compiled statements that must execute on one SQLite connection."""
    statements: List[CompiledQuery]
    action: str = "COMMIT"
