@echo off
echo ====================================================================
echo Starting Multi-Agent Data Analyst (Text-to-SQL with AST Guardrails)
echo ====================================================================

if not exist .venv (
    echo [1/4] Creating Python virtual environment...
    python -m venv .venv
)

echo [2/4] Activating virtual environment and installing dependencies...
call .venv\Scripts\activate
pip install --upgrade pip -q
pip install -r requirements.txt -q

echo [3/4] Running 150-Query Offline Evaluation ^& AST Red-Team Audit...
python -m src.evaluator

echo [4/4] Launching Streamlit Multi-Agent Analytics Dashboard...
streamlit run app.py
pause
