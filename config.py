"""Environment, model choice and the numbers that bound every query."""
import os
from pathlib import Path
from dotenv import load_dotenv

# Build an Agent That Chats With Your SQL Database - Safely

load_dotenv()
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
DB_PATH: Path = PROJECT_ROOT / "store.db"
AUDIT_PATH: Path = PROJECT_ROOT / "audit.jsonl"
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
MODEL: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
# Hard caps. These are enforced in code, never in the prompt.
MAX_ROWS: int = 50
QUERY_TIMEOUT_SECONDS: float = 3.0
MAX_REPAIR_ATTEMPTS: int = 2