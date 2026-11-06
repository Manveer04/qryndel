"""
SQLite backend compiler.

Compiles a logical plan into a parameterized ``CompiledQuery`` containing
``sql`` (with ``?`` placeholders) and ``params`` (the values). Every literal
and ``$parameter`` value is bound; identifiers are checked against a strict
pattern before they are written into SQL.

Compilation rules for FieldPath:
  qualifier=None, len(parts)==1 : simple column           -> name
  qualifier=Q,    len(parts)==1 : qualified column        -> Q.name
  qualifier=None, len(parts) >1 : JSON path (expr)        -> json_extract(parts[0], '$.rest')
                                  JSON path (projection)  -> json_extract(parts[0], '$.rest') AS "full.path"
  qualifier=Q,    len(parts) >1 : qualified JSON path     -> json_extract(Q.parts[0], '$.rest')
                                  qualified JSON path     -> json_extract(Q.parts[0], '$.rest') AS "Q.full.path"
"""

from __future__ import annotations
import re
import sqlite3
from typing import Any, Dict, List, Mapping

from qryndel.backends.base import BackendCompiler
from qryndel.capabilities import Capabilities
from qryndel.errors import UdlError
from qryndel.logical_plan import (
    CompiledQuery,
    CompiledTransaction,
    DeletePlan,
    Distinct as DistinctPlan,
    Filter,
    GroupBy as GroupByPlan,
    Having as HavingPlan,
    InsertPlan,
    Join,
    Limit as LimitPlan,
    PlanNode,
    Project,
    Scan,
    Sort,
    TransactionPlan,
    UpdatePlan,
)

class ParameterError(UdlError, ValueError):
    """Raised when a query parameter has no runtime value."""

    code = "UDL_PARAMETER_ERROR"


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"Unsafe SQL identifier: {value!r}")
    return value


from qryndel.ast_nodes import (
    AndCondition,
    AggregateField,
    Condition,
    FieldPath,
    JoinCondition,
    NotCondition,
    NullPredicate,
    OrCondition,
    WhereExpr,
    ParameterRef,
)


_SQLITE_VERSION = sqlite3.sqlite_version_info


class SqliteBackend(BackendCompiler):
    """Compiles a Qryndel logical plan to a parameterized ``CompiledQuery``."""

    name: str = "sqlite"
    capabilities = Capabilities(
        backend="sqlite",
        select=True,
        nested_paths=True,
        inner_join=True,
        left_join=True,
        # RIGHT and FULL OUTER JOIN were added in SQLite 3.39.0.
        right_join=_SQLITE_VERSION >= (3, 39, 0),
        full_join=_SQLITE_VERSION >= (3, 39, 0),
        group_by=True,
        having=True,
        aggregate_functions=frozenset({"COUNT", "SUM", "AVG", "MIN", "MAX"}),
        writes=True,
        transactions=True,
        parameter_binding=True,
        native_execution=True,
        # NULLS FIRST / NULLS LAST were added in SQLite 3.30.0.
        null_ordering=_SQLITE_VERSION >= (3, 30, 0),
    )

    def compile(
        self,
        plan: PlanNode,
        parameters: Mapping[str, Any] | None = None,
    ) -> CompiledQuery:
        """Compile a logical plan into a parameterized ``CompiledQuery``."""
        params: List[Any] = []

        if isinstance(plan, TransactionPlan):
            return CompiledTransaction(
                statements=[
                    self.compile(statement, parameters=parameters)
                    for statement in plan.statements
                ],
                action=plan.action,
            )

        # Handle write plans
        if isinstance(plan, InsertPlan):
            sql = _compile_insert_params(plan, params, parameters)
            return CompiledQuery(sql=sql, params=params, is_write=True)
        if isinstance(plan, UpdatePlan):
            sql = _compile_update_params(plan, params, parameters)
            return CompiledQuery(sql=sql, params=params, is_write=True)
        if isinstance(plan, DeletePlan):
            sql = _compile_delete_params(plan, params, parameters)
            return CompiledQuery(sql=sql, params=params, is_write=True)

        # Read-only SELECT path
        columns: list[FieldPath] = []
        distinct = False
        where_expr = None
        having_expr = None
        group_fields = []
        order_fields = []
        limit_count = None

        node = plan
        while True:
            if isinstance(node, Project):
                columns = node.columns
            elif isinstance(node, Filter):
                where_expr = node.predicate
            elif isinstance(node, GroupByPlan):
                group_fields = node.fields
            elif isinstance(node, HavingPlan):
                having_expr = node.predicate
            elif isinstance(node, Sort):
                order_fields = node.order_fields
            elif isinstance(node, LimitPlan):
                limit_count = node.count
            elif isinstance(node, DistinctPlan):
                distinct = True

            if hasattr(node, "child"):
                node = node.child
            else:
                break

        # Filters may be pushed into join branches by the optimizer.
        predicates = []
        having_predicate = having_expr

        def collect_filters(current: PlanNode) -> None:
            if isinstance(current, Filter):
                predicates.append(current.predicate)
            elif isinstance(current, HavingPlan):
                nonlocal having_predicate
                having_predicate = current.predicate
            if isinstance(current, Join):
                collect_filters(current.left)
                collect_filters(current.right)
            elif hasattr(current, "child"):
                collect_filters(current.child)

        collect_filters(plan)
        where_expr = None
        for predicate in predicates:
            where_expr = predicate if where_expr is None else AndCondition(where_expr, predicate)
        having_expr = having_predicate

        known_tables = _collect_tables(node)
        from_sql = _compile_from_clause(node, known_tables)

        distinct_sql = "DISTINCT " if distinct else ""
        columns_sql = ", ".join(_compile_projection(f, known_tables) for f in columns)

        parts = [f"SELECT {distinct_sql}{columns_sql}", f"FROM {from_sql}"]

        if where_expr is not None:
            where_sql = _compile_where_expr_params(
                where_expr, known_tables, params, parameters
            )
            parts.append(f"WHERE {where_sql}")

        if group_fields:
            parts.append(
                "GROUP BY " + ", ".join(
                    _compile_field_expr(field, known_tables) for field in group_fields
                )
            )

        if having_expr is not None:
            having_sql = _compile_where_expr_params(
                having_expr, known_tables, params, parameters
            )
            parts.append(f"HAVING {having_sql}")

        if order_fields:
            order_parts = []
            for of in order_fields:
                dir_sql = f" {of.direction}" if of.direction else ""
                nulls_sql = f" NULLS {of.nulls}" if of.nulls else ""
                order_expr = (
                    _compile_projection(of.field, known_tables)
                    if isinstance(of.field, AggregateField)
                    else _compile_field_expr(of.field, known_tables)
                )
                order_parts.append(f"{order_expr}{dir_sql}{nulls_sql}")
            parts.append(f"ORDER BY {', '.join(order_parts)}")

        if limit_count is not None:
            parts.append(f"LIMIT {limit_count}")

        sql = "\n".join(parts) + ";"
        return CompiledQuery(sql=sql, params=params, is_write=False)

    def compile_sql(self, plan: PlanNode) -> str:
        """Backward-compat helper: return raw SQL string only."""
        return self.compile(plan).sql

    def format_output(self, compiled: CompiledQuery) -> str:
        """Render a compiled query as a human-readable string."""
        if isinstance(compiled, CompiledTransaction):
            lines = [f"Transaction: {compiled.action}"]
            for index, statement in enumerate(compiled.statements, 1):
                lines.append(f"Statement {index}: {statement.sql}")
                if statement.params:
                    lines.append(f"Params {index}: {statement.params}")
            return "\n".join(lines)
        lines = [f"SQL:  {compiled.sql}"]
        if compiled.params:
            lines.append(f"Params: {compiled.params}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _field_dotted(field: FieldPath) -> str:
    """Return the full dotted path string for *field* (used for AS aliases)."""
    # Semantic resolution keeps an explicit qualifier in ``parts`` and also
    # stores it separately.  Use the original path for the result alias so it
    # is not rendered twice (for example ``u.u.name``).
    return ".".join(field.parts)


def _split_field(field: FieldPath, known_tables: set[str]) -> tuple[str, list[str]]:
    """Return ``(column_ref, json_path_parts)`` for *field*.

    ``u.name`` (alias known) -> ("u.name", []); ``profile.address.city`` ->
    ("profile", ["address", "city"]). Every identifier is validated.
    """
    q = field.qualifier
    parts = list(field.parts)
    for part in parts:
        _safe_identifier(part)
    if q:
        _safe_identifier(q)
    if q and parts and parts[0] == q:
        parts = parts[1:]
    if q is None and len(parts) >= 2 and parts[0] in known_tables:
        q = parts[0]
        parts = parts[1:]
    column = f"{q}.{parts[0]}" if q else parts[0]
    return column, parts[1:]


def _compile_field_expr(field: FieldPath, known_tables: set[str] = frozenset()) -> str:
    """Render a FieldPath as a SQL column expression (no AS alias)."""
    column, json_parts = _split_field(field, known_tables)
    if not json_parts:
        return column
    return f"json_extract({column}, '$.{'.'.join(json_parts)}')"


def _compile_field_proj(field: FieldPath, known_tables: set[str] = frozenset()) -> str:
    """Render a FieldPath as a SELECT column, aliased to its dotted UDL path."""
    expression = _compile_field_expr(field, known_tables)
    if expression == field.parts[-1] and len(field.parts) == 1:
        return expression
    return f'{expression} AS "{_field_dotted(field)}"'


def _compile_projection(field: Any, known_tables: set[str] = frozenset()) -> str:
    """Compile either a normal field or an aggregate projection."""
    if isinstance(field, AggregateField):
        argument = "*" if field.field is None else _compile_field_expr(field.field, known_tables)
        return f"{field.function}({argument})"
    return _compile_field_proj(field, known_tables)


# ---------------------------------------------------------------------------
# Parameterized WHERE compilation
# ---------------------------------------------------------------------------

def _compile_where_expr_params(
    expr: WhereExpr,
    known_tables: set[str] = frozenset(),
    params: List[Any] = None,
    parameters: Mapping[str, Any] | None = None,
) -> str:
    """Recursively compile a WHERE expression into SQL with ``?`` placeholders.

    Appends literal values to *params* in order.
    """
    if params is None:
        params = []

    if isinstance(expr, Condition):
        field_sql = _compile_condition_field(expr.field, known_tables)
        if expr.value is None and expr.operator in {"=", "!="}:
            # A literal NULL comparison is a null check (`= NULL` -> IS NULL).
            return f"{field_sql} IS {'NOT ' if expr.operator == '!=' else ''}NULL"
        params.append(_resolve_parameter(expr.value, parameters))
        return f"{field_sql} {expr.operator} ?"

    if isinstance(expr, NullPredicate):
        return _compile_null_predicate(expr, known_tables)

    if isinstance(expr, AndCondition):
        left = _compile_where_expr_params(expr.left, known_tables, params, parameters)
        right = _compile_where_expr_params(expr.right, known_tables, params, parameters)
        return f"({left} AND {right})"

    if isinstance(expr, OrCondition):
        left = _compile_where_expr_params(expr.left, known_tables, params, parameters)
        right = _compile_where_expr_params(expr.right, known_tables, params, parameters)
        return f"({left} OR {right})"

    if isinstance(expr, NotCondition):
        child = _compile_where_expr_params(expr.child, known_tables, params, parameters)
        return f"(NOT {child})"

    raise ValueError(f"Unknown WHERE expression type: {type(expr).__name__}")


def _resolve_parameter(value: Any, parameters: Mapping[str, Any] | None) -> Any:
    if not isinstance(value, ParameterRef):
        return value
    if parameters is None or value.name not in parameters:
        raise ParameterError(f"missing parameter: ${value.name}")
    return parameters[value.name]


def _compile_condition_field(field: Any, known_tables: set[str] = frozenset()) -> str:
    if isinstance(field, AggregateField):
        argument = "*" if field.field is None else _compile_field_expr(field.field, known_tables)
        return f"{field.function}({argument})"
    return _compile_field_expr(field, known_tables)


def _compile_null_predicate(
    predicate: NullPredicate,
    known_tables: set[str] = frozenset(),
) -> str:
    """Compile NULL and document-existence predicates for SQLite.

    A plain column always exists, so ``IS MISSING`` is false and ``EXISTS``
    is true. For a JSON path, SQL ``NULL`` from ``json_type`` means the key is
    absent, and the string ``'null'`` means it holds JSON null.
    """
    column, json_parts = _split_field(predicate.field, known_tables)
    kind = predicate.predicate
    if not json_parts:
        if kind == "IS_NULL":
            return f"{column} IS NULL"
        if kind == "IS_NOT_NULL":
            return f"{column} IS NOT NULL"
        if kind == "IS_MISSING":
            return "0"
        if kind in {"IS_NOT_MISSING", "EXISTS"}:
            return "1"
        raise ValueError(f"Unknown null predicate: {kind}")

    json_type = f"json_type({column}, '$.{'.'.join(json_parts)}')"
    if kind == "IS_NULL":
        return f"{json_type} = 'null'"
    if kind == "IS_NOT_NULL":
        return f"({json_type} IS NOT NULL AND {json_type} != 'null')"
    if kind == "IS_MISSING":
        return f"{json_type} IS NULL"
    if kind in {"IS_NOT_MISSING", "EXISTS"}:
        return f"{json_type} IS NOT NULL"
    raise ValueError(f"Unknown null predicate: {kind}")


def _compile_join_condition(cond: JoinCondition, known_tables: set[str] = frozenset()) -> str:
    """Compile a JOIN ON condition."""
    left = _compile_field_expr(cond.left, known_tables)
    right = _compile_field_expr(cond.right, known_tables)
    return f"{left} {cond.operator} {right}"


def _collect_tables(node: PlanNode) -> set[str]:
    """Collect all table names and aliases from the plan tree."""
    tables: set[str] = set()

    def _walk(n: PlanNode) -> None:
        if isinstance(n, Scan):
            tables.add(n.table_name)
            if n.alias:
                tables.add(n.alias)
        elif isinstance(n, Join):
            _walk(n.left)
            _walk(n.right)
        elif hasattr(n, "child"):
            _walk(n.child)

    _walk(node)
    return tables


def _compile_from_clause(node: PlanNode, known_tables: set[str] = frozenset()) -> str:
    """Compile the FROM clause, handling joins recursively."""
    if isinstance(node, Scan):
        table = _safe_identifier(node.table_name)
        alias_sql = f" AS {_safe_identifier(node.alias)}" if node.alias else ""
        return f"{table}{alias_sql}"
    if isinstance(node, Join):
        left_sql = _compile_from_clause(node.left, known_tables)
        right_sql = _compile_from_clause(node.right, known_tables)
        cond_sql = _compile_join_condition(node.condition, known_tables) if node.condition else ""
        join_keyword = f"{node.join_type} JOIN"
        on_clause = f" ON {cond_sql}" if cond_sql else ""
        return f"{left_sql} {join_keyword} {right_sql}{on_clause}"
    if hasattr(node, "child"):
        return _compile_from_clause(node.child, known_tables)
    raise ValueError(f"Unexpected node type in FROM clause: {type(node).__name__}")


# ---------------------------------------------------------------------------
# Parameterized write compilation helpers
# ---------------------------------------------------------------------------

def _compile_insert_params(
    plan: InsertPlan,
    params: List[Any],
    parameters: Mapping[str, Any] | None = None,
) -> str:
    """Compile an InsertPlan into an INSERT INTO ... VALUES (?, ...) statement."""
    table = _safe_identifier(plan.table)
    columns = ", ".join(_safe_identifier(column) for column in plan.values.keys())
    placeholders = ", ".join("?" for _ in plan.values)
    params.extend(_resolve_parameter(value, parameters) for value in plan.values.values())
    return f"INSERT INTO {table} ({columns})\nVALUES ({placeholders});"


def _compile_update_params(
    plan: UpdatePlan,
    params: List[Any],
    parameters: Mapping[str, Any] | None = None,
) -> str:
    """Compile an UpdatePlan into an UPDATE ... SET ... WHERE statement."""
    set_parts = []
    for assign in plan.assignments:
        col_parts = list(assign.field.parts)
        if plan.alias and col_parts and col_parts[0] == plan.alias:
            col_parts = col_parts[1:]
        col = ".".join(_safe_identifier(part) for part in col_parts)
        set_parts.append(f"{col} = ?")
        params.append(_resolve_parameter(assign.value, parameters))
    set_sql = ", ".join(set_parts)
    alias_sql = f" AS {_safe_identifier(plan.alias)}" if plan.alias else ""
    sql = f"UPDATE {_safe_identifier(plan.table)}{alias_sql}\nSET {set_sql}"
    if plan.condition is not None:
        known_tables = {plan.table}
        if plan.alias:
            known_tables.add(plan.alias)
        where_sql = _compile_where_expr_params(
            plan.condition,
            known_tables=known_tables,
            params=params,
            parameters=parameters,
        )
        sql += f"\nWHERE {where_sql}"
    return sql + ";"


def _compile_delete_params(
    plan: DeletePlan,
    params: List[Any],
    parameters: Mapping[str, Any] | None = None,
) -> str:
    """Compile a DeletePlan into a DELETE FROM ... WHERE statement."""
    alias_sql = f" AS {_safe_identifier(plan.alias)}" if plan.alias else ""
    sql = f"DELETE FROM {_safe_identifier(plan.table)}{alias_sql}"
    if plan.condition is not None:
        known_tables = {plan.table}
        if plan.alias:
            known_tables.add(plan.alias)
        where_sql = _compile_where_expr_params(
            plan.condition,
            known_tables=known_tables,
            params=params,
            parameters=parameters,
        )
        sql += f"\nWHERE {where_sql}"
    return sql + ";"
