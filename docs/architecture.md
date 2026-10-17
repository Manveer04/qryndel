# Architecture

The v0.1.0 execution path is deliberately small:

```text
UDL source -> Lark parser -> AST -> semantic validation -> logical plan -> SQLite compiler -> executor
```

The parser reports syntax errors, semantic validation checks the query against
the supplied schema when one is available, and the SQLite backend emits SQL
with bound parameters. The executor returns dictionaries for result rows.

The public release supports SQLite only. Other adapters and broader planning
work remain outside the v0.1.0 surface.
