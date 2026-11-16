import pytest

from qryndel.contracts import (
    Compare,
    ContractViolation,
    MISSING,
    NoMissing,
    NonNull,
    RowCount,
    Unique,
    verify_results,
)


def test_contracts_accept_valid_results():
    rows = [{"id": 1, "total": 10}, {"id": 2, "total": 20}]

    assert verify_results(
        rows,
        [NoMissing("id"), NonNull("total"), Unique("id"), Compare("total", ">=", 0), RowCount(2, 2)],
    ) == rows


def test_missing_and_null_are_distinct():
    rows = [{"id": 1}, {"id": 2, "email": None}]

    with pytest.raises(ContractViolation) as error:
        verify_results(rows, [NoMissing("email"), NonNull("email")])

    assert len(error.value.failures) == 2
    assert MISSING is not None


def test_unique_range_and_cardinality_fail_with_typed_error():
    with pytest.raises(ContractViolation) as error:
        verify_results(
            [{"id": 1, "total": -1}, {"id": 1, "total": 2}],
            [Unique("id"), Compare("total", ">=", 0), RowCount(maximum=1)],
        )

    assert error.value.code == "UDL_CONTRACT_VIOLATION"
    assert {failure.contract for failure in error.value.failures} == {
        "UNIQUE",
        "COMPARE",
        "ROW_COUNT",
    }


def test_nested_field_contracts_use_missing_sentinel():
    rows = [{"profile": {"email": "a@example.com"}}, {"profile": {}}]

    with pytest.raises(ContractViolation):
        verify_results(rows, [NoMissing("profile.email")])
