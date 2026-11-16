# Roadmap

Planned areas are subject to technical review and community discussion.

## Delivered

- **v0.1.0:** core parser, semantic checks, logical plan, parameterized
  SQLite compiler, Python API and CLI.
- **v0.2.0:** richer expressions (`ORDER BY`, `LIMIT`, `DISTINCT`, joins,
  nested JSON fields), aggregation and grouping, `TYPE` schema declarations,
  `RULE` predicates, safe mutations, transactions, execution budgets,
  `explain` and `preview`.
- **v0.3.0:** column-to-column comparisons, arithmetic, `AS` aliases,
  `NUMBER()`/`TEXT()` conversions, schema checks against the live database,
  schema-drift reports, and result contracts. Hardened on a 1.5M-row
  real-world database: text-vs-number checks, always-true WHERE guard,
  recursion limits, lock handling, faster large reads.

## Next

- Enforce `TYPE` field types on values (for example, reject `'abc'` for a
  `Number` field), not just field names.
- String and date functions (`LOWER`, `LENGTH`, string concatenation, date
  arithmetic), `CASE`, and `IN (...)` lists. `IN` would also replace long
  `OR` chains, which are correct but slow (5,000 terms took about 9 s on
  100K rows).
- Index hints in `explain` (warn when a filter or join has no index).
- Expressions in `JOIN ... ON`.
- Review a PostgreSQL adapter for the relational subset.
- Evaluate MongoDB and graph database support.
- Portable query diagnostics: report when a query would behave differently
  on another engine.
- Developer tooling and editor integration.

No target dates or compatibility guarantees are attached to these items.
