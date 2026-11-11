import re
import time
from dataclasses import dataclass, field
import pandas as pd
from src.config import MAX_SELF_HEAL_RETRIES
from src.db_manager import EnterpriseDatabaseManager
from src.ast_guardrail import ReadOnlyASTGuardrail, ASTValidationResult


@dataclass
class AgentExecutionTrace:
    user_question: str
    schema_tables_inspected: list[str]
    initial_sql: str
    final_sql: str
    ast_validation: ASTValidationResult
    self_healing_attempts: int
    healing_logs: list[dict] = field(default_factory=list)
    execution_latency_ms: float = 0.0
    row_count: int = 0
    synthesized_insight: str = ""
    recommended_chart: str = "bar"
    error: str | None = None


class SchemaInspectorAgent:
    """Agent 1: Inspects live PostgreSQL / SQLite schema metadata and prunes irrelevant tables."""

    def __init__(self, db: EnterpriseDatabaseManager):
        self.db = db

    def inspect_relevant_schema(self, question: str) -> dict[str, list[str]]:
        full_schema = self.db.get_schema_metadata()
        q_lower = question.lower()
        selected = {}
        for table, cols in full_schema.items():
            if any(k in q_lower for k in [table.rstrip("s"), table]):
                selected[table] = cols
        return selected if selected else full_schema


class NL2SQLCompilerAgent:
    """Agent 2: Compiles natural language analytical queries into deterministic SQL."""

    # Common hallucinated column aliases mapped to actual schema columns for realistic stress testing
    HALLUCINATION_REPAIR_MAP = {
        "customer_name": "full_name",
        "client_name": "full_name",
        "revenue": "total_amount",
        "order_total": "total_amount",
        "price": "unit_price",
        "product_category": "category",
        "ticket_priority": "priority",
        "satisfaction_score": "csat_score",
        "spend": "annual_spend",
        "orders_table": "orders",
        "customers_table": "customers",
    }

    def compile_sql(self, question: str, inject_hallucinated_column: bool = False) -> str:
        q = question.strip()
        q_low = q.lower()

        # Pass through raw SQL if user/red-team explicitly submits SQL or destructive command
        if re.match(r"^(drop|delete|update|insert|alter|truncate|grant|create|select\s+.+\s+from)\b", q_low):
            return q

        if "region" in q_low and "top" not in q_low and ("revenue" in q_low or "sales" in q_low or "total" in q_low or "order" in q_low):
            col = "revenue" if inject_hallucinated_column else "total_amount"
            return (
                f"SELECT c.region, ROUND(SUM(o.{col}), 2) AS total_revenue, "
                f"COUNT(o.order_id) AS order_count "
                f"FROM customers c JOIN orders o ON c.customer_id = o.customer_id "
                f"WHERE o.status = 'Completed' "
                f"GROUP BY c.region ORDER BY total_revenue DESC"
            )

        if "category" in q_low or "product" in q_low:
            col = "price" if inject_hallucinated_column else "unit_price"
            return (
                f"SELECT p.category, ROUND(SUM(oi.line_total), 2) AS category_revenue, "
                f"SUM(oi.quantity) AS units_sold, ROUND(AVG(p.{col}), 2) AS avg_unit_price "
                f"FROM products p JOIN order_items oi ON p.product_id = oi.product_id "
                f"GROUP BY p.category ORDER BY category_revenue DESC"
            )

        if "segment" in q_low and ("spend" in q_low or "customer" in q_low or "annual" in q_low):
            col = "spend" if inject_hallucinated_column else "annual_spend"
            return (
                f"SELECT segment, COUNT(customer_id) AS customer_count, "
                f"ROUND(AVG({col}), 2) AS avg_annual_spend, "
                f"ROUND(SUM({col}), 2) AS total_segment_spend "
                f"FROM customers GROUP BY segment ORDER BY total_segment_spend DESC"
            )

        if "ticket" in q_low or "support" in q_low or "csat" in q_low or "priority" in q_low:
            col = "satisfaction_score" if inject_hallucinated_column else "csat_score"
            return (
                f"SELECT priority, COUNT(ticket_id) AS ticket_volume, "
                f"ROUND(AVG(resolution_hours), 2) AS avg_resolution_hours, "
                f"ROUND(AVG({col}), 2) AS avg_csat "
                f"FROM support_tickets GROUP BY priority ORDER BY avg_resolution_hours DESC"
            )

        if "top" in q_low and "customer" in q_low:
            col = "customer_name" if inject_hallucinated_column else "full_name"
            return (
                f"SELECT c.{col}, c.region, c.segment, ROUND(SUM(o.total_amount), 2) AS lifetime_value "
                f"FROM customers c JOIN orders o ON c.customer_id = o.customer_id "
                f"WHERE o.status = 'Completed' "
                f"GROUP BY c.customer_id, c.{col}, c.region, c.segment "
                f"ORDER BY lifetime_value DESC LIMIT 10"
            )

        if "monthly" in q_low or "trend" in q_low or "month" in q_low:
            return (
                "SELECT SUBSTR(order_date, 1, 7) AS order_month, "
                "ROUND(SUM(total_amount), 2) AS monthly_revenue, "
                "COUNT(order_id) AS order_volume "
                "FROM orders WHERE status = 'Completed' "
                "GROUP BY order_month ORDER BY order_month ASC"
            )

        if "status" in q_low and "order" in q_low:
            return (
                "SELECT status, COUNT(order_id) AS order_count, "
                "ROUND(SUM(total_amount), 2) AS gross_amount "
                "FROM orders GROUP BY status ORDER BY order_count DESC"
            )

        # Default executive overview query
        return (
            "SELECT c.region, c.segment, COUNT(o.order_id) AS completed_orders, "
            "ROUND(SUM(o.total_amount), 2) AS total_revenue "
            "FROM customers c JOIN orders o ON c.customer_id = o.customer_id "
            "WHERE o.status = 'Completed' "
            "GROUP BY c.region, c.segment ORDER BY total_revenue DESC"
        )


class SelfHealingExecutionAgent:
    """
    Agent 3: Parses database traceback errors (e.g., `no such column`, `UndefinedColumn`,
    `no such table`, syntax ambiguities) and automatically rewrites the SQL query
    against live schema metadata.
    """

    def __init__(self, db: EnterpriseDatabaseManager):
        self.db = db

    def repair_sql_from_traceback(self, broken_sql: str, traceback_msg: str) -> str:
        repaired = broken_sql
        schema = self.db.get_schema_metadata()
        all_real_cols = {
            col_def.split(" ")[0]
            for cols in schema.values()
            for col_def in cols
        }

        # 1. Extract offending column from PostgreSQL / SQLite error message
        col_match = re.search(r"(?:no such column:|column\s+)([a-zA-Z0-9_\.]+)", traceback_msg, re.IGNORECASE)
        if col_match:
            bad_token = col_match.group(1).split(".")[-1]
            if bad_token in NL2SQLCompilerAgent.HALLUCINATION_REPAIR_MAP:
                good_col = NL2SQLCompilerAgent.HALLUCINATION_REPAIR_MAP[bad_token]
                repaired = re.sub(rf"\b{re.escape(bad_token)}\b", good_col, repaired)
                return repaired

        # 2. Scan for any known hallucinated column or table aliases
        for bad_alias, real_target in NL2SQLCompilerAgent.HALLUCINATION_REPAIR_MAP.items():
            if re.search(rf"\b{re.escape(bad_alias)}\b", repaired) and bad_alias not in all_real_cols:
                repaired = re.sub(rf"\b{re.escape(bad_alias)}\b", real_target, repaired)

        return repaired


class InsightSynthesizerAgent:
    """Agent 4: Synthesizes quantitative findings and selects optimal visual encoding."""

    @staticmethod
    def synthesize(df: pd.DataFrame, question: str) -> tuple[str, str]:
        if df.empty:
            return "Query executed safely with 0 matching records.", "table"

        cols = list(df.columns)
        num_cols = list(df.select_dtypes(include=["number"]).columns)
        dim_cols = [c for c in cols if c not in num_cols]

        chart_type = "line" if any("month" in c or "date" in c for c in cols) else "bar"

        if num_cols and dim_cols:
            primary_metric = num_cols[0]
            primary_dim = dim_cols[0]
            top_row = df.iloc[0]
            total_val = df[primary_metric].sum()
            top_share = (float(top_row[primary_metric]) / total_val * 100.0) if total_val else 0.0
            summary = (
                f"Across {len(df)} groups, '{top_row[primary_dim]}' leads in {primary_metric} "
                f"with {top_row[primary_metric]:,.2f} ({top_share:.1f}% of total {total_val:,.2f})."
            )
            return summary, chart_type

        return f"Retrieved {len(df)} rows across {len(cols)} validated columns.", "table"


class MultiAgentAnalyticsOrchestrator:
    """Coordinates Schema Inspector, NL2SQL Compiler, AST Guardrail, Self-Healing Loop, and Insight Synthesizer."""

    def __init__(self, db_manager: EnterpriseDatabaseManager | None = None):
        self.db = db_manager or EnterpriseDatabaseManager()
        self.schema_agent = SchemaInspectorAgent(self.db)
        self.compiler_agent = NL2SQLCompilerAgent()
        self.guardrail = ReadOnlyASTGuardrail()
        self.healer_agent = SelfHealingExecutionAgent(self.db)
        self.synthesizer_agent = InsightSynthesizerAgent()

    def run_pipeline(
        self,
        user_question: str,
        custom_sql_override: str | None = None,
        simulate_hallucination: bool = False
    ) -> tuple[pd.DataFrame, AgentExecutionTrace]:
        t0 = time.perf_counter()
        schema_subset = self.schema_agent.inspect_relevant_schema(user_question)

        initial_sql = (
            custom_sql_override.strip()
            if custom_sql_override and custom_sql_override.strip()
            else self.compiler_agent.compile_sql(user_question, inject_hallucinated_column=simulate_hallucination)
        )

        current_sql = initial_sql
        ast_res = self.guardrail.validate_sql(current_sql)

        trace = AgentExecutionTrace(
            user_question=user_question,
            schema_tables_inspected=list(schema_subset.keys()),
            initial_sql=initial_sql,
            final_sql=current_sql,
            ast_validation=ast_res,
            self_healing_attempts=0
        )

        # Gate 1: AST Guardrail Check
        if not ast_res.is_safe:
            trace.execution_latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            trace.error = ast_res.blocked_reason
            trace.synthesized_insight = f"Blocked by Read-Only AST Guardrail: {ast_res.blocked_reason}"
            return pd.DataFrame(), trace

        # Gate 2: Execution with Self-Healing Traceback Loop
        for attempt in range(MAX_SELF_HEAL_RETRIES + 1):
            try:
                df = self.db.execute_readonly_query(current_sql)
                trace.final_sql = current_sql
                trace.row_count = len(df)
                trace.execution_latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
                insight, chart = self.synthesizer_agent.synthesize(df, user_question)
                trace.synthesized_insight = insight
                trace.recommended_chart = chart
                return df, trace
            except Exception as exc:
                err_msg = str(exc)
                if attempt >= MAX_SELF_HEAL_RETRIES:
                    trace.error = f"Execution failed after {attempt} self-healing retries: {err_msg}"
                    trace.execution_latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
                    return pd.DataFrame(), trace

                repaired_sql = self.healer_agent.repair_sql_from_traceback(current_sql, err_msg)
                re_val = self.guardrail.validate_sql(repaired_sql)
                trace.self_healing_attempts += 1
                trace.healing_logs.append({
                    "attempt": attempt + 1,
                    "db_traceback": err_msg,
                    "broken_sql": current_sql,
                    "repaired_sql": repaired_sql,
                    "ast_verified": re_val.is_safe
                })
                if not re_val.is_safe:
                    trace.error = re_val.blocked_reason
                    return pd.DataFrame(), trace
                current_sql = repaired_sql

        return pd.DataFrame(), trace
