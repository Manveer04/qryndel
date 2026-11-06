"""
Abstract base class for Qryndel backend compilers.

Every backend compiler must implement:

* ``compile(plan) -> Any`` — compile a logical plan into a backend-specific
  representation (string, dict, or custom object).
* ``format_output(compiled) -> str`` — render the compiled output as a
  human-readable string for the CLI.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Mapping

from qryndel.logical_plan import PlanNode
from qryndel.capabilities import Capabilities, preflight


class BackendCompiler(ABC):
    """Interface all backend compilers must implement."""

    name: str = "base"
    capabilities: Capabilities = Capabilities(backend="base")

    def preflight(self, plan: PlanNode) -> None:
        preflight(plan, self.capabilities)

    @abstractmethod
    def compile(self, plan: PlanNode, parameters: Mapping[str, Any] | None = None) -> Any:
        """Compile *plan* into a backend-specific representation.

        Returns a string, dict, or custom object that ``format_output``
        can render.
        """
        ...

    def format_output(self, compiled: Any) -> str:
        """Render *compiled* as a human-readable string (default: str)."""
        return str(compiled)
