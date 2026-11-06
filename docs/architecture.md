# Architecture

```text
UDL source
  -> parser.py        Lark LALR grammar (grammar.lark) -> AST (ast_nodes.py)
  -> semantic.py      merge TYPE declarations into the schema, expand RULEs
                      (rules.py), resolve and check field references, reject
                      UPDATE/DELETE without WHERE
  -> logical_plan.py  Scan, Join, Filter, GroupBy, Having, Distinct, Sort,
                      Project, Limit / InsertPlan, UpdatePlan, DeletePlan,
                      TransactionPlan
  -> optimizer.py     fuse adjacent filters, push qualified predicates into
                      INNER JOIN inputs
  -> capabilities.py  preflight: fail if the plan needs a feature the backend
                      (or the installed SQLite version) does not declare
  -> safety.py        read_only / allowed_tables / require WHERE policy
  -> backends/sqlite.py  SQL with ? placeholders plus a params list
  -> executor.py      run on SQLite: explicit transactions for writes,
                      rollback on error or budget overrun, row/byte/time
                      budgets for reads
```

`sdk.Client` connects these steps. `cli.py` is a thin wrapper around the
client.

## Design Rules

- **Values are always bound.** The compiler never writes a literal or
  parameter value into SQL text. Identifiers are checked against
  `[A-Za-z_][A-Za-z0-9_]*` before they are emitted.
- **Fail closed.** Unknown syntax, unsupported capabilities, policy
  violations and exceeded budgets all raise before data changes, or roll
  back if the change already started.
- **Backends see plans, not syntax.** Rules are expanded and fields resolved
  before planning, so a backend compiles only logical plan nodes.
  `backends/base.py` defines the interface any future adapter must
  implement.
- **Diagnostics never leak values.** `explain()` redacts parameters, and
  execution metrics record counts and timings, never row contents.

## Package Layout

| Module | Responsibility |
| --- | --- |
| `grammar.lark`, `parser.py`, `ast_nodes.py` | Syntax |
| `rules.py`, `semantic.py` | Meaning |
| `logical_plan.py`, `optimizer.py` | Planning |
| `capabilities.py`, `backends/` | Backend contract and SQLite compiler |
| `safety.py`, `budgets.py` | Mutation policy, previews, limits |
| `executor.py`, `metrics.py` | Execution |
| `explain.py`, `errors.py` | Diagnostics |
| `sdk.py`, `cli.py` | Public entry points |
