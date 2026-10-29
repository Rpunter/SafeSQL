"""The chat REPL - ask in English, watch every safety step, read the a
nswer.
Run: PYTHONPATH=. uv run python -m src.chat
Commands: /schema, /policy, /quit
"""

import sys
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from src.config import (
    AUDIT_PATH, MAX_REPAIR_ATTEMPTS, MAX_ROWS, MODEL, QUERY_TIMEOUT_SECONDS,
    )
from src.graph import build_graph

from src.policy import ALLOWED_TABLES, denied_columns, denied_tables,describe_schema

console = Console()

STEP_STYLE = {
    "draft": ("[dim]drafted[/dim]", "white"),
    "allowed": ("[green]guard: allowed[/green]", "green"),
    "blocked": ("[red]guard: blocked[/red]", "red"),
    "db-error": ("[yellow]database error[/yellow]", "yellow"),
    "executed": ("[cyan]executed[/cyan]", "cyan"),
    "declined": ("[magenta]model declined[/magenta]", "magenta"),
    "refused": ("[red]refused[/red]", "red"),
    "answer": ("", ""),}

def print_policy() -> None:
    """Show the rules in force, so the reader knows what should be impossible."""
    body = (
        f"readable tables {', '.join(sorted(ALLOWED_TABLES))}\n"
        f"hidden tables {', '.join(sorted(denied_tables())) or 'none'}\n"
        f"hidden columns {', '.join(sorted(denied_columns())) or 'none'}\n"
        f"row cap {MAX_ROWS}\n"
        f"query timeout {QUERY_TIMEOUT_SECONDS:g}s\n"
        f"repair attempts {MAX_REPAIR_ATTEMPTS}\n"
        f"connection read-only + SQLite authorizer\n"
        f"audit log {AUDIT_PATH.name}"
    )
    console.print(Panel(body, title="policy", border_style="blue"))

def render_trace(trace: list[dict[str, str]]) -> None:
    """Print each step the agent took, including the attempts that failed."""
    for event in trace:
        label, _ = STEP_STYLE.get(event["step"], (event["step"], "white"))
        if not label:
            continue
        detail = escape(" ".join(event["detail"].split())[:160])
        console.print(f" {label} {detail}")

def render_rows(columns: list[str], rows: list[tuple]) -> None:
    """Show result rows in a table, truncated for the terminal."""
    if not columns:
        return
    table = Table(show_header=True, header_style="bold cyan", box=None, pad_edge=False)
    for column in columns:
        table.add_column(str(column), overflow="fold", max_width=40)
    for row in rows[:15]:
        table.add_row(*[str(value) for value in row])
    console.print(table)
    if len(rows) > 15:
        console.print(f" [dim]... {len(rows) - 15} more rows[/dim]")

def run_turn(graph, question: str, history: list[dict[str, str]]) -> None:
    """Send one question through the graph and print everything it did."""
    state = graph.invoke({"question": question, "history": history, "trace": []})
    render_trace(state.get("trace", []))
    if state.get("safe_sql") and not state.get("blocked"):
        console.print(Syntax(state["safe_sql"], "sql", theme="ansi_dark", word_wrap=True))
        render_rows(state.get("columns", []), state.get("rows", []))
        history.append({"question": question, "sql": state["safe_sql"]})
        console.print(Panel(state.get("answer", "(no answer)"), border_style="green"))

def main() -> None:
    """Start the REPL."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    graph = build_graph()
    history: list[dict[str, str]] = []
    console.print(Panel(f"Safe SQL agent on [bold]{MODEL}[/bold] - /schema /policy /quit", border_style="blue"))
    print_policy()
    while True:
        try:
            question = console.input("\n[bold blue]you >[/bold blue]").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question:
            continue
        if question in {"/quit", "/exit"}:
            break
        if question == "/schema":
            console.print(Panel(describe_schema(), title="schema the model sees", border_style="blue"))
            continue
        if question == "/policy":
            print_policy()
            continue
        try:
            run_turn(graph, question, history)
        except Exception as err:  # keep the REPL alive on any transport failure
            console.print(f"[red]error:[/red] {err}")


if __name__ == "__main__":
    main()

        