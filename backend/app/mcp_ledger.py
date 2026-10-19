"""仅持久化幂等摘要和引用；不持久化高德原始响应或任务运行态。"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path


class RequestLedger:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS mcp_requests (
                owner TEXT, operation TEXT, request_id TEXT, digest TEXT NOT NULL,
                state TEXT NOT NULL, reference TEXT NOT NULL,
                PRIMARY KEY(owner, operation, request_id))""")
        os.chmod(path, 0o600)

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def get(self, owner: str, operation: str, request_id: str):
        with self.connect() as db:
            row = db.execute("SELECT digest,state,reference FROM mcp_requests WHERE owner=? AND operation=? AND request_id=?",
                             (owner, operation, request_id)).fetchone()
        return {"digest": row[0], "state": row[1], "reference": json.loads(row[2])} if row else None

    def put(self, owner: str, operation: str, request_id: str, digest: str, state: str, reference: dict):
        with self.connect() as db:
            db.execute("INSERT INTO mcp_requests VALUES (?,?,?,?,?,?) ON CONFLICT(owner,operation,request_id) "
                       "DO UPDATE SET state=excluded.state,reference=excluded.reference",
                       (owner, operation, request_id, digest, state, json.dumps(reference)))
