"""Backend interface for the SQLite-only public release."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping


class BackendCompiler(ABC):
    name = "base"

    def preflight(self, plan: Any) -> None:
        return None

    @abstractmethod
    def compile(self, plan: Any, parameters: Mapping[str, Any] | None = None) -> Any:
        raise NotImplementedError

    def format_output(self, compiled: Any) -> str:
        return str(compiled)
