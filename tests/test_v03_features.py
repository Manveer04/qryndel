"""End-to-end tests for behaviour added in the v0.3.0 public release."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from qryndel import (
    Client,
    ContractViolation,
    NonNull,
    RowCount,
    Unique,
    load_sqlite_schema,
)
from qryndel.ast_nodes import BinaryOp, FieldPath, ProjectionItem
from qryndel.backends import get_backend
from qryndel.capabilities import required_capabilities
from qryndel.logical_plan import Filter, Join, build_plan
from qryndel.optimizer import optimize_plan
from qryndel.parser import ParseError, parse
from qryndel.semantic import SemanticError, validate

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "inventory.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE products (id INTEGER PRIMARY KEY, sku TEXT NOT NULL, master_stock INTEGER, cost REAL, meta TEXT);
        CREATE TABLE listings (id INTEGER PRIMARY KEY, product_id INTEGER, platform TEXT, current_stock INTEGER, price REAL);
        CREATE TABLE fees (platform TEXT, gross REAL, fees REAL, qty INTEGER);
        INSERT INTO products VALUES
            (1, 'A', 10, 2.0, '{"color": "red"}'),
            (2, 'B', 0, 5.0, '{"color": "blue"}'),
            (3, 'C', 7, 1.5, NULL);
        INSERT INTO listings VALUES
            (1, 1, 'shopee', 10, 4.0),
            (2, 1, 'lazada', 8, 4.5),
            (3, 2, 'shopee', 3, 9.0),
            (4, 3, 'tiktok', 7, 3.0);
        INSERT INTO fees VALUES ('shopee', 200.0, 22.0, 7), ('lazada', 100.0, 10.0, 2), ('shopee', 100.0, 11.0, 2);
        """
    )
    connection.commit()
    connection.close()
    return path


def rows(db, query, **kwargs):
    return Client(str(db)).execute(query, **kwargs).rows


# --- Column-to-column comparison -----------------------------------------

def test_stock_drift_across_a_join(db):
    query = (
        "FROM listings AS l JOIN products AS p ON l.product_id = p.id "
        "WHERE l.current_stock != p.master_stock "
        "RETURN p.sku, l.platform, l.current_stock, p.master_stock ORDER BY p.sku, l.platform"
    )
    assert rows(db, query) == [
        {"p.sku": "A", "l.platform": "lazada", "l.current_stock": 8, "p.master_stock": 10},
        {"p.sku": "B", "l.platform": "shopee", "l.current_stock": 3, "p.master_stock": 0},
    ]


def test_column_comparison_is_not_pushed_into_one_side_of_a_join():
    """Regression: the optimizer used to look only at the left column."""
    query = parse(
        "FROM listings AS l JOIN products AS p ON l.product_id = p.id "
        "WHERE l.current_stock != p.master_stock RETURN l.id"
    )
    validate(query, {"listings": ["id", "product_id", "current_stock"], "products": ["id", "master_stock"]})
    plan = optimize_plan(build_plan(query))
    filters = []

    def walk(node):
        if isinstance(node, Filter):
            filters.append(node)
            assert not isinstance(node.child, (type(None),))
        if isinstance(node, Join):
            assert not isinstance(node.left, Filter) and not isinstance(node.right, Filter)
            walk(node.left)
            walk(node.right)
        elif hasattr(node, "child"):
            walk(node.child)

    walk(plan)
    assert len(filters) == 1


def test_rule_comparing_two_columns(db):
    query = (
        "RULE underpriced(x) = x.price < x.current_stock\n"
        "FROM listings AS l WHERE underpriced(l) RETURN id ORDER BY id"
    )
    assert rows(db, query) == [{"id": 1}, {"id": 2}, {"id": 4}]


# --- Arithmetic -----------------------------------------------------------

def test_fee_percentage_and_stock_value(db):
    fee = rows(
        db,
        "FROM fees RETURN platform, SUM(fees) / SUM(gross) * 100 AS fee_pct "
        "GROUP BY platform ORDER BY fee_pct DESC",
    )
    assert [(r["platform"], round(r["fee_pct"], 2)) for r in fee] == [("shopee", 11.0), ("lazada", 10.0)]
    value = rows(db, "FROM products RETURN SUM(master_stock * cost) AS stock_value")
    assert value == [{"stock_value": 30.5}]


def test_division_is_never_integer_division(db):
    assert rows(db, "FROM fees WHERE platform = 'lazada' RETURN qty / 4 AS q") == [{"q": 0.5}]


def test_precedence_parentheses_and_unary_minus(db):
    result = rows(db, "FROM products WHERE id = 1 RETURN master_stock + 2 * 3 AS a, (master_stock + 2) * 3 AS b, -cost AS c")
    assert result == [{"a": 16, "b": 36, "c": -2.0}]


def test_unaliased_expression_is_keyed_by_its_text(db):
    assert rows(db, "FROM products WHERE id = 3 RETURN master_stock * 2") == [{"master_stock * 2": 14}]


def test_arithmetic_in_where_and_having(db):
    assert rows(db, "FROM listings WHERE price * current_stock > 30 RETURN id ORDER BY id") == [
        {"id": 1}, {"id": 2}
    ]
    assert rows(
        db, "FROM listings RETURN platform GROUP BY platform HAVING SUM(price * current_stock) > 50"
    ) == [{"platform": "shopee"}]


def test_update_with_an_expression(db):
    client = Client(str(db))
    result = client.execute("UPDATE products SET master_stock = master_stock - $sold WHERE sku = 'A'", params={"sold": 3})
    assert result.rows_affected == 1
    assert client.execute("FROM products WHERE sku = 'A' RETURN master_stock").rows == [{"master_stock": 7}]


def test_parameters_bind_in_sql_text_order(db):
    compiled = Client(str(db)).compile(
        "FROM listings WHERE price > $min RETURN id, price * $rate AS converted ORDER BY price + $bump",
        # SQL order is SELECT (rate), WHERE (min), ORDER BY (bump)
        params={"min": 1, "rate": 3.2, "bump": 0},
    )
    assert compiled.params == [3.2, 1, 0]
    assert compiled.sql.index("?") < compiled.sql.index("WHERE")


def test_literal_values_are_still_bound(db):
    compiled = Client(str(db)).compile("FROM products WHERE cost > 2 RETURN cost * 1.06 AS with_tax")
    assert "1.06" not in compiled.sql and compiled.params == [1.06, 2]


# --- Aliases --------------------------------------------------------------

def test_aliases_rename_result_keys_and_work_in_order_by(db):
    result = rows(
        db,
        "FROM listings RETURN platform AS channel, COUNT(*) AS listings GROUP BY platform ORDER BY listings DESC, channel",
    )
    assert result == [
        {"channel": "shopee", "listings": 2},
        {"channel": "lazada", "listings": 1},
        {"channel": "tiktok", "listings": 1},
    ]


def test_duplicate_result_names_are_rejected():
    with pytest.raises(SemanticError, match="Duplicate result column 'n'"):
        validate(parse("FROM t RETURN a AS n, b AS n"))


def test_return_items_keep_simple_types():
    query = parse("FROM t RETURN a, COUNT(*), a + 1, b AS bee")
    assert isinstance(query.fields[0], FieldPath)
    assert isinstance(query.fields[2], ProjectionItem) and isinstance(query.fields[2].expression, BinaryOp)
    assert query.fields[3].alias == "bee"


# --- Aggregate placement --------------------------------------------------

@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("FROM t WHERE SUM(a) > 1 RETURN a", "only allowed in HAVING"),
        ("FROM t RETURN SUM(COUNT(*))", "cannot be nested"),
    ],
)
def test_aggregate_placement_errors(query, message):
    with pytest.raises(SemanticError, match=message):
        validate(parse(query))


def test_only_count_accepts_star():
    with pytest.raises(ParseError, match="only COUNT"):
        parse("FROM t RETURN SUM(*)")


def test_bare_words_compare_columns_but_insert_keeps_strings():
    query = parse("FROM t WHERE city = town RETURN city")
    assert isinstance(query.where.value, FieldPath)
    insert = parse("INSERT INTO t { city: Singapore }")
    assert insert.values == {"city": "Singapore"}


def test_capabilities_name_the_new_features():
    plan = build_plan(parse("FROM t WHERE a > b RETURN a * 2 AS d"))
    assert {"computed_expressions", "column_comparison"} <= required_capabilities(plan)
    caps = get_backend("sqlite").capabilities
    assert caps.computed_expressions and caps.column_comparison


# --- Schema loading -------------------------------------------------------

def test_load_schema_marks_text_columns_as_open_documents(db):
    schema = load_sqlite_schema(db)
    assert set(schema) == {"products", "listings", "fees"}
    assert schema["products"]["master_stock"] == "Number"
    assert isinstance(schema["products"]["meta"], dict)


def test_auto_schema_catches_typos_with_a_suggestion(db):
    client = Client(str(db), schema="auto")
    with pytest.raises(SemanticError, match="Unknown field 'platfrom'.*Did you mean 'platform'"):
        client.compile("FROM listings RETURN platfrom")
    with pytest.raises(SemanticError, match="Unknown source"):
        client.compile("FROM listing RETURN id")


def test_auto_schema_allows_json_paths_in_text_columns(db):
    client = Client(str(db), schema="auto")
    assert client.execute("FROM products WHERE meta.color = 'blue' RETURN sku").rows == [{"sku": "B"}]
    with pytest.raises(SemanticError):
        client.compile("FROM products RETURN master_stock.value")  # numeric column is not a document


def test_refresh_schema_sees_new_columns(db):
    client = Client(str(db), schema="auto")
    connection = sqlite3.connect(db)
    connection.execute("ALTER TABLE products ADD COLUMN reorder_level INTEGER")
    connection.commit()
    connection.close()
    with pytest.raises(SemanticError):
        client.compile("FROM products RETURN reorder_level")
    client.refresh_schema()
    client.compile("FROM products RETURN reorder_level")


def test_schema_string_must_be_auto(db):
    with pytest.raises(ValueError, match='"auto"'):
        Client(str(db), schema="yes")


def test_auto_schema_on_missing_database_is_a_clear_error(tmp_path):
    from qryndel import UdlError

    missing = tmp_path / "missing.db"
    with pytest.raises(UdlError, match="unable to open"):
        Client(str(missing), schema="auto")
    assert not missing.exists()


# --- Contracts ------------------------------------------------------------

def test_contracts_verify_read_results(db):
    result = Client(str(db)).execute(
        "FROM products RETURN sku, meta", contracts=[Unique("sku"), RowCount(minimum=1)]
    )
    assert result.verified
    with pytest.raises(ContractViolation, match="'meta' is null in 1 row"):
        Client(str(db)).execute("FROM products RETURN sku, meta", contracts=[NonNull("meta")])


def test_contracts_on_writes_are_rejected_before_running(db):
    with pytest.raises(ValueError, match="read queries only"):
        Client(str(db)).execute("DELETE FROM products WHERE id = 1", contracts=[RowCount(maximum=1)])
    assert rows(db, "FROM products WHERE id = 1 RETURN sku") == [{"sku": "A"}]


# --- Schema drift ---------------------------------------------------------

def test_check_schema_reports_drift_and_clean_tables(db):
    reports = Client(str(db)).check_schema(
        "TYPE products {\n  id: ID required\n  sku: Text required\n  master_stock: Number\n  supplier: Text\n  ALLOW extra fields\n}\n"
        "TYPE listings {\n  id: ID required\n  product_id: Number\n  platform: Text\n  current_stock: Number\n  price: Number\n}\n"
        "TYPE warehouses { id: ID }\n"
    )
    by_name = {report.source: report for report in reports}
    assert [(c.kind, c.field) for c in by_name["products"].changes] == [("removed", "supplier")]
    assert by_name["listings"].clean
    assert by_name["warehouses"].changes[0].kind == "table_missing"


def test_declaration_only_program_cannot_be_executed(db):
    with pytest.raises(SemanticError, match="no statement to run"):
        Client(str(db)).execute("TYPE products { id: ID }")


# --- CLI ------------------------------------------------------------------

def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "qryndel.cli", *args], capture_output=True, text=True, cwd=str(PROJECT_ROOT)
    )


def test_cli_checks_schema_by_default(db):
    completed = run_cli("--db", str(db), "FROM listings RETURN platfrom")
    assert completed.returncode == 1
    assert "Did you mean 'platform'" in completed.stderr
    unchecked = run_cli("--db", str(db), "--no-schema-check", "FROM listings RETURN platfrom")
    assert "no such column" in unchecked.stderr


def test_cli_show_schema_and_check_schema(db, tmp_path):
    shown = run_cli("--db", str(db), "--show-schema", "--json")
    assert json.loads(shown.stdout)["listings"]["price"] == "Number"
    declarations = tmp_path / "types.qryndel"
    declarations.write_text("TYPE fees { platform: Text\n gross: Number\n fees: Number\n qty: Number }\n")
    assert run_cli("--db", str(db), "--check-schema", str(declarations)).returncode == 0
    declarations.write_text("TYPE fees { platform: Number }\n")
    drift = run_cli("--db", str(db), "--check-schema", str(declarations))
    assert drift.returncode == 1 and "type_changed: platform" in drift.stdout


def test_cli_arithmetic_and_alias_output(db):
    completed = run_cli("--db", str(db), "FROM products WHERE id = 1 RETURN sku, master_stock * cost AS value")
    assert completed.stdout.splitlines() == ["sku | value", "A | 20.0"]


def test_agent_guardrails_example_runs():
    completed = subprocess.run(
        [sys.executable, "-m", "examples.agent_guardrails"], capture_output=True, text=True, cwd=str(PROJECT_ROOT)
    )
    assert completed.returncode == 0, completed.stderr
    assert "Did you mean 'name'?" in completed.stdout
    assert "client is read-only" in completed.stdout
    assert '"rows": [{"name": "Diana"}' in completed.stdout
