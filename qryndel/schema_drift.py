"""Report-only schema snapshots and drift diagnostics.

Compare what a program declares (``TYPE`` blocks or a mapping) with what a
SQLite table actually has. Nothing is ever changed; the result is a report.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class SchemaField:
    name: str
    type_name: str
    required: bool = False


@dataclass(frozen=True)
class SchemaSnapshot:
    source: str
    fields: Mapping[str, SchemaField]


@dataclass(frozen=True)
class SchemaChange:
    kind: str
    field: str
    expected: str | None = None
    actual: str | None = None
    message: str = ""


@dataclass(frozen=True)
class SchemaDriftReport:
    source: str
    changes: tuple[SchemaChange, ...]
    report_only: bool = True

    @property
    def clean(self) -> bool:
        return not self.changes


def snapshot_from_mapping(source: str, schema: Mapping[str, Any]) -> SchemaSnapshot:
    fields: dict[str, SchemaField] = {}

    def walk(value: Mapping[str, Any], prefix: str = "") -> None:
        for name, definition in value.items():
            path = f"{prefix}.{name}" if prefix else name
            if isinstance(definition, Mapping):
                if "type" in definition or "type_name" in definition:
                    fields[path] = SchemaField(
                        path,
                        str(definition.get("type", definition.get("type_name"))),
                        bool(definition.get("required", False)),
                    )
                else:
                    walk(definition, path)
            else:
                fields[path] = SchemaField(path, str(definition))

    walk(schema)
    return SchemaSnapshot(source, fields)


def inspect_sqlite_schema(db_path: str | os.PathLike[str], table: str) -> SchemaSnapshot:
    """Inspect a SQLite table without modifying it (or creating the file)."""
    from qryndel.executor import _connect

    connection = _connect(db_path)
    try:
        rows = connection.execute(f'PRAGMA table_info("{table.replace(chr(34), chr(34) * 2)}")').fetchall()
    finally:
        connection.close()
    if not rows:
        raise ValueError(f"SQLite table not found: {table}")
    # An INTEGER PRIMARY KEY is the rowid and can never be NULL, even though
    # PRAGMA table_info reports notnull=0 for it.
    fields = {
        row[1]: SchemaField(
            row[1],
            _normalize_sqlite_type(row[2]),
            bool(row[3]) or (bool(row[5]) and (row[2] or "").upper() == "INTEGER"),
        )
        for row in rows
    }
    return SchemaSnapshot(table, fields)


def _normalize_sqlite_type(type_name: str) -> str:
    upper = type_name.upper()
    if "BOOL" in upper:
        return "Boolean"
    if "INT" in upper:
        return "Number"
    if any(token in upper for token in ("REAL", "FLOA", "DOUB", "NUM", "DEC")):
        return "Number"
    return "Text"


def _types_match(expected: str, actual: str) -> bool:
    """Compare a declared UDL type with a normalized SQLite type.

    ``ID`` matches any column type (ids may be integers or text). ``List<...>``
    and ``Json`` are stored as JSON text in SQLite.
    """
    wanted = expected.strip().lower()
    found = actual.strip().lower()
    if wanted in {"id", "any"}:
        return True
    if wanted.startswith("list<") or wanted in {"json", "list", "document"}:
        return found == "text"
    if wanted in {"integer", "int", "float", "real", "decimal"}:
        wanted = "number"
    if wanted in {"string", "str", "varchar"}:
        wanted = "text"
    if wanted == "bool":
        wanted = "boolean"
    return wanted == found


def detect_schema_drift(
    expected: SchemaSnapshot | Mapping[str, Any],
    actual: SchemaSnapshot,
    *,
    source: str | None = None,
) -> SchemaDriftReport:
    if not isinstance(expected, SchemaSnapshot):
        expected = snapshot_from_mapping(source or actual.source, expected)
    changes: list[SchemaChange] = []
    expected_names = set(expected.fields)
    actual_names = set(actual.fields)
    for field in sorted(expected_names - actual_names):
        definition = expected.fields[field]
        changes.append(
            SchemaChange("removed", field, definition.type_name, None, "Expected field is absent.")
        )
    for field in sorted(actual_names - expected_names):
        definition = actual.fields[field]
        changes.append(
            SchemaChange("added", field, None, definition.type_name, "Backend has an undeclared field.")
        )
    for field in sorted(expected_names & actual_names):
        wanted = expected.fields[field]
        found = actual.fields[field]
        if not _types_match(wanted.type_name, found.type_name):
            changes.append(
                SchemaChange(
                    "type_changed",
                    field,
                    wanted.type_name,
                    found.type_name,
                    "Field type differs from the declared schema.",
                )
            )
        if wanted.required and not found.required:
            changes.append(
                SchemaChange(
                    "nullable_changed",
                    field,
                    "required",
                    "optional",
                    "Required field is nullable in the backend.",
                )
            )
    return SchemaDriftReport(source or actual.source, tuple(changes))


def format_schema_drift(report: SchemaDriftReport) -> str:
    lines = [f"SCHEMA DRIFT ({report.source})", f"Status: {'CLEAN' if report.clean else 'DRIFT DETECTED'}"]
    for change in report.changes:
        lines.append(f"{change.kind}: {change.field} - {change.message}")
        if change.expected is not None or change.actual is not None:
            lines.append(f"  expected={change.expected!r}, actual={change.actual!r}")
    return "\n".join(lines)


def detect_type_drift(
    types: Iterable[Any],
    db_path: str | os.PathLike[str],
) -> list[SchemaDriftReport]:
    """Check each ``TYPE`` declaration against the matching SQLite table.

    *types* are ``TypeDefinition`` nodes (``parse_program(text).types``). A
    declaration with ``ALLOW extra fields`` does not report undeclared
    columns. A table that does not exist is reported as ``table_missing``.
    """
    reports: list[SchemaDriftReport] = []
    for definition in types:
        expected = SchemaSnapshot(
            definition.name,
            {
                field.name: SchemaField(field.name, field.type_name, field.required)
                for field in definition.fields
            },
        )
        try:
            actual = inspect_sqlite_schema(db_path, definition.name)
        except ValueError:
            reports.append(
                SchemaDriftReport(
                    definition.name,
                    (SchemaChange("table_missing", definition.name, "table", None,
                                  "Declared table does not exist in the database."),),
                )
            )
            continue
        report = detect_schema_drift(expected, actual, source=definition.name)
        if definition.allow_extra:
            report = SchemaDriftReport(
                report.source,
                tuple(change for change in report.changes if change.kind != "added"),
            )
        reports.append(report)
    return reports
