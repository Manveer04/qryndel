"""Result contracts: checks a query result must pass before it is returned."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Iterable, Iterator, Mapping, Sequence
from qryndel.errors import UdlError
from qryndel.budgets import BudgetExceededError, QueryBudget


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"


MISSING = _Missing()


def _value(row: Mapping[str, Any], field: str) -> Any:
    if field in row:
        return row[field]
    value: Any = row
    for part in field.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return MISSING
        value = value[part]
    return value


@dataclass(frozen=True)
class ContractFailure:
    contract: str
    field: str | None
    message: str
    violating_row_count: int
    samples: tuple[dict[str, str], ...] = ()


class ContractViolation(UdlError):
    """Raised when a result fails one or more declared contracts."""

    code = "UDL_CONTRACT_VIOLATION"

    def __init__(self, failures: Sequence[ContractFailure]):
        self.failures = tuple(failures)
        super().__init__(
            "; ".join(failure.message for failure in self.failures),
            code=self.code,
        )


class ResultContract:
    name = "contract"

    def check(self, rows: Sequence[Mapping[str, Any]]) -> ContractFailure | None:
        raise NotImplementedError


def _sample(field: str | None, row: Mapping[str, Any]) -> dict[str, str]:
    return {field or "row": "<redacted>"}


class NoMissing(ResultContract):
    def __init__(self, field: str):
        self.field = field
        self.name = "NO_MISSING"

    def check(self, rows):
        bad = [row for row in rows if _value(row, self.field) is MISSING]
        if not bad:
            return None
        return ContractFailure(
            self.name,
            self.field,
            f"field {self.field!r} is missing in {len(bad)} row(s)",
            len(bad),
            tuple(_sample(self.field, row) for row in bad[:3]),
        )


class NonNull(ResultContract):
    def __init__(self, field: str):
        self.field = field
        self.name = "NON_NULL"

    def check(self, rows):
        bad = [row for row in rows if _value(row, self.field) is None]
        if not bad:
            return None
        return ContractFailure(
            self.name,
            self.field,
            f"field {self.field!r} is null in {len(bad)} row(s)",
            len(bad),
            tuple(_sample(self.field, row) for row in bad[:3]),
        )


class NonEmpty(ResultContract):
    """The field must hold a real value: not missing, not NULL, not '' or blanks.

    ``NonNull`` accepts ``''`` because an empty string is not NULL; use
    ``NonEmpty`` when blank text should count as no value.
    """

    def __init__(self, field: str):
        self.field = field
        self.name = "NON_EMPTY"

    def check(self, rows):
        bad = [row for row in rows if _is_empty(_value(row, self.field))]
        if not bad:
            return None
        return ContractFailure(
            self.name,
            self.field,
            f"field {self.field!r} is empty in {len(bad)} row(s)",
            len(bad),
            tuple(_sample(self.field, row) for row in bad[:3]),
        )


def _is_empty(value: Any) -> bool:
    return value is MISSING or value is None or (isinstance(value, str) and not value.strip())


class Unique(ResultContract):
    def __init__(self, field: str):
        self.field = field
        self.name = "UNIQUE"

    def check(self, rows):
        seen: dict[Any, int] = {}
        duplicates = 0
        for row in rows:
            value = _value(row, self.field)
            if value is MISSING:
                continue
            try:
                if value in seen:
                    duplicates += 1
                else:
                    seen[value] = 1
            except TypeError:
                return ContractFailure(
                    self.name,
                    self.field,
                    f"field {self.field!r} contains an unhashable value",
                    1,
                    (_sample(self.field, row),),
                )
        if not duplicates:
            return None
        return ContractFailure(
            self.name,
            self.field,
            f"field {self.field!r} contains {duplicates} duplicate row(s)",
            duplicates,
        )


class Compare(ResultContract):
    _operators = {
        "=": lambda actual, expected: actual == expected,
        "!=": lambda actual, expected: actual != expected,
        ">": lambda actual, expected: actual > expected,
        ">=": lambda actual, expected: actual >= expected,
        "<": lambda actual, expected: actual < expected,
        "<=": lambda actual, expected: actual <= expected,
    }

    def __init__(self, field: str, operator: str, expected: Any):
        if operator not in self._operators:
            raise ValueError(f"unsupported contract operator: {operator}")
        self.field = field
        self.operator = operator
        self.expected = expected
        self.name = "COMPARE"

    def check(self, rows):
        predicate = self._operators[self.operator]
        bad = []
        for row in rows:
            value = _value(row, self.field)
            if value is MISSING or value is None:
                continue
            try:
                valid = predicate(value, self.expected)
            except (TypeError, ValueError):
                valid = False
            if not valid:
                bad.append(row)
        if not bad:
            return None
        return ContractFailure(
            self.name,
            self.field,
            f"field {self.field!r} violates {self.operator} in {len(bad)} row(s)",
            len(bad),
            tuple(_sample(self.field, row) for row in bad[:3]),
        )


class RowCount(ResultContract):
    def __init__(self, minimum: int | None = None, maximum: int | None = None):
        if minimum is not None and minimum < 0:
            raise ValueError("minimum row count must not be negative")
        if maximum is not None and maximum < 0:
            raise ValueError("maximum row count must not be negative")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError("minimum row count must not exceed maximum")
        self.minimum = minimum
        self.maximum = maximum
        self.name = "ROW_COUNT"

    def check(self, rows):
        count = len(rows)
        if self.minimum is not None and count < self.minimum:
            message = f"result has {count} row(s), below minimum {self.minimum}"
        elif self.maximum is not None and count > self.maximum:
            message = f"result has {count} row(s), above maximum {self.maximum}"
        else:
            return None
        return ContractFailure(self.name, None, message, count)


def verify_results(
    rows: Iterable[Mapping[str, Any]],
    contracts: Iterable[ResultContract],
) -> list[Mapping[str, Any]]:
    """Materialize and verify a result before returning it as verified data."""
    materialized = list(rows)
    failures = [
        failure
        for contract in contracts
        if (failure := contract.check(materialized)) is not None
    ]
    if failures:
        raise ContractViolation(failures)
    return materialized


class StreamingContractVerifier:
    """Incrementally verify the built-in contracts across result chunks."""

    def __init__(self, contracts: Iterable[ResultContract]):
        self.contracts = tuple(contracts)
        supported = (NoMissing, NonNull, NonEmpty, Unique, Compare, RowCount)
        unsupported = [contract for contract in self.contracts if not isinstance(contract, supported)]
        if unsupported:
            raise TypeError(
                "streaming verification does not support custom contract types: "
                + ", ".join(type(contract).__name__ for contract in unsupported)
            )
        self._rows_seen = 0
        self._bad_counts = {id(contract): 0 for contract in self.contracts}
        self._samples = {id(contract): [] for contract in self.contracts}
        self._unique_values = {id(contract): {} for contract in self.contracts if isinstance(contract, Unique)}
        self._duplicate_counts = {id(contract): 0 for contract in self.contracts if isinstance(contract, Unique)}
        self._unhashable = {id(contract): False for contract in self.contracts if isinstance(contract, Unique)}

    def consume(self, rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
        """Consume one chunk and return it unchanged for downstream delivery."""
        chunk = list(rows)
        self._rows_seen += len(chunk)
        for row in chunk:
            for contract in self.contracts:
                value = _value(row, getattr(contract, "field", ""))
                bad = False
                if isinstance(contract, NoMissing):
                    bad = value is MISSING
                elif isinstance(contract, NonNull):
                    bad = value is None
                elif isinstance(contract, NonEmpty):
                    bad = _is_empty(value)
                elif isinstance(contract, Compare):
                    if value is not MISSING and value is not None:
                        try:
                            bad = not contract._operators[contract.operator](value, contract.expected)
                        except (TypeError, ValueError):
                            bad = True
                elif isinstance(contract, Unique):
                    if value is not MISSING:
                        try:
                            if value in self._unique_values[id(contract)]:
                                self._duplicate_counts[id(contract)] += 1
                            else:
                                self._unique_values[id(contract)][value] = True
                        except TypeError:
                            self._unhashable[id(contract)] = True
                if bad:
                    self._bad_counts[id(contract)] += 1
                    if len(self._samples[id(contract)]) < 3:
                        self._samples[id(contract)].append(_sample(getattr(contract, "field", None), row))
        return chunk

    def finish(self) -> None:
        """Validate accumulated state and raise after the final chunk."""
        failures = []
        for contract in self.contracts:
            key = id(contract)
            count = self._bad_counts.get(key, 0)
            if isinstance(contract, Unique):
                if self._unhashable[key]:
                    failures.append(ContractFailure(contract.name, contract.field, f"field {contract.field!r} contains an unhashable value", 1, tuple(self._samples[key])))
                elif self._duplicate_counts[key]:
                    duplicates = self._duplicate_counts[key]
                    failures.append(ContractFailure(contract.name, contract.field, f"field {contract.field!r} contains {duplicates} duplicate row(s)", duplicates))
            elif isinstance(contract, RowCount):
                message = None
                if contract.minimum is not None and self._rows_seen < contract.minimum:
                    message = f"result has {self._rows_seen} row(s), below minimum {contract.minimum}"
                elif contract.maximum is not None and self._rows_seen > contract.maximum:
                    message = f"result has {self._rows_seen} row(s), above maximum {contract.maximum}"
                if message:
                    failures.append(ContractFailure(contract.name, None, message, self._rows_seen))
            elif count:
                failures.append(ContractFailure(contract.name, contract.field, self._failure_message(contract, count), count, tuple(self._samples[key])))
        if failures:
            raise ContractViolation(failures)

    @staticmethod
    def _failure_message(contract: ResultContract, count: int) -> str:
        if isinstance(contract, NoMissing):
            return f"field {contract.field!r} is missing in {count} row(s)"
        if isinstance(contract, NonNull):
            return f"field {contract.field!r} is null in {count} row(s)"
        if isinstance(contract, NonEmpty):
            return f"field {contract.field!r} is empty in {count} row(s)"
        return f"field {contract.field!r} violates {contract.operator} in {count} row(s)"


def iter_verified_results(
    chunks: Iterable[Iterable[Mapping[str, Any]]],
    contracts: Iterable[ResultContract],
    budget: QueryBudget | None = None,
) -> Iterator[Mapping[str, Any]]:
    """Yield verified chunks while retaining cross-chunk contract state."""
    verifier = StreamingContractVerifier(contracts)
    rows_seen = 0
    bytes_seen = 0
    for chunk in chunks:
        delivered = verifier.consume(chunk)
        rows_seen += len(delivered)
        bytes_seen += len(json.dumps(delivered, ensure_ascii=False, default=str, separators=(",", ":")).encode("utf-8"))
        if budget and budget.max_result_rows is not None and rows_seen > budget.max_result_rows:
            raise BudgetExceededError(
                f"stream exceeds max_result_rows={budget.max_result_rows}"
            )
        if budget and budget.max_result_bytes is not None and bytes_seen > budget.max_result_bytes:
            raise BudgetExceededError(
                f"stream exceeds max_result_bytes={budget.max_result_bytes}"
            )
        yield from delivered
    verifier.finish()
