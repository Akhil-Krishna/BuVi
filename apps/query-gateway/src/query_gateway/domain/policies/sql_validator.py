"""The SQL allow-list validator (Section 13). The security boundary before any database.

Section 13, v1 rules, and where each is enforced below:

* `SELECT` statements only, single statement                  -> `_check_statement_shape`
* no DDL/DML anywhere (including data-modifying CTEs)           -> `_check_nodes`
* no `INTO`/materialization, no locking clauses                  -> `_check_nodes`
* no volatile/administrative/file/network functions              -> function allow-list
* identifiers must resolve against the catalog the caller may see -> `_resolve_tables`,
  `_qualify` (unknown columns), `_check_columns` (PII / agent visibility)
* reject on parse failure -- never "best effort"                 -> `validate`

Design choices that matter for security:

* **Allow-lists, not deny-lists.** A function is permitted only if its parsed class (or, for
  functions sqlglot does not model, its lower-cased name) is listed. Anything unknown fails.
* **What executes is regenerated from the validated tree**, fully qualified, comments
  stripped -- never the caller's text. A parser differential between sqlglot and Postgres
  cannot smuggle a second statement past the check, and the regenerated SQL is re-parsed and
  re-checked before it is returned.
* **Execution does not trust this module alone** (Section 13: "never rely on the parser
  alone"): the connector also runs a single prepared statement, in a read-only transaction,
  with an empty `search_path`, as a read-only database role.
* **Rejections carry a reason and at most the offending keyword or identifier** as the
  caller wrote it. Parser messages and the SQL text never leave this module.
* **One validator, the data source's dialect** (Phase A8). SQL is parsed and regenerated in the
  engine's dialect; an engine without a dialect here is rejected, never parsed as another.
  Regeneration drops comments, so MySQL's executable `/*! ... */` comments never execute.

Pure: sqlglot is a parser, not a framework or I/O.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, cast

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError, TokenError
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope

from query_gateway.domain.value_objects.policy import DataSourcePolicy, Purpose, TablePolicy

#: Engine -> sqlglot dialect. An engine missing here cannot be validated, so it cannot run.
DIALECTS: Final[dict[str, str]] = {"postgres": "postgres", "mysql": "mysql"}
DEFAULT_MAX_SQL_LENGTH: Final = 50_000
DEFAULT_MAX_NODES: Final = 5_000
_MAX_DETAIL: Final = 128


class RejectionReason(StrEnum):
    EMPTY = "EMPTY"
    TOO_LONG = "TOO_LONG"
    INVALID_CHARACTERS = "INVALID_CHARACTERS"
    PARSE_ERROR = "PARSE_ERROR"
    MULTIPLE_STATEMENTS = "MULTIPLE_STATEMENTS"
    NOT_A_SELECT = "NOT_A_SELECT"
    WRITE_OPERATION = "WRITE_OPERATION"
    INTO_CLAUSE = "INTO_CLAUSE"
    LOCKING_CLAUSE = "LOCKING_CLAUSE"
    PARAMETER = "PARAMETER"
    FUNCTION_NOT_ALLOWED = "FUNCTION_NOT_ALLOWED"
    QUALIFIED_FUNCTION = "QUALIFIED_FUNCTION"
    TABLE_FUNCTION = "TABLE_FUNCTION"
    CAST_NOT_ALLOWED = "CAST_NOT_ALLOWED"
    TABLE_NOT_ALLOWED = "TABLE_NOT_ALLOWED"
    AMBIGUOUS_TABLE = "AMBIGUOUS_TABLE"
    COLUMN_NOT_ALLOWED = "COLUMN_NOT_ALLOWED"
    PII_COLUMN = "PII_COLUMN"
    TOO_COMPLEX = "TOO_COMPLEX"


MESSAGES: Final[dict[RejectionReason, str]] = {
    RejectionReason.EMPTY: "The query is empty.",
    RejectionReason.TOO_LONG: "The query is too long.",
    RejectionReason.INVALID_CHARACTERS: "The query contains invalid characters.",
    RejectionReason.PARSE_ERROR: "The query could not be parsed.",
    RejectionReason.MULTIPLE_STATEMENTS: "Only a single statement is allowed.",
    RejectionReason.NOT_A_SELECT: "Only SELECT queries are allowed.",
    RejectionReason.WRITE_OPERATION: "The query uses a blocked operation.",
    RejectionReason.INTO_CLAUSE: "SELECT ... INTO is not allowed.",
    RejectionReason.LOCKING_CLAUSE: "Row-locking clauses are not allowed.",
    RejectionReason.PARAMETER: "Query parameters are not supported.",
    RejectionReason.FUNCTION_NOT_ALLOWED: "The query uses a function that is not allowed.",
    RejectionReason.QUALIFIED_FUNCTION: "Schema-qualified function calls are not allowed.",
    RejectionReason.TABLE_FUNCTION: "Table functions are not allowed.",
    RejectionReason.CAST_NOT_ALLOWED: "The query uses a cast that is not allowed.",
    RejectionReason.TABLE_NOT_ALLOWED: "The query references a table that is not available.",
    RejectionReason.AMBIGUOUS_TABLE: "A table name is ambiguous; qualify it with its schema.",
    RejectionReason.COLUMN_NOT_ALLOWED: "The query references a column that is not available.",
    RejectionReason.PII_COLUMN: "The query references a column classified as personal data.",
    RejectionReason.TOO_COMPLEX: "The query is too complex.",
}

# --- Operations -----------------------------------------------------------------------------

#: Statement kinds that may not appear anywhere in the tree, mapped to the keyword reported.
_WRITE_NODES: Final[dict[type[exp.Expression], str]] = {
    cls: keyword
    for name, keyword in (
        ("Insert", "INSERT"),
        ("Update", "UPDATE"),
        ("Delete", "DELETE"),
        ("Merge", "MERGE"),
        ("Create", "CREATE"),
        ("Drop", "DROP"),
        ("Alter", "ALTER"),
        ("AlterTable", "ALTER"),
        ("TruncateTable", "TRUNCATE"),
        ("Copy", "COPY"),
        ("Grant", "GRANT"),
        ("Revoke", "REVOKE"),
        ("Set", "SET"),
        ("Command", "COMMAND"),
        ("Transaction", "TRANSACTION"),
        ("Commit", "COMMIT"),
        ("Rollback", "ROLLBACK"),
        ("Pragma", "PRAGMA"),
        ("Use", "USE"),
        ("LoadData", "LOAD"),
        ("Analyze", "ANALYZE"),
        ("Refresh", "REFRESH"),
        ("Comment", "COMMENT"),
        ("Kill", "KILL"),
    )
    if isinstance(cls := getattr(exp, name, None), type)
}

_SELECT_ROOTS: Final = (exp.Select, exp.Union, exp.Intersect, exp.Except)

# --- Functions ------------------------------------------------------------------------------

#: Parsed function classes that are read-only, deterministic-enough builtins. Aggregates,
#: window functions, math, string, date/time, conditional and JSON accessors. Deliberately
#: absent: session/server introspection (`CurrentUser`, `CurrentVersion`, ...), set-returning
#: generators, and anything that can block, sleep, write, or reach the filesystem or network.
_ALLOWED_FUNCTION_NAMES: Final = (
    # aggregates
    "Count",
    "Sum",
    "Avg",
    "Min",
    "Max",
    "Stddev",
    "StddevPop",
    "StddevSamp",
    "Variance",
    "VariancePop",
    "ArrayAgg",
    "GroupConcat",
    "LogicalAnd",
    "LogicalOr",
    "PercentileCont",
    "PercentileDisc",
    "Corr",
    "CovarPop",
    "CovarSamp",
    "Median",
    "Mode",
    # window
    "RowNumber",
    "Rank",
    "DenseRank",
    "Lag",
    "Lead",
    "FirstValue",
    "LastValue",
    "NthValue",
    "Ntile",
    "PercentRank",
    "CumeDist",
    # math
    "Abs",
    "Ceil",
    "Floor",
    "Round",
    "Trunc",
    "Sign",
    "Sqrt",
    "Cbrt",
    "Ln",
    "Log",
    "Log2",
    "Log10",
    "Exp",
    "Pow",
    "Greatest",
    "Least",
    "Degrees",
    "Radians",
    "Pi",
    # string
    "Concat",
    "ConcatWs",
    "Substring",
    "Trim",
    "Replace",
    "Length",
    "Upper",
    "Lower",
    "Initcap",
    "Pad",
    "Left",
    "Right",
    "StrPosition",
    "SplitPart",
    "RegexpReplace",
    "RegexpLike",
    "RegexpExtract",
    "Reverse",
    "Chr",
    "Unicode",
    "Ascii",
    "MD5",
    # date and time
    "TimestampTrunc",
    "DateTrunc",
    "Extract",
    "CurrentDate",
    "CurrentTimestamp",
    "CurrentTime",
    "Date",
    "StrToDate",
    "StrToTime",
    "TimeToStr",
    "DateAdd",
    "DateSub",
    "DateDiff",
    "TimestampAdd",
    "TimestampSub",
    "TimestampDiff",
    "DateFromParts",
    "TimestampFromParts",
    "UnixToTime",
    "TimeToUnix",
    "ToChar",
    "ToNumber",
    # implicit date/string conversions sqlglot inserts around MySQL date arguments
    "TsOrDsToDate",
    "TsOrDsToTimestamp",
    "TsOrDsToTime",
    "TimeStrToTime",
    "DateStrToDate",
    # date parts (MySQL YEAR(), MONTH(), ... and their Postgres equivalents)
    "Year",
    "Quarter",
    "Month",
    "Week",
    "WeekOfYear",
    "Day",
    "DayOfMonth",
    "DayOfWeek",
    "DayOfYear",
    "Hour",
    "Minute",
    "Second",
    # conditional and types
    "Case",
    "If",
    "Coalesce",
    "Nullif",
    "Cast",
    "TryCast",
    "Exists",
    "Array",
    "ArraySize",
    "ArrayContains",
    # JSON accessors
    "JSONExtract",
    "JSONExtractScalar",
    "JSONBExtract",
    "JSONBExtractScalar",
)
_ALLOWED_FUNCTIONS: Final[frozenset[type[Any]]] = frozenset(
    cls for name in _ALLOWED_FUNCTION_NAMES if isinstance(cls := getattr(exp, name, None), type)
)

#: Postgres builtins sqlglot does not model (parsed as `Anonymous`), by lower-cased name.
_POSTGRES_ANONYMOUS: Final[frozenset[str]] = frozenset(
    {
        "age",
        "make_date",
        "make_time",
        "make_timestamp",
        "make_interval",
        "date_part",
        "justify_days",
        "justify_hours",
        "justify_interval",
        "isfinite",
        "btrim",
        "char_length",
        "character_length",
        "octet_length",
        "strpos",
        "translate",
        "starts_with",
        "to_hex",
        "quote_ident",
        "quote_literal",
        "format",
        "width_bucket",
        "div",
        "gcd",
        "lcm",
        "scale",
        "factorial",
        "json_extract_path_text",
        "jsonb_extract_path_text",
        "json_extract_path",
        "jsonb_extract_path",
        "jsonb_typeof",
        "json_typeof",
        "jsonb_array_length",
        "json_array_length",
        "array_length",
        "cardinality",
        "array_position",
        "regexp_match",
        "regexp_count",
        "string_to_array",
        "array_to_string",
        "percentile_cont",
        "percentile_disc",
        "mode",
        "every",
        "bit_and",
        "bit_or",
        "regr_slope",
        "regr_intercept",
        "regr_r2",
        "stddev_samp",
        "stddev_pop",
        "var_samp",
        "var_pop",
        "covar_pop",
        "covar_samp",
        "corr",
    }
)

#: MySQL builtins sqlglot does not model: read-only date, string and numeric helpers only.
#: Deliberately absent: SLEEP, BENCHMARK, LOAD_FILE, GET_LOCK/RELEASE_LOCK, USER(),
#: CONNECTION_ID(), SYS_EXEC and anything else that blocks, reads files or reveals the session.
_MYSQL_ANONYMOUS: Final[frozenset[str]] = frozenset(
    {
        "makedate",
        "maketime",
        "last_day",
        "dayname",
        "monthname",
        "yearweek",
        "weekday",
        "to_days",
        "from_days",
        "period_add",
        "period_diff",
        "sec_to_time",
        "time_to_sec",
        "char_length",
        "character_length",
        "locate",
        "instr",
        "lpad",
        "rpad",
        "field",
        "elt",
        "truncate",
        "format",
    }
)
_ALLOWED_ANONYMOUS: Final[dict[str, frozenset[str]]] = {
    "postgres": _POSTGRES_ANONYMOUS,
    "mysql": _MYSQL_ANONYMOUS,
}

#: Operators and connectors sqlglot models as `Func` subclasses; they are not function calls.
_OPERATOR_BASES: Final = (exp.Binary, exp.Connector, exp.Unary)

#: Casts to object-identifier types resolve catalog names -- blind schema probing (Section 13).
_BLOCKED_CAST_PREFIXES: Final = ("reg",)

_UNKNOWN_COLUMN: Final = re.compile(r"Column '([^']{1,128})'")


@dataclass(frozen=True)
class ValidationOutcome:
    ok: bool
    reason: RejectionReason | None = None
    #: The offending keyword, function or identifier as written; never parser text.
    detail: str | None = None
    #: The SQL to execute: regenerated from the validated tree. None when rejected.
    sql: str | None = None
    tables: tuple[str, ...] = ()

    @property
    def message(self) -> str:
        return MESSAGES[self.reason] if self.reason else "The query is valid."

    @property
    def sql_hash(self) -> str | None:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest() if self.sql else None

    def to_audit(self) -> dict[str, Any]:
        """`query_executions.validation_result`."""
        return {
            "valid": self.ok,
            "reason": self.reason.value if self.reason else None,
            "detail": self.detail,
            "tables": list(self.tables),
            "normalized_sql_sha256": self.sql_hash,
        }


class _RejectedError(Exception):
    def __init__(self, reason: RejectionReason, detail: str | None = None) -> None:
        super().__init__(reason.value)
        self.reason = reason
        self.detail = detail[:_MAX_DETAIL] if detail else None


def _keyword(node: exp.Expression) -> str:
    if isinstance(node, exp.Command):
        first = str(node.this or "").strip().split()
        return first[0].upper()[:32] if first else "COMMAND"
    return _WRITE_NODES.get(type(node), type(node).__name__.upper())


class SqlValidator:
    def __init__(
        self, *, max_length: int = DEFAULT_MAX_SQL_LENGTH, max_nodes: int = DEFAULT_MAX_NODES
    ) -> None:
        self._max_length = max_length
        self._max_nodes = max_nodes

    def validate(
        self, sql: str, policy: DataSourcePolicy, *, purpose: Purpose
    ) -> ValidationOutcome:
        dialect = DIALECTS.get(policy.engine)
        if dialect is None:
            return ValidationOutcome(ok=False, reason=RejectionReason.PARSE_ERROR)
        try:
            canonical, tables = self._validate(sql, policy, purpose, dialect)
        except _RejectedError as rejected:
            return ValidationOutcome(ok=False, reason=rejected.reason, detail=rejected.detail)
        return ValidationOutcome(ok=True, sql=canonical, tables=tables)

    # --- pipeline -------------------------------------------------------------------------

    def _validate(
        self, sql: str, policy: DataSourcePolicy, purpose: Purpose, dialect: str
    ) -> tuple[str, tuple[str, ...]]:
        if not sql or not sql.strip():
            raise _RejectedError(RejectionReason.EMPTY)
        if len(sql) > self._max_length:
            raise _RejectedError(RejectionReason.TOO_LONG)
        if "\x00" in sql:
            raise _RejectedError(RejectionReason.INVALID_CHARACTERS)

        root = self._parse_single(sql, dialect)
        self._check_statement_shape(root)
        self._check_nodes(root, dialect)
        root = normalize_identifiers(root, dialect=dialect)
        referenced = self._resolve_tables(root, policy, purpose)
        qualified = self._qualify(root, policy, dialect)
        if purpose is not Purpose.SQL_EDITOR:
            self._check_columns(qualified, policy)

        canonical = qualified.sql(dialect=dialect, comments=False)
        # Defense in depth: what executes must itself pass the structural checks.
        again = self._parse_single(canonical, dialect)
        self._check_statement_shape(again)
        self._check_nodes(again, dialect)
        return canonical, tuple(sorted({t.qualified_name for t in referenced}))

    def _parse_single(self, sql: str, dialect: str) -> exp.Expression:
        try:
            statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
        except (ParseError, TokenError, ValueError):
            raise _RejectedError(RejectionReason.PARSE_ERROR) from None
        except RecursionError:
            raise _RejectedError(RejectionReason.TOO_COMPLEX) from None
        if not statements:
            raise _RejectedError(RejectionReason.EMPTY)
        if len(statements) > 1:
            raise _RejectedError(RejectionReason.MULTIPLE_STATEMENTS)
        return cast(exp.Expression, statements[0])

    def _check_statement_shape(self, root: exp.Expression) -> None:
        if isinstance(root, _SELECT_ROOTS):
            return
        if type(root) in _WRITE_NODES:
            raise _RejectedError(RejectionReason.WRITE_OPERATION, _keyword(root))
        raise _RejectedError(RejectionReason.NOT_A_SELECT)

    def _check_nodes(self, root: exp.Expression, dialect: str) -> None:
        for count, node in enumerate(root.walk(), start=1):
            if count > self._max_nodes:
                raise _RejectedError(RejectionReason.TOO_COMPLEX)
            if type(node) in _WRITE_NODES:
                raise _RejectedError(
                    RejectionReason.WRITE_OPERATION, _keyword(cast(exp.Expression, node))
                )
            if isinstance(node, exp.Into):
                raise _RejectedError(RejectionReason.INTO_CLAUSE)
            if isinstance(node, exp.Lock):
                raise _RejectedError(RejectionReason.LOCKING_CLAUSE)
            if isinstance(node, exp.Placeholder | exp.Parameter | exp.SessionParameter):
                # includes MySQL `@user_var` and `@@system_var` (server state disclosure)
                raise _RejectedError(RejectionReason.PARAMETER)
            if isinstance(node, exp.Dot) and isinstance(node.expression, exp.Func):
                raise _RejectedError(RejectionReason.QUALIFIED_FUNCTION, node.expression.name)
            if isinstance(node, exp.Cast | exp.TryCast):
                target = node.to.sql(dialect=dialect).lower() if node.to else ""
                if target.startswith(_BLOCKED_CAST_PREFIXES):
                    raise _RejectedError(RejectionReason.CAST_NOT_ALLOWED, target)
            if isinstance(node, exp.Func) and not isinstance(node, _OPERATOR_BASES):
                self._check_function(node, dialect)

    @staticmethod
    def _check_function(node: exp.Func, dialect: str) -> None:
        if isinstance(node, exp.Anonymous):
            name = str(node.name).lower()
            if name not in _ALLOWED_ANONYMOUS[dialect]:
                raise _RejectedError(RejectionReason.FUNCTION_NOT_ALLOWED, name)
            return
        if type(node) not in _ALLOWED_FUNCTIONS:
            raise _RejectedError(RejectionReason.FUNCTION_NOT_ALLOWED, node.sql_name().lower())

    def _resolve_tables(
        self, root: exp.Expression, policy: DataSourcePolicy, purpose: Purpose
    ) -> list[TablePolicy]:
        cte_names = {cte.alias_or_name for cte in root.find_all(exp.CTE)}
        referenced: list[TablePolicy] = []
        for table in root.find_all(exp.Table):
            if not isinstance(table.this, exp.Identifier):
                raise _RejectedError(RejectionReason.TABLE_FUNCTION)
            written = ".".join(p for p in (table.catalog, table.db, table.name) if p)
            if table.catalog:
                raise _RejectedError(RejectionReason.TABLE_NOT_ALLOWED, written)
            if not table.db and table.name in cte_names:
                continue
            if table.db:
                resolved = policy.table(table.db, table.name)
            else:
                candidates = policy.tables_named(table.name)
                if len(candidates) > 1:
                    raise _RejectedError(RejectionReason.AMBIGUOUS_TABLE, written)
                resolved = candidates[0] if candidates else None
            if resolved is None:
                raise _RejectedError(RejectionReason.TABLE_NOT_ALLOWED, written)
            if purpose is not Purpose.SQL_EDITOR and not resolved.is_visible_to_agent:
                raise _RejectedError(RejectionReason.TABLE_NOT_ALLOWED, written)
            table.set("db", exp.to_identifier(resolved.schema_name, quoted=True))
            table.set("this", exp.to_identifier(resolved.table_name, quoted=True))
            referenced.append(resolved)
        return referenced

    @staticmethod
    def _schema(policy: DataSourcePolicy) -> dict[str, object]:
        schema: dict[str, dict[str, dict[str, str]]] = {}
        for table in policy.tables:
            if policy.table(table.schema_name, table.table_name) is None:
                continue
            schema.setdefault(table.schema_name, {})[table.table_name] = {
                column.name: "text" for column in table.columns
            }
        return dict(schema)

    def _qualify(
        self, root: exp.Expression, policy: DataSourcePolicy, dialect: str
    ) -> exp.Expression:
        try:
            return qualify(
                root,
                schema=self._schema(policy),
                dialect=dialect,
                validate_qualify_columns=True,
                quote_identifiers=True,
                identify=True,
            )
        except OptimizeError as error:
            match = _UNKNOWN_COLUMN.search(str(error))
            raise _RejectedError(
                RejectionReason.COLUMN_NOT_ALLOWED, match.group(1) if match else None
            ) from None
        except RecursionError:
            raise _RejectedError(RejectionReason.TOO_COMPLEX) from None
        except Exception:
            # Fail closed: a tree the qualifier cannot analyze is not executed.
            raise _RejectedError(RejectionReason.PARSE_ERROR) from None

    @staticmethod
    def _check_columns(root: exp.Expression, policy: DataSourcePolicy) -> None:
        """Agent-path column policy (Section 12): no personal-data columns, anywhere."""
        for scope in traverse_scope(root):
            for column in scope.columns:
                source = scope.sources.get(column.table)
                if not isinstance(source, exp.Table):
                    continue  # derived table or CTE: its own scope is checked
                table = policy.table(source.db, source.name)
                policy_column = table.column(column.name) if table else None
                if policy_column is not None and policy_column.is_pii:
                    raise _RejectedError(
                        RejectionReason.PII_COLUMN,
                        f"{source.db}.{source.name}.{column.name}",
                    )
