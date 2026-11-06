# Qryndel v0.2.0

Qryndel v0.2.0 completes the UDL language on SQLite. On top of v0.1.0's
filtered reads, it adds sorting and paging, joins, aggregation with
`GROUP BY` / `HAVING`, nested JSON fields, reusable `RULE` predicates,
`TYPE` schema declarations, and safe writes (`INSERT`, `UPDATE`, `DELETE`,
and `BEGIN ... COMMIT|ROLLBACK` transactions).

Safety is built in: `UPDATE` and `DELETE` require `WHERE`, writes are
transactional and roll back on errors or exceeded budgets, clients can be
read-only or limited to specific tables, and `explain` / `preview` show what
a statement will do without running it.

Queries and API calls written for v0.1.0 keep working. See `CHANGELOG.md` for
the small API changes.

Install from a source checkout with `python -m pip install .`. Python 3.10+
and SQLite are required. `RIGHT JOIN` / `FULL JOIN` need SQLite 3.39+.

PostgreSQL, MongoDB, Neo4j and graph queries are not part of this release.
