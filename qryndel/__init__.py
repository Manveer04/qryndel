"""Qryndel, an experimental SQLite implementation of Unified Data Language."""

__version__ = "0.1.0"

from qryndel.sdk import Client, QueryResult
from qryndel.errors import SourceSpan, UdlError

__all__ = ["Client", "QueryResult", "SourceSpan", "UdlError", "__version__"]
