"""The agent graph: draft SQL, guard it, run it, explain it - repair on rejection."""
import re

from langgraph.graph import END, START, StateGraph
from src.audit import record
from src.config import MAX_REPAIR_ATTEMPTS
from src.db import QueryError, run_query
from src.guard import guard
from src.llm import build_answer_prompt, build_sql_prompt, get_llm
from src.policy import describe_schema
from src.state import AgentState
SQL_BLOCK = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)

def _event(state: AgentState, step: str, detail: str) -> list[dict[str, str]]:
    """Write one step to the audit log and return it for the in-memory trace."""
    record(step, question=state.get("question", ""), detail=detail)
    return [{"step": step, "detail": detail}]

def extract_sql(text: str) -> str:
    """Pull SQL out of the model's reply, fenced or bare."""
    match = SQL_BLOCK.search(text)
    return (match.group(1) if match else text).strip()

def write_sql(state: AgentState) -> dict:
    """Ask the model for one SELECT, given the schema and any repair feedback."""
    schema = state.get("schema") or describe_schema()   
    messages = build_sql_prompt(state["question"], schema, state.get("history", []), state.get("feedback", ""))
    reply = get_llm().invoke(messages).content
    text = reply if isinstance(reply, str) else str(reply)
    if text.strip().upper().startswith("NO_QUERY"):
        reason = text.split(":", 1)[-1].strip() or "the question does not fit this database."
        return {"schema": schema, "blocked": True, "answer": f"I can't answer that here - {reason}", "trace": _event(state, "declined", reason)} 
    sql = extract_sql(text)
    return {"schema": schema, "draft_sql": sql, "trace": _event(state, "draft", sql)}

def check_sql(state: AgentState) -> dict:
    """Run the guard. This is the only door to the database."""
    verdict = guard(state.get("draft_sql", ""))
    if verdict.ok:
        return {"safe_sql": verdict.sql, "feedback": "", "trace": _event(state, "allowed", verdict.sql)}
    return {
        "feedback": verdict.reason,
        "attempt": state.get("attempt", 0) + 1,
        "trace": _event(state, "blocked", f"{verdict.layer}: {verdict.reason}"),}

def execute(state: AgentState) -> dict:
    """Run the approved SQL against the read-only, authorized connection."""
    try:
        columns, rows = run_query(state["safe_sql"])
    except QueryError as err:
        return {
            "feedback": f"the database rejected it: {err}",
            "attempt": state.get("attempt", 0) + 1,
            "trace": _event(state, "db-error", str(err)),
        }
    return {"columns": columns, "rows": rows, "trace": _event(state, "executed", f"{len(rows)} row(s)")}

def answer(state: AgentState) -> dict:
    """Turn the rows into a sentence the person asking can read."""
    messages = build_answer_prompt(
        state["question"], state["safe_sql"], state["columns"], state["rows"])
    reply = get_llm(temperature=0.2).invoke(messages).content
    text = reply if isinstance(reply, str) else str(reply)
    return {"answer": text.strip(), "trace": _event(state, "answer", "done")}

def refuse(state: AgentState) -> dict:
    """Give up after too many rejected attempts, saying which rule stopped it."""
    return {
        "blocked": True,
        "answer": f"I couldn't build a query that passes the safety rules. Last reason: {state.get('feedback', 'unknown')}",
        "trace": _event(state, "refused", state.get("feedback", "")),
    }

def _after_draft(state: AgentState) -> str:
    """Skip straight to the end when the model declined to write SQL."""
    return "refused" if state.get("blocked") else "check"

def _after_guard(state: AgentState) -> str:
    """Approved queries run; rejected ones go back to the model until the cap."""
    if state.get("safe_sql") and not state.get("feedback"):
        return "execute"
    return "retry" if state.get("attempt", 0) <= MAX_REPAIR_ATTEMPTS else "refuse"

def _after_execute(state: AgentState) -> str:
    """A database error is also repairable - same loop, same cap."""
    if state.get("feedback"):
        return "retry" if state.get("attempt", 0) <= MAX_REPAIR_ATTEMPTS else "refuse"
    return "answer"

def build_graph():
    """Wire the nodes into the compiled graph."""
    builder = StateGraph(AgentState)
    builder.add_node("write_sql", write_sql)
    builder.add_node("check_sql", check_sql)
    builder.add_node("execute", execute)
    builder.add_node("answer", answer)
    builder.add_node("refuse", refuse)
    builder.add_edge(START, "write_sql")
    builder.add_conditional_edges("write_sql", _after_draft, {"check": "check_sql", "refused": END})
    builder.add_conditional_edges("check_sql", _after_guard, {"execute": "execute", "retry": "write_sql", "refuse": "refuse"})
    builder.add_conditional_edges("execute", _after_execute, {"answer": "answer", "retry": "write_sql", "refuse": "refuse"})
    builder.add_edge("answer", END)
    builder.add_edge("refuse", END)
    return builder.compile()

