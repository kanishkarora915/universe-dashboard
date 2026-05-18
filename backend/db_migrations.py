"""
Pragmatic SQLite migrations — versioned schema changes per database.

Why: Manual ALTER TABLE statements scattered across 10+ files. Hard to
know what schema version is on production, hard to add new columns
safely, no rollback path.

What this provides:
  • _migrations table per DB (tracks applied migrations)
  • Migration registration API: register(db_path, version, name, sql)
  • Idempotent runner: apply_all_pending(db_path) — safe to call multiple times
  • Per-DB version tracking — get_version(db_path)

What this does NOT provide:
  • Down migrations / rollback (SQLite ALTER TABLE limitations)
  • Multi-statement transactions (SQLite limits)
  • Auto-discovery of migrations from filesystem (kept simple)

Usage in app startup:

    from db_migrations import register, apply_all_pending

    # Define migrations once, at module load time
    register(SCALPER_DB, 1, "initial",
        '''CREATE TABLE IF NOT EXISTS scalper_trades (...)''')
    register(SCALPER_DB, 2, "add_reversal_source",
        '''ALTER TABLE scalper_trades ADD COLUMN source TEXT DEFAULT 'verdict' ''')

    # Apply on startup (run for each DB)
    apply_all_pending(SCALPER_DB)

To add a new schema change:
    1. Pick the next unused version number for that DB
    2. Add register(DB_PATH, N, "description", SQL)
    3. Deploy. apply_all_pending() runs it once.

Failed migrations stop the chain — logged but don't crash app.
Already-applied migrations are skipped via _migrations table check.
"""

import sqlite3
from typing import Dict, List, Tuple

# Module-level registry: {db_path: [(version, name, sql), ...]}
_registry: Dict[str, List[Tuple[int, str, str]]] = {}


def register(db_path: str, version: int, name: str, sql: str) -> None:
    """Register a migration. Multiple registrations OK (de-duplicated by version).

    Args:
        db_path:  full path to the SQLite DB
        version:  monotonically increasing integer (1, 2, 3, ...)
        name:     short human-readable description (e.g., "add_source_column")
        sql:      single SQL statement to execute on apply
    """
    db_path = str(db_path)
    if db_path not in _registry:
        _registry[db_path] = []
    # De-dupe by version (idempotent registration)
    existing = {v for v, _, _ in _registry[db_path]}
    if version not in existing:
        _registry[db_path].append((version, name, sql))


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    """Create the _migrations tracking table if missing."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)


def get_version(db_path: str) -> int:
    """Return current schema version (highest applied) or 0 if none."""
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        _ensure_migrations_table(conn)
        row = conn.execute("SELECT MAX(version) FROM _migrations").fetchone()
        return row[0] if row and row[0] is not None else 0
    finally:
        conn.close()


def get_applied_versions(db_path: str) -> List[int]:
    """Return list of applied migration versions."""
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        _ensure_migrations_table(conn)
        rows = conn.execute("SELECT version FROM _migrations ORDER BY version").fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def apply_all_pending(db_path: str) -> Dict:
    """Apply all registered migrations not yet in _migrations table.

    Returns:
        {
            "db": db_path,
            "applied": [list of version ints just applied],
            "skipped": [list already-applied],
            "errors":  [{"version": N, "error": str}, ...]
        }

    Errors stop the chain (later migrations are NOT attempted) but don't
    raise — caller sees them in the dict and decides what to do.
    """
    db_path = str(db_path)
    result = {"db": db_path, "applied": [], "skipped": [], "errors": []}

    if db_path not in _registry:
        return result  # No migrations registered for this DB — fine

    migrations = sorted(_registry[db_path], key=lambda m: m[0])
    if not migrations:
        return result

    try:
        applied_versions = set(get_applied_versions(db_path))
    except Exception as e:
        result["errors"].append({"version": -1, "error": f"Could not read _migrations: {e}"})
        return result

    conn = sqlite3.connect(db_path, timeout=30.0)
    try:
        _ensure_migrations_table(conn)
        for version, name, sql in migrations:
            if version in applied_versions:
                result["skipped"].append(version)
                continue
            try:
                # SQLite executes one statement per execute() call.
                # Most migrations are single-statement (ALTER TABLE, CREATE INDEX).
                conn.executescript(sql)
                conn.execute(
                    "INSERT INTO _migrations (version, name) VALUES (?, ?)",
                    (version, name),
                )
                conn.commit()
                result["applied"].append(version)
                # Structured log if available
                try:
                    from structured_logger import log
                    log.info(
                        "db_migration_applied",
                        db=db_path,
                        version=version,
                        name=name,
                    )
                except Exception:
                    pass
            except Exception as e:
                result["errors"].append({"version": version, "name": name, "error": str(e)})
                # Stop the chain — don't apply later migrations on broken state
                try:
                    from structured_logger import log
                    log.error(
                        "db_migration_failed",
                        db=db_path,
                        version=version,
                        name=name,
                        error=str(e),
                    )
                except Exception:
                    pass
                break
    finally:
        conn.close()

    return result


def apply_all_registered() -> Dict[str, Dict]:
    """Apply pending migrations for ALL registered DBs.
    Returns {db_path: result_dict_from_apply_all_pending}.
    """
    out = {}
    for db_path in list(_registry.keys()):
        out[db_path] = apply_all_pending(db_path)
    return out


def status() -> Dict[str, Dict]:
    """Snapshot of all DBs' migration state. Used by /api/db/status admin endpoint."""
    out = {}
    for db_path in _registry.keys():
        try:
            applied = get_applied_versions(db_path)
            registered = sorted({v for v, _, _ in _registry[db_path]})
            pending = [v for v in registered if v not in applied]
            out[db_path] = {
                "current_version": max(applied) if applied else 0,
                "applied_count": len(applied),
                "registered_count": len(registered),
                "pending": pending,
            }
        except Exception as e:
            out[db_path] = {"error": str(e)}
    return out
