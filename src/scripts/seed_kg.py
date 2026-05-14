"""
ETL: JSON fixtures → TypeDB knowledge graph.

Loads robots.json, tools.json, positions/ and process_steps.json,
then inserts all entities and relations into the TypeDB database.

Usage:
    python -m scripts.seed_kg
    python -m scripts.seed_kg --db capability_kg --host localhost --port 1729
    python -m scripts.seed_kg --dry-run   # print TypeQL without connecting
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from capability_matcher.loader import (
    load_all_positions,
    load_process_steps,
    load_robots,
    load_tools,
)
from capability_matcher.typedb_log import TypeDBLog

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "resources" / "typedb" / "schema.tql"


def _robot_insert(r) -> str:
    ops = ", ".join(f"has op-index {op}" for op in r.supported_ops)
    return (
        f'insert $r isa robot, has robot-id {r.robot_id}, '
        f'has robot-label "{r.label}", has robot-model "{r.model}", '
        f'has enabled true, has busy false, has ready true, '
        f'has error-id 0, has manually-disabled false, {ops};'
    )


def _tool_insert(t) -> str:
    return (
        f'insert $t isa tool, has tool-id {t.tool_id}, '
        f'has tool-label "{t.label}", has tool-model "{t.model}", '
        f'has tool-type "{t.type}", has can-grip {str(t.can_grip).lower()}, '
        f'has can-reference {str(t.can_reference).lower()}, '
        f'has grip-range-min {t.grip_range_um[0]}, has grip-range-max {t.grip_range_um[1]}, '
        f'has payload-kg {t.payload_kg};'
    )


def _position_insert(p) -> str:
    pos = p.position
    return (
        f'insert $p isa maze-position, has position-name "{p.position_name}", '
        f'has description "{p.description}", '
        f'has x-mm {pos["X"]}, has y-mm {pos["Y"]}, has z-mm {pos["Z"]}, '
        f'has a-deg {pos["A"]}, has b-deg {pos.get("B", 0.0)}, has c-deg {pos.get("C", 0.0)};'
    )


def _step_insert(s) -> str:
    pre = ", ".join(f'has precondition "{c}"' for c in s.preconditions)
    post = ", ".join(f'has postcondition "{c}"' for c in s.postconditions)
    return (
        f'insert $s isa process-step, has step-index {s.step_index}, '
        f'has step-name "{s.step_name}", has op-index {s.op_index}, '
        f'has component "{s.component}", has position-name "{s.position_name}"'
        + (f", {pre}" if pre else "")
        + (f", {post}" if post else "")
        + ";"
    )


def build_typeql(dry_run: bool = False) -> list[str]:
    statements: list[str] = []

    statements.append(f'# Schema loaded separately from {SCHEMA_PATH}')

    robots = load_robots()
    for r in robots.values():
        statements.append(_robot_insert(r))

    tools = load_tools()
    for t in tools.values():
        statements.append(_tool_insert(t))

    positions = load_all_positions()
    for p in positions.values():
        statements.append(_position_insert(p))

    steps = load_process_steps()
    for s in steps:
        statements.append(_step_insert(s))

    # valid-tool-at relations
    for p in positions.values():
        for vt in p.valid_tools:
            statements.append(
                f'match $t isa tool, has tool-id {vt.tool_id}; '
                f'$p isa maze-position, has position-name "{p.position_name}"; '
                f'insert (capability-tool: $t, position: $p) isa valid-tool-at, '
                f'has open-position {vt.open_position};'
            )

    # valid-base-at relations
    for p in positions.values():
        for vb in p.valid_bases:
            for base_id in vb.base_id_list:
                statements.append(
                    f'match $r isa robot, has robot-id {vb.robot_id}; '
                    f'$p isa maze-position, has position-name "{p.position_name}"; '
                    f'insert (positioned-robot: $r, position: $p) isa valid-base-at, '
                    f'has base-id {base_id};'
                )

    return statements


def run(host: str, port: int, db: str, dry_run: bool) -> None:
    statements = build_typeql(dry_run=dry_run)

    if dry_run:
        for s in statements:
            print(s)
        print(f"\n# {len(statements)} statements generated")
        return

    try:
        from typedb.driver import TypeDB, Credentials, DriverOptions, TransactionType
    except ImportError:
        print("typedb-driver not installed. Run: pip install typedb-driver==3.10.0", file=sys.stderr)
        sys.exit(1)

    username = os.environ.get("TYPEDB_USERNAME", "admin")
    password = os.environ.get("TYPEDB_PASSWORD", "password")
    creds = Credentials(username, password)
    driver_opts = DriverOptions(is_tls_enabled=False)

    log = TypeDBLog(db=db)
    log.session_start(host, port)
    ok = err = 0

    with TypeDB.driver(f"{host}:{port}", creds, driver_opts) as driver:
        if not driver.databases.contains(db):
            driver.databases.create(db)
            print(f"created database: {db}")

        # load schema (file already contains the 'define' keyword)
        schema_tql = SCHEMA_PATH.read_text()
        with driver.transaction(db, TransactionType.SCHEMA) as tx:
            with log.op("SCHEMA", schema_tql) as ctx:
                tx.query(schema_tql).resolve()
                tx.commit()
                ctx.rows = 0
            ok += 1
        print("schema loaded")

        # insert data
        data_stmts = [s for s in statements if not s.startswith("#")]
        with driver.transaction(db, TransactionType.WRITE) as tx:
            for stmt in data_stmts:
                op_type = "QUERY" if stmt.startswith("match") else "INSERT"
                try:
                    with log.op(op_type, stmt) as ctx:
                        tx.query(stmt).resolve()
                        ctx.rows = 1
                    ok += 1
                except Exception:
                    err += 1
                    raise
            tx.commit()

        print(f"inserted {len(data_stmts)} statements into '{db}'")

    log.session_end(statements_ok=ok, statements_err=err)


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed TypeDB knowledge graph from JSON fixtures")
    parser.add_argument("--host", default=os.environ.get("TYPEDB_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("TYPEDB_PORT", 1729)))
    parser.add_argument("--db", default=os.environ.get("TYPEDB_DB", "capability_kg"))
    parser.add_argument("--dry-run", action="store_true", help="print TypeQL without connecting")
    args = parser.parse_args()
    run(args.host, args.port, args.db, args.dry_run)


if __name__ == "__main__":
    main()
