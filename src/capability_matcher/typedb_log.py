"""
Structured logger for TypeDB communication.

Writes JSON-lines to DATA_DIR/logs/typedb.log (default: ./data/logs/typedb.log).
Each line is one operation record:

  {"ts": "...", "op": "INSERT|QUERY|SCHEMA|ERROR", "db": "...",
   "tql": "...", "rows": N, "duration_ms": N, "error": "..."}

Usage:
    from capability_matcher.typedb_log import TypeDBLog

    log = TypeDBLog(db="capability_kg")
    with log.op("INSERT", stmt) as ctx:
        tx.query(stmt)
        ctx.rows = 1          # optional: set result count
"""
from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Generator


def _log_path() -> Path:
    return Path(os.environ.get("DATA_DIR", "./data")) / "logs" / "typedb.log"


def _build_logger() -> logging.Logger:
    logger = logging.getLogger("typedb")
    if logger.handlers:
        return logger

    log_file = _log_path()
    log_file.parent.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)

    stderr = logging.StreamHandler()
    stderr.setFormatter(logging.Formatter("[typedb] %(message)s"))
    logger.addHandler(stderr)

    logger.setLevel(logging.DEBUG)
    return logger


@dataclass
class _OpContext:
    rows: int | None = field(default=None)


class TypeDBLog:
    """Thin structured logger scoped to one TypeDB database session."""

    def __init__(self, db: str) -> None:
        self._db = db
        self._logger = _build_logger()

    def _write(self, record: dict) -> None:
        self._logger.info(json.dumps(record, ensure_ascii=False))

    @contextmanager
    def op(self, op_type: str, tql: str) -> Generator[_OpContext, None, None]:
        """Context manager that logs start + result/error of one TypeDB operation."""
        ctx = _OpContext()
        t0 = time.perf_counter()
        try:
            yield ctx
            elapsed = round((time.perf_counter() - t0) * 1000, 2)
            self._write({
                "ts": datetime.now(timezone.utc).isoformat(),
                "op": op_type,
                "db": self._db,
                "tql": tql[:500],  # truncate very long statements
                "rows": ctx.rows,
                "duration_ms": elapsed,
            })
        except Exception as exc:
            elapsed = round((time.perf_counter() - t0) * 1000, 2)
            self._write({
                "ts": datetime.now(timezone.utc).isoformat(),
                "op": "ERROR",
                "db": self._db,
                "tql": tql[:500],
                "error": str(exc),
                "duration_ms": elapsed,
            })
            raise

    def session_start(self, host: str, port: int) -> None:
        self._write({
            "ts": datetime.now(timezone.utc).isoformat(),
            "op": "SESSION_START",
            "db": self._db,
            "host": host,
            "port": port,
        })

    def session_end(self, statements_ok: int, statements_err: int) -> None:
        self._write({
            "ts": datetime.now(timezone.utc).isoformat(),
            "op": "SESSION_END",
            "db": self._db,
            "statements_ok": statements_ok,
            "statements_err": statements_err,
        })
