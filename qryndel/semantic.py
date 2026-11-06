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
    TransactionStatement,
    UpdateStatement,
    WhereExpr,
)
from qryndel.rules import SemanticError as _RuleSemanticError, expand_rules


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
            raise ValidationError(
                f"Unknown field '{_to_nice(field)}' in source '{source_name}'"
            )
        if len(found_table_list) > 1:
            raise ValidationError(
                f"Ambiguous field '{_to_nice(field)}' found in multiple sources: {', '.join(sorted(found_table_list))}"
            )
        field.parts = parts
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
        )
        if not found:
            raise ValidationError(
                f"Unknown field '{field_path}' in source '{qualifier}'"
            )
        field.parts = [qualifier] + rest
        field.qualifier = qualifier
        return

    # Not a table qualifier — try nested document path on the source table
    tbl = normal_aliases.get(source_name, source_name)
    if tbl not in field_map:
        raise ValidationError(f"Unknown source '{tbl}'")
    full_path = ".".join(parts)
    tbl_fields = field_map.get(tbl, set())
    found = full_path in tbl_fields or any(
        f.startswith(full_path + ".") for f in tbl_fields
    )
    if not found:
        # Check if the first part itself is a known nested root
        if parts[0] not in tbl_fields:
            raise ValidationError(f"Unknown qualifier '{qualifier}'")
        raise ValidationError(
            f"Unknown nested field '{'.'.join(rest)}' in '{qualifier}'"
        )
    field.parts = parts
    field.qualifier = source_name


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
        if isinstance(expr.field, AggregateField):
            if not allow_aggregate:
                raise ValidationError("Aggregate expressions are only allowed in HAVING")
            if expr.field.field is not None:
                _resolve_field(
                    expr.field.field, source_name, alias, normal_aliases, field_map, schema
                )
        else:
            _resolve_field(expr.field, source_name, alias, normal_aliases, field_map, schema)

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

        # Validate field exists
        _resolve_field(assign.field, source_name, alias, normal_aliases, field_map, schema)

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
        effective_schema.update(declared_schema)
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

    # Validate RETURN fields
    if query.fields is not None:
        for field in query.fields:
            if isinstance(field, AggregateField):
                if field.field is not None:
                    _resolve_field(field.field, source_name, alias, normal_aliases, field_map, schema)
            else:
                _resolve_field(field, source_name, alias, normal_aliases, field_map, schema)

    # Validate WHERE clause
    if query.where is not None:
        _resolve_where(query.where, source_name, alias, normal_aliases, field_map, schema)

    # Validate ORDER BY fields
    if query.order is not None:
        for of in query.order:
            if isinstance(of.field, AggregateField):
                if of.field.field is not None:
                    _resolve_field(of.field.field, source_name, alias, normal_aliases, field_map, schema)
            else:
                _resolve_field(of.field, source_name, alias, normal_aliases, field_map, schema)

    if query.having is not None and query.group_by is None and not any(
        isinstance(field, AggregateField) for field in query.fields or ()
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
