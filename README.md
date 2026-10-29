[README.md](https://github.com/user-attachments/files/34139487/README.md)
# SafeSQL# safe-sql-agent

A text-to-SQL chat agent that answers questions about a SQLite database in plain
English — and is built so that no layer of the system, including the language
model itself, is trusted to keep that access safe. The LLM writes SQL; it never
runs SQL. Every query is parsed, checked against an explicit allowlist, capped,
and finally handed to a read-only SQLite connection that enforces the same
allowlist a second time at the engine level.

The project doubles as a worked example of defense in depth for LLM-to-database
agents: `src/redteam.py` runs a battery of hostile prompts and hand-crafted SQL
against the agent and reports which layer stopped each one, and whether the
database file changed at all.

## How a question becomes an answer

The agent is a small [LangGraph](https://github.com/langchain-ai/langgraph)
state machine (`src/graph.py`):

```
START -> write_sql -> check_sql -> execute -> answer -> END
              ^            |          |
              |            v          v
              +--------- retry ---- retry
              |            |          |
              v            v          v
             END <------ refuse <-----+
```

1. **write_sql** — the model (via Groq) is shown the *allowlisted* schema only
   and asked for one `SELECT`. If it declines (`NO_QUERY: ...`), the run ends
   immediately.
2. **check_sql** — `src/guard.py` parses the draft with `sqlglot`, rejects
   anything that isn't a single read-only `SELECT`/set operation, rejects
   `SELECT *`, rejects any table or column not on the allowlist, rejects
   unrecognized functions, and clamps `LIMIT` to `MAX_ROWS`. A rejection sends
   the reason back to `write_sql` as repair feedback.
3. **execute** — the guarded, rewritten SQL runs against a read-only SQLite
   connection (`src/db.py`) that has its own `sqlite3` authorizer enforcing
   the *same* table/column allowlist independently, plus a progress-handler
   timeout. A database error is also fed back as repair feedback.
4. **answer** — a second, separate LLM call turns the result rows into one to
   three sentences, explicitly told that row contents are data, not
   instructions.
5. **refuse** — reached after `MAX_REPAIR_ATTEMPTS` failed repair cycles;
   returns the last rejection reason instead of guessing.

Every step is appended to an in-memory trace (shown in the REPL) and to an
append-only audit log (`audit.jsonl`), so a rejected query is always visible
even though it never reached the database.

## Defense layers

The security model deliberately does not rely on the prompt. Each layer below
would still hold even if the one above it were removed or the model ignored
its instructions entirely:

| Layer | Where | What it stops |
|---|---|---|
| System prompt | `src/llm.py` | Tells the model the rules; advisory only — never trusted alone |
| Static SQL guard | `src/guard.py` (`sqlglot`) | Non-`SELECT` statements, multiple statements, `SELECT *`, denied tables/columns, unrecognized functions, oversized `LIMIT` — checked over the whole parse tree, including subqueries and CTEs |
| SQLite authorizer | `src/db.py` | Re-enforces the table/column allowlist at the engine level, on already-resolved names, independent of the guard |
| Read-only connection | `src/db.py` | `sqlite3.connect(..., mode=ro)` — no write ever reaches the file regardless of what parses |
| Row cap | `src/config.py` (`MAX_ROWS`) | Bulk data exfiltration through legitimate-looking aggregate queries |
| Query timeout | `src/config.py` (`QUERY_TIMEOUT_SECONDS`) | Resource-exhaustion / cartesian-product queries |
| Audit log | `src/audit.py` | Every attempt, allowed or blocked, recorded to `audit.jsonl` |

The access policy itself lives in `src/policy.py` as an explicit allowlist
(`ALLOWED_COLUMNS`), derived against the real schema read from the database.
Anything not listed is denied by construction — including the entire
`payment_methods` table and `customers.password_hash`, which exist in the
database but nowhere in the allowlist.

## Project structure

```
safe-sql-agent/
  src/
    config.py     env vars and the hard limits (MAX_ROWS, timeout, repair attempts)
    state.py      the LangGraph state shape passed between nodes
    policy.py     the access policy: allowed tables/columns/functions, schema introspection
    guard.py      static SQL guard: parses and validates a draft query with sqlglot
    db.py         read-only SQLite connection + authorizer + query execution
    llm.py        Groq model wrapper and the prompts for both LLM steps
    graph.py      the LangGraph state machine wiring the steps together
    chat.py       interactive REPL (`/schema`, `/policy`, `/quit`)
    audit.py      append-only JSONL audit log
    seed.py       builds a deterministic fake e-commerce store.db
    attacks.py    the corpus of legitimate and hostile SQL/prompts used by redteam
    redteam.py    runs the attack corpus through each layer and reports breaches
  store.db        seeded SQLite database (generated, not checked in by default)
  audit.jsonl     audit log (generated)
  pyproject.toml
```

## Setup

Requires Python 3.14+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

Create a `.env` in the project root with a [Groq](https://console.groq.com/keys)
API key:

```bash
cp .env.example .env
# then edit .env and set:
# GROQ_API_KEY=your-key-here
# GROQ_MODEL=openai/gpt-oss-120b   (optional, this is the default)
```

Seed the database (deterministic — same data every run, includes a
prompt-injection payload planted in one product description and a SQL-injection
style customer name, on purpose, to be attacked by the red team):

```bash
PYTHONPATH=. uv run python -m src.seed
```

## Usage

Start the chat REPL:

```bash
PYTHONPATH=. uv run python -m src.chat
```

Commands inside the REPL:

- `/schema` — show the schema exactly as the model sees it (allowlisted tables/columns only)
- `/policy` — show the active policy: readable/hidden tables, hidden columns, row cap, timeout, repair attempts
- `/quit` — exit

Each turn prints the trace of what happened (drafted → allowed/blocked →
executed → answer), the final SQL, and the result rows.

Run the red team against the running agent:

```bash
PYTHONPATH=. uv run python -m src.redteam
# add --no-llm to skip the two rounds that call the model (guard-only checks still run)
```

This runs five rounds — ordinary queries still work, the guard alone against
hostile SQL, the guard removed (read-only + authorizer only), the full agent
against hostile natural-language prompts, and the model with all safety
instructions stripped from its prompt — and finishes by hashing `store.db`
before and after to prove nothing was written.

## Configuration

All limits are enforced in code, never left to the prompt (`src/config.py`):

| Variable | Default | Meaning |
|---|---|---|
| `GROQ_API_KEY` | — | required; Groq API key |
| `GROQ_MODEL` | `openai/gpt-oss-120b` | model used for both LLM steps |
| `MAX_ROWS` | 50 | hard cap on rows returned per query |
| `QUERY_TIMEOUT_SECONDS` | 3.0 | query is interrupted past this wall-clock time |
| `MAX_REPAIR_ATTEMPTS` | 2 | rejected/errored queries the model may retry before the agent refuses |
