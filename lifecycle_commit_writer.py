# -*- coding: utf-8 -*-
"""Lifecycle commit writer (initial skeleton).

This module implements a simple, robust write-layer for order lifecycle commits.

Design goals / decisions
- Use SQLite for lifecycle store to provide ACID guarantees for lifecycle state
  changes. SQLite is a lightweight, well-supported option that provides
  transactional semantics required for safe lifecycle commits.
- Provide a prepare/finalize workflow:
  1. prepare_commit(...) - validate and insert a "prepared" transition record
     inside a DB transaction and return a commit_token.
  2. The orchestrator performs any external writes (runtime files, queue
     writes) while the lifecycle transition is in prepared state.
  3. finalize_commit(commit_token, success=True|False) - atomically mark the
     prepared transition as committed or aborted.

Rationale for prepare/finalize
- Many lifecycle commits require additional side-effects outside the lifecycle
  store (runtime files, order queues). Those cannot participate in the same
  local DB transaction. The prepare/finalize pattern allows the lifecycle
  change to be recorded durably (but not final) and then finalized after
  external actions succeed. If external actions fail, the orchestrator calls
  finalize_commit(..., success=False) to mark the transition aborted.

Schema (created automatically on first use)
- metadata(k TEXT PRIMARY KEY, v TEXT)
- transitions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    candidate_event TEXT NOT NULL,
    evidence_id TEXT,
    target_name TEXT,
    lifecycle_store TEXT,
    payload TEXT, -- JSON encoded commit_contract/plan
    status TEXT NOT NULL, -- prepared | committed | aborted
    commit_token TEXT,
    created_at TEXT NOT NULL,
    applied_at TEXT
  )
- journal(id INTEGER PRIMARY KEY AUTOINCREMENT, commit_token TEXT, action TEXT, payload TEXT, created_at TEXT)

Indexes
- idx_transitions_order(order_id)
- idx_transitions_status(status)
- idx_transitions_evidence(evidence_id)

Rollback / recovery policy
- All lifecycle store mutations are performed inside SQLite transactions.
  If any DB operation fails, the transaction is rolled back by SQLite.
- For distributed/side-effect writes (runtime/queue) the orchestrator must
  perform those between prepare and finalize. If they fail, call
  finalize_commit(..., success=False). The writer records aborted transitions
  (for audit) and remains idempotent so replays are safe.
- The writer provides simple recovery helpers (e.g. list_prepared) so a
  supervisor can inspect and retry or abort stale prepared transitions.

This is an initial, opinionated implementation intended to be extended as
project needs and operational experience grow.
"""

from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4


def _now_text() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


class LifecycleCommitWriter:
    """A small lifecycle commit writer using SQLite.

    Typical usage:

        writer = LifecycleCommitWriter(db_path)
        prep = writer.prepare_commit(commit_contract, commit_plan, snapshot, context)
        if not prep.get("ok"):
            # handle blocked / issues
        # do runtime/queue writes here (external)
        writer.finalize_commit(prep["commit_token"], success=True)

    The implementation keeps prepared records until finalized so an
    external operator or recovery process can inspect pending commits.
    """

    def __init__(self, db_path: str | Path) -> None:
        if db_path is None or not str(db_path).strip():
            raise ValueError("db_path is required")
        self.db_path = Path(db_path)
        # ensure parent dir exists
        if not self.db_path.parent.exists():
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        # Pragmas for reasonable durability
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA foreign_keys=ON")
        except Exception:
            # If pragmas are not supported for some reason, we continue with
            # the default SQLite settings rather than failing hard.
            pass
        return conn

    def _ensure_schema(self) -> None:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    k TEXT PRIMARY KEY,
                    v TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS transitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT NOT NULL,
                    candidate_event TEXT NOT NULL,
                    evidence_id TEXT,
                    target_name TEXT,
                    lifecycle_store TEXT,
                    payload TEXT,
                    status TEXT NOT NULL,
                    commit_token TEXT,
                    created_at TEXT NOT NULL,
                    applied_at TEXT
                );

                CREATE TABLE IF NOT EXISTS journal (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    commit_token TEXT,
                    action TEXT,
                    payload TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_transitions_order ON transitions(order_id);
                CREATE INDEX IF NOT EXISTS idx_transitions_status ON transitions(status);
                CREATE INDEX IF NOT EXISTS idx_transitions_evidence ON transitions(evidence_id);
                """
            )
            conn.commit()
        finally:
            conn.close()

    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {k: row[k] for k in row.keys()}

    def _existing_duplicate(self, conn: sqlite3.Connection, order_id: str, event: str, evidence_id: str) -> Optional[Dict[str, Any]]:
        cur = conn.cursor()
        # If evidence_id provided, prefer matching it
        if evidence_id:
            cur.execute("SELECT * FROM transitions WHERE evidence_id = ? AND status = 'committed' LIMIT 1", (evidence_id,))
            row = cur.fetchone()
            if row:
                return self._row_to_dict(row)

        # Fallback: check for a committed or prepared transition with same order+event
        cur.execute(
            "SELECT * FROM transitions WHERE order_id = ? AND candidate_event = ? AND status IN ('committed','prepared') LIMIT 1",
            (order_id, event),
        )
        row = cur.fetchone()
        if row:
            return self._row_to_dict(row)
        return None

    def prepare_commit(
        self,
        commit_contract: Any,
        commit_plan: Any,
        current_lifecycle_snapshot: Any = None,
        context: Any = None,
    ) -> Dict[str, Any]:
        """Validate and prepare a lifecycle commit.

        On success returns a dict with keys: ok, prepared (True), commit_token, transition_id.
        On blocked/invalid returns ok=False and issues.
        """
        contract = deepcopy(commit_contract) if isinstance(commit_contract, dict) else {}
        if not contract:
            return {"ok": False, "prepared": False, "issues": ["commit_contract is required"]}

        order_id = str(contract.get("order_id") or "").strip()
        event = str(contract.get("candidate_lifecycle_event") or "").strip()
        evidence_id = str(contract.get("evidence_id") or "").strip()
        lifecycle_store = str(contract.get("lifecycle_store") or "").strip()

        field_values = {
            "order_id": order_id,
            "candidate_lifecycle_event": event,
            "lifecycle_store": lifecycle_store,
        }
        missing = [f for f, value in field_values.items() if not value]
        if missing:
            return {"ok": False, "prepared": False, "issues": ["missing fields: " + ", ".join(missing)]}

        token = uuid4().hex
        payload = json.dumps({"commit_contract": contract, "commit_plan": commit_plan or {}}, ensure_ascii=False)

        conn = self._connect()
        try:
            cur = conn.cursor()
            # Start an explicit immediate transaction to reduce TOCTOU with external readers
            cur.execute("BEGIN IMMEDIATE")

            # Duplicate detection
            dup = self._existing_duplicate(conn, order_id, event, evidence_id)
            if dup:
                # If duplicate is already committed, treat as blocked/duplicate
                return {
                    "ok": False,
                    "prepared": False,
                    "issues": ["duplicate lifecycle transition exists"],
                    "existing": dup,
                }

            now = _now_text()
            cur.execute(
                "INSERT INTO transitions (order_id, candidate_event, evidence_id, target_name, lifecycle_store, payload, status, commit_token, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (order_id, event, evidence_id or None, contract.get("target_name"), lifecycle_store, payload, "prepared", token, now),
            )
            transition_id = cur.lastrowid
            # journal entry (optional audit trail)
            cur.execute("INSERT INTO journal (commit_token, action, payload, created_at) VALUES (?, ?, ?, ?)", (token, "prepared", payload, now))
            conn.commit()
            return {"ok": True, "prepared": True, "commit_token": token, "transition_id": transition_id}
        except Exception as exc:  # pragma: no cover - defensive
            try:
                conn.rollback()
            except Exception:
                pass
            return {"ok": False, "prepared": False, "issues": [f"prepare failed: {exc}"]}
        finally:
            conn.close()

    def finalize_commit(self, commit_token: str, success: bool, metadata: Any = None) -> Dict[str, Any]:
        """Finalize a previously prepared commit.

        - If success is True, the prepared transition is marked 'committed' and
          applied_at timestamp recorded.
        - If success is False, the prepared transition is marked 'aborted'.

        Returns a dict with ok True/False and status information.
        """
        if not commit_token:
            return {"ok": False, "issues": ["commit_token is required"]}

        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            cur.execute("SELECT * FROM transitions WHERE commit_token = ? AND status = 'prepared' LIMIT 1", (commit_token,))
            row = cur.fetchone()
            if not row:
                conn.rollback()
                return {"ok": False, "issues": ["no prepared transition found for commit_token"]}

            now = _now_text()
            if success:
                cur.execute("UPDATE transitions SET status = 'committed', applied_at = ? WHERE id = ?", (now, row["id"]))
                cur.execute("INSERT INTO journal (commit_token, action, payload, created_at) VALUES (?, ?, ?, ?)", (commit_token, "committed", json.dumps(metadata or {}), now))
                conn.commit()
                return {"ok": True, "finalized": True, "status": "committed", "transition_id": row["id"]}
            else:
                # Mark as aborted for audit. Keep the record for later inspection.
                cur.execute("UPDATE transitions SET status = 'aborted', applied_at = ? WHERE id = ?", (now, row["id"]))
                cur.execute("INSERT INTO journal (commit_token, action, payload, created_at) VALUES (?, ?, ?, ?)", (commit_token, "aborted", json.dumps(metadata or {}), now))
                conn.commit()
                return {"ok": True, "finalized": True, "status": "aborted", "transition_id": row["id"]}
        except Exception as exc:  # pragma: no cover - defensive
            try:
                conn.rollback()
            except Exception:
                pass
            return {"ok": False, "issues": [f"finalize failed: {exc}"]}
        finally:
            conn.close()

    def list_prepared(self, lifecycle_store: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return all currently prepared transitions (optionally filtered by lifecycle_store)."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            if lifecycle_store:
                cur.execute("SELECT * FROM transitions WHERE status = 'prepared' AND lifecycle_store = ? ORDER BY created_at", (lifecycle_store,))
            else:
                cur.execute("SELECT * FROM transitions WHERE status = 'prepared' ORDER BY created_at")
            rows = cur.fetchall()
            return [self._row_to_dict(r) for r in rows]
        finally:
            conn.close()

    def read_store_snapshot(self, lifecycle_store: str) -> Dict[str, Any]:
        """Build a lightweight snapshot of the lifecycle store from DB rows.

        Snapshot fields approximate those used by dry-run/preview code in the
        repository (snapshot_valid, lifecycle_store, existing_transitions, existing_events).
        """
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM transitions WHERE lifecycle_store = ? ORDER BY created_at", (lifecycle_store,))
            rows = cur.fetchall()
            existing = []
            for r in rows:
                payload = None
                try:
                    payload = json.loads(r["payload"]) if r["payload"] else None
                except Exception:
                    payload = None
                existing.append(
                    {
                        "id": r["id"],
                        "order_id": r["order_id"],
                        "candidate_lifecycle_event": r["candidate_event"],
                        "evidence_id": r["evidence_id"],
                        "status": r["status"],
                        "created_at": r["created_at"],
                        "applied_at": r["applied_at"],
                        "payload": payload,
                    }
                )
            return {"snapshot_valid": True, "lifecycle_store": lifecycle_store, "existing_transitions": existing, "existing_events": []}
        finally:
            conn.close()
