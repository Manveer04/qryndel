# Roadmap

Planned areas are subject to technical review and community discussion.

## Delivered

- **v0.1.0:** core parser, semantic checks, logical plan, parameterized
  SQLite compiler, Python API and CLI.
- **v0.2.0:** richer expressions (`ORDER BY`, `LIMIT`, `DISTINCT`, joins,
  nested JSON fields), aggregation and grouping, `TYPE` schema declarations,
  `RULE` predicates, safe mutations, transactions, execution budgets,
  `explain` and `preview`.

## Next

- Enforce `TYPE` field types, not just field names.
- Review a PostgreSQL adapter for the relational subset.
- Evaluate MongoDB and graph database support.
- Portable query diagnostics: report when a query would behave differently
  on another engine.
- Developer tooling and editor integration.

No target dates or compatibility guarantees are attached to these items.
