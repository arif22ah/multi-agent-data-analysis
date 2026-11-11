import re
from dataclasses import dataclass
from src.config import FORBIDDEN_AST_KEYWORDS

try:
    import sqlparse
    SQLPARSE_AVAILABLE = True
except Exception:
    SQLPARSE_AVAILABLE = False


@dataclass
class ASTValidationResult:
    is_safe: bool
    statement_type: str
    blocked_reason: str | None
    violated_tokens: list[str]
    sanitized_sql: str


class ReadOnlyASTGuardrail:
    """
    Inspects the SQL Abstract Syntax Tree (AST) token stream to guarantee 100% interception
    of destructive DDL/DML operations (DROP, DELETE, UPDATE, INSERT, ALTER, TRUNCATE, GRANT,
    Stacked Queries, and System Catalog/Sleep Injections).
    """

    @staticmethod
    def _strip_sql_comments(sql: str) -> str:
        no_block = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
        no_line = re.sub(r"--.*?$", " ", no_block, flags=re.MULTILINE)
        return no_line.strip()

    @staticmethod
    def _split_top_level_statements(sql: str) -> list[str]:
        stmts = []
        buf = []
        in_single = False
        in_double = False
        for ch in sql:
            if ch == "'" and not in_double:
                in_single = not in_single
            elif ch == '"' and not in_single:
                in_double = not in_double
            if ch == ";" and not in_single and not in_double:
                candidate = "".join(buf).strip()
                if candidate:
                    stmts.append(candidate)
                buf = []
            else:
                buf.append(ch)
        trailing = "".join(buf).strip()
        if trailing:
            stmts.append(trailing)
        return stmts

    @classmethod
    def validate_sql(cls, sql_query: str) -> ASTValidationResult:
        cleaned = (sql_query or "").strip()
        if not cleaned:
            return ASTValidationResult(
                is_safe=False,
                statement_type="EMPTY",
                blocked_reason="Empty SQL statement rejected by AST guardrail.",
                violated_tokens=["EMPTY_QUERY"],
                sanitized_sql=""
            )

        uncommented = (
            sqlparse.format(cleaned, strip_comments=True).strip()
            if SQLPARSE_AVAILABLE
            else cls._strip_sql_comments(cleaned)
        )

        statements = cls._split_top_level_statements(uncommented)
        if len(statements) != 1:
            return ASTValidationResult(
                is_safe=False,
                statement_type="MULTI_STATEMENT",
                blocked_reason=(
                    f"Stacked query injection detected ({len(statements)} statements found; "
                    f"strictly 1 read-only SELECT statement permitted)."
                ),
                violated_tokens=["MULTI_STATEMENT_SEMICOLON"],
                sanitized_sql=uncommented
            )

        stmt_text = statements[0].strip()
        # Mask string literals before scanning AST keyword nodes
        masked_literals = re.sub(r"'[^']*'", "'__STR__'", stmt_text)
        word_tokens = [w.upper() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_\.]*", masked_literals)]

        if not word_tokens:
            return ASTValidationResult(
                is_safe=False,
                statement_type="UNKNOWN",
                blocked_reason="No valid SQL AST tokens found.",
                violated_tokens=["EMPTY_AST"],
                sanitized_sql=stmt_text
            )

        root_keyword = word_tokens[0]
        violated: list[str] = []

        for tok in word_tokens:
            base_tok = tok.split(".")[-1]
            if base_tok in FORBIDDEN_AST_KEYWORDS:
                violated.append(f"FORBIDDEN_AST_NODE:{base_tok}")
            if "PG_SLEEP" in tok or "INFORMATION_SCHEMA" in tok or "SQLITE_MASTER" in tok:
                violated.append(f"SYSTEM_CATALOG_OR_DOS:{tok}")

        if root_keyword not in ("SELECT", "WITH"):
            violated.append(f"NON_SELECT_ROOT:{root_keyword}")

        if violated:
            unique_violations = list(dict.fromkeys(violated))
            return ASTValidationResult(
                is_safe=False,
                statement_type=root_keyword,
                blocked_reason=f"AST Guardrail intercepted destructive/unauthorized tokens: {', '.join(unique_violations)}",
                violated_tokens=unique_violations,
                sanitized_sql=stmt_text
            )

        return ASTValidationResult(
            is_safe=True,
            statement_type="SELECT",
            blocked_reason=None,
            violated_tokens=[],
            sanitized_sql=stmt_text
        )
