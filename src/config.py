import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
REPORT_DIR = BASE_DIR / "reports"

for directory in [DATA_DIR, REPORT_DIR]:
    directory.mkdir(parents=True, exist_ok=True)

POSTGRES_URI = os.getenv(
    "POSTGRES_URI",
    f"sqlite:///{DATA_DIR / 'enterprise_analytics.db'}"
)
SQLITE_DB_PATH = DATA_DIR / "enterprise_analytics.db"

FORBIDDEN_AST_KEYWORDS = {
    "DROP", "DELETE", "UPDATE", "INSERT", "ALTER",
    "TRUNCATE", "GRANT", "REVOKE", "CREATE", "REPLACE",
    "EXEC", "EXECUTE", "MERGE", "CALL", "ATTACH", "DETACH", "PRAGMA"
}

MAX_SELF_HEAL_RETRIES = 3
