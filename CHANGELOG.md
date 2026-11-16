# Changelog

All notable public changes to Qryndel are recorded here.

## [0.3.0] - 2026-11-17

Expressions, aliases, schema checks against the real database, and result
contracts. Still SQLite-only.

### Added

- **Column-to-column comparisons:** either side of a comparison can be a
  column or an expression, e.g.
  `WHERE l.current_stock != p.master_stock` or `WHERE price < cost * 1.2`.
  Rules can compare columns too (`RULE underpriced(x) = x.price < x.cost`).
- **Arithmetic:** `+`, `-`, `*`, `/`, unary minus and parentheses in
  `RETURN`, `WHERE`, `HAVING`, `ORDER BY`, inside aggregates
  (`SUM(qty * unit_cost)`), and in `UPDATE ... SET stock = stock - $sold`.
  `/` always divides as decimals (`7 / 2` is `3.5`). Literal values in
  expressions are still bound parameters.
- **`AS` aliases in `RETURN`:** `RETURN SUM(fees) / SUM(gross) * 100 AS fee_pct`.
  Aliases can be used in `ORDER BY`. A computed column without `AS` is named
  after its UDL text, such as `"qty * unit_cost"`. Two result columns with
  the same name are an error, because one would silently overwrite the other.
- **Schema loading from the database:** `Client(path, schema="auto")` reads
  every table and view, so an unknown table or column is reported before any
  SQL runs, with a hint: `Unknown field 'platfrom' in source 'orders'. Did
  you mean 'platform'?`. TEXT/JSON columns stay open for nested paths.
  `client.refresh_schema()` re-reads it. The CLI checks the schema by
  default (`--no-schema-check` turns it off) and `--show-schema` prints it.
- **Schema drift checks:** `client.check_schema(program)` (CLI:
  `--check-schema FILE`) compares a program's `TYPE` declarations with the
  real tables and reports removed, added, retyped and nullable columns, and
  missing tables. Report-only; nothing is changed. A program may now consist
  of declarations only.
- **Result contracts:** `client.execute(query, contracts=[...])` checks the
  rows before returning them: `NonNull`, `NoMissing`, `Unique`, `Compare`,
  `RowCount`. A failure raises `ContractViolation` (`UDL_CONTRACT_VIOLATION`)
  with redacted samples. `iter_verified_results` checks results chunk by
  chunk.
- New capability flags `computed_expressions` and `column_comparison`, so a
  backend that cannot do these fails before compiling.
- **`NUMBER(x)` and `TEXT(x)` conversions** for data imported as text:
  `WHERE NUMBER(price) > 1000`, `ORDER BY NUMBER(price) DESC`.
- **`TYPE ... price: Number` now converts the column** wherever the program
  uses it, so comparisons, sorting, `MIN`/`MAX` and results are numeric even
  when the column is stored as TEXT. With `ALLOW extra fields`, a TYPE block
  adds to a schema loaded from the database instead of replacing it.
- **`NonEmpty(field)` contract**: like `NonNull`, but also rejects `''` and
  blank strings.
- `Client(default_budget=...)` applies a budget to every `execute` that
  passes none, and `Client(busy_timeout_ms=...)` sets how long to wait for
  another connection's lock (default 5000 ms).
- `DatabaseLockedError` (`UDL_DATABASE_LOCKED`) with a suggestion, instead of
  a generic "database is locked" execution error.
- `examples/agent_guardrails.py`: a provider-neutral tool function that gives
  an AI agent read-only, schema-checked, budgeted database access.

### Changed

- A bare word on the right of a comparison is now a column:
  `WHERE city = Singapore` compares two columns. Quote strings:
  `WHERE city = 'Singapore'`. `INSERT { city: Singapore }` still stores the
  string, as before.
- `Program.statement` is `None` for a declaration-only program; running one
  raises a `SemanticError`.
- `-5` is parsed as unary minus applied to `5`; results are unchanged.
- `SUM(*)`, `AVG(*)`, `MIN(*)` and `MAX(*)` are parse errors; only `COUNT(*)`
  takes `*`.
- `ExecutionMetrics.result_bytes` is `None` unless a `max_result_bytes`
  budget is set.
- A parameter set to `None` still follows SQL semantics and matches nothing
  in `x = $p`; only a written `NULL` means `IS NULL`. This is deliberate:
  `WHERE owner_id = $user` with `user=None` must not match every unowned row.
  It is now documented.

### Safety

- **Comparing a text column with a number is an error when the schema is
  known** (`schema="auto"`, or the CLI default). In SQLite `price > 1000` on
  a TEXT column compares text, so `'999.99' > '1000'` is true; on a
  real-world import this returned 112,081 rows instead of 844. The message
  explains both fixes (`NUMBER(price)` or a TYPE declaration).
- **`UPDATE`/`DELETE` reject WHERE clauses that can never be false**: no
  column at all (`1 = 1`), a column compared with itself (`price = price`),
  `x IS NULL OR x IS NOT NULL`, and `p OR NOT p`. These used to delete every
  row despite the "WHERE required" rule. This catches common mistakes, not
  every possible tautology, so keep `max_affected_rows` as the backstop.

### Fixed

- The optimizer could push a predicate that compares columns from two
  joined tables into one side of the join.
- The statements inside a transaction are now included in the backend
  capability check (it used to stop at the transaction itself).
- Schema inspection no longer creates an empty database file when the path
  does not exist, and an `INTEGER PRIMARY KEY` is no longer reported as
  nullable.
- Deeply nested or very long queries crashed with `RecursionError` (from 200
  nested parentheses or a 1,000-term `OR`), and 500 `OR` terms overflowed
  SQLite's own parser. The AST is now built inside the LALR parser without
  recursion, `AND`/`OR` chains become balanced trees, and nesting deeper than
  200 levels is a clear `ParseError`.
- An integer parameter outside SQLite's 64-bit range raised a raw
  `OverflowError`; it is now a `ParameterError`. Integer literals that large
  become decimals, as in SQLite.
- `max_duration_ms` now also bounds waiting for another connection's lock
  (it used to wait SQLite's fixed 5 s).
- Writes start with `BEGIN IMMEDIATE`, so concurrent writers wait for each
  other instead of risking a deadlock error mid-transaction.
- Large reads were about twice as slow as plain SQLite because every result
  was JSON-encoded to fill the `result_bytes` metric. It is now measured only
  under a `max_result_bytes` budget (`None` otherwise); a 1M-row read went
  from 2.1x to about 1.3x native time.
- `max_result_bytes` read the whole result before checking it (10 s and over
  1 GB of memory to reject a 1M-row read); it now checks while fetching and
  stops early.

## [0.2.0] - 2026-11-06

The second public release. Qryndel is still SQLite-only; this release fills in
the rest of the language on that backend.

### Added

- **Sorting and paging:** `ORDER BY field [ASC|DESC] [NULLS FIRST|LAST]`,
  `LIMIT n`, and `RETURN DISTINCT`.
- **Joins:** `JOIN` / `INNER JOIN`, `LEFT JOIN`, and (on SQLite 3.39+)
  `RIGHT JOIN` and `FULL JOIN`, with table aliases and qualified fields such
  as `u.name`.
- **Aggregation:** `COUNT(*)`, `COUNT`, `SUM`, `AVG`, `MIN`, `MAX`, with
  `GROUP BY` and `HAVING`. Aggregates can be used in `ORDER BY`.
- **Nested fields:** dotted paths into JSON columns (`profile.address.city`)
  compile to `json_extract`. `IS MISSING`, `IS NOT MISSING`, and `EXISTS`
  tell an absent key apart from a JSON `null`.
- **Rules:** `RULE name(x) = <condition>` declares a reusable predicate that
  can be called in `WHERE`, including from other rules and inside
  transactions.
- **Schemas in the language:** `TYPE table { field: Type [required|optional] }`
  turns on field checking and required-field checks for `INSERT`.
  `ALLOW extra fields` relaxes it.
- **Writes:** `INSERT INTO t { field: value, ... }`,
  `UPDATE t SET f = v WHERE ...`, `DELETE FROM t WHERE ...`. `UPDATE` and
  `DELETE` without `WHERE` are rejected.
- **Transactions:** `BEGIN; <write>; <write>; COMMIT` (or `ROLLBACK`). Every
  statement commits together or not at all.
- **Comments:** `-- like this` to the end of a line.
- **Safety controls:** `Client(read_only=True)`, `Client(allowed_tables=[...])`,
  and execution budgets (`max_result_rows`, `max_result_bytes`,
  `max_affected_rows`, `max_duration_ms`). A write that goes over budget is
  rolled back.
- **Diagnostics:** `Client.explain()` shows the logical plan and SQL with
  parameter values redacted. `Client.preview()` counts the rows a write would
  affect without changing data. `QueryResult.metrics` reports timing and
  row counts.
- **CLI flags:** `--explain`, `--preview`, `--read-only`, `--max-rows`,
  `--max-affected-rows`, `--timeout-ms`, and `--json`. Errors are printed as
  `error[CODE]: message` with exit status 1 instead of a traceback.

### Changed

- `parse_program()` now returns a `Program` (types, rules, statement).
  `parse()` still returns the statement directly.
- `semantic.validate()` returns the validated statement (rules expanded,
  fields resolved). It used to return `None`.
- `SqliteBackend.capabilities` is now a `Capabilities` dataclass instead of a
  `frozenset` of names.
- `QueryResult` gained `rows_affected`, `last_insert_id`, `transaction`,
  `statements`, `metrics`, and `is_write`. Reads behave as before.
- Missing parameters raise `ParameterError`, which is both a `UdlError`
  (code `UDL_PARAMETER_ERROR`) and a `ValueError`. The message is unchanged:
  `missing parameter: $name`.
- Executing against a database file that does not exist raises
  `ExecutionError` instead of silently creating an empty file.
- The test suite creates `test.db` automatically; running `setup_db.py` first
  is no longer required.

### Kept from 0.1.0

- `Client("path.db")`, `client.compile(...)`, `client.execute(...)`, and
  `result.rows`.
- `WHERE x = NULL` still means `IS NULL`, and `!= NULL` means `IS NOT NULL`.
- CLI output for reads (`header`, then one row per line, ` | ` separated).
- Raw SQL strings given to the executor are limited to `SELECT`.

### Still not included

PostgreSQL, MongoDB, Neo4j, graph `MATCH` queries, and non-SQLite backends.

## [0.1.0] - 2026-10-18

First public release. Includes the UDL parser, AST, semantic validation,
logical planning, SQLite compilation and execution, named parameters, a
Python API, a CLI, and reproducible tests and examples.
