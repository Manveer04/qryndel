"""Concurrency coverage for per-call SQLite connection isolation."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

from qryndel.executor import execute


def test_concurrent_readers_use_independent_connections(tmp_path):
    path = str(tmp_path / "concurrent.db")
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE values_table (id INTEGER PRIMARY KEY)")
    connection.executemany("INSERT INTO values_table VALUES (?)", [(1,), (2,), (3,)])
    connection.commit()
    connection.close()

    def read_rows():
        return execute("SELECT id FROM values_table ORDER BY id", path)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: read_rows(), range(16)))

    assert all(rows == [{"id": 1}, {"id": 2}, {"id": 3}] for rows in results)
