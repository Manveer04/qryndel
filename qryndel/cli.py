"""Command-line interface: ``udl <query | file.qryndel> [options]``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from qryndel import __version__
from qryndel.errors import UdlError
from qryndel.explain import format_explain
from qryndel.sdk import Client, QueryResult


def _parse_param(raw: str) -> tuple[str, Any]:
    name, separator, value = raw.partition("=")
    if separator != "=" or not name.startswith("$") or len(name) < 2:
        raise argparse.ArgumentTypeError("--param must use $name=value")
    try:
        return name[1:], json.loads(value)
    except json.JSONDecodeError:
        return name[1:], value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="udl",
        description="Compile and run Unified Data Language (UDL) against SQLite.",
    )
    parser.add_argument("query", nargs="?", help="inline UDL text or a path to a .qryndel file")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    parser.add_argument("--db", default="test.db", help="SQLite database file (default: test.db)")
    parser.add_argument(
        "--param", action="append", default=[], type=_parse_param, metavar="$NAME=VALUE",
        help="bind a query parameter; VALUE is parsed as JSON when possible (repeatable)",
    )
    parser.add_argument("--explain", action="store_true", help="show the plan and SQL without executing")
    parser.add_argument("--preview", action="store_true", help="count rows a write would affect, without writing")
    parser.add_argument("--read-only", action="store_true", help="reject INSERT, UPDATE and DELETE")
    parser.add_argument("--max-rows", type=int, metavar="N", help="fail if a read returns more than N rows")
    parser.add_argument("--max-affected-rows", type=int, metavar="N", help="roll back a write that affects more than N rows")
    parser.add_argument("--timeout-ms", type=int, metavar="MS", help="abort a statement running longer than MS")
    parser.add_argument("--json", action="store_true", help="print results as JSON")
    return parser


def _print_result(result: QueryResult, as_json: bool) -> None:
    if as_json:
        if result.transaction is not None:
            payload: Any = {
                "transaction": result.transaction,
                "rows_affected": result.rows_affected,
                "statements": list(result.statements),
            }
        elif result.is_write:
            payload = {"rows_affected": result.rows_affected, "last_insert_id": result.last_insert_id}
        else:
            payload = result.rows
        print(json.dumps(payload, ensure_ascii=False, default=str))
        return
    if result.transaction is not None:
        print(f"Transaction {result.transaction}: {result.rows_affected} rows affected")
        return
    if result.is_write:
        print(f"Rows affected: {result.rows_affected}")
        return
    if not result.rows:
        return
    print(" | ".join(result.rows[0]))
    for row in result.rows:
        print(" | ".join(str(value) for value in row.values()))


def main(argv: Sequence[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print(__version__)
        return
    if not args.query:
        parser.print_usage(sys.stderr)
        raise SystemExit(1)
    if args.explain and args.preview:
        parser.error("--explain and --preview cannot be combined")

    source = Path(args.query)
    query = source.read_text(encoding="utf-8") if source.is_file() else args.query
    params = dict(args.param)
    client = Client(args.db, read_only=args.read_only)
    budget = {
        key: value
        for key, value in {
            "max_result_rows": args.max_rows,
            "max_affected_rows": args.max_affected_rows,
            "max_duration_ms": args.timeout_ms,
        }.items()
        if value is not None
    }

    try:
        if args.explain:
            report = client.explain(query.strip(), params=params or None)
            print(json.dumps(report.to_dict(), default=str) if args.json else format_explain(report))
            return
        if args.preview:
            preview = client.preview(query.strip(), params=params)
            if args.json:
                print(json.dumps(preview.to_dict()))
            else:
                print(f"Preview {preview.operation} on {', '.join(preview.tables)}: "
                      f"{preview.estimated_rows} rows would be affected (no data changed)")
            return
        result = client.execute(query.strip(), params=params, budget=budget or None)
    except UdlError as exc:
        if args.json:
            print(json.dumps({"error": exc.to_dict()}), file=sys.stderr)
        else:
            print(f"error[{exc.code}]: {exc.message}", file=sys.stderr)
        raise SystemExit(1) from None
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    _print_result(result, args.json)


if __name__ == "__main__":
    main()
