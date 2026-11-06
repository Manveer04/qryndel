"""Tests for language-level TYPE declarations."""

import pytest

from qryndel.ast_nodes import Query, TypeDefinition
from qryndel.parser import ParseError, parse_program
from qryndel.semantic import SemanticError, validate


def test_parse_type_definition_with_modifiers_and_list_type():
    program = parse_program(
        "TYPE Student {"
        " id: ID required"
        " name: Text required"
        " age: Number optional"
        " subjects: List<Text>"
        " ALLOW extra fields"
        "} FROM Student RETURN name"
    )

    assert isinstance(program.statement, Query)
    assert len(program.types) == 1
    student = program.types[0]
    assert isinstance(student, TypeDefinition)
    assert student.name == "Student"
    assert student.allow_extra is True
    assert [(field.name, field.type_name, field.required) for field in student.fields] == [
        ("id", "ID", True),
        ("name", "Text", True),
        ("age", "Number", False),
        ("subjects", "List<Text>", False),
    ]


def test_type_declaration_can_validate_query_without_python_schema():
    program = parse_program(
        "TYPE Student { name: Text required age: Number } "
        "FROM Student WHERE age > 15 RETURN name"
    )
    validate(program)


def test_type_declaration_rejects_unknown_field():
    program = parse_program(
        "TYPE Student { name: Text } FROM Student RETURN nmae"
    )
    with pytest.raises(SemanticError, match="Unknown field.*nmae"):
        validate(program)


def test_type_required_fields_are_enforced_for_insert():
    program = parse_program(
        "TYPE Student { id: ID required name: Text required } "
        "INSERT INTO Student { name: 'Aisha' }"
    )
    with pytest.raises(SemanticError, match="missing required.*id"):
        validate(program)


def test_type_allow_extra_fields():
    program = parse_program(
        "TYPE Student { name: Text ALLOW extra fields } "
        "INSERT INTO Student { name: 'Aisha', nickname: 'Ash' }"
    )
    validate(program)


def test_duplicate_type_field_is_rejected():
    with pytest.raises(ParseError, match="Duplicate field"):
        parse_program("TYPE Student { name: Text name: Text } FROM Student RETURN name")


def test_duplicate_type_definition_is_rejected():
    program = parse_program(
        "TYPE Student { name: Text } TYPE Student { id: ID } FROM Student RETURN name"
    )
    with pytest.raises(SemanticError, match="Duplicate TYPE"):
        validate(program)
