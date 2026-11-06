# UDL Language Reference (v0.2.0)

A UDL program is an optional list of `TYPE` declarations, then an optional
list of `RULE` declarations, then exactly one statement. Keywords are
uppercase. Whitespace is free-form, and `--` starts a comment that runs to
the end of the line.

```text
program   := TYPE* RULE* statement
statement := query | insert | update | delete | transaction
```

## Values

| Form | Meaning |
| --- | --- |
| `42`, `-3`, `19.99` | Numbers |
| `"text"` or `'text'` | Strings |
| `TRUE`, `FALSE` | Booleans (stored by SQLite as 1 / 0) |
| `NULL` | Null. `x = NULL` means `x IS NULL`; `x != NULL` means `x IS NOT NULL` |
| `$name` | A named parameter, bound at execution time |

Every value, literal or parameter, is sent to SQLite as a bound parameter.

## Fields

- `name` is a column of the only table in scope, or of the one table that
  has it when there is a join.
- `u.name` is a column qualified by a table name or alias.
- `profile.address.city` is a path into a JSON column, compiled to
  `json_extract(profile, '$.address.city')`.

Identifiers must match `[A-Za-z_][A-Za-z0-9_]*`.

A qualified field appears in results under its dotted name, for example
`{"u.name": "Alice"}`. Aggregates appear under their text, such as
`"COUNT(*)"`.

## Queries

```udl
FROM source [AS alias]
[ [INNER | LEFT | RIGHT | FULL] JOIN source [AS alias] ON field op field ]*
[WHERE condition]
RETURN [DISTINCT] item, ...
[GROUP BY field, ...]
[HAVING condition]
[ORDER BY item [ASC | DESC] [NULLS FIRST | NULLS LAST], ...]
[LIMIT n]
```

`AS` is optional before an alias. `JOIN` on its own means `INNER JOIN`.
`RETURN` items are fields or aggregates: `COUNT(*)`, `COUNT(f)`, `SUM(f)`,
`AVG(f)`, `MIN(f)`, `MAX(f)`. `HAVING` needs `GROUP BY` or an aggregate in
`RETURN`, and is the only place an aggregate may appear in a condition.
`LIMIT` takes a non-negative integer.

### Conditions

```udl
field op value                 -- op is one of = != > < >= <=
field IS NULL | IS NOT NULL
field IS MISSING | IS NOT MISSING | EXISTS
rule_name(alias)
NOT condition
condition AND condition        -- AND binds tighter than OR
condition OR condition
( condition )
```

On a plain column, `IS MISSING` is always false and `EXISTS` always true. On
a JSON path, `IS MISSING` means the key is absent, and `IS NULL` means the
key is present and holds JSON `null`.

## Rules

```udl
RULE is_adult(x) = x.age >= 18
RULE can_drive(x) = is_adult(x) AND x.has_license = TRUE

FROM users AS u WHERE can_drive(u) RETURN name
```

A rule is a named condition with one parameter. Calling it replaces the
parameter prefix in the body with the argument, so here `x.age` becomes
`u.age`. Rules can call other rules. Recursive rules and duplicate names are
errors. Rules can be used in the `WHERE` of queries, `UPDATE`, `DELETE`, and
the statements inside a transaction.

## Types

```udl
TYPE users {
  id: ID required
  name: Text required
  age: Number optional
  tags: List<Text>
  ALLOW extra fields
}
```

A `TYPE` declares the fields of a table. When a program has a declaration,
or the `Client` was given a `schema`, fields are checked:

- An unknown field is an error.
- `INSERT` must set every `required` field.
- `ALLOW extra fields` lets `INSERT` set undeclared fields.

A `TYPE` declaration replaces a `schema` entry with the same name. Type
names such as `Text` and `Number` are recorded but not enforced yet.

## Writes

```udl
INSERT INTO users { name: "Hana", age: 22, email: $email }
UPDATE users [AS u] SET age = 23, city = "Jakarta" WHERE name = "Hana"
DELETE FROM users [AS u] WHERE name = "Hana"
```

`UPDATE` and `DELETE` must have a `WHERE` clause. Each write runs in its own
transaction.

## Transactions

```udl
BEGIN
UPDATE accounts SET balance = 50 WHERE id = 1;
UPDATE accounts SET balance = 150 WHERE id = 2;
COMMIT
```

A transaction holds two or more write statements separated by `;`, and ends
with `COMMIT` or `ROLLBACK`. If any statement fails, or a budget is exceeded,
all of them are rolled back. `ROLLBACK` runs the statements, reports what
they would have changed, then discards the changes.

## Errors

Every error is a `qryndel.UdlError` with a stable `code` and `to_dict()`:

| Code | Raised when |
| --- | --- |
| `UDL_PARSE_ERROR` | The text does not match the grammar (includes line and column) |
| `UDL_SEMANTIC_ERROR` | Unknown field or table, ambiguous field, missing `WHERE` on a write, bad rule |
| `UDL_PARAMETER_ERROR` | A `$name` has no value |
| `UDL_CAPABILITY_UNSUPPORTED` | The installed SQLite lacks a feature, such as `RIGHT JOIN` before 3.39 |
| `UDL_SAFETY_BLOCKED` | A write is blocked by `read_only` or `allowed_tables` |
| `UDL_BUDGET_EXCEEDED` | A row, size, affected-row, or time budget was exceeded |
| `UDL_EXECUTION_ERROR` | SQLite reported an error, or the database file does not exist |
