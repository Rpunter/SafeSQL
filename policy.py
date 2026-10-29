"""The access policy: exactly which columns and functions the agent may touch."""
import sqlite3
from functools import lru_cache
from src.config import DB_PATH

# The whole policy. Note what is missing: customers.password_hash, and the payment_methods table entirely.

ALLOWED_COLUMNS: dict[str, frozenset[str]] = {
"customers": frozenset({"id", "name", "email", "city", "country","signup_date"}),
"products": frozenset({"id", "name", "category", "price", "stock","description"}),
"orders": frozenset({"id", "customer_id", "order_date", "status","total"}),
"order_items": frozenset({"id", "order_id", "product_id", "quantity", "unit_price"}),}

ALLOWED_TABLES: frozenset[str] = frozenset(ALLOWED_COLUMNS)

ALLOWED_FUNCTIONS: frozenset[str] = frozenset(
{"count", "sum", "avg", "min", "max", "total",
"round", "abs", "coalesce", "ifnull", "nullif",
"length", "lower", "upper", "trim", "ltrim", "rtrim",
"substr", "substring", "replace", "instr", "printf", "like",
"glob",
"date", "time", "datetime", "strftime", "julianday",
"group_concat", "iif",
# window functions - ordinary analytics, and SQLite asks about them too
"row_number", "rank", "dense_rank", "ntile",
"lag", "lead", "first_value", "last_value", "nth_value",})

def is_readable(table: str, column: str) -> bool:
    """Whether one resolved table.column pair may be read. Used by the authorizer."""
    return column.lower() in ALLOWED_COLUMNS.get(table.lower(), frozenset())

@lru_cache(maxsize=1)
def _real_schema() -> dict[str, list[tuple[str, str]]]:
    """Read the actual tables and columns out of the database, once."""
    schema: dict[str, list[tuple[str, str]]] = {}
    with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        for table in tables:
            info = conn.execute(f"PRAGMA table_info({table})").fetchall()
            schema[table.lower()] = [(row[1], row[2]) for row in info]
    return schema

@lru_cache(maxsize=1)
def denied_tables() -> frozenset[str]:
    """Real tables the agent may not read."""
    return frozenset(set(_real_schema()) - ALLOWED_TABLES)

@lru_cache(maxsize=1)
def denied_columns() -> frozenset[str]:
    """Columns that exist in allowed tables but were deliberately leftout."""
    denied: set[str] = set()
    for table, columns in _real_schema().items():
        if table in ALLOWED_TABLES:
            allowed = ALLOWED_COLUMNS[table]
            denied |= {name.lower() for name, _ in columns if name.lower() not in allowed}
    return frozenset(denied)

def describe_schema(full: bool = False) -> str:
    """Render a schema description. By default this is what the model is shown - allowlisted columns only.
    full=True gives the real schema including the secrets, used only to simulate an unguarded agent."""
    lines: list[str] = []
    for table, columns in sorted(_real_schema().items()):
        if not full and table not in ALLOWED_TABLES:
            continue
        visible = [f"{name} {ctype}" for name, ctype in columns if full or name.lower() in ALLOWED_COLUMNS[table]]
        lines.append(f"{table}({', '.join(visible)})")
    return "\n".join(lines)

def starter_policy() -> str:
    """Print an ALLOWED_COLUMNS block for whatever database is configured."""
    lines = ["ALLOWED_COLUMNS: dict[str, frozenset[str]] = {"]
    for table, columns in sorted(_real_schema().items()):
        names = ", ".join(f'"{name}"' for name, _ in columns)
        lines.append(f' "{table}": frozenset({{{names}}}),')
        lines.append("}")
    return "\n".join(lines)

if __name__ == "__main__":
    print(starter_policy())
