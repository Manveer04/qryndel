"""Regression tests for problems found by testing v0.3.0 against a 1.5M-row
real-world database (all-TEXT columns, no indexes, 1M-row tables).

The fixtures here are small and synthetic; no external data is needed.
"""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from qryndel import (
    BudgetExceededError,
    Client,
    DatabaseLockedError,
    NonEmpty,
    NonNull,
    UdlError,
)
from qryndel.contracts import ContractViolation, iter_verified_results
from qryndel.parser import MAX_NESTING_DEPTH, ParseError, parse
from qryndel.semantic import SemanticError


@pytest.fixture
def textdb(tmp_path):
    """Numbers stored as TEXT, like a CSV import."""
    path = tmp_path / "text.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE items (id TEXT, price TEXT, qty TEXT, title TEXT)")
    con.executemany(
        "INSERT INTO items VALUES (?, ?, ?, ?)",
        [("1", "999.99", "1", "a"), ("2", "1500.00", "2", ""), ("3", "250.5", "3", None), ("4", "6735.00", "1", "  ")],
    )
    con.commit()
    con.close()
    return path


# --- numbers stored as text ----------------------------------------------

def test_text_column_compared_with_number_is_rejected(textdb):
    with pytest.raises(SemanticError, match=r"stored as text.*NUMBER\(price\)"):
        Client(str(textdb), schema="auto").compile("FROM items WHERE price > 1000 RETURN id")


def test_without_a_schema_sqlite_semantics_are_unchanged(textdb):
    # Text comparison: '999.99' > '1000' is true, as plain SQLite would answer.
    ids = [r["id"] for r in Client(str(textdb)).execute("FROM items WHERE price > 1000 RETURN id ORDER BY id").rows]
    assert ids == ["1", "2", "3", "4"]


def test_number_conversion_compares_and_sorts_numerically(textdb):
    client = Client(str(textdb), schema="auto")
    assert [r["id"] for r in client.execute("FROM items WHERE NUMBER(price) > 1000 RETURN id ORDER BY id").rows] == ["2", "4"]
    assert client.execute("FROM items RETURN MAX(NUMBER(price)) AS hi").rows == [{"hi": 6735}]
    top = client.execute("FROM items RETURN id ORDER BY NUMBER(price) DESC LIMIT 1").rows
    assert top == [{"id": "4"}]


def test_text_conversion(textdb):
    rows = Client(str(textdb)).execute("FROM items WHERE id = '3' RETURN TEXT(NUMBER(qty) * 2) AS doubled").rows
    assert rows == [{"doubled": "6"}]


def test_type_number_declaration_converts_every_use(textdb):
    client = Client(str(textdb), schema="auto")
    program = "TYPE items { price: Number\n qty: Number\n ALLOW extra fields }\n"
    rows = client.execute(program + "FROM items WHERE price > 1000 RETURN id, price ORDER BY price DESC").rows
    assert rows == [{"id": "4", "price": 6735}, {"id": "2", "price": 1500}]
    # ALLOW extra fields keeps the database's other columns (id) valid.
    # 999.99*1 + 1500*2 + 250.5*3 + 6735*1
    assert client.execute(program + "FROM items RETURN SUM(price * qty) AS total").rows == [{"total": 11486.49}]


def test_arithmetic_on_text_columns_still_works(textdb):
    rows = Client(str(textdb), schema="auto").execute("FROM items WHERE price * 1 > 1000 RETURN COUNT(*) AS n").rows
    assert rows == [{"n": 2}]


# --- always-true WHERE on writes ------------------------------------------

@pytest.mark.parametrize(
    "query",
    [
        "DELETE FROM items WHERE 1 = 1",
        "DELETE FROM items WHERE price = price",
        "DELETE FROM items WHERE price >= price",
        "DELETE FROM items WHERE price IS NULL OR price IS NOT NULL",
        "DELETE FROM items WHERE NOT (id = '1') OR id = '1'",
        "UPDATE items SET qty = '0' WHERE 2 > 1",
        "BEGIN; UPDATE items SET qty = '0' WHERE id = '1'; DELETE FROM items WHERE 1 = 1; COMMIT",
    ],
)
def test_where_clauses_that_match_every_row_are_rejected(textdb, query):
    with pytest.raises(SemanticError, match="would change every row"):
        Client(str(textdb)).execute(query)
    assert len(Client(str(textdb)).execute("FROM items RETURN id").rows) == 4


def test_ordinary_where_clauses_still_run(textdb):
    client = Client(str(textdb))
    assert client.execute("DELETE FROM items WHERE id = '1' OR price = '250.5'").rows_affected == 2
    assert client.execute("UPDATE items SET qty = qty + 1 WHERE id != '2'").rows_affected == 1


# --- deep / long queries --------------------------------------------------

def test_long_or_chains_and_many_parameters_work(textdb):
    client = Client(str(textdb))
    text = "FROM items WHERE " + " OR ".join(f"id = $p{i}" for i in range(2000)) + " RETURN COUNT(*) AS n"
    params = {f"p{i}": str(i) for i in range(2000)}
    assert client.execute(text, params=params).rows == [{"n": 4}]


def test_deep_parentheses_parse(textdb):
    query = "FROM items WHERE " + "(" * 2000 + "id = '1'" + ")" * 2000 + " RETURN id"
    assert Client(str(textdb)).execute(query).rows == [{"id": "1"}]


def test_too_deep_nesting_is_a_clean_parse_error():
    with pytest.raises(ParseError, match="nested too deeply"):
        parse("FROM t RETURN " + " + ".join("1" for _ in range(MAX_NESTING_DEPTH + 50)) + " AS x")


def test_or_chains_are_balanced_trees():
    where = parse("FROM t WHERE " + " OR ".join(f"a = {i}" for i in range(1024)) + " RETURN a").where
    depth, node = 0, where
    while hasattr(node, "left"):
        node, depth = node.left, depth + 1
    assert depth == 10  # log2(1024), not 1023


# --- numbers out of range -------------------------------------------------

def test_huge_integer_parameter_is_a_parameter_error(textdb):
    with pytest.raises(UdlError, match="too large for SQLite"):
        Client(str(textdb)).execute("FROM items WHERE id = $p RETURN id", params={"p": 2**70})


def test_huge_integer_literal_becomes_a_decimal():
    assert parse("FROM t WHERE a > 100000000000000000000 RETURN a").where.value == 1e20


# --- NULL parameters keep SQL semantics -----------------------------------

def test_none_parameter_matches_nothing_but_literal_null_is_a_null_check(textdb):
    client = Client(str(textdb))
    assert client.execute("FROM items WHERE title = NULL RETURN id").rows == [{"id": "3"}]
    # Deliberate: `owner = $user` with user=None must not match unowned rows.
    assert client.execute("FROM items WHERE title = $t RETURN id", params={"t": None}).rows == []


# --- NonEmpty -------------------------------------------------------------

def test_nonempty_rejects_null_empty_and_blank_strings(textdb):
    client = Client(str(textdb))
    with pytest.raises(ContractViolation, match="'title' is null in 1 row"):
        client.execute("FROM items RETURN title", contracts=[NonNull("title")])  # '' and '  ' pass NonNull
    with pytest.raises(ContractViolation, match="'title' is empty in 3 row"):
        client.execute("FROM items RETURN title", contracts=[NonEmpty("title")])
    rows = list(iter_verified_results([[{"t": "x"}], [{"t": "y"}]], [NonEmpty("t")]))
    assert len(rows) == 2
    with pytest.raises(ContractViolation):
        list(iter_verified_results([[{"t": "x"}], [{"t": ""}]], [NonEmpty("t")]))


# --- locks and concurrency ------------------------------------------------

def _hold_write_lock(path):
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    return holder


def test_lock_wait_is_bounded_by_the_time_budget(textdb):
    holder = _hold_write_lock(textdb)
    try:
        started = time.perf_counter()
        with pytest.raises(DatabaseLockedError) as info:
            Client(str(textdb)).execute("UPDATE items SET qty = '9' WHERE id = '1'", budget={"max_duration_ms": 200})
        assert time.perf_counter() - started < 2
        assert info.value.code == "UDL_DATABASE_LOCKED"
        assert "WAL" in info.value.suggested_action
    finally:
        holder.execute("ROLLBACK")
        holder.close()


def test_busy_timeout_option(textdb):
    holder = _hold_write_lock(textdb)
    try:
        started = time.perf_counter()
        with pytest.raises(DatabaseLockedError):
            Client(str(textdb), busy_timeout_ms=100).execute("UPDATE items SET qty = '9' WHERE id = '1'")
        assert time.perf_counter() - started < 2
    finally:
        holder.execute("ROLLBACK")
        holder.close()


def test_concurrent_transactions_do_not_deadlock(tmp_path):
    path = tmp_path / "counter.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE c (id INTEGER, n INTEGER)")
    con.execute("INSERT INTO c VALUES (1, 0), (2, 0)")
    con.commit()
    con.close()
    errors = []

    def worker():
        client = Client(str(path))
        for _ in range(25):
            try:
                client.execute("BEGIN; UPDATE c SET n = n + 1 WHERE id = 1; UPDATE c SET n = n + 1 WHERE id = 2; COMMIT")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert Client(str(path)).execute("FROM c RETURN n ORDER BY id").rows == [{"n": 150}, {"n": 150}]


# --- large results ---------------------------------------------------------

@pytest.fixture
def bigdb(tmp_path):
    path = tmp_path / "big.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE r (id INTEGER, payload TEXT)")
    con.executemany("INSERT INTO r VALUES (?, ?)", ((i, "x" * 50) for i in range(50_000)))
    con.commit()
    con.close()
    return path


def test_byte_budget_stops_reading_early(bigdb):
    started = time.perf_counter()
    with pytest.raises(BudgetExceededError, match="max_result_bytes"):
        Client(str(bigdb)).execute("FROM r RETURN id, payload", budget={"max_result_bytes": 20_000})
    assert time.perf_counter() - started < 1.0


def test_result_bytes_metric_is_only_measured_when_budgeted(bigdb):
    client = Client(str(bigdb))
    assert client.execute("FROM r WHERE id < 10 RETURN id").metrics.result_bytes is None
    measured = client.execute("FROM r WHERE id < 10 RETURN id", budget={"max_result_bytes": 10_000}).metrics
    assert measured.result_bytes == len('[{"id":0},{"id":1},{"id":2},{"id":3},{"id":4},{"id":5},{"id":6},{"id":7},{"id":8},{"id":9}]')


def test_default_budget_applies_when_none_is_passed(bigdb):
    client = Client(str(bigdb), default_budget={"max_result_rows": 100})
    with pytest.raises(BudgetExceededError, match="max_result_rows=100"):
        client.execute("FROM r RETURN id")
    assert len(client.execute("FROM r RETURN id", budget={"max_result_rows": 60_000}).rows) == 50_000
