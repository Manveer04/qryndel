"""Semantic analysis and validation.

Checks field references against a schema when one is available, applies
TYPE declarations, expands RULE calls, and enforces write-safety rules such
as rejecting UPDATE and DELETE without WHERE.
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional, Sequence, Set

from qryndel.ast_nodes import (
    AndCondition,
    AggregateField,
    Assignment,
    Condition,
    DeleteStatement,
    FieldPath,
    InsertStatement,
    Join,
    NotCondition,
    NullPredicate,
    OrCondition,
    OrderField,
    Program,
    Query,
    RuleCall,
    TypeDefinition,
    Statement,
    TypeField,
    ProjectionItem,
    TransactionStatement,
    UpdateStatement,
    WhereExpr,
)
from qryndel.expressions import contains_aggregate, iter_aggregates, iter_fields, result_name, to_text
from qryndel.rules import SemanticError as _RuleSemanticError, expand_rules

import difflib


class SemanticError(_RuleSemanticError):
    """Raised when semantic validation fails."""
    pass


ValidationError = SemanticError


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

def _to_nice(field: FieldPath) -> str:
    """Pretty-print a field path for error messages."""
    return ".".join(field.parts)


def _nested_keys(d: Any, prefix: str = "") -> Set[str]:
    """Recursively collect all leaf-key paths from a nested dict schema."""
    keys: Set[str] = set()
    if isinstance(d, dict):
        for k, v in d.items():
            if str(k).startswith("__udl_"):
                continue
            full = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                keys.add(full)
                keys |= _nested_keys(v, full)
            else:
                keys.add(full)
    return keys


def _schema_from_types(types: Sequence[TypeDefinition]) -> Dict[str, Any]:
    """Convert TYPE declarations into the schema shape used by validation."""
    declared: Dict[str, Any] = {}
    for type_def in types:
        if type_def.name in declared:
            raise ValidationError(f"Duplicate TYPE definition: '{type_def.name}'")
        table: Dict[str, Any] = {field.name: field for field in type_def.fields}
        if type_def.allow_extra:
            table["__udl_allow_extra__"] = True
        declared[type_def.name] = table
    return declared


def _build_field_map(schema: Optional[Dict[str, Any]], tables: Sequence[str]) -> Dict[str, Set[str]]:
    """
    Build a map of table → {field_names} from the schema for the
    given list of tables. Returns empty dict when schema is None or empty.
    """
    if schema is None or not schema:
        return {}

    field_map: Dict[str, Set[str]] = {}
    for table in tables:
        if table not in schema:
            raise ValidationError(f"Unknown source: '{table}'")
        if isinstance(schema[table], dict):
            field_map[table] = _nested_keys(schema[table])
        elif isinstance(schema[table], list):
            field_map[table] = set(schema[table])
        else:
            raise ValidationError(
                f"Schema for '{table}' must be a dict or list, got {type(schema[table]).__name__}"
            )
    return field_map


# ---------------------------------------------------------------------------
# Field resolution
# ---------------------------------------------------------------------------

OPEN_DOCUMENT = "__udl_open__"


def _is_open_path(schema: Optional[Dict[str, Any]], table: str, parts: Sequence[str]) -> bool:
    """True when *parts* runs into a column marked as an open JSON document.

    Schemas loaded from a database mark TEXT/JSON columns this way, because
    their JSON contents cannot be known in advance.
    """
    node: Any = (schema or {}).get(table)
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
        if isinstance(node, dict) and node.get(OPEN_DOCUMENT):
            return True
    return False


_NUMBER_TYPES = {"number", "integer", "int", "float", "real", "decimal"}


def _column_type(schema: Optional[Dict[str, Any]], table: Optional[str], column: str) -> Optional[str]:
    """'Number' / 'Text' / 'Boolean' / declared type name, or None when unknown."""
    if not schema or table is None:
        return None
    entry = schema.get(table)
    if not isinstance(entry, dict) or column not in entry:
        return None
    value = entry[column]
    if isinstance(value, TypeField):
        return value.type_name
    if isinstance(value, dict):
        return "Text" if value.get(OPEN_DOCUMENT) else None
    if isinstance(value, str):
        return value
    return None


def _annotate(field: FieldPath, table: Optional[str], schema: Optional[Dict[str, Any]]) -> None:
    """Record the column's table; a TYPE ``Number`` declaration converts it."""
    field.table = table
    column = field.parts[-1] if len(field.parts) == 1 or field.qualifier == field.parts[0] else None
    if column is None or schema is None or table is None:
        return
    declared = (schema.get(table) or {}).get(column) if isinstance(schema.get(table), dict) else None
    if isinstance(declared, TypeField) and declared.type_name.strip().lower() in _NUMBER_TYPES:
        field.cast = "NUMERIC"


def _suggest(name: str, candidates: Set[str]) -> str:
    top_level = {candidate.split(".")[0] for candidate in candidates}
    matches = difflib.get_close_matches(name, sorted(top_level), n=1, cutoff=0.6)
    return f". Did you mean '{matches[0]}'?" if matches else ""


def _resolve_field(
    field: FieldPath,
    source_name: str,
    alias: Optional[str],
    normal_aliases: Dict[str, str],
    field_map: Dict[str, Set[str]],
    schema: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Validate that a field reference exists in the schema.

    If the field has one part, it is looked up in all available tables
    (raises an error if the name is ambiguous). If it has two+ parts,
    the first part is treated as either a table qualifier (name/alias)
    or as a nested document root.
    """
    parts = field.parts
    if parts is None or len(parts) == 0:
        raise ValidationError("Empty field reference")

    # If schema is empty (None or {}), skip resolution entirely
    if not field_map:
        return

    if len(parts) == 1:
        # Unqualified — look in every available table, deduplicating by table name
        found_tables = {}
        for tbl_qualifier, tbl_real in normal_aliases.items():
            if tbl_real in found_tables:
                continue
            tbl_fields = field_map.get(tbl_real, set())
            # Check exact match or nested path prefix match
            for f in tbl_fields:
                if f == parts[0] or f.startswith(parts[0] + "."):
                    found_tables[tbl_real] = tbl_qualifier
                    break
        found_table_list = list(found_tables.values())
        if len(found_table_list) == 0:
            known = set().union(*(field_map.get(t, set()) for t in set(normal_aliases.values())))
            raise ValidationError(
                f"Unknown field '{_to_nice(field)}' in source '{source_name}'"
                + _suggest(parts[0], known)
            )
        if len(found_table_list) > 1:
            raise ValidationError(
                f"Ambiguous field '{_to_nice(field)}' found in multiple sources: {', '.join(sorted(found_table_list))}"
            )
        field.parts = parts
        _annotate(field, normal_aliases.get(found_table_list[0], found_table_list[0]), schema)
        return

    # Multi-part field: could be table.field, alias.field, or nested.path
    qualifier = parts[0]
    rest = parts[1:]

    if qualifier in normal_aliases:
        # Table-qualified or alias-qualified: qualifier.field[.nested...]
        tbl = normal_aliases[qualifier]
        field_path = ".".join(rest)
        tbl_fields = field_map.get(tbl, set())
        # Check exact match or prefix match for nested paths
        found = field_path in tbl_fields or any(
            f.startswith(field_path + ".") for f in tbl_fields
        ) or _is_open_path(schema, tbl, rest)
        if not found:
            raise ValidationError(
                f"Unknown field '{field_path}' in source '{qualifier}'"
                + _suggest(rest[0], tbl_fields)
            )
        field.parts = [qualifier] + rest
        field.qualifier = qualifier
        if len(rest) == 1:
            _annotate(field, tbl, schema)
        else:
            field.table = tbl
        return

    # Not a table qualifier — try nested document path on the source table
    tbl = normal_aliases.get(source_name, source_name)
    if tbl not in field_map:
        raise ValidationError(f"Unknown source '{tbl}'")
    full_path = ".".join(parts)
    tbl_fields = field_map.get(tbl, set())
    found = full_path in tbl_fields or any(
        f.startswith(full_path + ".") for f in tbl_fields
    ) or _is_open_path(schema, tbl, parts)
    if not found:
        # Check if the first part itself is a known nested root
        if parts[0] not in tbl_fields:
            raise ValidationError(
                f"Unknown qualifier '{qualifier}'" + _suggest(qualifier, tbl_fields | set(normal_aliases))
            )
        raise ValidationError(
            f"Unknown nested field '{'.'.join(rest)}' in '{qualifier}'"
        )
    field.parts = parts
    field.qualifier = source_name
    field.table = tbl


def _is_number_literal(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_text_number_comparison(condition: Condition, schema: Optional[Dict[str, Any]]) -> None:
    """Reject `text_column > 1000`: SQLite would compare the two as text.

    Equality is included: `zip_code = 1001` never matches the text '01001'.
    """
    for side, other in ((condition.field, condition.value), (condition.value, condition.field)):
        if not isinstance(side, FieldPath) or side.cast or not _is_number_literal(other):
            continue
        column = side.parts[-1]
        declared = _column_type(schema, side.table, column)
        if declared is None or declared.strip().lower() != "text":
            continue
        shown = to_text(condition.field) + f" {condition.operator} " + to_text(condition.value)
        raise ValidationError(
            f"Column '{_to_nice(side)}' is stored as text, so `{shown}` would compare text, "
            f"not numbers (for text, '999.99' > '1000'). Write NUMBER({_to_nice(side)}) to "
            f"compare as numbers, or declare `{column}: Number` in a TYPE block."
        )


def _resolve_expr(
    expr: Any,
    source_name: str,
    alias: Optional[str],
    normal_aliases: Dict[str, str],
    field_map: Dict[str, Set[str]],
    schema: Optional[Dict[str, Any]] = None,
    *,
    allow_aggregate: bool = False,
    result_aliases: frozenset = frozenset(),
) -> None:
    """Check aggregate placement and resolve every field in a value expression."""
    aggregates = list(iter_aggregates(expr))
    if aggregates and not allow_aggregate:
        raise ValidationError("Aggregate expressions are only allowed in RETURN, HAVING and ORDER BY")
    for aggregate in aggregates:
        if aggregate.field is not None and contains_aggregate(aggregate.field):
            raise ValidationError(f"Aggregates cannot be nested: {aggregate.function}(...)")
    for field in iter_fields(expr):
        if len(field.parts) == 1 and field.parts[0] in result_aliases:
            continue  # refers to a RETURN ... AS alias
        _resolve_field(field, source_name, alias, normal_aliases, field_map, schema)


def _resolve_where(
    expr: WhereExpr,
    source_name: str,
    alias: Optional[str],
    normal_aliases: Dict[str, str],
    field_map: Dict[str, Set[str]],
    schema: Optional[Dict[str, Any]] = None,
    allow_aggregate: bool = False,
) -> None:
    """Recursively resolve field references in a WHERE expression."""
    if isinstance(expr, Condition):
        for side in (expr.field, expr.value):
            if contains_aggregate(side) and not allow_aggregate:
                raise ValidationError("Aggregate expressions are only allowed in HAVING")
            _resolve_expr(
                side, source_name, alias, normal_aliases, field_map, schema,
                allow_aggregate=allow_aggregate,
            )
        _check_text_number_comparison(expr, schema)

    elif isinstance(expr, NullPredicate):
        _resolve_field(expr.field, source_name, alias, normal_aliases, field_map, schema)

    elif isinstance(expr, AndCondition):
        _resolve_where(expr.left, source_name, alias, normal_aliases, field_map, schema, allow_aggregate)
        _resolve_where(expr.right, source_name, alias, normal_aliases, field_map, schema, allow_aggregate)

    elif isinstance(expr, OrCondition):
        _resolve_where(expr.left, source_name, alias, normal_aliases, field_map, schema, allow_aggregate)
        _resolve_where(expr.right, source_name, alias, normal_aliases, field_map, schema, allow_aggregate)

    elif isinstance(expr, NotCondition):
        _resolve_where(expr.child, source_name, alias, normal_aliases, field_map, schema, allow_aggregate)

    elif isinstance(expr, RuleCall):
        # Rule calls are expanded before validation, so this should not occur
        raise ValidationError(f"Unexpanded rule call: '{expr.name}'")


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _build_aliases(
    source_name: str,
    alias: Optional[str],
    joins: List[Join],
) -> Dict[str, str]:
    """
    Build a map of qualifier → real table name.

    Includes the main source and all joined sources, using aliases where
    available.
    """
    aliases: Dict[str, str] = {}
    aliases[source_name] = source_name
    if alias:
        aliases[alias] = source_name
    for join in joins:
        src = join.right.name
        aliases[src] = src
        if join.alias:
            aliases[join.alias] = src
    return aliases


def _validate_join_condition(
    condition: Any,
    source_name: str,
    alias: Optional[str],
    normal_aliases: Dict[str, str],
    field_map: Dict[str, Set[str]],
    schema: Optional[Dict[str, Any]] = None,
) -> None:
    """Validate and resolve fields in a JOIN condition."""
    _resolve_field(condition.left, source_name, alias, normal_aliases, field_map, schema)
    _resolve_field(condition.right, source_name, alias, normal_aliases, field_map, schema)


def _check_join_type(join_type: str) -> None:
    """Validate join type string."""
    valid = {"INNER", "LEFT", "RIGHT", "FULL"}
    if join_type not in valid:
        raise ValidationError(f"Invalid join type: '{join_type}'")


# ---------------------------------------------------------------------------
# Write statement validation
# ---------------------------------------------------------------------------

def _validate_insert(
    stmt: InsertStatement,
    schema: Optional[Dict[str, Any]] = None,
) -> None:
    """Validate an INSERT statement."""
    if not stmt.values:
        raise ValidationError("INSERT requires at least one field")

    if schema is None:
        return

    # Validate target table exists
    if stmt.table not in schema:
        raise ValidationError(f"Unknown source: '{stmt.table}'")

    table_schema = schema[stmt.table]
    if isinstance(table_schema, dict):
        known_fields = _nested_keys(table_schema)
    elif isinstance(table_schema, list):
        known_fields = set(table_schema)
    else:
        raise ValidationError(
            f"Schema for '{stmt.table}' must be a dict or list, got {type(table_schema).__name__}"
        )

    # Check for duplicate fields in INSERT
    seen = set()
    for key in stmt.values:
        if key in seen:
            raise ValidationError(f"Duplicate field '{key}' in INSERT")
        seen.add(key)

        # Validate field exists in schema
        if key not in known_fields:
            if isinstance(table_schema, dict) and table_schema.get("__udl_allow_extra__"):
                continue
            raise ValidationError(f"Unknown field '{key}' in source '{stmt.table}'")

    if isinstance(table_schema, dict):
        required = [
            key for key, value in table_schema.items()
            if isinstance(value, TypeField) and value.required
        ]
        missing = [key for key in required if key not in stmt.values]
        if missing:
            raise ValidationError(
                f"INSERT is missing required field(s) in '{stmt.table}': {', '.join(missing)}"
            )


def _always_true_reason(expr: Any) -> Optional[str]:
    """Why a WHERE clause would match every row, or None if it might not.

    Catches the common ways to defeat the "UPDATE/DELETE need WHERE" rule by
    accident: no column at all (`1 = 1`), a column compared with itself
    (`price = price`), and `x IS NULL OR x IS NOT NULL` / `p OR NOT p`.
    """
    if isinstance(expr, Condition):
        if next(iter_fields(expr.field), None) is None and next(iter_fields(expr.value), None) is None:
            return "it does not refer to any column"
        if expr.operator in {"=", ">=", "<="} and repr(expr.field) == repr(expr.value):
            return f"it compares {to_text(expr.field)} with itself"
        return None
    if isinstance(expr, AndCondition):
        left, right = _always_true_reason(expr.left), _always_true_reason(expr.right)
        return left if left and right else None
    if isinstance(expr, OrCondition):
        reason = _always_true_reason(expr.left) or _always_true_reason(expr.right)
        if reason:
            return reason
        disjuncts = _flatten(expr, OrCondition)
        keys = {repr(item) for item in disjuncts}
        for item in disjuncts:
            if isinstance(item, NotCondition) and repr(item.child) in keys:
                return f"it is true for every row: (p) OR NOT (p)"
            if isinstance(item, NullPredicate) and item.predicate in {"IS_NULL", "IS_NOT_NULL"}:
                opposite = NullPredicate(
                    field=item.field,
                    predicate="IS_NOT_NULL" if item.predicate == "IS_NULL" else "IS_NULL",
                )
                if repr(opposite) in keys:
                    return f"{_to_nice(item.field)} IS NULL OR {_to_nice(item.field)} IS NOT NULL is always true"
        return None
    return None


def _flatten(expr: Any, node_type) -> list:
    items, stack = [], [expr]
    while stack:
        node = stack.pop()
        if isinstance(node, node_type):
            stack.extend((node.left, node.right))
        else:
            items.append(node)
    return items


def _reject_always_true(statement: str, table: str, where: Any) -> None:
    reason = _always_true_reason(where) if where is not None else None
    if reason:
        raise ValidationError(
            f"{statement} on '{table}' would change every row: the WHERE clause can never be false "
            f"({reason}). Use a condition that selects specific rows."
        )


def _validate_update(
    stmt: UpdateStatement,
    schema: Optional[Dict[str, Any]] = None,
) -> None:
    """Validate an UPDATE statement."""
    if stmt.where is None:
        raise ValidationError(
            "UPDATE without WHERE is not allowed. Add a WHERE clause that "
            "selects the rows to change."
        )

    if not stmt.assignments:
        raise ValidationError("UPDATE requires at least one SET assignment")
    _reject_always_true("UPDATE", stmt.table, stmt.where)

    if schema is None:
        return

    # Validate target table exists
    if stmt.table not in schema:
        raise ValidationError(f"Unknown source: '{stmt.table}'")

    table_schema = schema[stmt.table]
    if isinstance(table_schema, dict):
        known_fields = _nested_keys(table_schema)
    elif isinstance(table_schema, list):
        known_fields = set(table_schema)
    else:
        raise ValidationError(
            f"Schema for '{stmt.table}' must be a dict or list, got {type(table_schema).__name__}"
        )

    # Build normal_aliases for WHERE and SET field resolution
    alias = stmt.alias
    source_name = stmt.table
    normal_aliases: Dict[str, str] = {source_name: source_name}
    if alias:
        normal_aliases[alias] = source_name

    all_tables = [source_name]
    field_map = _build_field_map(schema, all_tables)

    # Check for duplicate assignments
    seen_fields = set()
    for assign in stmt.assignments:
        field_str = ".".join(assign.field.parts)
        if field_str in seen_fields:
            raise ValidationError(f"Duplicate assignment to field '{field_str}' in UPDATE")
        seen_fields.add(field_str)

        # Validate field exists, and every field used in the new value
        _resolve_field(assign.field, source_name, alias, normal_aliases, field_map, schema)
        _resolve_expr(assign.value, source_name, alias, normal_aliases, field_map, schema)

    # Validate WHERE clause
    if stmt.where is not None:
        _resolve_where(stmt.where, source_name, alias, normal_aliases, field_map, schema)


def _validate_delete(
    stmt: DeleteStatement,
    schema: Optional[Dict[str, Any]] = None,
) -> None:
    """Validate a DELETE statement."""
    if stmt.where is None:
        raise ValidationError(
            "DELETE without WHERE is not allowed. Add a WHERE clause that "
            "selects the rows to delete."
        )
    _reject_always_true("DELETE", stmt.table, stmt.where)

    if schema is None:
        return

    # Validate target table exists
    if stmt.table not in schema:
        raise ValidationError(f"Unknown source: '{stmt.table}'")

    # Build normal_aliases for WHERE field resolution
    alias = stmt.alias
    source_name = stmt.table
    normal_aliases: Dict[str, str] = {source_name: source_name}
    if alias:
        normal_aliases[alias] = source_name

    all_tables = [source_name]
    field_map = _build_field_map(schema, all_tables)

    # Validate WHERE clause
    if stmt.where is not None:
        _resolve_where(stmt.where, source_name, alias, normal_aliases, field_map, schema)


# ---------------------------------------------------------------------------
# Main validation entry point
# ---------------------------------------------------------------------------

def validate(
    program_or_query: Program | Query | InsertStatement | UpdateStatement | DeleteStatement | TransactionStatement,
    schema: Optional[Dict[str, Any]] = None,
) -> Statement:
    """
    Validate a Qryndel program or query and return the validated statement.

    If a ``Program`` is passed, its TYPE declarations are merged into the
    schema (declarations win) and its rules are expanded before the inner
    statement is validated. Statements can also be validated directly.

    The returned statement has rules expanded and field qualifiers
    resolved, ready for ``build_statement_plan``.

    Raises ``ValidationError`` if validation fails.
    """

    # --- Expand rules if input is a Program ---
    if isinstance(program_or_query, Program):
        program = program_or_query
        declared_schema = _schema_from_types(program.types)
        effective_schema = dict(schema or {})
        for table, declared in declared_schema.items():
            existing = effective_schema.get(table)
            if declared.get("__udl_allow_extra__") and isinstance(existing, dict):
                # Keep the other columns we know about; declarations win.
                merged = dict(existing)
                merged.update(declared)
                effective_schema[table] = merged
            else:
                effective_schema[table] = declared
        if not effective_schema:
            effective_schema = None
        if program.rules:
            try:
                expanded = expand_rules(
                    program, schema=effective_schema
                )
            except _RuleSemanticError as e:
                raise ValidationError(e.message) from e
        else:
            expanded = program.statement
        # Validate the expanded (or original) statement
        return validate(expanded, schema=effective_schema)

    stmt = program_or_query
    if isinstance(stmt, Query):
        _validate_query(stmt, schema)
    elif isinstance(stmt, InsertStatement):
        _validate_insert(stmt, schema)
    elif isinstance(stmt, UpdateStatement):
        _validate_update(stmt, schema)
    elif isinstance(stmt, DeleteStatement):
        _validate_delete(stmt, schema)
    elif isinstance(stmt, TransactionStatement):
        if not stmt.statements:
            raise ValidationError("Transaction must contain at least one statement")
        if stmt.action not in {"COMMIT", "ROLLBACK"}:
            raise ValidationError(f"Unknown transaction action '{stmt.action}'")
        for statement in stmt.statements:
            if isinstance(statement, InsertStatement):
                _validate_insert(statement, schema)
            elif isinstance(statement, UpdateStatement):
                _validate_update(statement, schema)
            elif isinstance(statement, DeleteStatement):
                _validate_delete(statement, schema)
            else:
                raise ValidationError("Transactions only support write statements")
    else:
        raise ValidationError(
            f"Expected a query or write statement, got {type(stmt).__name__}"
        )
    return stmt


def _validate_query(query: Query, schema: Optional[Dict[str, Any]]) -> None:
    """Validate a table Query."""
    source_name = query.source.name
    alias = query.source.alias

    # Build available tables and field map
    all_tables = [source_name] + [j.right.name for j in query.joins]
    field_map = _build_field_map(schema, all_tables)
    normal_aliases = _build_aliases(source_name, alias, query.joins)

    # Validate RETURN items
    result_aliases: set[str] = set()
    names: set[str] = set()
    for item in query.fields or ():
        expression = item.expression if isinstance(item, ProjectionItem) else item
        _resolve_expr(
            expression, source_name, alias, normal_aliases, field_map, schema,
            allow_aggregate=True,
        )
        name = result_name(item)
        if name in names:
            raise ValidationError(
                f"Duplicate result column '{name}'. Give one of them a different name with AS."
            )
        names.add(name)
        if isinstance(item, ProjectionItem) and item.alias:
            result_aliases.add(item.alias)

    # Validate WHERE clause
    if query.where is not None:
        _resolve_where(query.where, source_name, alias, normal_aliases, field_map, schema)

    # Validate ORDER BY items (they may name a RETURN alias)
    if query.order is not None:
        for of in query.order:
            _resolve_expr(
                of.field, source_name, alias, normal_aliases, field_map, schema,
                allow_aggregate=True, result_aliases=frozenset(result_aliases),
            )

    if query.having is not None and query.group_by is None and not any(
        contains_aggregate(field) for field in query.fields or ()
    ):
        raise ValidationError("HAVING requires GROUP BY or an aggregate in RETURN")

    # Validate GROUP BY fields and HAVING expressions
    if query.group_by is not None:
        for field in query.group_by:
            _resolve_field(field, source_name, alias, normal_aliases, field_map, schema)
    if query.having is not None:
        _resolve_where(
            query.having,
            source_name,
            alias,
            normal_aliases,
            field_map,
            schema,
            allow_aggregate=True,
        )

    # Validate JOINs
    for join in query.joins:
        _check_join_type(join.join_type)
        if join.condition:
            _validate_join_condition(
                join.condition, source_name, alias, normal_aliases, field_map, schema
            )
