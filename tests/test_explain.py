from qryndel.backends import get_backend
from qryndel.explain import format_explain, explain_plan
from qryndel.logical_plan import build_plan
from qryndel.parser import parse


def test_explain_redacts_sql_parameters():
    plan = build_plan(parse("FROM users WHERE name = $username RETURN name"))
    compiler = get_backend("sqlite")
    compiled = compiler.compile(plan, parameters={"username": "secret-user"})

    report = explain_plan(plan, compiler, compiled)
    output = format_explain(report)

    assert "secret-user" not in output
    assert "<redacted>" in output
    assert "select" in report.required_capabilities
