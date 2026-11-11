import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pytest
from src.ast_guardrail import ReadOnlyASTGuardrail
from src.multi_agent_system import MultiAgentAnalyticsOrchestrator
from src.evaluator import run_offline_evaluation_suite


@pytest.mark.parametrize("destructive_sql", [
    "DROP TABLE customers;",
    "DELETE FROM orders WHERE order_id = 1;",
    "UPDATE products SET unit_price = 0;",
    "TRUNCATE TABLE order_items;",
    "ALTER TABLE customers ADD COLUMN hacked TEXT;",
    "GRANT ALL ON orders TO public;",
    "SELECT * FROM customers; DROP TABLE orders;",
    "INSERT INTO products VALUES (99, 'X', 'Cat', 1, 1, 1);"
])
def test_100_percent_destructive_sql_interception(destructive_sql: str):
    res = ReadOnlyASTGuardrail.validate_sql(destructive_sql)
    assert res.is_safe is False
    assert len(res.violated_tokens) > 0


def test_valid_readonly_select_passes_ast():
    sql = "SELECT region, SUM(total_amount) FROM customers c JOIN orders o ON c.customer_id = o.customer_id GROUP BY region"
    res = ReadOnlyASTGuardrail.validate_sql(sql)
    assert res.is_safe is True
    assert res.statement_type == "SELECT"


def test_self_healing_traceback_loop_recovers_hallucinated_column():
    orchestrator = MultiAgentAnalyticsOrchestrator()
    df, trace = orchestrator.run_pipeline(
        "What is the total revenue and order count by region?",
        simulate_hallucination=True
    )
    assert not df.empty
    assert trace.self_healing_attempts == 1
    assert "total_amount" in trace.final_sql
    assert trace.error is None


def test_150_query_offline_benchmark_accuracy():
    summary = run_offline_evaluation_suite()
    assert summary["total_benchmark_queries"] == 150
    assert summary["ast_destructive_interception_rate_pct"] == 100.0
    assert summary["measured_execution_accuracy_pct"] >= 94.0
