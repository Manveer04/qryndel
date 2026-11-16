# Real-World Testing (v0.3.0)

Before release, v0.3.0 was tested against a public e-commerce dataset
imported from CSV into SQLite: 9 tables, about 1.55 million rows (the largest
table has 1,000,163 rows), every column stored as TEXT, and no indexes. All
writes ran on throwaway copies. The dataset is not part of this repository;
`tests/test_v03_hardening.py` reproduces each finding on small synthetic
tables.

## What was tested

| Area | How | Result |
| --- | --- | --- |
| UDL vs native SQL | 27 hand-written query pairs: filters, 2-5 table joins, LEFT JOIN, GROUP BY / HAVING, arithmetic, column comparisons, NULL ordering, Unicode | All identical |
| Generated queries | 3,000 random valid queries vs independently rendered SQL, on real rows with NULLs and `''` mixed in | All identical |
| Parser and compiler fuzzing | 15,000 random, token-soup and mutated inputs | Only clean `UdlError`s |
| NULL and empty strings | `''` vs `NULL` vs blanks, `COUNT(col)`, `AVG`, NULL arithmetic, division by zero, NULL ordering | Correct; NULL parameters documented |
| Destructive queries | missing or always-true WHERE, read-only, table allow-list, previews, parameter injection | Protected after fixes |
| Rollback | failing statement in a transaction, row and time budgets on 112k- and 1M-row updates, a process killed mid-transaction | No partial changes |
| Concurrency | 8 writers x 50 increments with readers; 6 x 40 two-statement transactions; held locks; WAL mode | No lost updates, no deadlocks |
| Performance | aggregates, joins, scans and full reads on 100K-1M rows; time and peak memory | Aggregates and joins at native speed; full reads about 1.3x native |

## Problems found and fixed

- Comparing a TEXT column with a number (`price > 1000`) silently compared
  text: 112,081 rows instead of 844. It is now an error when the schema is
  known, and `NUMBER(x)` or `TYPE ... price: Number` give the numeric answer.
- `DELETE ... WHERE 1 = 1` (also `price = price` and
  `x IS NULL OR x IS NOT NULL`) deleted every row despite the "WHERE
  required" rule. These are now rejected.
- Deep or long queries crashed with `RecursionError`, and long `OR` chains
  overflowed SQLite's parser.
- Huge integer parameters raised a raw `OverflowError`.
- `max_duration_ms` did not cover waiting for a lock (always 5 s).
- Large reads were 2x slower than native because of metric encoding, and
  `max_result_bytes` read the whole result before checking it.

## Known limits

- The always-true check catches common patterns, not every tautology (for
  example `NOT (p) OR p AND 2 = 2`). Use `max_affected_rows` as the hard
  limit.
- `MIN`/`MAX`/`ORDER BY` on a TEXT column are not flagged, because text order
  may be what you want. Use `NUMBER(x)` when it is not.
- There is no string concatenation, `CASE`, `IN (...)` or date functions yet.
