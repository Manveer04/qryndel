import sqlite3
import subprocess
import sys

import pytest

from qryndel import Client, __version__
from qryndel.backends import get_backend
from qryndel.executor import ExecutionError
from qryndel.parser import ParseError, parse
from qryndel.semantic import SemanticError, validate


def make_db(path):
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE users (id INTEGER, name TEXT, age INTEGER, city TEXT, active INTEGER)")
    connection.executemany(
        "INSERT INTO users VALUES (?, ?, ?, ?, ?)",
        [
            (1, "Alice", 30, "Singapore", 1),
            (2, "Bob", 17, "Jakarta", 0),
            (3, "Cara", 22, "Singapore", 1),
            (4, "Dana", 45, "Kuala Lumpur", 1),
        ],
    )
    connection.commit()
    connection.close()


def run(db, query, params=None):
    return Client(str(db)).execute(query, params=params or {}).rows


def test_version_and_only_public_backend():
    assert __version__ == "0.1.0"
    assert get_backend("sqlite").capabilities == frozenset(
        {"from", "where", "return", "and", "or", "not", "parameters", "null_predicates"}
    )
    with pytest.raises(ValueError, match="Unknown backend"):
        get_backend("postgresql")


def test_core_parser_and_alias():
    query = parse("FROM users AS u WHERE age >= 18 RETURN name, age")
    assert query.source.name == "users"
    assert query.source.alias == "u"
    assert [field.name for field in query.fields] == ["name", "age"]


@pytest.mark.parametrize("operator", ["=", "!=", ">", "<", ">=", "<="])
def test_comparison_operators_parse(operator):
    query = parse(f"FROM users WHERE age {operator} 18 RETURN name")
    assert query.where.operator == operator


def test_boolean_precedence_and_parentheses(tmp_path):
    db = tmp_path / "users.db"
    make_db(db)
    rows = run(db, "FROM users WHERE age >= $minimum AND (city = 'Singapore' OR city = 'Jakarta') RETURN name", {"minimum": 18})
    assert rows == [{"name": "Alice"}, {"name": "Cara"}]
    assert run(db, "FROM users WHERE NOT active = TRUE RETURN name") == [{"name": "Bob"}]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("FROM users WHERE name = 'Alice' RETURN name", [{"name": "Alice"}]),
        ("FROM users WHERE age = 17 RETURN name", [{"name": "Bob"}]),
        ("FROM users WHERE age = 17.0 RETURN name", [{"name": "Bob"}]),
        ("FROM users WHERE active = FALSE RETURN name", [{"name": "Bob"}]),
    ],
)
def test_supported_literal_types(tmp_path, query, expected):
    db = tmp_path / "users.db"
    make_db(db)
    assert run(db, query) == expected


def test_parameter_binding_and_injection_is_not_sql(tmp_path):
    db = tmp_path / "users.db"
    make_db(db)
    result = Client(str(db)).compile("FROM users WHERE name = $name RETURN name", params={"name": "Alice' OR 1=1 --"})
    assert result.params == ["Alice' OR 1=1 --"]
    assert "?" in result.sql
    assert run(db, "FROM users WHERE name = $name RETURN name", {"name": "Alice' OR 1=1 --"}) == []


def test_missing_parameter_is_clear(tmp_path):
    db = tmp_path / "users.db"
    make_db(db)
    with pytest.raises(ValueError, match=r"missing parameter: \$minimum"):
        Client(str(db)).compile("FROM users WHERE age >= $minimum RETURN name")


def test_null_predicates_and_null_values(tmp_path):
    db = tmp_path / "records.db"
    connection = sqlite3.connect(db)
    connection.execute("CREATE TABLE records (name TEXT, value INTEGER)")
    connection.executemany("INSERT INTO records VALUES (?, ?)", [("A", None), ("B", 1)])
    connection.commit()
    connection.close()
    assert run(db, "FROM records WHERE value IS NULL RETURN name") == [{"name": "A"}]
    assert run(db, "FROM records WHERE value IS NOT NULL RETURN name") == [{"name": "B"}]
    assert run(db, "FROM records WHERE value = NULL RETURN name") == [{"name": "A"}]


def test_semantic_validation_with_schema():
    query = parse("FROM users RETURN name")
    validate(query, {"users": {"name": "Text"}})
    with pytest.raises(SemanticError, match="Unknown field"):
        validate(parse("FROM users RETURN missing"), {"users": {"name": "Text"}})


@pytest.mark.parametrize(
    "query",
    [
        "RETURN name",
        "FROM users",
        "FROM users WHERE age ??? 18 RETURN name",
        "FROM users WHERE age = 18 RETURN",
        "FROM users RETURN name ORDER BY age",
        "MATCH (u:User) RETURN u.name",
        "INSERT INTO users {name: 'A'}",
    ],
)
def test_invalid_or_deferred_syntax_is_rejected(query):
    with pytest.raises(ParseError):
        parse(query)


def test_sqlite_execution_error_is_structured(tmp_path):
    db = tmp_path / "empty.db"
    with pytest.raises(ExecutionError, match="SQLite error"):
        Client(str(db)).execute("FROM missing RETURN name")


def test_cli_operation(tmp_path):
    db = tmp_path / "users.db"
    make_db(db)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "qryndel.cli",
            "--db",
            str(db),
            "FROM users WHERE age >= $minimum RETURN name",
            "--param",
            "$minimum=18",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.splitlines() == ["name", "Alice", "Cara", "Dana"]


def test_cli_version():
    completed = subprocess.run([sys.executable, "-m", "qryndel.cli", "--version"], check=True, capture_output=True, text=True)
    assert completed.stdout.strip() == "0.1.0"
