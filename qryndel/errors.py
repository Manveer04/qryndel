"""Common structured error model for public UDL APIs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class SourceSpan:
    line: int
    column: int
    length: int = 1


class UdlError(Exception):
    """Base error carrying stable machine-readable diagnostic context."""

    code = "UDL_ERROR"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        source_span: SourceSpan | None = None,
        backend: str | None = None,
        capability: str | None = None,
        suggested_action: str | None = None,
        docs_url: str | None = None,
    ):
        self.message = message
        self.code = code or self.code
        self.source_span = source_span
        self.backend = backend
        self.capability = capability
        self.suggested_action = suggested_action
        self.docs_url = docs_url
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "source_span": asdict(self.source_span) if self.source_span else None,
            "backend": self.backend,
            "capability": self.capability,
            "suggested_action": self.suggested_action,
            "docs_url": self.docs_url,
        }
