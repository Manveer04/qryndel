"""Lexer and parser for the public v0.1.0 UDL grammar."""

from __future__ import annotations

from pathlib import Path

from lark import Lark, Transformer, UnexpectedInput

from qryndel.ast_nodes import (
    AndCondition,
    Condition,
    FieldPath,
    NotCondition,
    NullPredicate,
    OrCondition,
    ParameterRef,
    Query,
    Source,
)
from qryndel.errors import SourceSpan, UdlError


class ParseError(UdlError):
    code = "UDL_PARSE_ERROR"

    def __init__(self, message: str, *, source_span: SourceSpan | None = None):
        super().__init__(message, code=self.code, source_span=source_span)


class _Transformer(Transformer):
    def start(self, children):
        return children[0]

    def query(self, children):
        source = children[0]
        where = next((child for child in children[1:] if isinstance(child, (Condition, NullPredicate, AndCondition, OrCondition, NotCondition))), None)
        fields = next(child for child in children[1:] if isinstance(child, list))
        return Query(source=source, fields=fields, where=where)

    def from_clause(self, children):
        source = children[0]
        alias = str(children[1]) if len(children) > 1 else None
        return Source(source.name, alias)

    def source_name(self, children):
        return Source(str(children[0]))

    def alias(self, children):
        return str(children[0])

    def where_clause(self, children):
        return children[0]

    def return_clause(self, children):
        return children[0]

    def or_expr(self, children):
        result = children[0]
        for child in children[1:]:
            result = OrCondition(result, child)
        return result

    def and_expr(self, children):
        result = children[0]
        for child in children[1:]:
            result = AndCondition(result, child)
        return result

    def not_expr(self, children):
        return children[0]

    def negated(self, children):
        return NotCondition(children[0])

    def primary(self, children):
        return children[0]

    def condition(self, children):
        if len(children) == 2:
            return NullPredicate(children[0], str(children[1]))
        return Condition(children[0], str(children[1]), children[2])

    def field(self, children):
        return FieldPath([str(child) for child in children])

    def operator(self, children):
        return str(children[0])

    def operator(self, children):
        return str(children[0])

    def value(self, children):
        raw = str(children[0])
        if raw.startswith("$"):
            return ParameterRef(raw[1:])
        if raw.lower() in {"true", "false"}:
            return raw.lower() == "true"
        if raw.lower() == "null":
            return None
        if raw.startswith(('"', "'")):
            return raw[1:-1]
        try:
            return float(raw) if "." in raw else int(raw)
        except ValueError:
            return raw

    def field_list(self, children):
        return list(children)

    def is_null(self, _children):
        return "IS_NULL"

    def is_not_null(self, _children):
        return "IS_NOT_NULL"


_GRAMMAR = (Path(__file__).parent / "grammar.lark").read_text(encoding="utf-8")
_PARSER = Lark(_GRAMMAR, parser="lalr")


def parse_program(text: str) -> Query:
    try:
        return _Transformer().transform(_PARSER.parse(text))
    except UnexpectedInput as exc:
        span = SourceSpan(exc.line, exc.column) if getattr(exc, "line", None) else None
        raise ParseError(str(exc), source_span=span) from exc


def parse(text: str) -> Query:
    return parse_program(text)
