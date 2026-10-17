# Qryndel

Qryndel is an experimental implementation of Unified Data Language (UDL), a
small query language intended to separate how a query is written from how it
is executed. The first public release focuses on a reliable SQLite path.

The project is early-stage and the language may change.

## Why UDL?

Applications often couple query text directly to one database engine. UDL
provides a compact, database-oriented syntax and a pipeline that parses it,
checks its meaning, builds a logical plan, and compiles that plan to SQLite.

## Example

```udl
FROM users
WHERE age >= $minimum AND (city = "Singapore" OR city = "Jakarta")
RETURN name, age
```

The query uses named parameters rather than interpolating values into query
text.

## Installation

Qryndel v0.1.0 is distributed from the source repository. From a checkout:

```bash
python -m venv .venv
# Activate .venv using the command for your shell.
python -m pip install --upgrade pip
python -m pip install .
```

Python 3.10 or newer is required.

## Getting Started

Create the reproducible example database and run the query through the CLI:

```bash
python setup_db.py
udl "FROM users WHERE age >= $minimum RETURN name, age" --param '$minimum=18'
```

The same query can be run through the Python API:

```python
from qryndel import Client

client = Client("test.db")
result = client.execute(
    "FROM users WHERE age >= $minimum RETURN name, age",
    params={"minimum": 18},
)

print(result.rows)
```

The fixture created by `setup_db.py` contains `users` and `orders` tables.

## Supported In v0.1.0

- `FROM`, `WHERE`, and `RETURN` table queries.
- `AND`, `OR`, `NOT`, parentheses, and comparison operators.
- String, numeric, boolean, and `NULL` values where supported by SQLite.
- Named query parameters such as `$minimum`.
- Basic table aliases using `AS`.
- SQLite compilation and execution with bound parameters.
- Parser and semantic errors with source-aware messages.
- A Python API and `udl` command-line entry point.

The SQLite compiler also supports the basic `IS NULL` and `IS NOT NULL`
predicates. The release intentionally does not promise portability to another
database engine.

## Current Limitations

This release does not support PostgreSQL, MongoDB, Neo4j, graph queries,
aggregations, grouping, transactions, or data-modifying statements as public
v0.1.0 features. Advanced implementation work remains in the private
development codebase for later review.

The public release is SQLite-first; it is not a claim that UDL can query any
database today.

## Implementation Overview

The core path is:

```text
UDL text -> lexer/parser -> AST -> semantic validation -> logical plan -> SQLite SQL
```

Values are passed separately from generated SQL. The package does not build
SQL by concatenating user-provided values.

## Development And Testing

Install the development dependency and create the fixture before running the
tests:

```bash
python -m pip install -e ".[test]"
python setup_db.py
python -m pytest -q
```

Tests use temporary SQLite databases where possible and do not require an
external database service.

## Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md). Future work may include richer
expressions, aggregation, schema validation, safe mutations, transactions,
additional adapters, diagnostics, and developer tooling. These are plans,
not compatibility promises.

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
