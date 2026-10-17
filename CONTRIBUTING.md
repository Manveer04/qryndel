# Contributing

Thanks for taking the time to improve Qryndel.

## Bugs

Open a bug report with the query, Python version, operating system, expected
result, actual result, and a small SQLite fixture or reproduction when
possible. Do not include credentials or private data.

## Features

Open a feature request describing the use case and proposed UDL syntax. Major
changes to syntax, semantics, type behavior, portability, or public APIs
should be discussed as an RFC before implementation.

## Development Setup

```bash
python -m venv .venv
python -m pip install -e ".[test]"
python setup_db.py
python -m pytest -q
```

Fork the repository, create a focused branch, and keep changes small enough to
review. New behavior should include tests and documentation when appropriate.

## Pull Requests

Include a short explanation of the change, test results, and compatibility
considerations. Do not add generated databases, caches, credentials, or
unrelated formatting changes. A pull request may be merged after maintainer
review and passing CI.

Contributors receive credit through commits, pull requests, release notes,
and `CONTRIBUTORS.md`. Contributions intentionally submitted for inclusion are
intended to be distributed under the MIT License; contributors retain their
rights under that license. No copyright assignment is required.
