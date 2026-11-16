# Security Policy

## Supported Releases

Only the latest public release receives security fixes. Qryndel is
experimental and does not make enterprise security or cross-database safety
claims.

| Version | Supported |
| ------- | --------- |
| 0.3.x   | Yes       |
| 0.2.x   | No        |
| 0.1.x   | No        |

## Security Model

- Every literal and `$parameter` value is sent to SQLite as a bound
  parameter, including values inside arithmetic expressions. Values are never
  concatenated into SQL text. Result-column names given with `AS` must be
  plain identifiers.
- Table, column and alias names must match `[A-Za-z_][A-Za-z0-9_]*` before
  they are written into SQL.
- `UPDATE` and `DELETE` without `WHERE`, or with a `WHERE` that can never be
  false (`1 = 1`, `price = price`, `x IS NULL OR x IS NOT NULL`), are
  rejected during validation. Use `max_affected_rows` (or
  `Client(default_budget=...)`) as the hard limit on how much a write can
  change.
- `Client(read_only=True)` and `Client(allowed_tables=...)` block writes
  before any SQL runs.
- Writes run in `BEGIN IMMEDIATE` transactions; errors, exceeded budgets and
  killed processes leave no partial changes.
- Queries nested deeper than 200 levels are rejected before they can exhaust
  the call stack.
- `explain()` and `--explain` redact parameter values.
- Raw SQL strings passed to the executor are limited to `SELECT`.
- Schema loading (`schema="auto"`, `--show-schema`, `--check-schema`) opens
  the database read-only and never creates or changes the file.
- Contract failures report counts and redacted samples, not row values.

These controls do not replace database permissions. Run Qryndel with a
database user and file permissions that match what the application needs.

## Reporting A Vulnerability

Please use GitHub's private vulnerability reporting feature when it is enabled
for the repository. If it is not enabled, contact the repository owner through
GitHub before opening a public issue. The owner must configure the private
reporting channel after creating the repository.

Do not include real credentials, production data, or secrets in a report.
Include the affected version, a minimal reproduction, impact, and any safe
mitigation you know.
