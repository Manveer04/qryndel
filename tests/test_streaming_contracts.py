"""Tests for v33 incremental result contract verification."""

import pytest

from qryndel.contracts import (
    ContractViolation,
    NoMissing,
    NonNull,
    RowCount,
    Unique,
    iter_verified_results,
)
from qryndel.budgets import BudgetExceededError, QueryBudget


def test_streaming_verifier_checks_unique_across_chunks():
    chunks = [[{"id": 1}], [{"id": 2}], [{"id": 1}]]

    with pytest.raises(ContractViolation, match="duplicate"):
        list(iter_verified_results(chunks, [Unique("id")]))


def test_streaming_verifier_checks_row_count_at_end():
    chunks = [[{"id": 1}], [{"id": 2}]]

    with pytest.raises(ContractViolation, match="above maximum"):
        list(iter_verified_results(chunks, [RowCount(maximum=1)]))


def test_streaming_verifier_preserves_chunks_when_valid():
    chunks = [[{"id": 1}], [{"id": 2}]]

    assert list(iter_verified_results(chunks, [NoMissing("id"), NonNull("id"), Unique("id")])) == [
        {"id": 1},
        {"id": 2},
    ]


def test_streaming_failure_can_occur_after_rows_were_yielded():
    chunks = [[{"id": 1}], [{"id": 1}]]
    iterator = iter_verified_results(chunks, [Unique("id")])

    assert next(iterator) == {"id": 1}
    assert next(iterator) == {"id": 1}
    with pytest.raises(ContractViolation):
        next(iterator)


def test_streaming_result_budget_stops_before_over_limit_chunk_is_yielded():
    chunks = [[{"id": 1}], [{"id": 2}]]

    with pytest.raises(BudgetExceededError, match="max_result_rows=1"):
        list(iter_verified_results(chunks, [Unique("id")], QueryBudget(max_result_rows=1)))
