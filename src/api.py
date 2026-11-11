from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from src.multi_agent_system import MultiAgentAnalyticsOrchestrator
from src.evaluator import run_offline_evaluation_suite

app = FastAPI(
    title="Multi-Agent Data Analyst API",
    description="Text-to-SQL Analytics Pipeline with Read-Only AST Guardrails & Self-Healing Execution",
    version="1.0.0"
)

orchestrator = MultiAgentAnalyticsOrchestrator()


class QueryRequest(BaseModel):
    question: str
    custom_sql_override: str | None = None
    simulate_hallucination: bool = False


@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "multi-agent-nl2sql-analyst"}


@app.post("/analyze")
def analyze_question(req: QueryRequest):
    df, trace = orchestrator.run_pipeline(
        user_question=req.question,
        custom_sql_override=req.custom_sql_override,
        simulate_hallucination=req.simulate_hallucination
    )
    return {
        "question": trace.user_question,
        "initial_sql": trace.initial_sql,
        "final_sql": trace.final_sql,
        "ast_safe": trace.ast_validation.is_safe,
        "blocked_reason": trace.ast_validation.blocked_reason,
        "self_healing_attempts": trace.self_healing_attempts,
        "healing_logs": trace.healing_logs,
        "execution_latency_ms": trace.execution_latency_ms,
        "synthesized_insight": trace.synthesized_insight,
        "rows": df.to_dict(orient="records")
    }


@app.get("/benchmark")
def get_benchmark():
    return run_offline_evaluation_suite()
