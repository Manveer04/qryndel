from qryndel import SourceSpan, UdlError
from qryndel.capabilities import CapabilityError
from qryndel.executor import ExecutionError
from qryndel.parser import ParseError, parse


def test_structured_error_serializes_diagnostic_context():
    error = UdlError(
        "Unsupported operation",
        code="UDL_FEATURE_UNSUPPORTED",
        source_span=SourceSpan(2, 4, 3),
        backend="sqlite",
        capability="right_join",
        suggested_action="Use PostgreSQL.",
    )

    assert error.to_dict() == {
        "code": "UDL_FEATURE_UNSUPPORTED",
        "message": "Unsupported operation",
        "source_span": {"line": 2, "column": 4, "length": 3},
        "backend": "sqlite",
        "capability": "right_join",
        "suggested_action": "Use PostgreSQL.",
        "docs_url": None,
    }


def test_parse_errors_include_source_location_when_available():
    try:
        parse("FROM users RETURN")
    except ParseError as error:
        assert error.code == "UDL_PARSE_ERROR"
        assert error.source_span is not None
        assert error.source_span.line == 1
        assert error.source_span.column > 0
    else:
        raise AssertionError("invalid query did not raise ParseError")
