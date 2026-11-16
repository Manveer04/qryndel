"""Lexer and parser for the Qryndel v0.3.0 grammar (Lark LALR)."""

from __future__ import annotations
from pathlib import Path

from lark import Lark, Transformer, UnexpectedInput
from lark.exceptions import VisitError
from qryndel.errors import SourceSpan, UdlError

from qryndel.ast_nodes import (
    AndCondition,
    AggregateField,
    Assignment,
    BinaryOp,
    Convert,
    Condition,
    DeleteStatement,
    FieldPath,
    GroupByClause,
    HavingClause,
    InsertStatement,
    Join,
    JoinCondition,
    Negate,
    NotCondition,
    NullPredicate,
    OrderField,
    OrCondition,
    ParameterRef,
    Program,
    ProjectionItem,
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

# Reusable parser instance. The transformer runs *inside* the LALR parser
# (bottom-up, no recursion), so deeply nested input cannot overflow Python's
# call stack while building the AST.
_lark_parser: Lark | None = None

#: Deepest AST nesting accepted. Deeper input is almost certainly generated
#: by mistake and would exhaust the call stack in later stages.
MAX_NESTING_DEPTH = 200

_SQLITE_INT_MIN = -(2**63)
_SQLITE_INT_MAX = 2**63 - 1


def _get_parser() -> Lark:
    global _lark_parser
    if _lark_parser is None:
        _lark_parser = Lark(_GRAMMAR, parser="lalr", transformer=_QryndelTransformer())
    return _lark_parser


def _balanced(nodes: list, node_type):
    """Combine an AND/OR chain into a balanced tree (depth log2(n), not n)."""
    while len(nodes) > 1:
        paired = [node_type(left=nodes[i], right=nodes[i + 1]) for i in range(0, len(nodes) - 1, 2)]
        if len(nodes) % 2:
            paired.append(nodes[-1])
        nodes = paired
    return nodes[0]


def _check_depth(root) -> None:
    """Reject ASTs nested deeper than MAX_NESTING_DEPTH, without recursion."""
    stack = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > MAX_NESTING_DEPTH:
            raise ParseError(
                f"Query is nested too deeply (more than {MAX_NESTING_DEPTH} levels). "
                "Split it up, or use a shorter chain of operators."
            )
        if isinstance(node, (list, tuple)):
            stack.extend((item, depth) for item in node)
        elif isinstance(node, dict):
            stack.extend((item, depth) for item in node.values())
        elif hasattr(node, "__dataclass_fields__"):
            stack.extend((getattr(node, name), depth + 1) for name in node.__dataclass_fields__)


class ParseError(UdlError):
    """Raised when Qryndel text cannot be parsed."""

    code = "UDL_PARSE_ERROR"

    def __init__(self, message: str, *, source_span: SourceSpan | None = None):
        super().__init__(message, code=self.code, source_span=source_span)


_STAR = object()  # marker for COUNT(*)


class _QryndelTransformer(Transformer):
    """Converts Lark parse tree into custom AST nodes."""

    # --- Top-level ---

    def start(self, children):
        types = [c for c in children if isinstance(c, TypeDefinition)]
        rules = [c for c in children if isinstance(c, RuleDefinition)]
        statements = [c for c in children if isinstance(
            c, (
                Query,
                InsertStatement,
                UpdateStatement,
                DeleteStatement,
                TransactionStatement,
            )
        )]
        stmt = statements[0] if statements else None
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
        return _balanced(list(children), OrCondition)

    def and_expr(self, children):
        return _balanced(list(children), AndCondition)

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

    def rule_call(self, children):
        name = str(children[0])
        arg = children[1]
        return RuleCall(name=name, argument=arg)

    def field(self, children):
        names = [str(c) for c in children]
        return FieldPath(parts=names)

    def operator(self, children):
        return str(children[0])

    # --- Values and arithmetic ---

    def literal(self, children):
        token = children[0]
        raw = str(token)
        kind = token.type
        if kind == "BOOL_KW":
            return raw.lower() == "true"
        if kind == "NULL_KW":
            return None
        if kind == "NUMBER":
            if "." in raw:
                return float(raw)
            value = int(raw)
            # Like SQLite, integers too large for 64 bits become decimals.
            return value if value <= _SQLITE_INT_MAX else float(raw)
        return raw[1:-1]  # STRING / SINGLE_STRING

    def parameter(self, children):
        return ParameterRef(name=str(children[0])[1:])

    def negative_number(self, children):
        raw = str(children[0])
        return -(float(raw) if "." in raw else int(raw))

    def bare_word(self, children):
        # INSERT { city: Singapore } keeps the v0.1 meaning: the string 'Singapore'.
        return str(children[0])

    def insert_value(self, children):
        return children[0]

    def atom(self, children):
        return children[0]

    def unary(self, children):
        return children[0]

    def conversion(self, children):
        return Convert(target=str(children[0]).upper(), operand=children[1])

    def negate(self, children):
        operand = children[0]
        if isinstance(operand, (int, float)) and not isinstance(operand, bool):
            return -operand  # fold "-5" into a literal
        return Negate(operand=operand)

    def plus(self, _children):
        return "+"

    def minus(self, _children):
        return "-"

    def times(self, _children):
        return "*"

    def divide(self, _children):
        return "/"

    def _fold(self, children):
        result = children[0]
        for index in range(1, len(children), 2):
            result = BinaryOp(op=children[index], left=result, right=children[index + 1])
        return result

    def expr(self, children):
        return self._fold(children)

    def term(self, children):
        return self._fold(children)

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
        expression = children[0]
        alias = str(children[1]) if len(children) > 1 else None
        if alias is None and isinstance(expression, (FieldPath, AggregateField)):
            return expression
        return ProjectionItem(expression=expression, alias=alias)

    def aggregate(self, children):
        function = str(children[0]).upper()
        argument = children[1]
        if argument is _STAR:
            if function != "COUNT":
                raise ParseError(f"{function}(*) is not valid; only COUNT(*) accepts *")
            return AggregateField(function=function, field=None)
        return AggregateField(function=function, field=argument)

    def agg_star(self, _children):
        return _STAR

    def aggregate_arg(self, children):
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

    A program may contain TYPE and RULE declarations followed by at most
    one statement (``Program.statement`` is ``None`` for declaration-only
    programs).
    Raises ``ParseError`` on invalid syntax.
    """
    parser = _get_parser()
    try:
        result = parser.parse(text)
    except UnexpectedInput as e:
        span = None
        if getattr(e, "line", None) is not None and getattr(e, "column", None) is not None:
            span = SourceSpan(e.line, e.column)
        raise ParseError(str(e), source_span=span) from e
    except ParseError:
        raise
    except VisitError as e:
        if isinstance(e.orig_exc, ParseError):
            raise e.orig_exc from None
        raise ParseError(str(e.orig_exc)) from e
    except RecursionError as e:
        raise ParseError("Query is too complex to parse") from e
    _check_depth(result)
    return result


def parse(text: str) -> Statement | None:
    """Parse *text* and return its single statement.

    TYPE and RULE declarations are discarded; use ``parse_program`` to keep
    them. Raises ``ParseError`` on invalid syntax.
    """
    return parse_program(text).statement
