# Qryndel

Qryndel is an experimental implementation of Unified Data Language (UDL), a
small query language intended to separate how a query is written from how it
is executed. Qryndel is SQLite-first: v0.2.0 covers reads, joins,
aggregation, safe writes and transactions on SQLite.

The project is early-stage and the language may change.

## Why UDL?

Applications often couple query text directly to one database engine. UDL
provides a compact, database-oriented syntax and a pipeline that parses it,
checks its meaning, builds a logical plan, and compiles that plan to SQLite.
Safety rules, such as "no `DELETE` without `WHERE`", are part of the
language rather than left to code review.

## Example

```udl
RULE is_adult(x) = x.age >= $minimum

FROM users AS u
JOIN orders AS o ON u.id = o.user_id
WHERE is_adult(u) AND (u.city = "Singapore" OR u.city = "Jakarta")
RETURN u.name, COUNT(*), SUM(o.amount)
GROUP BY u.name
HAVING COUNT(*) >= 2
ORDER BY SUM(o.amount) DESC
LIMIT 10
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
| Count rows a write would change | `client.preview(query)` | `--preview` |

`explain` output never includes parameter values. See
[SECURITY.md](SECURITY.md) for the full security model.

## Supported In v0.2.0

- `FROM`, `WHERE`, `RETURN [DISTINCT]`, `ORDER BY ... [ASC|DESC] [NULLS FIRST|LAST]`, `LIMIT`.
- `AND`, `OR`, `NOT`, parentheses, and `=`, `!=`, `>`, `<`, `>=`, `<=`.
- `JOIN`, `INNER JOIN`, `LEFT JOIN`, `RIGHT JOIN`, `FULL JOIN` with aliases.
- `COUNT`, `SUM`, `AVG`, `MIN`, `MAX`, `GROUP BY`, `HAVING`.
- Nested JSON fields (`profile.address.city`) and `IS [NOT] NULL`,
  `IS [NOT] MISSING`, `EXISTS`.
- `RULE` reusable predicates and `TYPE` schema declarations.
- `INSERT`, `UPDATE`, `DELETE`, and `BEGIN ... COMMIT|ROLLBACK` transactions.
- Named parameters (`$name`) and `--` line comments.
- Source-aware parse errors and structured `UdlError` codes.
- A Python API and the `udl` command-line tool.

The full syntax is in the [language reference](docs/language-reference.md).
Upgrading from 0.1.0? See the [changelog](CHANGELOG.md); existing 0.1.0
queries and API calls keep working.

## Current Limitations

PostgreSQL, MongoDB, Neo4j and graph `MATCH` queries are not part of v0.2.0.
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
