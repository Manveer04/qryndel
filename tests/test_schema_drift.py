import sqlite3

import pytest

from qryndel.schema_drift import (
    detect_schema_drift,
    format_schema_drift,
    inspect_sqlite_schema,
    snapshot_from_mapping,
)


def test_schema_drift_reports_removed_added_and_type_changes():
    expected = snapshot_from_mapping(
        "users",
        {"id": {"type": "Number", "required": True}, "email": "Text", "age": "Number"},
    )
    actual = snapshot_from_mapping("users", {"id": "Text", "name": "Text", "age": "Number"})

    report = detect_schema_drift(expected, actual)

    assert [(change.kind, change.field) for change in report.changes] == [
        ("removed", "email"),
        ("added", "name"),
        ("type_changed", "id"),
        ("nullable_changed", "id"),
    ]
    assert "DRIFT DETECTED" in format_schema_drift(report)


def test_sqlite_schema_inspection_is_read_only(tmp_path):
    db_path = str(tmp_path / "schema.db")
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE users (id INTEGER NOT NULL, name TEXT)")
    connection.commit()
    connection.close()

    snapshot = inspect_sqlite_schema(db_path, "users")

    assert snapshot.fields["id"].type_name == "Number"
    assert snapshot.fields["id"].required is True
    assert snapshot.fields["name"].type_name == "Text"


def test_sqlite_schema_inspection_rejects_missing_table(tmp_path):
    db_path = str(tmp_path / "schema.db")
    sqlite3.connect(db_path).close()

    with pytest.raises(ValueError, match="table not found"):
        inspect_sqlite_schema(db_path, "missing")
