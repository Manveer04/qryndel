# Security Policy

## Supported Releases

Only the latest public release receives security fixes. Qryndel is
experimental and does not make enterprise security or cross-database safety
claims.

| Version | Supported |
| ------- | --------- |
| 0.2.x   | Yes       |
| 0.1.x   | No        |

## Security Model

- Every literal and `$parameter` value is sent to SQLite as a bound
  parameter. Values are never concatenated into SQL text.
- Table, column and alias names must match `[A-Za-z_][A-Za-z0-9_]*` before
  they are written into SQL.
- `UPDATE` and `DELETE` without `WHERE` are rejected during validation.
- `Client(read_only=True)` and `Client(allowed_tables=...)` block writes
  before any SQL runs.
- Writes run in explicit transactions; errors and exceeded budgets roll back.
- `explain()` and `--explain` redact parameter values.
- Raw SQL strings passed to the executor are limited to `SELECT`.

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
