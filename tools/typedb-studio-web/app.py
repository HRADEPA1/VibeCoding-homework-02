"""
TypeDB Studio Web — lightweight browser-based TypeDB query interface.

GET  /           → Studio HTML UI
GET  /api/status → connection status + database list
POST /api/query  → execute a TypeQL statement
                   body: {"db": str, "query": str, "tx_type": "read"|"write"|"schema"}
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

TYPEDB_HOST = os.environ.get("TYPEDB_HOST", "localhost")
TYPEDB_PORT = int(os.environ.get("TYPEDB_PORT", 1729))
TYPEDB_USERNAME = os.environ.get("TYPEDB_USERNAME", "admin")
TYPEDB_PASSWORD = os.environ.get("TYPEDB_PASSWORD", "password")
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="TypeDB Studio Web", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _driver():
    try:
        from typedb.driver import TypeDB, Credentials, DriverOptions
        creds = Credentials(TYPEDB_USERNAME, TYPEDB_PASSWORD)
        opts = DriverOptions(is_tls_enabled=False)
        return TypeDB.driver(f"{TYPEDB_HOST}:{TYPEDB_PORT}", creds, opts)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Cannot connect to TypeDB: {exc}")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status():
    try:
        with _driver() as driver:
            dbs = [db.name for db in driver.databases.all()]
        return {"connected": True, "host": TYPEDB_HOST, "port": TYPEDB_PORT, "databases": dbs}
    except HTTPException as exc:
        return JSONResponse(status_code=200, content={
            "connected": False, "host": TYPEDB_HOST, "port": TYPEDB_PORT,
            "databases": [], "error": exc.detail,
        })


class QueryRequest(BaseModel):
    db: str
    query: str
    tx_type: str = "read"   # "read" | "write" | "schema"


def _rows_to_json(answer) -> list[dict[str, Any]]:
    """Convert TypeDB 3.10 ConceptRowIterator to a JSON-serialisable list."""
    rows = []
    try:
        if not hasattr(answer, "is_concept_rows") or not answer.is_concept_rows():
            return rows
        for row in answer:
            record: dict[str, Any] = {}
            for var in row.column_names():
                concept = row.get(var)
                if concept is None:
                    record[var] = None
                    continue
                record[var] = str(concept)
            rows.append(record)
    except Exception:
        pass
    return rows


@app.post("/api/query")
def run_query(req: QueryRequest):
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Query is empty")

    tx_type = req.tx_type.lower()
    if tx_type not in {"read", "write", "schema"}:
        raise HTTPException(status_code=400, detail="tx_type must be read, write, or schema")

    try:
        from typedb.driver import TransactionType
        tx_type_enum = {
            "read":   TransactionType.READ,
            "write":  TransactionType.WRITE,
            "schema": TransactionType.SCHEMA,
        }[tx_type]

        with _driver() as driver:
            if not driver.databases.contains(req.db):
                raise HTTPException(status_code=404, detail=f"Database '{req.db}' not found")

            with driver.transaction(req.db, tx_type_enum) as tx:
                result = tx.query(req.query).resolve()
                rows = _rows_to_json(result)
                if tx_type in {"write", "schema"}:
                    tx.commit()

        return {"ok": True, "tx_type": tx_type, "rows": rows, "count": len(rows)}

    except HTTPException:
        raise
    except Exception as exc:
        return JSONResponse(status_code=200, content={"ok": False, "error": str(exc), "rows": [], "count": 0})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8889, reload=True)
