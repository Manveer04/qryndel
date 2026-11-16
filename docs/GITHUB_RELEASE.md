# Qryndel v0.3.0

Qryndel v0.3.0 makes UDL useful for real reporting and inventory work on
SQLite, and was hardened by testing against a 1.5-million-row real-world
database (see `docs/real-world-testing.md`).

## New

- **Compare columns:** `WHERE l.current_stock != p.master_stock` finds stock
  that drifted between a marketplace and your master record.
- **Arithmetic:** `SUM(fees) / SUM(gross) * 100 AS fee_pct`,
  `SUM(qty * unit_cost) AS stock_value`, `SET stock = stock - $sold`.
  Division is always decimal.
- **Named results:** `RETURN ... AS name`, usable in `ORDER BY`.
- **Numbers stored as text:** `NUMBER(x)` / `TEXT(x)` conversions, and
  `TYPE ... price: Number` converts a TEXT column everywhere it is used.
- **Schema checks against the live database:** typos are caught before any
  SQL runs, with a "did you mean" hint. `--check-schema` reports drift
  between `TYPE` declarations and the real tables.
- **Result contracts:** `NonNull`, `NonEmpty`, `Unique`, `RowCount`,
  `Compare` and `NoMissing` checks on query results.
- **Client options:** `default_budget` for every query and `busy_timeout_ms`
  for lock waits.

## Safer

- Comparing a TEXT column with a number (`price > 1000`) is an error when the
  schema is known. In plain SQLite it compares text and, on the test data,
  returned 112,081 rows instead of 844.
- `UPDATE`/`DELETE` reject WHERE clauses that can never be false
  (`1 = 1`, `price = price`, `x IS NULL OR x IS NOT NULL`).
- Writes use `BEGIN IMMEDIATE`; a locked database gives
  `UDL_DATABASE_LOCKED` within the time budget instead of waiting 5 s.

## Fixed

- Deep or long queries no longer crash with `RecursionError`; a 5,000-term
  `OR` works and nesting beyond 200 levels is a clear error.
- Huge integer parameters give a `ParameterError` instead of `OverflowError`.
- Large reads are about 1.3x native SQLite time instead of 2x, and
  `max_result_bytes` stops reading as soon as the limit is passed.
- The optimizer no longer pushes a two-table column comparison into one side
  of a join.

## Behaviour changes

- An unquoted word on the right of a comparison is now a column name
  (`city = 'Singapore'`, not `city = Singapore`).
- `ExecutionMetrics.result_bytes` is `None` unless a `max_result_bytes`
  budget is set.

Everything else written for v0.1.0 and v0.2.0 keeps working. See
`CHANGELOG.md` for the full list.

Install from a source checkout with `python -m pip install .`. Python 3.10+
and SQLite are required. PostgreSQL, MongoDB, Neo4j and graph queries are not
part of this release.
