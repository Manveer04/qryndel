# Changelog

All notable public changes to Qryndel are recorded here.

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
