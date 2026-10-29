"""Execution: a read-only connection that SQLite itself polices."""
import sqlite3
import time
from typing import Any
from src.config import DB_PATH, MAX_ROWS, QUERY_TIMEOUT_SECONDS
from src.policy import ALLOWED_FUNCTIONS, ALLOWED_TABLES, is_readable

# The only three things a read-only SELECT ever asks permission for.
_READ_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}


class QueryError(RuntimeError):
    """Raised when SQLite refuses or aborts a query."""


def _authorizer(action: int, arg1: str | None, arg2: str | None, db_name: str | None, trigger: str | None) -> int:
    """Veto anything outside the policy, at the engine level.
    This is the right home for the exact table.column allowlist: by the time
    SQLite asks, it has already resolved names, so the pair here is always a
    real column.
    """
    if action not in _READ_ACTIONS:
        return sqlite3.SQLITE_DENY
    if db_name and db_name.lower() != "main":
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_READ:
        table = (arg1 or "").lower()
        if table not in ALLOWED_TABLES:
            return sqlite3.SQLITE_DENY
        # An empty column name means "reads no columns", as COUNT(*) does.
        if arg2 and not is_readable(table, arg2):
            return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() not in ALLOWED_FUNCTIONS:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _connect() -> sqlite3.Connection:
    """Open the database read-only with the authorizer and timeout attached."""
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.set_authorizer(_authorizer)
    started = time.monotonic()
    conn.set_progress_handler(
        lambda: 1 if time.monotonic() - started > QUERY_TIMEOUT_SECONDS else 0, 2000)
    return conn


def run_query(sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
    """Execute guarded SQL and return (column names, rows), capped at MAX_ROWS."""
    conn = _connect()
    try:
        cursor = conn.execute(sql)
        rows = cursor.fetchmany(MAX_ROWS)
        columns = [d[0] for d in cursor.description or []]
        return columns, rows
    except sqlite3.OperationalError as err:
        if "interrupted" in str(err).lower():
            raise QueryError(
                f"query took longer than {QUERY_TIMEOUT_SECONDS:g}s and was cancelled."
            ) from err
        raise QueryError(str(err)) from err
    except sqlite3.DatabaseError as err:
        raise QueryError(str(err)) from err
    finally:
        conn.close()