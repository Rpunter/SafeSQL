"""The state that flows through the graph."""
import operator
from typing import Annotated, Any, TypedDict

class AgentState(TypedDict, total=False):
    """Everything one question needs from draft to answer."""
    question: str
    history: list[dict[str, str]]
    schema: str

    draft_sql: str # what the model wrote
    safe_sql: str # what the guard approved and rewrote
    feedback: str # why the last attempt was rejected
    attempt: int

    columns: list[str]
    rows: list[tuple[Any, ...]]
    answer: str
    blocked: bool
    
    trace: Annotated[list[dict[str, str]], operator.add]