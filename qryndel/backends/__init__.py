"""Backend registry. The v0.2.0 public release ships the SQLite backend only."""

from __future__ import annotations

from qryndel.backends.base import BackendCompiler


_BACKENDS: dict[str, type[BackendCompiler]] = {}


def register_backend(name: str, cls: type[BackendCompiler]) -> None:
    """Register a backend compiler class under *name*."""
    _BACKENDS[name] = cls


def get_backend(name: str) -> BackendCompiler:
    """Return an instance of the named backend compiler.

    Raises ``ValueError`` if the backend is not registered.
    """
    if name not in _BACKENDS:
        known = ", ".join(sorted(_BACKENDS))
        raise ValueError(f"Unknown backend '{name}'. Known backends: {known}")
    return _BACKENDS[name]()


def _init_backends() -> None:
    from qryndel.backends.sqlite import SqliteBackend

    register_backend("sqlite", SqliteBackend)


_init_backends()


__all__ = [
    "BackendCompiler",
    "get_backend",
    "register_backend",
]
