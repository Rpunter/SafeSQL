"""Attack the agent on purpose, and prove which layer stops what.
Run: PYTHONPATH=. uv run python -m src.redteam [--no-llm]
"""

import hashlib
import sys
from rich.console import Console
from rich.markup import escape

from rich.table import Table
from src.attacks import (ATTACK_PROMPTS, FORBIDDEN_IN_EXECUTED, HOSTILE_SQL, LEGITIMATE_SQL,)
from src.config import DB_PATH
from src.db import QueryError, run_query
from src.guard import guard

console = Console()


def db_fingerprint() -> str:
    """Hash the database file, so any write anywhere would show up."""
    return hashlib.sha256(DB_PATH.read_bytes()).hexdigest()[:16]


def _table(title: str) -> Table:
    """Build an empty results table."""
    table = Table(title=title, header_style="bold cyan", title_justify="left")
    table.add_column("attack", style="bold", max_width=22)
    table.add_column("outcome", max_width=20)
    table.add_column("detail", overflow="fold", max_width=64)
    return table


def round_legitimate() -> int:
    """Round 0: real analytics queries must still pass and still return rows."""
    table, failures = _table("0. ordinary queries still work"), 0
    for name, sql in LEGITIMATE_SQL:
        verdict = guard(sql)
        if not verdict.ok:
            failures += 1
            table.add_row(name, "[red]BLOCKED[/red]", escape(verdict.reason))
            continue
        try:
            columns, rows = run_query(verdict.sql)
        except QueryError as err:
            failures += 1
            table.add_row(name, "[red]FAILED[/red]", escape(str(err)))
            continue
        limit = verdict.sql.rsplit("LIMIT", 1)[-1].strip() if "LIMIT" in verdict.sql else "-"
        table.add_row(name, "[green]ran[/green]",
            escape(f"{len(rows)} row(s), columns {columns}, limit {limit}"))
    console.print(table)
    return failures


def round_guard_only() -> int:
    """Round 1: hostile SQL into the guard. Everything marked "stop" must fail."""
    table, failures = _table("1. the guard alone (no model involved)"), 0
    for name, sql, expected in HOSTILE_SQL:
        verdict = guard(sql)
        if not verdict.ok:
            table.add_row(name, "[green]blocked[/green]",
                escape(f"{verdict.layer}: {verdict.reason}"))
        elif expected == "stop":
            failures += 1
            table.add_row(name, "[red]APPROVED[/red]", escape(verdict.sql[:120]))
        else:
            table.add_row(name, "[yellow]approved[/yellow]", escape(f"payload is inert: {verdict.sql[:80]}"))
    console.print(table)
    return failures


def round_guard_removed() -> int:
    """Round 2: the same SQL straight to SQLite, with the guard taken out."""
    table, failures = _table("2. guard removed - read-only mode + authorizer only"), 0
    for name, sql, expected in HOSTILE_SQL:
        try:
            columns, rows = run_query(sql)
        except QueryError as err:
            table.add_row(name, "[green]refused[/green]", escape(str(err)))
            continue
        if expected == "stop":
            failures += 1
            table.add_row(name, "[red]RAN[/red]", escape(f"{len(rows)} row(s): {columns}"))
        else:
            table.add_row(name, "[yellow]ran[/yellow]",
                escape(f"harmless: {len(rows)} row(s) of {columns}"))
    console.print(table)
    return failures


def round_full_agent() -> int:
    """Round 3: hostile prompts through the real agent, model and all."""
    from src.graph import build_graph
    graph = build_graph()
    table, failures = _table("3. the whole agent (hostile prompts)"), 0
    for name, prompt in ATTACK_PROMPTS:
        state = graph.invoke({"question": prompt, "history": [], "trace": []})
        approved = [e["detail"] for e in state.get("trace", []) if e["step"] == "allowed"]
        blocked = [e["detail"] for e in state.get("trace", []) if e["step"] == "blocked"]
        errors = [e["detail"] for e in state.get("trace", []) if e["step"] == "db-error"]
        leak = next((s for s in approved if FORBIDDEN_IN_EXECUTED.search(s)), None)
        if leak:
            failures += 1
            table.add_row(name, "[red]LEAKED[/red]", escape(leak[:120]))
        elif blocked:
            table.add_row(name, "[green]blocked by guard[/green]", escape(blocked[0]))
        elif errors:
            table.add_row(name, "[green]stopped by sqlite[/green]", escape(errors[0]))
        elif state.get("blocked"):
            table.add_row(name, "[green]declined by model[/green]", escape(state.get("answer", "")[:120]))
        else:
            table.add_row(name, "[green]ran, harmless[/green]", escape((approved[0] if approved else "")[:120]))
    console.print(table)
    return failures


def round_model_off_the_leash() -> int:
    """Round 4: strip the safety rules from the prompt and let the model comply."""
    from src.graph import SQL_BLOCK, extract_sql
    from src.llm import NAIVE_SQL_SYSTEM, get_llm
    from src.policy import describe_schema
    llm = get_llm()
    schema = describe_schema(full=True)
    table, failures = _table("4. safety rules removed from the prompt"), 0
    for name, prompt in ATTACK_PROMPTS[:8]:
        reply = llm.invoke([("system", NAIVE_SQL_SYSTEM), ("human", f"Schema:\n{schema}\n\n{prompt}")]).content
        text = reply if isinstance(reply, str) else str(reply)
        if not SQL_BLOCK.search(text):
            table.add_row(name, "[blue]model refused[/blue]",
                escape(" ".join(text.split())[:80]))
            continue
        sql = extract_sql(text)
        verdict = guard(sql)
        wrote = " ".join(sql.split())[:78]
        if not verdict.ok:
            table.add_row(name, "[green]guard blocked[/green]", escape(f"model wrote: {wrote}\nblocked by: {verdict.layer}"))
        elif FORBIDDEN_IN_EXECUTED.search(sql):
            failures += 1
            table.add_row(name, "[red]APPROVED[/red]", escape(wrote))
        else:
            table.add_row(name, "[yellow]allowed, rewritten[/yellow]", escape(f"model wrote: {wrote}\nran as: {verdict.sql[:78]}"))
    console.print(table)
    return failures


def main() -> None:
    """Run the rounds and report whether the database survived untouched."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    before = db_fingerprint()
    failures = round_legitimate()
    console.print()
    failures += round_guard_only()
    console.print()
    failures += round_guard_removed()
    if "--no-llm" not in sys.argv:
        console.print()
        failures += round_full_agent()
        console.print()
        failures += round_model_off_the_leash()
    after = db_fingerprint()
    intact = before == after
    console.print(f"\ndatabase sha256 before {before} / after {after} - "
        f"{'[green]unchanged[/green]' if intact else '[red]MODIFIED[/red]'}")
    console.print("verdict: " + ("[green]every attack contained[/green]" if failures == 0 and intact
        else f"[red]{failures} breach(es)[/red]"))


if __name__ == "__main__":
    main()