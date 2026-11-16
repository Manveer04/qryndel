# Qryndel

[![Tests](https://github.com/Manveer04/qryndel/actions/workflows/tests.yml/badge.svg)](https://github.com/Manveer04/qryndel/actions/workflows/tests.yml)
[![Python 3.10–3.13](https://img.shields.io/badge/python-3.10%20%E2%80%93%203.13-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Release](https://img.shields.io/github/v/release/Manveer04/qryndel?include_prereleases)](https://github.com/Manveer04/qryndel/releases)

**A safe query language for SQLite.** Qryndel checks every query before it
runs: unknown columns, `DELETE` without a real `WHERE`, text compared with
numbers, runaway result sets. Values are always bound parameters, so SQL
injection is ruled out by design. Useful anywhere a query comes from somewhere
you don't fully trust: an app's users, a script, or an AI agent.

```text
$ udl "FROM users RETURN nmae"
error[UDL_SEMANTIC_ERROR]: Unknown field 'nmae' in source 'users'. Did you mean 'name'?

$ udl "DELETE FROM orders WHERE 1 = 1"
error[UDL_SEMANTIC_ERROR]: DELETE on 'orders' would change every row: the WHERE clause can never be false ...
```

Qryndel is an experimental implementation of Unified Data Language (UDL), a
small query language that separates how a query is written from how it is
executed. It is SQLite-first: reads, joins, aggregation, arithmetic, safe
writes and transactions, checked against the real database schema. It was
tested against a 1.5-million-row real-world dataset
([results](docs/real-world-testing.md)). The language may still change.

## Why UDL?

Applications often couple query text directly to one database engine, and
leave safety to code review. UDL parses each query, checks its meaning
against the schema, builds a logical plan, and compiles that plan to
parameterized SQLite. Safety rules, such as "no `DELETE` without `WHERE`",
are part of the language.

| You want to... | Qryndel gives you |
| --- | --- |
| Let an AI agent or end user query a database | Read-only clients, table allow-lists, row/time budgets, no SQL injection |
| Catch mistakes before they reach the database | Schema checks with "did you mean", text-vs-number checks, always-true `WHERE` checks |
| See what a query will do | `explain` (values redacted) and `preview` for writes |
| Trust the result | Result contracts: `Unique`, `NonNull`, `NonEmpty`, `RowCount`, `Compare` |
| Keep schemas honest | `TYPE` declarations and `--check-schema` drift reports for CI |

## Example

```udl
RULE is_adult(x) = x.age >= $minimum

FROM users AS u
JOIN orders AS o ON u.id = o.user_id
WHERE is_adult(u) AND (u.city = "Singapore" OR u.city = "Jakarta")
RETURN u.name AS customer,
       COUNT(*) AS orders,
       SUM(o.amount * o.quantity) AS spent
GROUP BY u.name
HAVING COUNT(*) >= 2
ORDER BY spent DESC
LIMIT 10
```

Comparisons can use columns on both sides, which is how you find stock that
has drifted between a marketplace and your master record:

```udl
FROM platform_listings AS l
JOIN products AS p ON l.product_id = p.id
WHERE l.current_stock != p.master_stock
RETURN p.master_sku AS sku, l.current_stock - p.master_stock AS difference
```

Values are passed as named parameters (`$minimum`) and bound by SQLite. They
are never concatenated into SQL text.

## Installation

Qryndel is distributed from the source repository. From a checkout:

```bash
python -m venv .venv
# Activate .venv using the command for your shell.
python -m pip install --upgrade pip
python -m pip install .
```

Python 3.10 or newer is required. `RIGHT JOIN` and `FULL JOIN` need SQLite
3.39 or newer, and `NULLS FIRST`/`NULLS LAST` need SQLite 3.30 or newer. On
older SQLite versions Qryndel reports a clear capability error before running
the query.

## Getting Started

Create the example database and run a query through the CLI:

```bash
python setup_db.py
udl "FROM users WHERE age >= $minimum RETURN name, age ORDER BY age DESC" --param '$minimum=18'
```

The same query through the Python API:

```python
from qryndel import Client

client = Client("test.db")
result = client.execute(
    "FROM users WHERE age >= $minimum RETURN name, age ORDER BY age DESC",
    params={"minimum": 18},
)
print(result.rows)
```

The fixture created by `setup_db.py` contains `users` (including JSON
`profile` and `settings` columns) and `orders` tables. Runnable programs are
in [`examples/`](examples).

## Catching Mistakes Before They Run

The CLI reads the database's tables and columns first, and
`Client(path, schema="auto")` does the same in Python:

```text
$ udl "FROM users RETURN nmae"
error[UDL_SEMANTIC_ERROR]: Unknown field 'nmae' in source 'users'. Did you mean 'name'?
```

`TYPE` declarations can be checked against the real tables, which is useful
in CI to notice that a migration changed a column the application relies on:

```bash
udl --check-schema examples/checks/fixture_types.qryndel   # exit 1 on drift
```

Data imported from CSV often stores numbers as TEXT, where SQLite compares
them as text (`'999.99' > '1000'`). When the schema is known, Qryndel refuses
that comparison and tells you how to fix it:

```text
$ udl "FROM order_items WHERE price > 1000 RETURN COUNT(*)"
error[UDL_SEMANTIC_ERROR]: Column 'price' is stored as text, so `price > 1000` would
compare text, not numbers ... Write NUMBER(price) to compare as numbers, or declare
`price: Number` in a TYPE block.
```

Results can be checked before your code uses them:

```python
from qryndel import Client, NonEmpty, NonNull, RowCount, Unique

client.execute(
    "FROM orders WHERE amount > 0 RETURN id, amount, product",
    contracts=[Unique("id"), NonNull("amount"), NonEmpty("product"), RowCount(minimum=1)],
)  # raises ContractViolation if any check fails
```

`NonNull` accepts `''`; `NonEmpty` also rejects empty and blank strings.
Other contracts: `NoMissing` (the field must exist) and `Compare`
(`Compare("amount", ">=", 0)`).

## Writing Data

```python
client.execute(
    "INSERT INTO users { name: $name, age: 22, email: $email, city: 'Singapore' }",
    params={"name": "Hana", "email": "hana@example.com"},
)
client.execute("UPDATE users SET city = 'Jakarta' WHERE name = 'Hana'")

# UPDATE or DELETE without WHERE is rejected before any SQL runs.
client.execute("DELETE FROM users")  # raises SemanticError

# Several writes commit together, or not at all.
client.execute("""
BEGIN
UPDATE users SET city = "Jakarta" WHERE name = "Eve";
DELETE FROM orders WHERE user_id = 5;
COMMIT
""")
```

Writes return `rows_affected` (and `last_insert_id` for `INSERT`) on the
result.

## Safety Controls

| Control | Python API | CLI |
| --- | --- | --- |
| Reject all writes | `Client(path, read_only=True)` | `--read-only` |
| Limit which tables writes touch | `Client(path, allowed_tables=["orders"])` | — |
| Cap rows returned | `budget={"max_result_rows": 1000}` | `--max-rows N` |
| Cap rows a write changes (rolls back) | `budget={"max_affected_rows": 10}` | `--max-affected-rows N` |
| Time limit | `budget={"max_duration_ms": 500}` | `--timeout-ms MS` |
| See the SQL without running it | `client.explain(query)` | `--explain` |
| Check tables and columns first | `Client(path, schema="auto")` | on by default (`--no-schema-check` to skip) |
| Check results before using them | `execute(..., contracts=[...])` | — |
| One budget for every query | `Client(path, default_budget={...})` | — |
| Lock wait limit | `Client(path, busy_timeout_ms=5000)` | capped by `--timeout-ms` |
| Count rows a write would change | `client.preview(query)` | `--preview` |

`explain` output never includes parameter values. See
[SECURITY.md](SECURITY.md) for the full security model.

## Safe Database Access For AI Agents

Give a model a tool that runs UDL instead of raw SQL. Qryndel checks each
query against the live schema, refuses writes, caps rows and time, and
returns short errors the model can act on:

```python
from qryndel import Client, UdlError

agent_db = Client("shop.db", schema="auto", read_only=True,
                  default_budget={"max_result_rows": 200, "max_duration_ms": 2000})

def query_database(udl: str) -> dict:
    try:
        return {"rows": agent_db.execute(udl).rows}
    except UdlError as error:
        return {"error": error.code, "message": error.message}  # e.g. "Did you mean 'name'?"
```

The full runnable version, with a tool description generated from the schema,
is [examples/agent_guardrails.py](examples/agent_guardrails.py)
(`python -m examples.agent_guardrails`). It does not depend on any particular
LLM provider.

## Supported In v0.3.0

- `FROM`, `WHERE`, `RETURN [DISTINCT]`, `ORDER BY ... [ASC|DESC] [NULLS FIRST|LAST]`, `LIMIT`.
- `AND`, `OR`, `NOT`, parentheses, and `=`, `!=`, `>`, `<`, `>=`, `<=`, with
  columns or expressions on either side.
- Arithmetic (`+ - * /`, unary minus), `NUMBER(x)` / `TEXT(x)` conversions,
  and `AS` names in `RETURN`.
- `JOIN`, `INNER JOIN`, `LEFT JOIN`, `RIGHT JOIN`, `FULL JOIN` with aliases.
- `COUNT`, `SUM`, `AVG`, `MIN`, `MAX` (over expressions), `GROUP BY`, `HAVING`.
- Nested JSON fields (`profile.address.city`) and `IS [NOT] NULL`,
  `IS [NOT] MISSING`, `EXISTS`.
- `RULE` reusable predicates and `TYPE` schema declarations.
- `INSERT`, `UPDATE` (including `SET stock = stock - $sold`), `DELETE`, and
  `BEGIN ... COMMIT|ROLLBACK` transactions.
- Schema checks against the live database, schema-drift reports, and result
  contracts.
- Named parameters (`$name`) and `--` line comments.
- Source-aware parse errors and structured `UdlError` codes.
- A Python API and the `udl` command-line tool.

The full syntax is in the [language reference](docs/language-reference.md).
Upgrading? See the [changelog](CHANGELOG.md). The one behaviour change in
0.3.0: an unquoted word on the right of a comparison is now a column name, so
write `city = 'Singapore'`, not `city = Singapore`.

## Tested On Real Data

v0.3.0 was tested against a 1.5-million-row public e-commerce dataset:
native-SQL comparisons, 3,000 generated queries, 15,000 fuzzed inputs,
rollback, concurrency and 1M-row workloads. See
[docs/real-world-testing.md](docs/real-world-testing.md) for what was found
and fixed.

## Current Limitations

PostgreSQL, MongoDB, Neo4j and graph `MATCH` queries are not part of v0.3.0.
UDL does not yet promise that a query gives identical results on engines
other than SQLite.

## Implementation Overview

```text
UDL text -> parser -> AST -> rule expansion + semantic validation
         -> logical plan -> optimizer -> safety policy -> SQLite SQL -> executor
```

See [docs/architecture.md](docs/architecture.md).

## Development And Testing

```bash
python -m pip install -e ".[test]"
python -m pytest -q
```

The test suite creates its own SQLite fixtures and needs no external
database service.

## Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md). These are plans, not compatibility
promises.

## Contributing

Bug reports, focused feature proposals, tests, and documentation improvements
are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull
request. Major language changes should start with an RFC in
[docs/rfcs](docs/rfcs/README.md).

## License

Qryndel is released under the [MIT License](LICENSE).

## Original Author

Qryndel was initiated by **Manveer Singh**, Original Creator and Founding
Maintainer.

AI-assisted development disclosure: [docs/AI_ASSISTED_DEVELOPMENT.md](docs/AI_ASSISTED_DEVELOPMENT.md).
