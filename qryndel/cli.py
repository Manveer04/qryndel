"""Command-line interface for the SQLite-focused v0.1.0 release."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from qryndel.sdk import Client
from qryndel import __version__


def main() -> None:
    args = sys.argv[1:]
    if args == ["--version"]:
        print(__version__)
        return
    if not args:
        print("Usage: udl <query.qryndel | 'inline query'> [--db PATH] [--param '$name=value']")
        raise SystemExit(1)

    db_path = "test.db"
    params: dict[str, Any] = {}
    query_args: list[str] = []
    index = 0
    while index < len(args):
        if args[index] == "--db" and index + 1 < len(args):
            db_path = args[index + 1]
            index += 2
        elif args[index] == "--param" and index + 1 < len(args):
            name, separator, raw = args[index + 1].partition("=")
            if separator != "=" or not name.startswith("$"):
                raise SystemExit("--param must use $name=value")
            try:
                params[name[1:]] = json.loads(raw)
            except json.JSONDecodeError:
                params[name[1:]] = raw
            index += 2
        else:
            query_args.append(args[index])
            index += 1

    if not query_args:
        raise SystemExit("No query provided")
    source = query_args[0]
    query = Path(source).read_text(encoding="utf-8") if Path(source).is_file() else source
    result = Client(db_path).execute(query.strip(), params=params)
    if not result.rows:
        return
    print(" | ".join(result.rows[0]))
    for row in result.rows:
        print(" | ".join(str(value) for value in row.values()))


if __name__ == "__main__":
    main()
