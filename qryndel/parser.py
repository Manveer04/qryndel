"""Lexer and parser for the Qryndel v0.2.0 grammar (Lark LALR)."""

from __future__ import annotations
from pathlib import Path

from lark import Lark, Transformer, UnexpectedInput
from lark.exceptions import VisitError
from qryndel.errors import SourceSpan, UdlError

from qryndel.ast_nodes import (
    AndCondition,
    AggregateField,
    Assignment,
    Condition,
    DeleteStatement,
    FieldPath,
    GroupByClause,
    HavingClause,
    InsertStatement,
    Join,
    JoinCondition,
    NotCondition,
    NullPredicate,
    OrderField,
    OrCondition,
    ParameterRef,
    Program,
    Query,
    Statement,
    RuleCall,
    RuleDefinition,
    Source,
    TypeDefinition,
    TypeField,
    TransactionStatement,
    UpdateStatement,
)

_GRAMMAR_PATH = Path(__file__).parent / "grammar.lark"
_GRAMMAR = _GRAMMAR_PATH.read_text(encoding="utf-8")

# Reusable parser instance
_lark_parser: Lark | None = None


def _get_parser() -> Lark:
    global _lark_parser
    if _lark_parser is None:
        _lark_parser = Lark(_GRAMMAR, parser="lalr")
    return _lark_parser


class ParseError(UdlError):
    """Raised when Qryndel text cannot be parsed."""

    code = "UDL_PARSE_ERROR"

    def __init__(self, message: str, *, source_span: SourceSpan | None = None):
        super().__init__(message, code=self.code, source_span=source_span)


class _QryndelTransformer(Transformer):
    """Converts Lark parse tree into custom AST nodes."""

    # --- Top-level ---

    def start(self, children):
        types = [c for c in children if isinstance(c, TypeDefinition)]
        rules = [c for c in children if isinstance(c, RuleDefinition)]
        stmt = [c for c in children if isinstance(
            c, (
                Query,
                InsertStatement,
                UpdateStatement,
                DeleteStatement,
                TransactionStatement,
            )
        )][0]
        return Program(types=types, rules=rules, statement=stmt)

    def atomic_statement(self, children):
        return children[0]

    def write_statement(self, children):
        return children[0]

    def transaction(self, children):
        action = str(children[-1]).upper()
        statements = [child for child in children[:-1] if isinstance(
            child, (InsertStatement, UpdateStatement, DeleteStatement)
        )]
        return TransactionStatement(statements=statements, action=action)

    def transaction_end(self, children):
        return str(children[0])

    # --- Language-level schemas ---

    def type_definition(self, children):
        name = str(children[0])
        fields = [c for c in children[1:] if isinstance(c, TypeField)]
        allow_extra = any(c is True for c in children[1:])
        seen = set()
        for field in fields:
            if field.name in seen:
                raise ParseError(f"Duplicate field '{field.name}' in TYPE {name}")
            seen.add(field.name)
        return TypeDefinition(name=name, fields=fields, allow_extra=allow_extra)

    def type_field(self, children):
        name = str(children[0])
        type_name = str(children[1])
        required = len(children) > 2 and str(children[2]).lower() == "required"
        return TypeField(name=name, type_name=type_name, required=required)

    def type_name(self, children):
        if len(children) == 2:
            return f"{children[0]}<{children[1]}>"
        return str(children[0])

    def field_modifier(self, children):
        return str(children[0])

    def allow_extra(self, children):
        return True

    # --- Rule definitions ---

    def rule_definition(self, children):
        name = str(children[0])
        param = str(children[1])
        expr = children[2]
        return RuleDefinition(name=name, parameter=param, expression=expr)

    # --- Table query (FROM) ---

    def query(self, children):
        source = children[0]
        joins = []
        where = None
        order = None
        limit = None
        group_by = None
        having = None
        fields = None
        distinct = False

        for child in children[1:]:
            if isinstance(child, Join):
                joins.append(child)
            elif isinstance(child, (Condition, NullPredicate, AndCondition, OrCondition, NotCondition, RuleCall)):
                where = child
            elif isinstance(child, GroupByClause):
                group_by = child.fields
            elif isinstance(child, HavingClause):
                having = child.expression
            elif isinstance(child, tuple):
                fields, distinct = child
            elif isinstance(child, list):
                if child and isinstance(child[0], OrderField):
                    order = child
                else:
                    fields = child
            elif isinstance(child, int):
                limit = child
            elif child is None:
                continue

        return Query(
            source=source,
            joins=joins,
            fields=fields,
            where=where,
            order=order,
            limit=limit,
            distinct=distinct,
            group_by=group_by,
            having=having,
        )

    # --- FROM clause ---

    def from_clause(self, children):
        source = children[0]
        if len(children) > 1:
            source.alias = children[1]
        return source

    def source_name(self, children):
        return Source(name=str(children[0]))

    def alias(self, children):
        return str(children[-1])

    # --- JOIN ---

    def join_clause(self, children):
        condition = children[-1]
        first = children[0]
        if isinstance(first, str) and first in ("INNER", "LEFT", "RIGHT", "FULL"):
            join_type = first
            source = children[1]
            alias = children[2] if len(children) > 3 else None
        else:
            join_type = "INNER"
            source = children[0]
            alias = children[1] if len(children) > 2 else None
        return Join(join_type=join_type, right=source, alias=alias, condition=condition)

    def join_type(self, children):
        return str(children[0])

    def join_condition(self, children):
        return JoinCondition(left=children[0], operator=str(children[1]), right=children[2])

    # --- Expression tree ---

    def where_clause(self, children):
        return children[0]

    def or_expr(self, children):
        if len(children) == 1:
            return children[0]
        result = children[0]
        for child in children[1:]:
            result = OrCondition(left=result, right=child)
        return result

    def and_expr(self, children):
        if len(children) == 1:
            return children[0]
        result = children[0]
        for child in children[1:]:
            result = AndCondition(left=result, right=child)
        return result

    def not_expr(self, children):
        if len(children) == 1:
            return children[0]
        return NotCondition(child=children[1])

    def primary(self, children):
        return children[0]

    def condition(self, children):
        if len(children) == 2:
            return NullPredicate(field=children[0], predicate=str(children[1]))
        field_node, operator, value = children
        return Condition(field=field_node, operator=str(operator), value=value)

    def null_predicate(self, children):
        return children[0]

    def is_null(self, _children):
        return "IS_NULL"

    def is_not_null(self, _children):
        return "IS_NOT_NULL"

    def is_missing(self, _children):
        return "IS_MISSING"

    def is_not_missing(self, _children):
        return "IS_NOT_MISSING"

    def exists(self, _children):
        return "EXISTS"

    def condition_field(self, children):
        return children[0]

    def rule_call(self, children):
        name = str(children[0])
        arg = children[1]
        return RuleCall(name=name, argument=arg)

    def field(self, children):
        names = [str(c) for c in children]
        return FieldPath(parts=names)

    def operator(self, children):
        return str(children[0])

    def value(self, children):
        raw = str(children[0])
        if raw.startswith("$"):
            return ParameterRef(name=raw[1:])
        if raw.lower() in ("true", "false"):
            return raw.lower() == "true"
        if raw.lower() == "null":
            return None
        try:
            if "." in raw:
                return float(raw)
            return int(raw)
        except ValueError:
            pass
        if raw.startswith('"') and raw.endswith('"'):
            return raw[1:-1]
        if raw.startswith("'") and raw.endswith("'"):
            return raw[1:-1]
        return raw

    # --- RETURN ---

    def return_clause(self, children):
        distinct = False
        fields = None
        for child in children:
            if isinstance(child, bool):
                distinct = child
            elif isinstance(child, list):
                fields = child
        return (fields, distinct)

    def distinct(self, children):
        return True

    def field_list(self, children):
        return list(children)

    def group_clause(self, children):
        return GroupByClause(fields=children[0])

    def group_field_list(self, children):
        return list(children)

    def having_clause(self, children):
        return HavingClause(expression=children[0])

    def return_item(self, children):
        return children[0]

    def aggregate(self, children):
        function = str(children[0]).upper()
        argument = children[1]
        return AggregateField(function=function, field=None if argument == "*" else argument)

    def aggregate_arg(self, children):
        if not children:
            return "*"
        return children[0]

    # --- ORDER BY ---

    def order_clause(self, children):
        return list(children)

    def order_field(self, children):
        field = children[0]
        direction = "ASC"
        nulls = None
        if len(children) > 1:
            for child in children[1:]:
                text = str(child).upper()
                if text in {"ASC", "DESC"}:
                    direction = text
                else:
                    nulls = text
        return OrderField(field=field, direction=direction, nulls=nulls)

    def direction(self, children):
        return str(children[0])

    def null_order(self, children):
        return str(children[0]).upper()

    def null_position(self, children):
        return str(children[0]).upper()

    # --- LIMIT ---

    def limit_clause(self, children):
        raw = str(children[0])
        if not raw.isdigit():
            raise ParseError(f"LIMIT must be a non-negative integer, got {raw}")
        return int(raw)

    # --- Write statements ---

    def insert_statement(self, children):
        table = str(children[0])
        values = children[1]
        return InsertStatement(table=table, values=values)

    def insert_values(self, children):
        result = {}
        for child in children:
            key, val = child
            if key in result:
                raise ParseError(f"Duplicate field '{key}' in INSERT")
            result[key] = val
        return result

    def insert_pair(self, children):
        key = str(children[0])
        val = children[1]
        return (key, val)

    def update_statement(self, children):
        table = str(children[0])
        alias = None
        assignments = []
        where = None
        idx = 1
        if idx < len(children) and isinstance(children[idx], str):
            alias = children[idx]
            idx += 1
        # Collect all assignment nodes (may be single or wrapped in list)
        while idx < len(children) and isinstance(children[idx], (Assignment, list)):
            child = children[idx]
            if isinstance(child, list):
                for a in child:
                    if isinstance(a, Assignment):
                        assignments.append(a)
            elif isinstance(child, Assignment):
                assignments.append(child)
            idx += 1
        # Remaining child is the WHERE clause
        if idx < len(children):
            where = children[idx]
        return UpdateStatement(table=table, alias=alias, assignments=assignments, where=where)

    def assignment(self, children):
        field_node = children[0]
        op = str(children[1])
        value = children[2]
        if op != "=":
            raise ParseError(f"Assignment requires '=', got '{op}'")
        return Assignment(field=field_node, value=value)

    def delete_statement(self, children):
        table = str(children[0])
        alias = None
        where = None
        idx = 1
        if idx < len(children) and isinstance(children[idx], str):
            alias = children[idx]
            idx += 1
        if idx < len(children):
            where = children[idx]
        return DeleteStatement(table=table, alias=alias, where=where)


def parse_program(text: str) -> Program:
    """
    Parse a Qryndel program string into a ``Program`` AST.

    A program may contain TYPE and RULE declarations followed by one
    statement.
    Raises ``ParseError`` on invalid syntax.
    """
    parser = _get_parser()
    try:
        tree = parser.parse(text)
    except UnexpectedInput as e:
        span = None
        if getattr(e, "line", None) is not None and getattr(e, "column", None) is not None:
            span = SourceSpan(e.line, e.column)
        raise ParseError(str(e), source_span=span) from e

    transformer = _QryndelTransformer()
    try:
        result = transformer.transform(tree)
    except VisitError as e:
        if isinstance(e.orig_exc, ParseError):
            raise e.orig_exc from None
        raise ParseError(str(e.orig_exc)) from e
    return result


def parse(text: str) -> Statement:
    """Parse *text* and return its single statement.

    TYPE and RULE declarations are discarded; use ``parse_program`` to keep
    them. Raises ``ParseError`` on invalid syntax.
    """
    return parse_program(text).statement
