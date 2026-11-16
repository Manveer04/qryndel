"""Give an AI agent (or any untrusted caller) safe, read-only database access.

The agent writes UDL; Qryndel checks it against the real schema, refuses
writes, caps the result size and run time, and returns errors as plain text
the agent can read and correct. Nothing here depends on a particular LLM
provider: register ``query_database`` as a tool in whichever framework you use.

Run it against the example database:

    python setup_db.py
    python -m examples.agent_guardrails
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from qryndel import Client, UdlError

DB_PATH = Path(__file__).resolve().parent.parent / "test.db"

# One client for the agent: read-only, schema-checked, bounded.
agent_db = Client(
    str(DB_PATH),
    schema="auto",
    read_only=True,
    default_budget={"max_result_rows": 200, "max_duration_ms": 2000},
)


def query_database(udl: str) -> str:
    """Tool function: run a UDL query and return JSON rows, or an error to fix."""
    try:
        result = agent_db.execute(udl)
    except UdlError as error:
        # Short, specific messages ("Did you mean 'name'?") let the model retry.
        return json.dumps({"error": error.code, "message": error.message})
    return json.dumps({"rows": result.rows})


TOOL_DESCRIPTION = (
    "Query the shop database with UDL. Syntax: FROM table [JOIN ...] [WHERE ...] "
    "RETURN fields [GROUP BY ...] [ORDER BY ...] [LIMIT n]. Read-only; at most 200 rows. "
    "Tables: " + ", ".join(f"{name}({', '.join(cols)})" for name, cols in agent_db.schema.items())
)


if __name__ == "__main__":
    if not DB_PATH.exists():
        sys.exit("Run `python setup_db.py` first.")
    print("Tool description for the model:\n ", TOOL_DESCRIPTION, "\n")
    for attempt in [
        "FROM users RETURN nmae",                                  # typo -> hint
        "FROM users RETURN name ORDER BY age DESC LIMIT 3",        # fixed query
        "DELETE FROM orders WHERE amount > 0",                     # write -> blocked
        "FROM orders RETURN product, SUM(amount * quantity) AS revenue "
        "GROUP BY product ORDER BY revenue DESC",                  # analytics
    ]:
        print(f"> {attempt}\n  {query_database(attempt)}\n")
