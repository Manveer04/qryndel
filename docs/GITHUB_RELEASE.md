# Qryndel v0.1.0

Qryndel v0.1.0 is the first public release of the Unified Data Language
implementation. It includes the core parser, semantic checks, logical plan,
parameterized SQLite compiler, Python API, CLI, and reproducible tests.

Install from a source checkout with `python -m pip install .`. Python 3.10+
and SQLite are required.

This release is intentionally SQLite-first. PostgreSQL, MongoDB, Neo4j, graph
queries, aggregation, transactions, and data-modifying statements are deferred
for later review.
