"""Append-only log of what the agent tried and what the guard decided."""
import json
import time
from typing import Any

from src.config import AUDIT_PATH

def record(event: str, **fields: Any) -> None:
    """Append one event. Logging must never be the reason a query fails."""
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event,**fields}
    try:
        with AUDIT_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass

def read_recent(limit: int = 20) -> list[dict[str, Any]]:
    """Return the most recent entries, newest last."""
    if not AUDIT_PATH.exists():
        return []
    lines = AUDIT_PATH.read_text(encoding="utf-8").splitlines()[-limit:]
    entries = []
    for line in lines:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return entries

