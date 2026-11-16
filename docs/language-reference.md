# UDL Language Reference (v0.3.0)

A UDL program is an optional list of `TYPE` declarations, then an optional
list of `RULE` declarations, then one statement. A program with only
declarations is allowed (it is used by `--check-schema`) but has nothing to
run. Keywords are
uppercase. Whitespace is free-form, and `--` starts a comment that runs to
the end of the line.

```text
program   := TYPE* RULE* [statement]
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

`TRUE`, `FALSE` and `NULL` are not case-sensitive. Every value, literal or
parameter, is sent to SQLite as a bound parameter, including values inside
expressions.

A `$parameter` whose value is `None` is compared as SQL `NULL`, so
`x = $p` matches nothing (only a written `NULL` turns into `IS NULL`). This is
deliberate: `WHERE owner_id = $user` with no user must not match every row
that has no owner. Use `x IS NULL` to ask for nulls.

A bare word is a column name: `WHERE city = town` compares two columns.
Quote text values: `WHERE city = 'Singapore'`. (In `INSERT { city: Singapore }`
a bare word is still stored as the string `'Singapore'`.)

## Expressions

Anywhere a value is expected (in `RETURN`, `WHERE`, `HAVING`, `ORDER BY`,
inside an aggregate, and on the right of `UPDATE ... SET`) you can write an
expression:

```udl
price * qty                     -- multiply
SUM(fees) / SUM(gross) * 100    -- aggregates combine like numbers
(master_stock + 2) * 3          -- parentheses
-cost                           -- unary minus
stock - $sold                   -- parameters
```

### Numbers stored as text

CSV imports often store every column as TEXT. SQLite then compares text, so
`price > 1000` is true for `'999.99'`. Convert explicitly:

```udl
FROM order_items WHERE NUMBER(price) > 1000 RETURN NUMBER(price) AS price ORDER BY price DESC
```

`NUMBER(x)` converts to a number (`'12.5'` becomes `12.5`) and `TEXT(x)` to
text. Or declare the column once and every use in the program is converted:

```udl
TYPE order_items { price: Number  ALLOW extra fields }
FROM order_items WHERE price > 1000 RETURN price
```

When Qryndel knows the schema (`schema="auto"`, the CLI default), comparing a
TEXT column with a number is an error that suggests both fixes. Arithmetic
(`price * qty`) already converts and is not affected.

`*` and `/` bind tighter than `+` and `-`; operators of the same strength run
left to right. `/` always divides as decimals: `7 / 2` is `3.5`, never `3`.
Dividing by zero gives `NULL`. Arithmetic with `NULL` gives `NULL`.

### Comparing columns

Either side of a comparison can be a column or an expression:

```udl
FROM platform_listings AS l
JOIN products AS p ON l.product_id = p.id
WHERE l.current_stock != p.master_stock
RETURN p.master_sku, l.current_stock - p.master_stock AS difference
```

As in SQL, a comparison involving `NULL` is never true, so rows where either
column is `NULL` are not returned by `!=`. Use `IS NULL` to find them.

## Fields

- `name` is a column of the only table in scope, or of the one table that
  has it when there is a join.
- `u.name` is a column qualified by a table name or alias.
- `profile.address.city` is a path into a JSON column, compiled to
  `json_extract(profile, '$.address.city')`.

Identifiers must match `[A-Za-z_][A-Za-z0-9_]*`.

## Result column names

| `RETURN` item | Key in each result row |
| --- | --- |
| `name` | `"name"` |
| `u.name` | `"u.name"` |
| `COUNT(*)` | `"COUNT(*)"` |
| `qty * price` | `"qty * price"` (the expression's text) |
| `qty * price AS total` | `"total"` |

Two items with the same name are an error, because one would overwrite the
other in the result row. Rename one with `AS`.

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

`AS` is optional before a table alias; in `RETURN` it is required
(`RETURN qty * price AS total`). `JOIN` on its own means `INNER JOIN`.
`RETURN` items are expressions, which may use aggregates: `COUNT(*)`,
`COUNT(e)`, `SUM(e)`, `AVG(e)`, `MIN(e)`, `MAX(e)`. Only `COUNT` accepts `*`,
and aggregates cannot be nested. `HAVING` needs `GROUP BY` or an aggregate in
`RETURN`, and is the only condition where an aggregate may appear. `ORDER BY`
may use a `RETURN` alias. `LIMIT` takes a non-negative integer.

### Conditions

```udl
expr op expr                   -- op is one of = != > < >= <=
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
names such as `Text` and `Number` are not enforced on values yet, but
`--check-schema` / `Client.check_schema()` compares them with the real
table: `Number` matches INTEGER/REAL columns, `Text` matches TEXT, `Boolean`
matches BOOLEAN, `List<...>` matches TEXT, and `ID` matches any type.

### Checking against the real database

`Client(path, schema="auto")` (the CLI does this by default) reads every
table and column from the database before validating. Unknown tables and
columns are reported with a suggestion, for example
`Unknown field 'platfrom' in source 'orders'. Did you mean 'platform'?`.
Paths inside TEXT/JSON columns (`profile.address.city`) are allowed, because
their contents can't be known in advance.

## Writes

```udl
INSERT INTO users { name: "Hana", age: 22, email: $email }
UPDATE users [AS u] SET age = 23, city = "Jakarta" WHERE name = "Hana"
UPDATE products SET master_stock = master_stock - $sold WHERE id = $id
DELETE FROM users [AS u] WHERE name = "Hana"
```

`UPDATE` and `DELETE` must have a `WHERE` clause, and it must be able to be
false: `WHERE 1 = 1`, `WHERE price = price`, `WHERE x IS NULL OR x IS NOT NULL`
and `WHERE p OR NOT p` are rejected because they would change every row. This
catches common mistakes, not every possible tautology; use the
`max_affected_rows` budget as the real limit. Each write runs in its own
transaction, started with `BEGIN IMMEDIATE`.

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
| `UDL_CONTRACT_VIOLATION` | A result failed a contract passed to `execute(contracts=...)` |
| `UDL_DATABASE_LOCKED` | Another connection held the lock longer than `busy_timeout_ms` / `max_duration_ms` |

## Limits

Queries may nest up to 200 levels deep (each operator or condition is a
level; parentheses alone do not count). Long `AND`/`OR` chains are fine: a
5,000-term `OR` is stored as a balanced tree. Integer parameters must fit in
64 bits; larger integer literals become decimals.
